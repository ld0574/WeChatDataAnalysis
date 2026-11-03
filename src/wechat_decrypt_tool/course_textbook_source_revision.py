"""One finite semantic revision of a completed source audit, never a retry loop."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

from . import course_textbook_resolution as original
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore, validate_result
from .course_wiki_writer import DraftJob

POLICY = "single-original-source-semantic-revision-v1"
BODY = copy.deepcopy(original.DECISION)
del BODY["oneOf"][0]["properties"]["points"]["items"]["properties"]["quotations"]
BODY["oneOf"][0]["properties"]["points"]["items"]["required"].remove("quotations")
PROMPT = (
    original.COMMON
    + """
依据这一份完整 source 修订 candidate，处理 prior_audit 指出的事实、条件和遗漏问题。
prior_audit 是编辑意见，不能代替原文；指出的问题若不符合原文，不要机械采纳。
只保留原文实际教授的全部有效知识，不添加外部常识、猜测或不存在的细节。
按定义、区别、条件、步骤、例子与限制完整提取有效教学；可合并同义重复点。
片段短、解释不完整或在别处重复，不等于没有教学；纯寒暄、行政、推广才可排除。
教学返回 kind=teaching 和 points，每点包括 label、teaching、category。
非教学返回 kind=non_teaching 和充分的 reason。不要自行输出 quotations；
程序会把每点引用固定到这一份完整原文，再让独立审核核对事实支持与遗漏。
这是一次有限修订，不是文章或人工验收。只输出符合 schema 的 JSON。
"""
)


def source_decision(body, source):
    import jsonschema

    jsonschema.validate(body, BODY)
    value = copy.deepcopy(body)
    for point in value.get("points", []):
        point["quotations"] = [source["text"]]
    original.validate_decision(value, source)
    return value


def inputs(root, parent_job):
    inventory, parent = original.inputs(root, parent_job)
    if parent.get("policy") != original.POLICY or parent.get("source_revision_rounds"):
        raise ValueError("requires_single_revision_original_resolution_parent")
    scope = sorted(issue["reference"] for issue in parent["issues"])
    if (
        not scope
        or len(scope) != len(set(scope))
        or any("resolution_audit" not in issue for issue in parent["issues"])
    ):
        raise ValueError("requires_unique_negative_completed_source_audits")
    folder = Path(root) / "wiki/textbook-keyed-jobs" / parent_job
    progress = json.loads((folder / "progress.json").read_bytes())
    if progress["stage"] != "literal_source_resolution_complete":
        raise ValueError("requires_completed_resolution_before_semantic_revision")
    _, grandparent = original.inputs(root, parent["parent_job"])
    manifest = json.loads((folder / "manifest.json").read_bytes())
    bound = original.ResolutionJob(
        TextbookStore(root),
        inventory,
        grandparent,
        manifest["recipe"]["model_digest"],
        0,
    )
    if bound.key != parent_job or parent["resolved_source_count"] != len(
        original.affected_sources(grandparent)
    ):
        raise ValueError("requires_unchanged_full_resolution_execution")
    decisions, audits = {}, {}
    old_scope = original.affected_sources(grandparent)
    for issue in parent["issues"]:
        ref = issue["reference"]
        number = old_scope.index(ref)
        source = inventory["sources"][ref]
        context = {
            "source": {"kind": source["kind"], "text": source["text"]},
            "parent_disposition": grandparent["source_dispositions"][ref],
            "parent_points": {
                key: point
                for key, point in grandparent["points"].items()
                if ref in point["citations"]
            },
        }
        for suffix, prompt, schema in (
            ("resolve", original.PROMPT, original.DECISION),
            ("audit", original.REVIEW, original.AUDIT),
        ):
            envelope = json.loads(
                (folder / f"source-{number:04d}-{suffix}.json").read_bytes()
            )
            _, binding = bound.stage_input(
                prompt,
                schema,
                context,
                evidence_packet={"sources": {ref: source}},
            )
            if envelope["input_sha256"] != binding or envelope["data_sha256"] != sha(
                canonical(envelope["data"])
            ):
                raise ValueError("requires_unchanged_original_source_audit_inputs")
            if suffix == "resolve":
                decision = envelope["data"]
                original.validate_decision(decision, source)
                context = {**context, "candidate": decision}
            else:
                audit = envelope["data"]
                original.validate_audit(audit)
                if (
                    audit != issue["resolution_audit"]
                    or audit["verdict"] != "needs_changes"
                ):
                    raise ValueError("requires_original_negative_source_audit")
        expected = {
            f"R{number:04d}_{n:03d}": {
                **{k: v for k, v in point.items() if k != "quotations"},
                "citations": [ref],
            }
            for n, point in enumerate(decision.get("points", []))
        }
        actual = {
            key: point
            for key, point in parent["points"].items()
            if ref in point["citations"]
        }
        if expected != actual or parent["source_dispositions"][ref][
            "non_teaching_reason"
        ] != decision.get("reason", ""):
            raise ValueError("requires_original_candidate_matches_compiled_ledger")
        decisions[ref], audits[ref] = decision, audit
    return inventory, parent, decisions, audits


def compile_ledger(inventory, parent, decisions, audits, job):
    scope = {issue["reference"] for issue in parent["issues"]}
    if set(decisions) != scope or set(audits) != scope:
        raise ValueError("requires_exact_source_revision_scope")
    by_page = {ref: n for n, refs in enumerate(inventory["pages"]) for ref in refs}
    points = {
        key: copy.deepcopy(point)
        for key, point in parent["points"].items()
        if not scope.intersection(point["citations"])
    }
    issues = []
    for number, ref in enumerate(sorted(scope)):
        original.validate_decision(decisions[ref], inventory["sources"][ref])
        original.validate_audit(audits[ref])
        if audits[ref]["verdict"] != "supported":
            issues.append(
                {
                    "page": by_page[ref],
                    "reference": ref,
                    "source_revision_audit": audits[ref],
                }
            )
        for n, point in enumerate(decisions[ref].get("points", [])):
            key = f"S{number:04d}_{n:03d}"
            if key in points:
                raise ValueError("requires_preserved_unrelated_point_identity")
            points[key] = {
                **{key: value for key, value in point.items() if key != "quotations"},
                "citations": [ref],
            }
    dispositions = {}
    for ref, old in parent["source_dispositions"].items():
        dispositions[ref] = {
            "points": [
                key for key, point in points.items() if ref in point["citations"]
            ],
            "non_teaching_reason": decisions[ref].get("reason", "")
            if ref in scope
            else old["non_teaching_reason"],
        }
    value = {
        **parent,
        "policy": POLICY,
        "job": job,
        "parent_job": parent["job"],
        "parent_ledger_sha256": sha(canonical(parent)),
        "source_revision_rounds": 1,
        "revised_source_count": len(scope),
        "points": points,
        "source_dispositions": dispositions,
        "issues": issues,
        "pages": [
            {
                "page": n,
                "points": [
                    key
                    for key, point in points.items()
                    if set(point["citations"]) <= set(refs)
                ],
                "local_review_passed": not any(issue["page"] == n for issue in issues),
            }
            for n, refs in enumerate(inventory["pages"])
        ],
        "automatic_local_evidence_review_passed": not issues,
        "state": "ledger_requires_semantic_repair"
        if issues
        else "ledger_ready_for_conceptual_reorganization",
    }
    validate_result(value, inventory, job)
    return value


class SourceRevisionJob(original.ResolutionJob):
    def __init__(self, store, inventory, parent, digest, wait_seconds):
        DraftJob.__init__(
            self,
            store,
            {"sources": inventory["sources"]},
            "qwen3.5:9b",
            digest,
            wait_seconds,
            pipeline=POLICY,
            job_directory="textbook-keyed-jobs",
            max_attempts=2,
            recipe_extra={
                "parent_sha256": sha(canonical(parent)),
                "scope": sorted(issue["reference"] for issue in parent["issues"]),
                "prompts": [PROMPT, original.REVIEW],
                "schemas": [BODY, original.AUDIT],
                "semantic_revision_rounds": 1,
                "text_profile": original.profile(),
                "module_sha256": {
                    name: sha(Path(__file__).with_name(name + ".py").read_bytes())
                    for name in (
                        "course_textbook_source_revision",
                        "course_textbook_resolution",
                        "course_textbook_evidence",
                        "course_textbook_ledger",
                        "course_wiki_writer",
                        "course_wiki_text_inference",
                    )
                },
            },
        )
        self.source_count = len(parent["issues"])
        self.finished_sources = 0

    def progress(self, state, stage, **extra):
        self.current = stage
        value = {
            "state": state,
            "stage": stage,
            "job": self.key,
            "policy": POLICY,
            "updated": time.time(),
            "completed_stages": self.completed,
            "completed_sources": self.finished_sources,
            "total_sources": self.source_count,
            "reader_article_published": False,
            **self.identity,
            **extra,
        }
        atomic_json(self.root / "progress.json", value)
        atomic_json(
            self.store.root / "wiki/textbook-source-revision-last-build.json", value
        )


def run(root, parent_job, *, wait_seconds=3600):
    from .course_pdf_ocr import local_model_digest

    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 3600:
        raise ValueError("requires_finite_source_revision_wait")
    root = Path(root).resolve()
    inventory, parent, old, old_audits = inputs(root, parent_job)
    store = TextbookStore(root)
    store.checkpoint()
    with schema_lease(
        root
        / "wiki/textbook-evidence"
        / parent["inventory_sha256"]
        / "literal-resolution.lock",
        timeout=0,
    ):
        job = SourceRevisionJob(
            store, inventory, parent, local_model_digest("qwen3.5:9b"), wait_seconds
        )
        contract = (
            root
            / "wiki/textbook-keyed-jobs"
            / parent_job
            / "source-revision-execution.json"
        )
        binding = {
            "job": job.key,
            "parent_sha256": sha(canonical(parent)),
            "scope": sorted(old),
            "semantic_revision_rounds": 1,
        }
        if contract.exists() and json.loads(contract.read_bytes()) != binding:
            raise ValueError(
                "requires_preserved_source_revision_budget_no_successor_loop"
            )
        if not contract.exists():
            atomic_json(contract, binding)
        decisions, audits = {}, {}
        try:
            for number, ref in enumerate(sorted(old)):
                source = inventory["sources"][ref]
                packet = {"sources": {ref: source}}
                context = {
                    "source": {"kind": source["kind"], "text": source["text"]},
                    "candidate": old[ref],
                    "prior_audit": old_audits[ref],
                }
                prefix = f"source-{number:04d}"
                body = job.stage(
                    prefix + "-revise",
                    PROMPT,
                    BODY,
                    context,
                    lambda value: source_decision(value, source),
                    evidence_packet=packet,
                )
                decision = source_decision(body, source)
                audit = job.stage(
                    prefix + "-audit",
                    original.REVIEW,
                    original.AUDIT,
                    {**context, "candidate": decision},
                    original.validate_audit,
                    evidence_packet=packet,
                )
                decisions[ref], audits[ref] = decision, audit
                job.finished_sources += 1
                job.progress("revising_negative_source_audits", prefix)
            ledger = compile_ledger(inventory, parent, decisions, audits, job.key)
            atomic_json(
                job.root / "ledger.json",
                {"data_sha256": sha(canonical(ledger)), "data": ledger},
            )
            job.progress(
                ledger["state"],
                "source_revision_complete",
                unresolved_sources=len(ledger["issues"]),
                knowledge_points=len(ledger["points"]),
            )
            return {
                "state": ledger["state"],
                "job": job.key,
                "revised_sources": len(decisions),
                "unresolved_sources": len(ledger["issues"]),
                "knowledge_points": len(ledger["points"]),
            }
        except BaseException as exc:
            job.progress(
                "paused" if isinstance(exc, InterruptedError) else "stopped",
                job.current,
                error_type=type(exc).__name__,
            )
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("command", choices=["run", "guard"])
    parser.add_argument("--parent", required=True)
    parser.add_argument("--wait-gpu-seconds", type=int, default=3600)
    args = parser.parse_args(argv)
    try:
        if args.command == "guard":
            from .course_gpu_guard import run as guard

            inputs(args.root, args.parent)
            guard(
                args.root,
                [
                    sys.executable,
                    "-m",
                    __spec__.name,
                    "--root",
                    str(args.root.resolve()),
                    "run",
                    "--parent",
                    args.parent,
                    "--wait-gpu-seconds",
                    str(args.wait_gpu_seconds),
                ],
                maximum_power=200,
                maximum_temperature=70,
                guard_name="wiki-revision-thermal-guard",
            )
        else:
            print(
                json.dumps(
                    run(args.root, args.parent, wait_seconds=args.wait_gpu_seconds)
                )
            )
    except Exception as exc:
        print(json.dumps({"state": "stopped", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
