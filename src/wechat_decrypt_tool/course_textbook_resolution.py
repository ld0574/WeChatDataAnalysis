"""Resolve only disputed ledger sources with literal evidence and a fresh audit.

This is a bound, finite successor, not a reset of the parent's attempt budget.
Every replacement fact is checked against a single complete original source;
unchanged source decisions and their accepted page reviews remain intact.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from pathlib import Path

import jsonschema

from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import load, sha
from .course_textbook_keyed_ledger import POINT_BODY
from .course_textbook_ledger import COMMON, TextbookStore, validate_result
from .course_wiki import TEXT, object_schema
from .course_wiki_text_inference import chat, profile
from .course_wiki_writer import DraftJob

POLICY = "disputed-source-literal-evidence-resolution-v1"
QUOTED_POINT = copy.deepcopy(POINT_BODY)
QUOTED_POINT["properties"]["quotations"] = {
    "type": "array",
    "minItems": 1,
    "items": TEXT,
    "uniqueItems": True,
}
QUOTED_POINT["required"].append("quotations")
DECISION = {
    "oneOf": [
        object_schema(
            {
                "kind": {"const": "teaching"},
                "points": {"type": "array", "minItems": 1, "items": QUOTED_POINT},
            }
        ),
        object_schema({"kind": {"const": "non_teaching"}, "reason": TEXT}),
    ]
}
AUDIT = object_schema(
    {
        "verdict": {"enum": ["supported", "needs_changes"]},
        "reason": TEXT,
        "missing_information": {"type": "array", "items": TEXT},
    }
)
PROMPT = (
    COMMON
    + """
现在只重新判读这一份完整原文，处理旧账本留下的排除/事实支持/遗漏争议。
parent_review 只是旧编辑判断，不是事实证据，不能机械照抄或用它替代 source。
若原文实际教授任何事实、区别、条件、步骤、例子或限制，kind=teaching，提取全部有效教学知识。
每点 label/teaching/category 之外必须给 quotations：支持该点的原文逐字连续子串，不得改写引文。
引文要体现有效依据，不用一个词或装饰性引用凑数；teaching 不能超过引文和完整原文实际支持的范围。
不要把“老师提到了某词”扩写成原文未教的知识。保留历史版本和适用条件，不用外部常识补造。
同义或跨来源重复的教学信息仍属教学；不能以别处已经教过为理由排除此来源。
纯寒暄、行政安排、推广、无意义识别或只做导航而无教学信息时，kind=non_teaching，reason 解释为什么。
必须区分“没有教学信息”和“讲过但不够完整”，不能因为片段短或难懂就判为无知识。
这只是内部写作输入，不能作为文章、人工验收或原视频可删的凭证。
"""
)
REVIEW = (
    COMMON
    + """
