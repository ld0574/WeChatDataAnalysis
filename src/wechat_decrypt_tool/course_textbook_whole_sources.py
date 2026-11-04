"""Fresh knowledge ledgers over complete original records, never text fragments.

The old inventories, jobs and budgets remain unchanged. Source groups are only
bounded scheduling units; conceptual authoring remains a separate operation.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
import sys
import time
import zipfile
from pathlib import Path

import psutil
from tokenizers import Tokenizer

from . import course_textbook_edition_editor as editor
from . import course_textbook_keyed_ledger as keyed
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_pdf_ocr import local_model_digest
from .course_textbook_evidence import checked_sha, load as load_parent, notebook_sources, sha
from .course_textbook_ledger import TextbookStore, audit_passed, validate_ledger, validate_result
from .course_wiki_writer import DraftJob

POLICY = "fresh-complete-original-knowledge-ledger-v1"
GROUP_CHARACTERS = 12000
EXTRACT = keyed.KEYED_EXTRACT + """
本次每个来源都是未截断的完整原始记录；长代码、空白和输出保持原样，不是一个原文分片。
context_sources 仅帮助理解前后指代，不得把其中独有的知识移到目标来源来凑覆盖。
一份完整记录可包含多个知识点，须保留其中有效教学的条件、步骤、例子和限制。
"""
REVIEW = keyed.KEYED_REVIEW + """
每个来源都是完整原始记录。只复核 targets 中实际给出的全部有效教学；完整相邻记录仅帮助理解。
不要因长代码没有逐行复述就判遗漏，但其实际教授的输入、行为、前提和限制必须在知识中表达。
"""


def require(value, code):
    if not value:
        raise ValueError("whole_sources_" + code)


def read(path):
    return json.loads(Path(path).read_bytes())


def groups_for(sources, limit=GROUP_CHARACTERS):
    require(type(limit) is int and 1 <= limit <= 24000, "bounded_group_size")
    groups, current, characters = [], [], 0
    for ref, row in sources.items():
        require(isinstance(row.get("text"), str), "literal_text_required")
        size = len(row["text"])
        if current and characters + size > limit:
            groups.append(current)
            current, characters = [], 0
        current.append(ref)
        characters += size
    if current:
        groups.append(current)
    require(bool(groups), "nonempty_source_scope")
    return groups


def build(parent_key, parent, supplements=(), *, selected_notebooks=()):
    require(sha(canonical(parent)) == checked_sha(parent_key), "parent_identity")
    originals = copy.deepcopy([*parent["originals"], *supplements])
    sources = {f"O{index:05d}": row for index, row in enumerate(originals)}
    value = {"policy": POLICY, "parent_inventory_sha256": parent_key,
        "lesson": parent["lesson"], "lesson_artifact": parent["lesson_artifact"],
        "source_export_sha256": parent["source_export_sha256"],
        "originals": originals, "sources": sources, "pages": groups_for(sources),
        "original_characters": sum(len(row["text"]) for row in originals),
        "group_character_limit": GROUP_CHARACTERS,
        "attachment_scope": {"selected_notebooks": list(selected_notebooks),
            "association_verified": False, "unresolved": True,
            "candidate_selection_is_semantic_evidence": False,
            "remaining_work": ["item_specific_reference_relations", "notebook_visual_dependencies",
                "all_other_recoverable_courseware_remains_in_full_goal"]},
        "source_lineage": copy.deepcopy(parent["attachment_scope"].get("source_lineage", {})),
        "reader_payload": False, "scheduling_pages_are_article_chapters": False,
        "whole_lesson_knowledge_coverage_verified": False,
        "semantic_accuracy_verified": False, "source_deletion_allowed": False}
    validate(value, parent)
    return value


def validate(value, parent):
    require(value["policy"] == POLICY and value["parent_inventory_sha256"] == sha(canonical(parent)), "actual_parent")
    require(value["originals"][:len(parent["originals"])] == parent["originals"], "all_parent_originals_unchanged")
    require(value["sources"] == {f"O{i:05d}": row for i, row in enumerate(value["originals"])}, "whole_original_identity")
    require(value["pages"] == groups_for(value["sources"], value["group_character_limit"]), "exact_group_allocation")
    require(value["original_characters"] == sum(len(row["text"]) for row in value["originals"]), "all_original_characters")
    require(value["reader_payload"] is False and value["source_deletion_allowed"] is False, "private_work_only")
    for row in value["originals"][len(parent["originals"]):]:
        require(row["kind"] == "document" and "member_sha256" in row["origin"], "actual_notebook_supplement")


def context_for(value, refs):
    sources = value["sources"]
    selected = set(refs)
    for ref in refs:
        index = int(ref[1:])
        for neighbor in [index - 1, index + 1]:
            key = f"O{neighbor:05d}"
            if key not in sources:
                continue
            a, b = sources[ref], sources[key]
            if a["kind"] in {"speech", "frame"} and b["kind"] in {"speech", "frame"}:
                selected.add(key)
            elif (a["kind"] == b["kind"] == "document"
                    and a["origin"]["member_sha256"] == b["origin"]["member_sha256"]
                    and abs(a["origin"]["cell"] - b["origin"]["cell"]) <= 1):
                selected.add(key)
    def project(row):
        return {"kind": row["kind"], "text": row["text"]}

    return {"sources": {ref: project(sources[ref]) for ref in refs},
        "context_sources": {ref: project(sources[ref]) for ref in sorted(selected - set(refs))},
        "scope": "complete_original_records_not_article_chapters"}


def module_bindings():
    names = ["course_textbook_whole_sources", "course_textbook_keyed_ledger", "course_textbook_ledger",
        "course_textbook_evidence", "course_wiki_writer", "course_textbook_gap_final_review",
        "course_textbook_edition", "course_textbook_edition_sources", "course_textbook_gap_plan",
        "course_wiki_text_inference"]
    return {name: sha(Path(__file__).with_name(name + ".py").read_bytes()) for name in names}


def review_context(context, candidate):
    return {**context, "candidate": {
        "points": {str(index): point for index, point in enumerate(candidate["points"])},
        "excluded_sources": candidate["excluded_sources"]}}


def checked_stage(job, name, prompt, schema, context, selected, normalize, *, cached_only=False):
    path = job.root / (name + ".json")
    require(not cached_only or path.is_file(), "completed_group_stage_missing")
    packet = {"sources": selected}
    expected = job.stage_input(prompt, schema, context, evidence_packet=packet)[1]
    data = job.stage(name, prompt, schema, context, normalize, evidence_packet=packet)
    actual, binding = editor.e.checked_cache(job.root, name)
    require(actual == data and binding == expected, "actual_stage_request_binding")
    return normalize(data), path


def process_group(extractor, reviewer, context, selected, prefix, *, cached_only=False):
    used = []

    def extract(suffix, extra=None):
        candidate, path = checked_stage(extractor, prefix + suffix, EXTRACT,
            keyed.source_schema(selected), {**context, **(extra or {})}, selected,
            lambda row: keyed.normalize_sources(row, selected), cached_only=cached_only)
        used.append(path)
        return candidate

    def review(candidate, suffix):
        audit, path = checked_stage(reviewer, prefix + suffix, REVIEW,
            keyed.review_schema(candidate), review_context(context, candidate), selected,
            lambda row: keyed.normalize_review(row, candidate, selected), cached_only=cached_only)
        used.append(path)
        return audit

    candidate = extract("-extract")
    audit = review(candidate, "-review")
    if not audit_passed(audit):
        candidate = extract("-repair", {"candidate": candidate, "review": audit})
        audit = review(candidate, "-repair-review")
    return {"candidate": candidate, "audit": audit}, used


def failure_status(exc):
    message = str(exc)
    safe = re.fullmatch(r"[A-Za-z0-9_-]{1,180}", message) and message.startswith(
        ("whole_sources_", "textbook_", "wiki_", "requires_", "gpu_", "local_model_"))
    return {"state": "paused" if isinstance(exc, InterruptedError) else "stopped",
        "error_type": type(exc).__name__,
        "reason_code": message if safe else None,
        "error_sha256": sha(message.encode())}


def backup(path, destination):
    raw = path.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    require(not destination.exists() or destination.read_bytes() == raw, "immutable_backup")
    if not destination.exists():
        destination.write_bytes(raw)
    require(destination.read_bytes() == raw, "exact_backup")


def prepare(root, parent_key, selections=()):
    root = Path(root).resolve()
    TextbookStore(root).checkpoint()
    parent = load_parent(root, parent_key)
    resources = read(root / "resources-manifest.json")
    supplements, selected, inputs = [], [], {}
    for item in selections:
        digest, member = checked_sha(item["archive_sha256"]), item["member"]
        require(isinstance(member, str) and member.endswith(".ipynb"), "notebook_member")
        row = next(x for x in resources["completed"] if x["sha256"] == digest)
        archive_path = (root / row["object"]).resolve()
        require(archive_path.is_relative_to(root) and sha(archive_path.read_bytes()) == digest, "retained_archive")
        with zipfile.ZipFile(archive_path) as archive:
            require(archive.getinfo(member).file_size <= 16 * 1024**2, "bounded_notebook")
            raw = archive.read(member)
        supplements.extend(notebook_sources(raw, archive_sha256=digest, member=member))
        selected.append({"archive_sha256": digest, "member": member, "member_sha256": sha(raw),
            "association_status": "candidate_not_verified", "basis": "retained_same_course_subject_metadata"})
        inputs[sha(raw) + ".ipynb"] = raw
    value = build(parent_key, parent, supplements, selected_notebooks=selected)
    key = sha(canonical(value))
    folder = root / "wiki/textbook-whole-record-evidence" / key
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "manifest.json"
    require(not path.exists() or read(path) == value, "immutable_whole_inventory")
    if not path.exists():
        atomic_json(path, value)
    for name, raw in inputs.items():
        target = folder / name
        require(not target.exists() or target.read_bytes() == raw, "immutable_notebook")
        if not target.exists():
            target.write_bytes(raw)
    dest = Path.home() / "Documents/CourseKnowledgeBackups/whole-record-inputs" / key
    for source in [*folder.iterdir(), root / "wiki/textbook-evidence" / parent_key / "manifest.json"]:
        backup(source, dest / ("parent-inventory.json" if source.parent != folder else source.name))
    return key, value


def load(root, key):
    root = Path(root).resolve()
    value = read(root / "wiki/textbook-whole-record-evidence" / checked_sha(key) / "manifest.json")
    require(sha(canonical(value)) == key, "whole_inventory_digest")
    parent = load_parent(root, value["parent_inventory_sha256"])
    validate(value, parent)
    supplements = []
    for item in value["attachment_scope"]["selected_notebooks"]:
        raw = (root / "wiki/textbook-whole-record-evidence" / key / (item["member_sha256"] + ".ipynb")).read_bytes()
        require(sha(raw) == item["member_sha256"], "notebook_bytes")
        supplements.extend(notebook_sources(raw, archive_sha256=item["archive_sha256"], member=item["member"]))
    require(value["originals"] == [*parent["originals"], *supplements], "all_originals_reconstructed")
    return value


class Job(editor.sources.AuditJob):
    def __init__(self, store, value, binding, model, tokenizer):
        self.tokenizer = Tokenizer.from_file(str(tokenizer))
        self.whole_key = sha(canonical(value))
        DraftJob.__init__(self, store, {"sources": value["sources"]}, model, binding["models"][model], 21600,
            pipeline=POLICY, job_directory="textbook-whole-source-jobs", max_attempts=2,
            recipe_extra={"binding": binding, "prompts": [EXTRACT, REVIEW], "maximum_context": 65536,
                "maximum_semantic_repairs_per_group": 1, "old_budgets_reset": False})

    def budget(self, messages, stage):
        count = sum(len(self.tokenizer.encode(row["content"]).ids) for row in messages)
        output = 12288 if stage.endswith(("extract", "repair")) else 8192
        context = max(32768, math.ceil((count + output + 1024) / 16384) * 16384)
        require(context <= 65536, "complete_stage_context_fits")
        return count, output, context

    def progress(self, state, stage, **extra):
        self.current = stage
        atomic_json(self.root / "progress.json", {**self.identity, "state": state, "stage": stage,
            "job": self.key, "whole_inventory": self.whole_key, "updated": time.time(), **extra})


def compile_ledger(value, key, outputs):
    require(len(outputs) == len(value["pages"]), "every_group_finished")
    points, dispositions, pages, issues = {}, {}, [], []
    for group, (candidate, audit) in enumerate(outputs):
        refs = value["pages"][group]
        selected = {ref: value["sources"][ref] for ref in refs}
        validate_ledger(candidate, selected)
        keyed.validate_audit(audit, candidate, selected)
        ids = []
        for index, point in enumerate(candidate["points"]):
            identity = f"P{group:04d}_{index:03d}"
            points[identity] = point
            ids.append(identity)
        excluded = {row["reference"]: row["reason"] for row in candidate["excluded_sources"]}
        for ref in refs:
            dispositions[ref] = {"points": [ids[i] for i, point in enumerate(candidate["points"]) if ref in point["citations"]],
                "non_teaching_reason": excluded.get(ref, "")}
        passed = audit_passed(audit)
        if not passed:
            issues.append({"group": group, "review": audit})
        pages.append({"page": group, "points": ids, "local_review_passed": passed})
    result = {"policy": POLICY, "job": key, "inventory_sha256": sha(canonical(value)),
        "parent_inventory_sha256": value["parent_inventory_sha256"], "lesson": value["lesson"],
        "points": points, "source_dispositions": dispositions, "pages": pages, "issues": issues,
        "all_original_characters_allocated": True, "automatic_local_evidence_review_passed": not issues,
        "whole_lesson_knowledge_coverage_verified": False, "semantic_accuracy_verified": False,
        "reader_article_published": False, "source_deletion_allowed": False,
        "state": "ledger_requires_semantic_repair" if issues else "ledger_ready_for_conceptual_reorganization"}
    validate_result(result, value, key)
    return result


def run(root, inventory_key, tokenizer, *, preflight=False):
    root = Path(root).resolve()
    store, value = TextbookStore(root), load(root, inventory_key)
    store.checkpoint()
    models = ["qwen3.5:9b", "course-defiant:q4"]
    binding = {"inventory": inventory_key, "module_sha256": module_bindings(),
        "models": {name: local_model_digest(name) for name in models},
        "tokenizer_sha256": sha(Path(tokenizer).read_bytes()), "maximum_semantic_repairs_per_group": 1,
        "maximum_structural_attempts": 2, "old_budgets_reset": False}
    key = sha(canonical(binding))[:32]
    folder = root / "wiki/textbook-keyed-jobs" / key
    folder.mkdir(parents=True, exist_ok=True)
    require(not (folder / "manifest.json").exists() or read(folder / "manifest.json") == {"id": key, "binding": binding}, "same_fresh_execution")
    atomic_json(folder / "manifest.json", {"id": key, "binding": binding})
    extractor, reviewer = [Job(store, value, binding, model, tokenizer) for model in models]
    capacities = []
    for refs in value["pages"]:
        context = context_for(value, refs)
        count, output, capacity = extractor.budget([
            {"content": EXTRACT}, {"content": json.dumps({**context, "previous_validation_failure": ""}, ensure_ascii=False)}], "extract")
        capacities.append({"input_tokens": count, "output_reserved": output, "context": capacity})
    atomic_json(folder / "request-preflight.json", capacities)
    summary = {"job": key, "inventory": inventory_key, "original_records": len(value["sources"]),
        "processing_groups": len(value["pages"]), "original_characters": value["original_characters"],
        "input_tokens_min": min(row["input_tokens"] for row in capacities),
        "input_tokens_max": max(row["input_tokens"] for row in capacities), "new_writing_calls": 0}
    if preflight:
        return {"state": "whole_source_preflight_passed", **summary, "model_calls": 0}
    lock = root / "wiki/textbook-evidence" / value["parent_inventory_sha256"] / "ledger-worker.lock"
    with schema_lease(lock, timeout=0):
        contract = lock.parent / "whole-source-ledger-execution.json"
        require(not contract.exists() or read(contract) == binding, "preserve_original_fresh_budget")
        if not contract.exists():
            atomic_json(contract, binding)
        if (folder / "ledger.json").exists():
            saved = read(folder / "ledger.json")
            require(sha(canonical(saved["data"])) == saved["data_sha256"], "ledger_checksum")
            validate_result(saved["data"], value, key)
            return {"state": saved["data"]["state"], **summary, "existing_result_retained": True}
        progress = {"state": "preparing_complete_original_knowledge", **summary,
            "completed_groups": 0, "knowledge_points": 0,
            "role_jobs": {"extractor": extractor.key, "reviewer": reviewer.key},
            "pid": psutil.Process().pid, "created": psutil.Process().create_time(),
            "source_deletion_allowed": False, "reader_article_published": False}

        def publish(**fields):
            progress.update(fields, updated=time.time())
            atomic_json(folder / "progress.json", progress)
            atomic_json(root / "wiki/whole-source-ledger-current.json", progress)

        publish()
        try:
            outputs = []
            dest = Path.home() / "Documents/CourseKnowledgeBackups/whole-source-ledgers" / key
            for source in [Path(__file__), folder / "manifest.json", folder / "request-preflight.json"]:
                backup(source, dest / source.name)
            for group, refs in enumerate(value["pages"]):
                store.checkpoint()
                checkpoint = folder / f"group-{group:04d}.json"
                context = context_for(value, refs)
                selected = {ref: value["sources"][ref] for ref in refs}
                if checkpoint.exists():
                    saved = read(checkpoint)
                    require(saved["input_sha256"] == sha(canonical(context))
                        and saved["data_sha256"] == sha(canonical(saved["data"])), "exact_completed_group")
                    for path, digest in saved["stage_files"].items():
                        require(sha(Path(path).read_bytes()) == digest, "completed_stage_unchanged")
                data, used = process_group(extractor, reviewer, context, selected,
                    f"group-{group:04d}", cached_only=checkpoint.exists())
                proof = {"input_sha256": sha(canonical(context)), "data": data,
                    "data_sha256": sha(canonical(data)),
                    "stage_files": {str(path): sha(path.read_bytes()) for path in used}}
                if checkpoint.exists():
                    require(saved == proof, "completed_group_reconstructed_from_bound_responses")
                else:
                    atomic_json(checkpoint, proof)
                candidate, audit = data["candidate"], data["audit"]
                validate_ledger(candidate, selected)
                keyed.validate_audit(audit, candidate, selected)
                outputs.append((candidate, audit))
                backup(checkpoint, dest / checkpoint.name)
                for job in [extractor, reviewer]:
                    for source in job.root.rglob("*.json"):
                        if source.name != "progress.json":
                            backup(source, dest / job.key / source.relative_to(job.root))
                publish(state="extracting_complete_original_knowledge", completed_groups=len(outputs),
                    groups_with_unresolved_reviews=sum(not audit_passed(row[1]) for row in outputs),
                    knowledge_points=sum(len(row[0]["points"]) for row in outputs))
            result = compile_ledger(value, key, outputs)
            atomic_json(folder / "ledger.json", {"data": result, "data_sha256": sha(canonical(result))})
            backup(folder / "ledger.json", dest / "ledger.json")
            final = {"state": result["state"], **summary, "completed_groups": len(outputs),
                "unresolved_groups": len(result["issues"]), "knowledge_points": len(result["points"]),
                "source_deletion_allowed": False, "reader_article_published": False}
            atomic_json(folder / "result.json", final)
            publish(**final)
            return final
        except Exception as exc:
            status = failure_status(exc)
            failure = folder / "failures" / (str(time.time_ns()) + ".json")
            atomic_json(failure, {**progress, **status, "updated": time.time()})
            publish(**status)
            for job in [extractor, reviewer]:
                if getattr(job, "current", None):
                    job.progress(status["state"], job.current, **{k: v for k, v in status.items() if k != "state"})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--parent", required=True)
    prepare_parser.add_argument("--notebook-archive")
    prepare_parser.add_argument("--notebook-member")
    for name in ["preflight", "worker", "guard"]:
        child = sub.add_parser(name)
        child.add_argument("--inventory", required=True)
        child.add_argument("--tokenizer", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        require(bool(args.notebook_archive) == bool(args.notebook_member), "paired_notebook_selection")
        selections = [{"archive_sha256": args.notebook_archive, "member": args.notebook_member}] if args.notebook_archive else []
        key, value = prepare(args.root, args.parent, selections)
        print(json.dumps({"state": "complete_original_inputs_prepared", "inventory": key,
            "sources": len(value["sources"]), "groups": len(value["pages"]), "characters": value["original_characters"], "model_calls": 0}))
    elif args.command in {"preflight", "worker"}:
        print(json.dumps(run(args.root, args.inventory, args.tokenizer, preflight=args.command == "preflight")))
    else:
        from .course_archive_gpu import checked_sample
        from .course_gpu_guard import run as guarded
        root = args.root.resolve()
        with schema_lease(root / "wiki/whole-source-ledger-dispatch.lock", timeout=0):
            previous = root / "wiki-revision-thermal-guard.json"
            if previous.exists():
                old = read(previous)
                try:
                    live = abs(psutil.Process(old["pid"]).create_time() - old["created"]) < .01
                except psutil.NoSuchProcess:
                    live = False
                require(not live and old["state"] == "worker_exited" and old.get("returncode") == 0,
                    "previous_wiki_guard_cleanly_exited")
                backup(previous, root / "wiki/whole-source-guard-history" / (sha(previous.read_bytes()) + ".json"))
            guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root), "worker",
                "--inventory", args.inventory, "--tokenizer", str(args.tokenizer.resolve())],
                maximum_temperature=70, maximum_power=200, guard_name="wiki-revision-thermal-guard",
                sampler=lambda: checked_sample(root))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps(failure_status(exc)))
        sys.exit(1)
