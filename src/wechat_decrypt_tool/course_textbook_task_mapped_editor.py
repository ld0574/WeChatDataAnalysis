"""First-use final editing with a required source/paragraph map for every task.

This format is unavailable to registered or attempted sections. It changes the
wire representation of new roles, never the original parent or role budgets.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

import jsonschema

from . import course_textbook_prospective_editor as prior
from .course_knowledge import atomic_json, schema_lease

original, reads = prior.original, prior.reads
whole, writing, guard, digest = prior.whole, prior.writing, prior.guard, prior.digest
POLICY = "first-editorial-required-task-paragraph-source-map-v1"
CONTRACT = "first-task-mapped-editorial.json"
NOTICE = """
先选择实际 paragraph_count（2–12），正文必须恰好该段数，按知识依赖写成连贯解释。
不得用空段、重复段、机械拆句凑数；每段必须实际解释至少一个任务点。
coverage 中每个指定任务都必须填写 paragraph_sources：键为实际段落的零起始编号，
值为真正支持该任务在该段讲解的主要原文编号，不能仅为通过检查而关联无关文字。
例如某任务确由第1和第3段充分讲解时，选择键 "0" 和 "2" 及各自真实支持来源。
已确认教学任务必须有至少一处映射；待审任务先依据原文判断，非教学时 teaching=false，
paragraph_sources={}，教学时 teaching=true 并填写真实映射。不得省略指定任务。
正文引用及原覆盖索引只由这些模型明确选择的关系机械投影，不由程序猜测或补齐。
来源选项和账本不是事实依据，完整 sources 才是依据；每段须有真实任务和来源支持。
不要把全部任务机械挂到首段，不编造事实或关联，不隐藏原未决事项。
结构填完不代表知识充分或引用正确；另一权重模型仍将逐点核对原文和真实正文。
"""


def require(ok, code):
    if not ok:
        raise ValueError("requires_task_mapped_editor_" + code)


def bindings():
    return {
        "entrypoints": {
            Path(m.__file__).stem: whole.sha(Path(m.__file__).read_bytes())
            for m in (sys.modules[__name__], prior)
        },
        "original": prior.bindings(),
    }


def wire_schema(data):
    visible = set(data["context"]["sources"])
    branches = []
    for count in range(2, 13):
        branch = copy.deepcopy(data["schema"])
        props = branch["properties"]
        branch["properties"] = {
            "paragraph_count": {"type": "integer", "const": count},
            **props,
        }
        branch["required"] = ["paragraph_count", *branch["required"]]
        paragraphs = props["paragraphs"]
        paragraphs.update(minItems=count, maxItems=count)
        paragraph = paragraphs["items"]
        paragraph["properties"].pop("citations")
        paragraph["required"].remove("citations")
        paragraph["properties"]["text"].update(minLength=1, pattern=r"\S")
        for key in data["ids"]:
            refs = data["ledger"]["points"][key]["citations"]
            require(
                bool(refs) and set(refs) <= visible, "all_primary_originals_visible"
            )

            def replace(node):
                if "paragraphs" in node.get("properties", {}):
                    old = node["properties"].pop("paragraphs")
                    links = {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            str(i): {
                                "type": "array",
                                "minItems": 1,
                                "uniqueItems": True,
                                "items": {"type": "string", "enum": refs},
                            }
                            for i in range(count)
                        },
                    }
                    if old.get("minItems", 0):
                        links["minProperties"] = 1
                    if old.get("maxItems") == 0:
                        links["maxProperties"] = 0
                    node["properties"]["paragraph_sources"] = links
                    node["required"] = [
                        "paragraph_sources" if name == "paragraphs" else name
                        for name in node["required"]
                    ]
                for child in node.get("oneOf", []):
                    replace(child)

            replace(props["coverage"]["properties"][key])
        branches.append(branch)
    return {"oneOf": branches}


def compile_candidate(raw, data):
    jsonschema.validate(raw, wire_schema(data))
    candidate = copy.deepcopy(raw)
    count = candidate.pop("paragraph_count")
    require(count == len(candidate["paragraphs"]), "same_actual_paragraph_count")
    texts = [row["text"].strip() for row in candidate["paragraphs"]]
    require(all(texts) and len(set(texts)) == len(texts), "distinct_nonempty_body")
    citations = [[] for _ in range(count)]
    mapping = {}
    for key, row in candidate["coverage"].items():
        chosen = row.pop("paragraph_sources")
        row["paragraphs"] = [i for i in range(count) if str(i) in chosen]
        mapping[key] = row["paragraphs"]
        for index in row["paragraphs"]:
            citations[index].extend(chosen[str(index)])
    for paragraph, refs in zip(candidate["paragraphs"], citations, strict=True):
        require(bool(refs), "every_body_paragraph_has_model_chosen_task_and_source")
        paragraph["citations"] = list(dict.fromkeys(refs))
    jsonschema.validate(candidate, data["schema"])
    writing.validate_section(candidate, data["ids"], data["ledger"], data["inventory"])
    require(
        [p["text"] for p in candidate["paragraphs"]]
        == [p["text"] for p in raw["paragraphs"]],
        "all_actual_prose_unchanged",
    )
    return candidate, {
        "actual_raw_sha256": digest(raw),
        "candidate_sha256": digest(candidate),
        "model_task_source_assignments_sha256": digest(raw["coverage"]),
        "paragraph_mapping": mapping,
        "citations_and_mappings_selected_by_model_not_added_by_program": True,
        "semantic_acceptance": False,
        "reader_publication_allowed": False,
    }


def prepare(
    root,
    parent,
    section,
    tokenizer,
    *,
    adapter="disposition",
    recovery_section="section-0007",
):
    with reads.bounded_ledger_reads(writing):
        result = original.prepare(
            root,
            parent,
            section,
            tokenizer,
            adapter=adapter,
            recovery_section=recovery_section,
        )
    return {**result, **bind_prepared(root, result["job"])}


def bind_prepared(root, key):
    root, folder, recipe, data = original.load(root, key)
    prior.unused(folder)
    # A first-use choice is fixed once, before any actual editorial request.
    require(not (folder / prior.CONTRACT).exists(), "no_other_reserved_wire_format")
    contract = contract_for(key, recipe, data)
    require(not (folder / CONTRACT).exists(), "first_task_mapped_registration")
    job = Job(root, folder, recipe, data, contract)
    measured = job.budget(
        original.messages(original.EDIT + NOTICE, data["context"]), "editorial-write"
    )
    atomic_json(folder / CONTRACT, contract)
    atomic_json(folder / "task-mapped-preflight.json", preflight(measured))
    (folder / "task-mapped-guard").mkdir()
    return {
        "state": "first_task_mapped_editorial_roles_prepared",
        "actual_wire_schema_sha256": contract["wire_schema_sha256"],
        "actual_write_input_tokens": measured[0],
        "requested_context": measured[2],
        "model_calls": 0,
    }


def contract_for(key, recipe, data):
    return {
        "policy": POLICY,
        "job": key,
        "code": bindings(),
        "recipe_sha256": digest(recipe),
        "inputs_sha256": digest(data),
        "wire_schema_sha256": digest(wire_schema(data)),
        "notice_sha256": digest(NOTICE),
        "attempts_per_role": 1,
        "automatic_semantic_repairs": 0,
        "old_budgets_reset": False,
        "reader_publication_allowed": False,
        "source_deletion_allowed": False,
    }


def preflight(measured):
    return {
        "actual_write_input_tokens": measured[0],
        "reserved_output": measured[1],
        "requested_context": measured[2],
        "model_calls": 0,
    }


def load(root, key):
    root, folder, recipe, data = original.load(root, key)
    contract = whole.read(folder / CONTRACT)
    require(
        contract == contract_for(key, recipe, data),
        "same_first_use_task_map_inputs_code_budget",
    )
    measured = Job(root, folder, recipe, data, contract).budget(
        original.messages(original.EDIT + NOTICE, data["context"]), "editorial-write"
    )
    require(
        whole.read(folder / "task-mapped-preflight.json") == preflight(measured),
        "same_actual_complete_role_preflight",
    )
    return root, folder, recipe, data, contract


class Job(original.Job):
    def __init__(self, root, folder, recipe, data, contract, *, read_only=False):
        super().__init__(root, folder, recipe, data, read_only=read_only)
        self.data, self.contract = data, contract

    retained = prior.Job.retained

    def stage(self, name, prompt, schema, context, validate):
        require(
            name in {"editorial-write", "editorial-review"}, "original_two_roles_only"
        )
        if name == "editorial-write":
            require(
                prompt == original.EDIT
                and schema == self.data["schema"]
                and context == self.data["context"],
                "complete_original_editorial_context",
            )
            actual_prompt, actual_schema = prompt + NOTICE, wire_schema(self.data)

            def validate_actual(value):
                candidate, _ = compile_candidate(value, self.data)
                validate(candidate)

            self.retained(
                "task-mapped-write-request.json",
                {
                    "execution_sha256": digest(self.contract),
                    "original_prompt_sha256": digest(prompt),
                    "actual_prompt_sha256": digest(actual_prompt),
                    "original_schema_sha256": digest(schema),
                    "actual_schema_sha256": digest(actual_schema),
                    "all_original_context_sha256": digest(context),
                    "original_budget_reset": False,
                },
            )
            with prior.prompt_alias(actual_prompt):
                raw = super().stage(
                    name, actual_prompt, actual_schema, context, validate_actual
                )
            candidate, derivation = compile_candidate(raw, self.data)
            self.retained("task-mapped-candidate-derivation.json", derivation)
            validate(candidate)
            return candidate
        actual_context = {
            **context,
            "task_mapped_representation_sha256": digest(self.contract),
            "task_source_assignments_are_factual_evidence": False,
        }
        return super().stage(name, prompt, schema, actual_context, validate)


def worker(root, key):
    root, folder, recipe, data, contract = load(root, key)
    guard.checkpoint(root)
    job = Job(root, folder, recipe, data, contract)
    with schema_lease(folder / "worker.lock", timeout=0):
        prior.unused(folder)
        atomic_json(folder / "started.json", job.identity)
        try:
            result = original.stages(job, data)
            result.update(
                task_mapped_execution_sha256=digest(contract),
                source_mapping_is_untrusted=True,
            )
            atomic_json(folder / "task-mapped-result.json", result)
            job.progress(
                result["state"], "complete", negative_checks=result["negative_checks"]
            )
            return result
        except BaseException as exc:
            failure = whole.failure_status(exc)
            atomic_json(folder / "task-mapped-failure.json", failure)
            job.progress(failure["state"], "retained_failure")
            raise


def verify(root, key):
    root, folder, recipe, data, contract = load(root, key)
    state = whole.read(folder / "progress.json")
    require(
        state["stage"] == "complete" and not writing.identity_live(state),
        "actually_complete_exited_worker",
    )
    result = original.stages(
        Job(root, folder, recipe, data, contract, read_only=True), data
    )
    result.update(
        task_mapped_execution_sha256=digest(contract), source_mapping_is_untrusted=True
    )
    require(
        result == whole.read(folder / "task-mapped-result.json"),
        "actual_complete_same_editorial_result",
    )
    return {**result, "new_model_calls": 0}


def run(root, key):
    root, folder, _, _, _ = load(root, key)
    prior.unused(folder)
    result = guard.run(
        root,
        folder / "task-mapped-guard",
        [
            sys.executable,
            "-m",
            __spec__.name,
            "--root",
            str(root),
            "--job",
            key,
            "worker",
        ],
    )
    if result["state"] == "private_worker_exited":
        result = verify(root, key)
        atomic_json(folder / "task-mapped-checked-result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job")
    parser.add_argument("--parent")
    parser.add_argument("--section")
    parser.add_argument("--tokenizer", type=Path)
    parser.add_argument(
        "--adapter",
        choices=["original", "continuation", "disposition"],
        default="disposition",
    )
    parser.add_argument("--recovery-section", default="section-0007")
    parser.add_argument(
        "command", choices=["prepare", "load", "run", "worker", "verify"]
    )
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(
                args.root,
                args.parent,
                args.section,
                args.tokenizer,
                adapter=args.adapter,
                recovery_section=args.recovery_section,
            )
        elif args.command == "load":
            _, _, _, data, _ = load(args.root, args.job)
            result = {
                "state": "task_mapped_editorial_inputs_authenticated",
                "job": args.job,
                "points": len(data["ids"]),
                "model_calls": 0,
            }
        else:
            result = globals()[args.command](args.root, args.job)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