独立复核单份完整 source 与 candidate，parent_review 不是事实依据。
教学判断要逐点核对事实、条件、例子和限制是否全部得到引文及原文支持，是否遗漏其他有效教学信息。
非教学判断要检查是否确实没有有效教学；重复教学不能排除，不能把行政导航误判成技术事实。
verdict=supported 仅在完整原文支持整个决定且没有教学遗漏时使用，missing_information 必须为空。
否则 verdict=needs_changes，写明具体问题和 missing_information，不为旧审查意见捏造缺失知识。
reason 解释支持或问题；不要只写“通过”，不要写相反判断却给 supported。
"""
)


def validate_decision(value, source):
    jsonschema.validate(value, DECISION)
    if value["kind"] == "non_teaching":
        if not value["reason"].strip():
            raise ValueError("missing_resolution_exclusion_reason")
        return
    seen = set()
    for point in value["points"]:
        for field in ("label", "teaching"):
            if not point[field].strip():
                raise ValueError("missing_resolution_teaching_text")
        identity = (point["label"].strip(), point["teaching"].strip())
        if identity in seen:
            raise ValueError("duplicate_resolution_teaching_point")
        seen.add(identity)
        for quotation in point["quotations"]:
            if (
                quotation not in source["text"]
                or len(quotation.strip()) < min(8, len(source["text"].strip()))
                or not quotation.strip()
            ):
                raise ValueError("missing_resolution_literal_source_support")


def validate_audit(value):
    jsonschema.validate(value, AUDIT)
    if not value["reason"].strip() or any(
        not row.strip() for row in value["missing_information"]
    ):
        raise ValueError("missing_resolution_audit_reason")
    if value["verdict"] == "supported" and value["missing_information"]:
        raise ValueError("requires_resolution_consistent_audit_verdict")


def affected_sources(parent):
    refs = set()
    page_rows = {row["page"]: row for row in parent["pages"]}
    for issue in parent["issues"]:
        audit = issue["review"]
        ids = page_rows[issue["page"]]["points"]
        for row in audit["reviews"]:
            if row["verdict"] != "supported":
                refs.update(parent["points"][ids[row["point"]]]["citations"])
        refs.update(
            row["reference"] for row in audit["excluded_sources"] if not row["valid"]
        )
        refs.update(
            ref for point in audit["missing_points"] for ref in point["citations"]
        )
    # Removing a multi-source point must not strand another cited source or
    # silently discard a correct neighboring point sharing that evidence.
    changed = True
    while changed:
        before = set(refs)
        for point in parent["points"].values():
            if refs.intersection(point["citations"]):
                refs.update(point["citations"])
        changed = refs != before
    if not refs <= set(parent["source_dispositions"]):
        raise ValueError("missing_resolution_known_original_sources")
    return sorted(refs)


def inputs(root, parent_job):
    if not re.fullmatch(r"[a-f0-9]{32}", parent_job):
        raise ValueError("requires_resolution_parent_job_id")
    saved = json.loads(
        (
            Path(root) / "wiki/textbook-keyed-jobs" / parent_job / "ledger.json"
        ).read_bytes()
    )
    parent = saved["data"]
    if saved["data_sha256"] != sha(canonical(parent)):
        raise ValueError("requires_resolution_unchanged_parent_ledger")
    inventory = load(root, parent["inventory_sha256"])
    validate_result(parent, inventory, parent_job)
    return inventory, parent


def compile_ledger(inventory, parent, decisions, audits, job):
    targeted = set(affected_sources(parent))
    if set(decisions) != targeted or set(audits) != targeted:
        raise ValueError("missing_resolution_exact_disputed_source_scope")
    by_page = {
        ref: number for number, refs in enumerate(inventory["pages"]) for ref in refs
    }
    kept = {
        key: copy.deepcopy(point)
        for key, point in parent["points"].items()
        if not targeted.intersection(point["citations"])
    }
    issues, replacements = [], {}
    for number, ref in enumerate(sorted(targeted)):
        decision, audit = decisions[ref], audits[ref]
        validate_decision(decision, inventory["sources"][ref])
        validate_audit(audit)
        if audit["verdict"] != "supported":
            issues.append(
                {"page": by_page[ref], "reference": ref, "resolution_audit": audit}
            )
        if decision["kind"] == "teaching":
            for index, point in enumerate(decision["points"]):
                key = f"R{number:04d}_{index:03d}"
                replacements[key] = {
                    **{k: v for k, v in point.items() if k != "quotations"},
                    "citations": [ref],
                }
    points = {**kept, **replacements}
    dispositions = {}
    for ref, old in parent["source_dispositions"].items():
        ids = [key for key, point in points.items() if ref in point["citations"]]
        reason = old["non_teaching_reason"]
        if ref in targeted:
            reason = decisions[ref].get("reason", "")
        dispositions[ref] = {"points": ids, "non_teaching_reason": reason}
    pages = [
        {
            "page": number,
            "points": [
                key
                for key, point in points.items()
                if set(point["citations"]) <= set(refs)
            ],
            "local_review_passed": not any(issue["page"] == number for issue in issues),
        }
        for number, refs in enumerate(inventory["pages"])
    ]
    result = {
        **parent,
        "policy": POLICY,
        "job": job,
        "parent_job": parent["job"],
        "parent_ledger_sha256": sha(canonical(parent)),
        "points": points,
        "source_dispositions": dispositions,
        "pages": pages,
        "issues": issues,
        "automatic_local_evidence_review_passed": not issues,
        "whole_lesson_knowledge_coverage_verified": False,
        "semantic_accuracy_verified": False,
        "reader_article_published": False,
        "source_deletion_allowed": False,
        "resolved_source_count": len(targeted),
        "state": "ledger_requires_semantic_repair"
        if issues
        else "ledger_ready_for_conceptual_reorganization",
    }
    validate_result(result, inventory, job)
    return result


class ResolutionJob(DraftJob):
    def __init__(self, store, inventory, parent, digest, wait_seconds):
        super().__init__(
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
                "inventory_sha256": sha(canonical(inventory)),
                "scope": affected_sources(parent),
                "prompts": [PROMPT, REVIEW],
                "schemas": [DECISION, AUDIT],
                "semantic_repair_rounds": 0,
                "text_profile": profile(),
                "module_sha256": {
                    name: sha((Path(__file__).parent / (name + ".py")).read_bytes())
                    for name in (
                        "course_textbook_resolution",
                        "course_textbook_evidence",
                        "course_textbook_ledger",
                        "course_wiki_writer",
                        "course_wiki_text_inference",
                    )
                },
            },
        )
        self.source_count = len(affected_sources(parent))
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
        atomic_json(self.store.root / "wiki/textbook-resolution-last-build.json", value)

    def generate_response(self, messages, *, schema):
        if (
            len(canonical({"messages": messages, "schema": schema}).decode("utf-8"))
            > 16000
        ):
            raise ValueError("requires_resolution_smaller_complete_original_context")
        return chat(self.model, messages, schema=schema)


def run(root, parent_job, *, wait_seconds=3600):
    from .course_pdf_ocr import local_model_digest

    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 3600:
        raise ValueError("requires_resolution_finite_wait_budget")
    root = Path(root).resolve()
    inventory, parent = inputs(root, parent_job)
    store = TextbookStore(root)
    store.checkpoint()
    with schema_lease(
        root
        / "wiki/textbook-evidence"
        / parent["inventory_sha256"]
        / "literal-resolution.lock",
        timeout=0,
    ):
        job = ResolutionJob(
            store, inventory, parent, local_model_digest("qwen3.5:9b"), wait_seconds
        )
        contract = (
            root
            / "wiki/textbook-keyed-jobs"
            / parent_job
            / "literal-resolution-execution.json"
        )
        binding = {
            "job": job.key,
            "parent_sha256": sha(canonical(parent)),
            "scope": affected_sources(parent),
        }
        if contract.exists():
            if json.loads(contract.read_bytes()) != binding:
                raise ValueError("requires_resolution_preserve_bound_budget")
        else:
            atomic_json(contract, binding)
        decisions, audits = {}, {}
        job.progress("resolving_disputed_sources", "originals")
        try:
            for number, ref in enumerate(affected_sources(parent)):
                source = inventory["sources"][ref]
                packet = {"sources": {ref: source}}
                context = {
                    "source": {"kind": source["kind"], "text": source["text"]},
                    "parent_disposition": parent["source_dispositions"][ref],
                    "parent_points": {
                        key: point
                        for key, point in parent["points"].items()
                        if ref in point["citations"]
                    },
                }
                prefix = f"source-{number:04d}"
                decision = job.stage(
                    prefix + "-resolve",
                    PROMPT,
                    DECISION,
                    context,
                    lambda value: validate_decision(value, source),
                    evidence_packet=packet,
                )
                audit = job.stage(
                    prefix + "-audit",
                    REVIEW,
                    AUDIT,
                    {**context, "candidate": decision},
                    validate_audit,
                    evidence_packet=packet,
                )
                decisions[ref], audits[ref] = decision, audit
                job.finished_sources += 1
                job.progress("resolving_disputed_sources", prefix)
            result = compile_ledger(inventory, parent, decisions, audits, job.key)
            atomic_json(
                job.root / "ledger.json",
                {"data_sha256": sha(canonical(result)), "data": result},
            )
            job.progress(
                result["state"],
                "literal_source_resolution_complete",
                unresolved_sources=len(result["issues"]),
                knowledge_points=len(result["points"]),
            )
            return {
                "state": result["state"],
                "job": job.key,
                "resolved_sources": len(decisions),
                "unresolved_sources": len(result["issues"]),
                "knowledge_points": len(result["points"]),
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
