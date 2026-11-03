"""A bound successor with mandatory per-source and per-review output keys.

The predecessor's accepted pages are verified and reused. Its spent attempts
remain untouched. Only missing references in its last clean partial candidate
receive new extraction, followed by a complete original-page evidence review.
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
import psutil

from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import checked_sha, load, sha
from .course_textbook_ledger import (
    AUDIT,
    EXTRACT,
    LEDGER,
    POINT,
    REVIEW,
    COMMON,
    LedgerJob,
    TextbookStore,
    audit_passed,
    validate_audit,
    validate_ledger,
    validate_result,
)
from .course_textbook_ledger_cache import compose
from .course_wiki import TEXT, object_schema
from .course_wiki_text_inference import chat, profile, validate_metrics
from .course_wiki_writer import DraftJob

POLICY = "textbook-required-source-keys-successor-v1"
PART_CHARACTERS = 2800
POINT_BODY = copy.deepcopy(POINT)
POINT_BODY["properties"].pop("citations")
POINT_BODY["required"].remove("citations")
SOURCE_RESULT = {
    "oneOf": [
        object_schema(
            {
                "kind": {"const": "teaching"},
                "points": {"type": "array", "minItems": 1, "items": POINT_BODY},
            }
        ),
        object_schema({"kind": {"const": "non_teaching"}, "reason": TEXT}),
    ]
}
VERDICT = object_schema(
    {
        "verdict": {"type": "string", "enum": ["supported", "partial", "unsupported"]},
        "reason": TEXT,
    }
)
EXCLUSION_REVIEW = object_schema({"valid": {"type": "boolean"}, "reason": TEXT})
KEYED_EXTRACT = (
    COMMON
    + """
对 sources 的每个引用逐项填写结果，所有键都必须出现。
如果该来源包含有效教学内容，kind=teaching，并提取其中全部有效知识点，写 label、teaching 和 category。
teaching 应保留原文实际讲授的区别、条件、步骤、例子及限制；不要写“老师提到某术语”的空摘要，不补造因果或历史版本信息。
每个输出知识点只依据该键对应的原始来源；引用由程序绑定，不能把其他来源教授的事实搬来凑数。
同一事实在不同来源重复出现仍可记录，后续会跨来源归并；不能为了减少重复而把含教学信息的来源判为非教学。
只有寒暄、广告、纯导航、无意义识别等没有有效教学知识的来源，kind=non_teaching，写明确 reason。
context_sources 只帮助理解上下文，candidate 和 review 只是待检查的编辑结果，不是事实证据。
不写文章或按时间排列的章节。不要遗漏原文内部的教学信息，也不要扩写课程外常识。
"""
)
KEYED_REVIEW = (
    COMMON
    + """
