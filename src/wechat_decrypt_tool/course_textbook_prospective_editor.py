"""First-use editorial roles with paragraph-bound point/source assignments.

Only new parent/section registrations are eligible. The original one-write,
one-review budget remains; no failed proposal may opt in retroactively.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sys

import jsonschema

from . import course_textbook_final_section_edit as original
from . import course_textbook_read_lease as reads
from .course_knowledge import atomic_json, schema_lease

whole, writing, guard, digest = original.whole, original.writing, original.private_guard, original.digest
POLICY = "first-editorial-paragraph-point-source-wire-format-v1"
CONTRACT = "first-point-source-editorial.json"
NOTICE = """
先选择实际 paragraph_count（2–12），正文必须正好该段数，不得用空段、重复段或机械拆句凑数。
每段 point_sources 只标这段实际完整解释的任务点，并选择该任务点真正支持本段的原文来源。
点的来源选项仅是允许范围，不是事实证据；全文仍须依据 sources 中真实文字独立讲解。
不要把全部任务随意塞进首段。先讲前置概念，再讲原理、步骤、例子、条件和限制，合并重复。
段落引用和 coverage 的实际索引由这些模型明确选择的点/原文关系机械投影，不另外猜编号。
coverage 保留每点判断及理由，待审任务为非教学时不能在正文关联；所有真实教学任务必须关联正文。
这种结构不证明覆盖或事实正确；另一模型会逐点检查实际段落是否充分解释且被引用原文支持。
"""


def require(ok, code):
    if not ok:
        raise ValueError("requires_prospective_editor_" + code)


def bindings():
    return {"entrypoints": {Path(m.__file__).stem: whole.sha(Path(m.__file__).read_bytes())
        for m in (sys.modules[__name__], reads)}, "original": original.bindings()}


def wire_schema(data):
    choices = []
    visible = set(data["context"]["sources"])
    for key in data["ids"]:
        refs = data["ledger"]["points"][key]["citations"]
        require(bool(refs) and set(refs) <= visible, "actual_primary_originals_visible")
        choices.append({"type": "object", "additionalProperties": False, "required": ["point", "sources"],
            "properties": {"point": {"type": "string", "const": key}, "sources": {"type": "array", "minItems": 1,
                "uniqueItems": True, "items": {"type": "string", "enum": refs}}}})
    branches = []
    for count in range(2, 13):
        branch = copy.deepcopy(data["schema"])
        props = branch["properties"]
        branch["properties"] = {"paragraph_count": {"type": "integer", "const": count}, **props}
        branch["required"] = ["paragraph_count", *branch["required"]]
        paragraphs = props["paragraphs"]
        paragraphs.update(minItems=count, maxItems=count)
        item = paragraphs["items"]
        item["properties"].pop("citations")
        item["required"].remove("citations")
        item["properties"]["text"].update(minLength=1, pattern=r"\S")
        item["properties"]["point_sources"] = {"type": "array", "minItems": 1, "uniqueItems": True,
            "items": {"oneOf": copy.deepcopy(choices)}}
        item["required"].append("point_sources")

        def strip(node):
            if "paragraphs" in node.get("properties", {}):
                del node["properties"]["paragraphs"]
                node["required"].remove("paragraphs")
            for value in node.get("oneOf", []):
                strip(value)

        for row in props["coverage"]["properties"].values():
            strip(row)
        branches.append(branch)
    return {"oneOf": branches}


def compile_candidate(raw, data):
    jsonschema.validate(raw, wire_schema(data))
    candidate = copy.deepcopy(raw)
    count = candidate.pop("paragraph_count")
    require(count == len(candidate["paragraphs"]), "same_actual_paragraph_count")
    texts = [row["text"].strip() for row in candidate["paragraphs"]]
    require(all(texts) and len(texts) == len(set(texts)), "distinct_nonempty_explanations")
    mapping = {key: [] for key in data["ids"]}
    for index, paragraph in enumerate(candidate["paragraphs"]):
        assignments = paragraph.pop("point_sources")
        require(len(assignments) == len({row["point"] for row in assignments}), "one_point_assignment_per_paragraph")
        paragraph["citations"] = list(dict.fromkeys(ref for row in assignments for ref in row["sources"]))
        for row in assignments:
            mapping[row["point"]].append(index)
    for key, row in candidate["coverage"].items():
        row["paragraphs"] = mapping[key]
    jsonschema.validate(candidate, data["schema"])
    writing.validate_section(candidate, data["ids"], data["ledger"], data["inventory"])
    require([p["text"] for p in candidate["paragraphs"]] == [p["text"] for p in raw["paragraphs"]], "all_actual_prose_unchanged")
    return candidate, {"actual_raw_sha256": digest(raw), "candidate_sha256": digest(candidate),
        "paragraph_mapping": mapping, "citations_selected_by_model_not_added_by_program": True,
        "semantic_acceptance": False, "reader_publication_allowed": False}


def unused(folder):
    require(not (folder / "started.json").exists() and not (folder / "progress.json").exists()
        and not list((folder / "attempts").glob("*.json")) and not list(folder.glob("response-*.json")),
        "all_original_editorial_roles_unused")


def prepare(root, parent, section, tokenizer, *, adapter="disposition", recovery_section="section-0007"):
    with reads.bounded_ledger_reads(writing):
        result = original.prepare(root, parent, section, tokenizer, adapter=adapter, recovery_section=recovery_section)
    return {**result, **bind_prepared(root, result["job"])}


def bind_prepared(root, key):
    root, folder, recipe, data = original.load(root, key)
    unused(folder)
    schema = wire_schema(data)
    contract = {"policy": POLICY, "job": key, "code": bindings(), "recipe_sha256": digest(recipe),
        "inputs_sha256": digest(data), "wire_schema_sha256": digest(schema), "notice_sha256": digest(NOTICE),
        "attempts_per_role": 1, "automatic_semantic_repairs": 0, "old_budgets_reset": False,
        "reader_publication_allowed": False, "source_deletion_allowed": False}
    require(not (folder / CONTRACT).exists(), "first_prospective_editorial_registration")
    job = Job(root, folder, recipe, data, contract)
    measured = job.budget(original.messages(original.EDIT + NOTICE, data["context"]), "editorial-write")
    atomic_json(folder / CONTRACT, contract)
    atomic_json(folder / "point-source-preflight.json", {"actual_write_input_tokens": measured[0],
        "reserved_output": measured[1], "requested_context": measured[2], "model_calls": 0})
    (folder / "point-source-guard").mkdir()
    return {"state": "first_point_source_editorial_roles_prepared", "actual_wire_schema_sha256": digest(schema),
        "actual_write_input_tokens": measured[0], "requested_context": measured[2], "model_calls": 0}


def load(root, key):
    root, folder, recipe, data = original.load(root, key)
    contract = whole.read(folder / CONTRACT)
    require(contract == {"policy": POLICY, "job": key, "code": bindings(), "recipe_sha256": digest(recipe),
        "inputs_sha256": digest(data), "wire_schema_sha256": digest(wire_schema(data)), "notice_sha256": digest(NOTICE),
        "attempts_per_role": 1, "automatic_semantic_repairs": 0, "old_budgets_reset": False,
        "reader_publication_allowed": False, "source_deletion_allowed": False}, "same_prospective_first_roles_and_budget")
    measured = Job(root, folder, recipe, data, contract).budget(
        original.messages(original.EDIT + NOTICE, data["context"]), "editorial-write")
    require(whole.read(folder / "point-source-preflight.json") == {"actual_write_input_tokens": measured[0],
        "reserved_output": measured[1], "requested_context": measured[2], "model_calls": 0}, "actual_complete_first_write_preflight")
    return root, folder, recipe, data, contract


@contextmanager
def prompt_alias(value):
    saved = original.EDIT
    original.EDIT = value
    try:
        yield
    finally:
        original.EDIT = saved


class Job(original.Job):
    def __init__(self, root, folder, recipe, data, contract, *, read_only=False):
        super().__init__(root, folder, recipe, data, read_only=read_only)
        self.data, self.contract = data, contract

    def retained(self, name, value):
        path = self.root / name
        if path.exists():
            require(whole.read(path) == value, "unchanged_exact_derivation")
        else:
            require(not self.read_only, "existing_derivation_without_inference")
            atomic_json(path, value)

    def stage(self, name, prompt, schema, context, validate):
        require(name in {"editorial-write", "editorial-review"}, "original_two_roles_only")
        if name == "editorial-write":
            require(prompt == original.EDIT and schema == self.data["schema"] and context == self.data["context"],
                "original_complete_editorial_request")
            actual_prompt, actual_schema = prompt + NOTICE, wire_schema(self.data)

            def actual_validate(value):
                candidate, _ = compile_candidate(value, self.data)
                validate(candidate)

            self.retained("point-source-write-request.json", {"execution_sha256": digest(self.contract),
                "original_prompt_sha256": digest(prompt), "actual_prompt_sha256": digest(actual_prompt),
                "original_schema_sha256": digest(schema), "actual_schema_sha256": digest(actual_schema),
                "all_original_context_sha256": digest(context), "original_budget_reset": False})
            with prompt_alias(actual_prompt):
                raw = super().stage(name, actual_prompt, actual_schema, context, actual_validate)
            candidate, derivation = compile_candidate(raw, self.data)
            self.retained("point-source-candidate-derivation.json", derivation)
            validate(candidate)
            return candidate
        actual_context = {**context, "point_source_representation_sha256": digest(self.contract),
            "point_source_assignments_are_factual_evidence": False}
        return super().stage(name, prompt, schema, actual_context, validate)


def worker(root, key):
    root, folder, recipe, data, contract = load(root, key)
    guard.checkpoint(root)
    job = Job(root, folder, recipe, data, contract)
    with schema_lease(folder / "worker.lock", timeout=0):
        unused(folder)
        atomic_json(folder / "started.json", job.identity)
        try:
            result = original.stages(job, data)
            result.update(prospective_execution_sha256=digest(contract), point_source_projection_untrusted=True)
            atomic_json(folder / "point-source-result.json", result)
            job.progress(result["state"], "complete", negative_checks=result["negative_checks"])
            return result
        except BaseException as exc:
            failure = whole.failure_status(exc)
            atomic_json(folder / "point-source-failure.json", failure)
            job.progress(failure["state"], "retained_failure")
            raise


def verify(root, key):
    root, folder, recipe, data, contract = load(root, key)
    state = whole.read(folder / "progress.json")
    require(state["stage"] == "complete" and not writing.identity_live(state), "actual_complete_exited_worker")
    result = original.stages(Job(root, folder, recipe, data, contract, read_only=True), data)
    result.update(prospective_execution_sha256=digest(contract), point_source_projection_untrusted=True)
    require(result == whole.read(folder / "point-source-result.json"), "actual_complete_same_editorial_result")
    return {**result, "new_model_calls": 0}


def run(root, key):
    root, folder, _, _, _ = load(root, key)
    unused(folder)
    result = guard.run(root, folder / "point-source-guard", [sys.executable, "-m", __spec__.name,
        "--root", str(root), "--job", key, "worker"])
    if result["state"] == "private_worker_exited":
        result = verify(root, key)
        atomic_json(folder / "point-source-checked-result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job")
    parser.add_argument("--parent")
    parser.add_argument("--section")
    parser.add_argument("--tokenizer", type=Path)
    parser.add_argument("--adapter", choices=["original", "continuation", "disposition"], default="disposition")
    parser.add_argument("--recovery-section", default="section-0007")
    parser.add_argument("command", choices=["prepare", "load", "run", "worker", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.root, args.parent, args.section, args.tokenizer,
                adapter=args.adapter, recovery_section=args.recovery_section)
        elif args.command == "load":
            _, _, _, data, _ = load(args.root, args.job)
            result = {"state": "prospective_editorial_inputs_authenticated", "job": args.job, "points": len(data["ids"]), "model_calls": 0}
        else:
            result = globals()[args.command](args.root, args.job)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