独立用全部 sources 检查 candidate，逐项填写 schema 规定的知识点编号及非教学来源编号，不能漏项。
每个知识点判断 supported/partial/unsupported：只认可其 citations 指向的原文能共同支持的完整表述，检查条件、数值、因果和例子。
missing_points 仅列真正不同且确实教授、但 candidate 未表达的有效知识，必须引用支持该知识的来源。
不要将同义改写、重复识别、更华丽的表达、口语或课程外常识算作遗漏。
逐项检查非教学排除是否成立；若有有效教学内容，valid=false 并在 missing_points 给出该知识。
reason 用一两句说明具体支持或缺失条件，简洁但不能只写“已支持”。
"""
)


def source_schema(refs):
    return object_schema({ref: SOURCE_RESULT for ref in refs})


def normalize_sources(value, sources):
    jsonschema.validate(value, source_schema(sources))
    points, excluded, positions = [], [], {}
    for ref in sources:
        result = value[ref]
        if result["kind"] == "non_teaching":
            excluded.append({"reference": ref, "reason": result["reason"]})
            continue
        for body in result["points"]:
            identity = (body["label"].strip(), body["teaching"].strip())
            if identity in positions:
                existing = points[positions[identity]]["citations"]
                if ref not in existing:
                    existing.append(ref)
            else:
                positions[identity] = len(points)
                points.append({**body, "citations": [ref]})
    candidate = {"points": points, "excluded_sources": excluded}
    validate_ledger(candidate, sources)
    return candidate


def review_schema(candidate):
    return object_schema(
        {
            "reviews": object_schema(
                {str(index): VERDICT for index in range(len(candidate["points"]))}
            ),
            "excluded_sources": object_schema(
                {
                    row["reference"]: EXCLUSION_REVIEW
                    for row in candidate["excluded_sources"]
                }
            ),
            "missing_points": {"type": "array", "items": POINT},
        }
    )


def normalize_review(value, candidate, sources):
    jsonschema.validate(value, review_schema(candidate))
    result = {
        "reviews": [
            {"point": i, **value["reviews"][str(i)]}
            for i in range(len(candidate["points"]))
        ],
        "excluded_sources": [
            {
                "reference": row["reference"],
                **value["excluded_sources"][row["reference"]],
            }
            for row in candidate["excluded_sources"]
        ],
        "missing_points": value["missing_points"],
    }
    validate_audit(result, candidate, sources)
    return result


def partitions(refs, sources):
    result, current, length = [], [], 0
    for ref in refs:
        count = len(sources[ref]["text"])
        if count > PART_CHARACTERS:
            raise ValueError("textbook_keyed_single_source_exceeds_budget")
        if current and length + count > PART_CHARACTERS:
            result.append(current)
            current, length = [], 0
        current.append(ref)
        length += count
    if current:
        result.append(current)
    return result


def merge_candidates(candidates, sources):
    points, exclusions, positions = [], [], {}
    for candidate in candidates:
        exclusions.extend(candidate["excluded_sources"])
        for point in candidate["points"]:
            identity = (point["label"].strip(), point["teaching"].strip())
            if identity in positions:
                previous = points[positions[identity]]["citations"]
                previous.extend(
                    ref for ref in point["citations"] if ref not in previous
                )
            else:
                positions[identity] = len(points)
                points.append(copy.deepcopy(point))
    result = {"points": points, "excluded_sources": exclusions}
    validate_ledger(result, sources)
    return result


def original_context(sources):
    return {
        "sources": {
            ref: {"kind": row["kind"], "text": row["text"]}
            for ref, row in sources.items()
        },
        "scope": "complete_bounded_originals_only_not_article_structure",
    }


def safe_file(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("textbook_keyed_unsafe_parent_binding")
    return path


def assert_terminal(progress):
    if progress["state"] != "stopped":
        raise ValueError("textbook_keyed_parent_not_terminal")
    try:
        process = psutil.Process(progress["pid"])
        if abs(process.create_time() - progress["created"]) < 0.01:
            raise ValueError("textbook_keyed_parent_worker_still_live")
    except psutil.NoSuchProcess:
        pass


def prepare(root, inventory_key, parent_key):
    root = Path(root).resolve()
    inventory = load(root, inventory_key)
    if not re.fullmatch(r"[a-f0-9]{32}", parent_key):
        raise ValueError("textbook_keyed_invalid_parent")
    folder = root / "wiki" / "textbook-ledger-jobs" / parent_key
    bindings = {}

    def read(path):
        raw = path.read_bytes()
        bindings[path.relative_to(root).as_posix()] = sha(raw)
        return json.loads(raw)

    parent = read(folder / "manifest.json")
    progress = read(folder / "progress.json")
    assert_terminal(progress)
    failed = re.fullmatch(r"page-(\d{4})-extract", progress["stage"])
    if not failed:
        raise ValueError("textbook_keyed_only_extract_omissions_have_a_successor")
    failed_page = int(failed[1])
    if not 0 <= failed_page < len(inventory["pages"]):
        raise ValueError("textbook_keyed_parent_page_outside_inventory")
    store = TextbookStore(root)
    store.checkpoint()
    with schema_lease(
        root / "wiki" / "textbook-evidence" / inventory_key / "ledger-worker.lock",
        timeout=0,
    ):
        old = LedgerJob(
            store, inventory, "qwen3.5:9b", parent["recipe"]["model_digest"], 0
        )
        if old.key != parent_key:
            raise ValueError("textbook_keyed_parent_recipe_changed")

        def retained(stage, prompt, schema, context, sources, validator):
            _, expected = old.stage_input(
                prompt, schema, context, evidence_packet={"sources": sources}
            )
            cached = read(folder / (stage + ".json"))
            if (
                cached["input_sha256"] != expected
                or sha(canonical(cached["data"])) != cached["data_sha256"]
            ):
                raise ValueError("textbook_keyed_parent_checkpoint_changed")
            validator(cached["data"])
            if "recovery" in cached:
                proof = cached["recovery"]
                receipt = read(safe_file(root, proof["receipt"]))
                if (
                    sha(canonical(receipt)) != checked_sha(proof["sha256"])
                    or receipt["input_sha256"] != expected
                    or receipt["data_sha256"] != cached["data_sha256"]
                    or receipt["job"] != parent_key
                    or receipt["stage"] != stage
                ):
                    raise ValueError("textbook_keyed_parent_recovery_receipt_changed")
                candidates = []
                for attempt in receipt["attempts"]:
                    path = safe_file(root, attempt["path"])
                    record = read(path)
                    if (
                        bindings[path.relative_to(root).as_posix()] != attempt["sha256"]
                        or record["input_sha256"] != expected
                        or record["model_digest"] != old.model_digest
                    ):
                        raise ValueError(
                            "textbook_keyed_parent_failed_response_changed"
                        )
                    validate_metrics(record["model_metrics"], old.model_digest)
                    candidates.append(json.loads(record["raw_response"]))
                reconstructed, composition = compose(candidates, sources)
                if (
                    reconstructed != cached["data"]
                    or composition != receipt["composition"]
                ):
                    raise ValueError("textbook_keyed_parent_recovery_not_reproducible")
            else:
                matched = False
                for path in sorted((folder / "attempts").glob(stage + "-*.json")):
                    record = read(path)
                    if (
                        record.get("accepted_structure")
                        and record["input_sha256"] == expected
                        and json.loads(record["raw_response"]) == cached["data"]
                    ):
                        validate_metrics(record["model_metrics"], old.model_digest)
                        matched = True
                if not matched:
                    raise ValueError(
                        "textbook_keyed_parent_accepted_model_response_unproven"
                    )
            return cached["data"]

        imports = []
        for page in range(failed_page):
            sources = {
                ref: inventory["sources"][ref] for ref in inventory["pages"][page]
            }
            context = original_context(sources)
            candidate = retained(
                f"page-{page:04d}-extract",
                EXTRACT,
                LEDGER,
                context,
                sources,
                lambda data: validate_ledger(data, sources),
            )
            audit = retained(
                f"page-{page:04d}-review",
                REVIEW,
                AUDIT,
                {**context, "candidate": candidate},
                sources,
                lambda data: validate_audit(data, candidate, sources),
            )
            if not audit_passed(audit):
                raise ValueError("textbook_keyed_parent_import_review_not_passed")
            imports.append({"page": page, "candidate": candidate, "audit": audit})
        sources = {
            ref: inventory["sources"][ref] for ref in inventory["pages"][failed_page]
        }
        _, expected = old.stage_input(
            EXTRACT,
            LEDGER,
            original_context(sources),
            evidence_packet={"sources": sources},
        )
        paths = sorted(
            (folder / "attempts").glob(f"page-{failed_page:04d}-extract-*.json")
        )
        if len(paths) != 2:
            raise ValueError("textbook_keyed_parent_budget_must_remain_exhausted")
        partial = None
        for path in paths:
            record = read(path)
            if (
                record["input_sha256"] != expected
                or record.get("validation_error")
                != "missing_textbook_exact_source_dispositions"
                or record.get("accepted_structure")
                or record["model_digest"] != old.model_digest
            ):
                raise ValueError(
                    "textbook_keyed_parent_failure_is_not_source_accounting"
                )
            validate_metrics(record["model_metrics"], old.model_digest)
            data = json.loads(record["raw_response"])
            classified = {
                ref for point in data["points"] for ref in point["citations"]
            } | {row["reference"] for row in data["excluded_sources"]}
            if classified - set(sources):
                continue
            try:
                validate_ledger(data, {ref: sources[ref] for ref in classified})
            except (ValueError, jsonschema.ValidationError):
                continue
            missing = [ref for ref in sources if ref not in classified]
            if missing:
                partial = {
                    "page": failed_page,
                    "candidate": data,
                    "missing_references": missing,
                    "attempt_path": path.relative_to(root).as_posix(),
                }
        if partial is None:
            raise ValueError("textbook_keyed_clean_partial_candidate_unavailable")
        value = {
            "policy": POLICY,
            "inventory_sha256": inventory_key,
            "parent_job": parent_key,
            "model_digest": old.model_digest,
            "parent_files_sha256": bindings,
            "imports": imports,
            "partial": partial,
            "fresh_pages": list(range(failed_page + 1, len(inventory["pages"]))),
            "maximum_part_characters": PART_CHARACTERS,
            "parent_attempt_budgets_preserved": True,
            "imported_pages_model_calls": 0,
            "semantic_repair_rounds_per_page": 1,
            "stage_attempt_budget": 2,
            "module_sha256": sha(Path(__file__).read_bytes()),
        }
        key = sha(canonical(value))
        target = root / "wiki" / "textbook-keyed-plans" / key / "manifest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        contract = (
            root / "wiki" / "textbook-evidence" / inventory_key / "keyed-successor.json"
        )
        binding = {"parent_job": parent_key, "successor_plan": key}
        if contract.exists() and json.loads(contract.read_bytes()) != binding:
            raise ValueError("textbook_keyed_successor_already_bound_preserve_budget")
        if target.exists() and json.loads(target.read_bytes()) != value:
            raise ValueError("textbook_keyed_successor_manifest_changed")
        atomic_json(target, value)
        atomic_json(contract, binding)
        return key, value


def load_plan(root, key):
    root = Path(root).resolve()
    checked_sha(key)
    value = json.loads(
        (root / "wiki" / "textbook-keyed-plans" / key / "manifest.json").read_bytes()
    )
    if (
        sha(canonical(value)) != key
        or value["policy"] != POLICY
        or value["module_sha256"] != sha(Path(__file__).read_bytes())
    ):
        raise ValueError("textbook_keyed_plan_or_runtime_changed")
    for relative, expected in value["parent_files_sha256"].items():
        if sha(safe_file(root, relative).read_bytes()) != expected:
            raise ValueError("textbook_keyed_parent_bound_file_changed")
    return value


class KeyedJob(DraftJob):
    def __init__(self, store, inventory, plan, wait_seconds):
        super().__init__(
            store,
            {"sources": inventory["sources"]},
            "qwen3.5:9b",
            plan["model_digest"],
            wait_seconds,
            pipeline=POLICY,
            job_directory="textbook-keyed-jobs",
            max_attempts=2,
            recipe_extra={
                "plan": plan,
                "prompts": [KEYED_EXTRACT, KEYED_REVIEW],
                "schemas": [POINT_BODY, SOURCE_RESULT, VERDICT, EXCLUSION_REVIEW],
                "text_profile": profile(),
                "module_sha256": {
                    name: sha((Path(__file__).parent / (name + ".py")).read_bytes())
                    for name in (
                        "course_textbook_keyed_ledger",
                        "course_textbook_evidence",
                        "course_textbook_ledger",
                        "course_wiki_writer",
                        "course_wiki_text_inference",
                    )
                },
            },
        )
        self.finished_pages = 0
        self.page_count = len(inventory["pages"])

    def progress(self, state, stage, **extra):
        self.current = stage
        value = {
            "state": state,
            "stage": stage,
            "job": self.key,
            "policy": POLICY,
            "updated": time.time(),
            "completed_stages": self.completed,
            "completed_pages": self.finished_pages,
            "source_pages": self.page_count,
            "reader_article_published": False,
            **self.identity,
            **extra,
        }
        atomic_json(self.root / "progress.json", value)
        atomic_json(self.store.root / "wiki/textbook-keyed-last-build.json", value)

    def generate_response(self, messages, *, schema):
        if (
            len(canonical({"messages": messages, "schema": schema}).decode("utf-8"))
            > 35000
        ):
            raise ValueError("requires_textbook_keyed_smaller_context")
        return chat(self.model, messages, schema=schema)

    def extract(self, name, refs, sources, *, candidate=None, audit=None):
        packet = {"sources": {ref: sources[ref] for ref in refs}}
        context = original_context(packet["sources"])
        if candidate is not None:
            context.update(
                candidate=candidate,
                review=audit,
                context_sources={
                    ref: {"kind": source["kind"], "text": source["text"]}
                    for ref, source in sources.items()
                    if ref not in refs
                },
            )
        data = self.stage(
            name,
            KEYED_EXTRACT,
            source_schema(refs),
            context,
            lambda value: normalize_sources(value, packet["sources"]),
            evidence_packet=packet,
        )
        return normalize_sources(data, packet["sources"])

    def review(self, name, candidate, sources):
        context = {
            **original_context(sources),
            "candidate": {
                "points": {
                    str(i): point for i, point in enumerate(candidate["points"])
                },
                "excluded_sources": candidate["excluded_sources"],
            },
        }
        value = self.stage(
            name,
            KEYED_REVIEW,
            review_schema(candidate),
            context,
            lambda data: normalize_review(data, candidate, sources),
            evidence_packet={"sources": sources},
        )
        return normalize_review(value, candidate, sources)


def repair_references(candidate, audit):
    refs = {
        ref
        for row in audit["reviews"]
        if row["verdict"] != "supported"
        for ref in candidate["points"][row["point"]]["citations"]
    }
    refs.update(ref for point in audit["missing_points"] for ref in point["citations"])
    refs.update(
        row["reference"] for row in audit["excluded_sources"] if not row["valid"]
    )
    while True:
        expanded = refs | {
            ref
            for point in candidate["points"]
            if refs.intersection(point["citations"])
            for ref in point["citations"]
        }
        if expanded == refs:
            return refs
        refs = expanded


def run(root, key, *, wait_seconds=3600):
    from .course_pdf_ocr import local_model_digest

    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 3600:
        raise ValueError("textbook_keyed_invalid_wait_budget")
    plan = load_plan(root, key)
    inventory = load(root, plan["inventory_sha256"])
    store = TextbookStore(root)
    store.checkpoint()
    if local_model_digest("qwen3.5:9b") != plan["model_digest"]:
        raise ValueError("textbook_keyed_model_revision_changed")
    lock = (
        Path(root)
        / "wiki"
        / "textbook-evidence"
        / plan["inventory_sha256"]
        / "ledger-worker.lock"
    )
    with schema_lease(lock, timeout=0):
        job = KeyedJob(store, inventory, plan, wait_seconds)
        contract = job.root.parent / (key + "-execution.json")
        binding = {"plan": key, "job": job.key, "model_digest": job.model_digest}
        if contract.exists() and json.loads(contract.read_bytes()) != binding:
            raise ValueError("textbook_keyed_execution_changed_preserve_budget")
        atomic_json(contract, binding)
        target = job.root / "ledger.json"
        if target.exists():
            saved = json.loads(target.read_bytes())
            if sha(canonical(saved["data"])) != saved["data_sha256"]:
                raise ValueError("textbook_keyed_ledger_checksum_changed")
            validate_result(saved["data"], inventory, job.key)
            return saved["data"]
        points, dispositions, pages, issues = {}, {}, [], []
        imported = {row["page"]: row for row in plan["imports"]}
        job.progress("preparing", "verified_parent_reuse")
        try:
            for page, refs in enumerate(inventory["pages"]):
                sources = {ref: inventory["sources"][ref] for ref in refs}
                prefix = f"page-{page:04d}"
                if page in imported:
                    candidate, audit = (
                        imported[page]["candidate"],
                        imported[page]["audit"],
                    )
                else:
                    candidates = []
                    targets = refs
                    if page == plan["partial"]["page"]:
                        candidates.append(copy.deepcopy(plan["partial"]["candidate"]))
                        targets = plan["partial"]["missing_references"]
                    for part, selected in enumerate(partitions(targets, sources)):
                        candidates.append(
                            job.extract(
                                prefix + f"-extract-{part:02d}",
                                selected,
                                sources,
                                candidate=plan["partial"]["candidate"]
                                if page == plan["partial"]["page"]
                                else None,
                            )
                        )
                    candidate = merge_candidates(candidates, sources)
                    audit = job.review(prefix + "-review", candidate, sources)
                    if not audit_passed(audit):
                        affected = repair_references(candidate, audit)
                        retained = {
                            "points": [
                                point
                                for point in candidate["points"]
                                if not affected.intersection(point["citations"])
                            ],
                            "excluded_sources": [
                                row
                                for row in candidate["excluded_sources"]
                                if row["reference"] not in affected
                            ],
                        }
                        repaired = [retained]
                        for part, selected in enumerate(
                            partitions(
                                [ref for ref in refs if ref in affected], sources
                            )
                        ):
                            repaired.append(
                                job.extract(
                                    prefix + f"-semantic-repair-{part:02d}",
                                    selected,
                                    sources,
                                    candidate=candidate,
                                    audit=audit,
                                )
                            )
                        candidate = merge_candidates(repaired, sources)
                        audit = job.review(
                            prefix + "-repair-review", candidate, sources
                        )
                validate_ledger(candidate, sources)
                validate_audit(audit, candidate, sources)
                ids = []
                for index, point in enumerate(candidate["points"]):
                    point_id = f"P{page:04d}_{index:03d}"
                    points[point_id] = point
                    ids.append(point_id)
                excluded = {
                    row["reference"]: row["reason"]
                    for row in candidate["excluded_sources"]
                }
                for ref in refs:
                    dispositions[ref] = {
                        "points": [
                            ids[i]
                            for i, point in enumerate(candidate["points"])
                            if ref in point["citations"]
                        ],
                        "non_teaching_reason": excluded.get(ref, ""),
                    }
                passed = audit_passed(audit)
                if not passed:
                    issues.append({"page": page, "review": audit})
                pages.append(
                    {
                        "page": page,
                        "points": ids,
                        "local_review_passed": passed,
                        "reused_parent": page in imported,
                    }
                )
                job.finished_pages += 1
                job.progress(
                    "extracting_knowledge",
                    prefix,
                    knowledge_points=len(points),
                    pages_requiring_semantic_repair=len(issues),
                )
            result = {
                "policy": POLICY,
                "job": job.key,
                "plan_sha256": key,
                "inventory_sha256": plan["inventory_sha256"],
                "lesson": inventory["lesson"],
                "parent_job": plan["parent_job"],
                "points": points,
                "source_dispositions": dispositions,
                "pages": pages,
                "issues": issues,
                "all_original_characters_allocated": True,
                "automatic_local_evidence_review_passed": not issues,
                "whole_lesson_knowledge_coverage_verified": False,
                "semantic_accuracy_verified": False,
                "reader_article_published": False,
                "source_deletion_allowed": False,
                "state": "ledger_requires_semantic_repair"
                if issues
                else "ledger_ready_for_conceptual_reorganization",
            }
            validate_result(result, inventory, job.key)
            atomic_json(target, {"data_sha256": sha(canonical(result)), "data": result})
            job.progress(
                result["state"],
                "private_ledger_complete",
                knowledge_points=len(points),
                pages_requiring_semantic_repair=len(issues),
            )
            return result
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
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("prepare")
    select.add_argument("--inventory", required=True)
    select.add_argument("--parent", required=True)
    for name in ("run", "guard"):
        command = sub.add_parser(name)
        command.add_argument("--plan", required=True)
        command.add_argument("--wait-gpu-seconds", type=int, default=3600)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            key, value = prepare(args.root, args.inventory, args.parent)
            print(
                json.dumps(
                    {
                        "plan": key,
                        "imported_pages": len(value["imports"]),
                        "partial_page": value["partial"]["page"],
                        "partial_missing_sources": len(
                            value["partial"]["missing_references"]
                        ),
                        "fresh_pages": len(value["fresh_pages"]),
                    }
                )
            )
        elif args.command == "guard":
            from .course_gpu_guard import run as guard

            load_plan(args.root, args.plan)
            guard(
                args.root,
                [
                    sys.executable,
                    "-m",
                    __spec__.name,
                    "--root",
                    str(args.root.resolve()),
                    "run",
                    "--plan",
                    args.plan,
                    "--wait-gpu-seconds",
                    str(args.wait_gpu_seconds),
                ],
                maximum_power=200,
                maximum_temperature=70,
                guard_name="wiki-revision-thermal-guard",
            )
        else:
            result = run(args.root, args.plan, wait_seconds=args.wait_gpu_seconds)
            print(
                json.dumps(
                    {
                        "state": result["state"],
                        "job": result["job"],
                        "knowledge_points": len(result["points"]),
                        "pages_requiring_semantic_repair": len(result["issues"]),
                    }
                )
            )
    except Exception as exc:
        reason = (
            str(exc)
            if type(exc) is ValueError
            and str(exc).startswith(("textbook_", "missing_", "requires_"))
            else type(exc).__name__
        )
        print(
            json.dumps(
                {"state": "stopped", "error_type": type(exc).__name__, "reason": reason}
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
