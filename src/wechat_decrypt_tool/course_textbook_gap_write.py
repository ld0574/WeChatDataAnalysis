"""Write and independently check focused additions to an audited lesson.

Artifacts stay private. A reading draft is produced only after all bounded units
finish; failed semantic reviews remain visible and never become acceptance.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
from pathlib import Path

import jsonschema
import psutil

from . import course_textbook_article as article
from . import course_textbook_gap_plan as planmod
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_pdf_ocr import local_model_digest
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore
from .course_wiki import object_schema
from .course_wiki_writer import DraftJob

POLICY = "evidence-focused-whole-lesson-additions-v1"
TEXT = {"type": "string", "minLength": 1}
WRITE = planmod.COMMON + """
你现在写给读者一篇教材的一个逻辑单元，而不是摘要、知识点清单或工作报告。
unit.complete_sources 是完整原文。依据它充分解释本单元的知识、步骤、区别、条件和例子。
unit.gap_sources 标明此前遗漏的具体知识，必须逐项实质讲清，不是仅提名词。
有 existing_section 时重写这个小节：保留有依据的正确知识、修正错误、补足缺口，形成连贯的完整讲解。
用多个有展开的段落，解释“是什么、为何如此、如何操作、何时适用”；材料未教的方面不编造。
不要因为小节太长而压缩成摘要；也不要重复同一意思凑字。步骤、代码和具体例子用段内换行清楚呈现。
不要写“老师说”“视频中”“来源编号”，不要输出 schema 字段名、占位句或让读者自行查原文。
每段 source_keys 仅指真正支持整段的原文。可以重新解释、归纳和绘图，但不能补造课程外事实。
图仅在帮助理解时绘制，flow 只表示有依据的顺序，relationships 表示关系；不为每个小节硬加流程图。
每个单元提供一道检验理解或应用的自测，有正确答案、解释和具体掌握标准。
若 unit 是 self_test，仅修正原自测，保留真实考点。
若提供 prior_candidate 和 negative_review，本次仅有的一轮修订需解决实际问题、保留正确解释。
"""
REVIEW = planmod.COMMON + """
独立检查 candidate 的每项实际断言是否由 complete_sources 支持，包含条件、数值、因果、图的方向及题目答案。
不要要求原文有同名标题、同措辞、自测题或重绘图；合理的解释和重组可接受，但其具体断言需有依据。
facts 每键都判断一次；通过时选择真正支撑内容的 source_keys，失败写具体原因。
candidate 按 p000、p001 等列出段落，d000 等列出图，q000 为自测，段落下标从 0 开始。
coverage 对每个 gap 点核对完整原文与实际段落，判断该项教学是否充分解释，并给出实际段落下标。
retention 对每段原有讲解判断正确知识是否被保留；原段存在错误而已据原文纠正可通过并说明。
不能因字面关键词出现就判覆盖；也不要把课程外扩展、冗余修辞当成必要知识。
"""
COVERAGE = planmod.COMMON + """
逐份反查 sources 的完整原文，对照整篇 article，确认有效教学是否已被充分讲解。
选择 covered 并列出真正讲清知识的 paragraph_ids；仅出现关键词不能通过。
原文中的条件、操作步骤、重要例子和限制也要核对。任何有效教学尚缺选 missing，具体说明缺口。
无有效教学才选 non_teaching，寒暄、广告、重复 OCR、导航无需展开。
资料重叠时可由同一段解释支持多份来源。不得因为来源未逐字复制或解释顺序改变而判缺失。
每份来源恰好一次；这是独立复查，不采纳之前的覆盖断言作为事实。
"""
READ = planmod.COMMON + """
通读 article，检查能否独立学习：是否按知识和依赖组织、概念衔接清楚、解释充分、例子可理解、
图与正文一致、自测能检验理解。不能把章节数量或字数当作质量。
分别判断 organisation、depth、coherence、figures、self_tests，各写具体理由。不重新改写正文。
"""


def require(value, reason):
    if not value:
        raise ValueError("requires_focused_writing_" + reason)


def read(path):
    return json.loads(Path(path).read_bytes())


def load_plan(root, key):
    require(re.fullmatch(r"[a-f0-9]{32}", key), "valid_plan_id")
    folder = Path(root) / "wiki/textbook-gap-plan-jobs" / key
    progress, result = read(folder / "progress.json"), read(folder / "result.json")
    require(not planmod.identity_live(progress) and progress["stage"] == "complete"
            and result["state"] == "private_knowledge_gap_plan_ready", "completed_exited_plan")
    manifest = read(folder / "manifest.json")
    require(manifest["id"] == key == sha(canonical(manifest["recipe"]))[:32], "unchanged_plan_manifest")
    inputs = planmod.parent_inputs(root, result["parent"])
    plan = read(folder / "private-authoring-plan.json")
    require(plan["parent_bindings"] == inputs["bindings"], "same_final_parent")
    topics = {f"t{i:03d}": v for i, v in enumerate(read(folder / "topic-proposal.json")["data"]["topics"])}
    require(plan == planmod.compile_plan(inputs, topics, plan["source_dispositions"]), "reproducible_plan")
    require(not any(u["evidence_missing"] for u in plan["units"]), "all_units_have_evidence")
    return inputs, plan, {name: sha((folder / name).read_bytes()) for name in
                         ["manifest.json", "private-authoring-plan.json", "result.json"]}


def writing_schema(unit):
    refs = {"type": "array", "minItems": 1, "uniqueItems": True,
            "items": {"enum": unit["source_ids"]}}
    quiz = object_schema({**article.QUIZ["properties"], "source_keys": refs})
    if "self_test" in unit["target"]:
        return object_schema({"self_test": quiz})
    paragraph = object_schema({"text": {"type": "string", "minLength": 40}, "source_keys": refs})
    graph = object_schema({**article.GRAPH["properties"], "source_keys": refs})
    return object_schema({"paragraphs": {"type": "array", "minItems": 2, "maxItems": 24, "items": paragraph},
                          "diagrams": {"type": "array", "maxItems": 4, "items": graph}, "self_test": quiz})


def without_sources(value):
    return {k: copy.deepcopy(v) for k, v in value.items() if k != "source_keys"}


def check_candidate(candidate, unit):
    jsonschema.validate(candidate, writing_schema(unit))
    for row in candidate.get("paragraphs", []):
        require(not re.fullmatch(r"\s*(?:diagrams|quiz|paragraphs|chapters|heading)\s*", row["text"]), "no_placeholder")
    for graph in candidate.get("diagrams", []):
        article.validate_graph(without_sources(graph))
    return candidate


def claims_of(candidate):
    return {**{f"p{i:03d}": row for i, row in enumerate(candidate.get("paragraphs", []))},
            **{f"d{i:03d}": row for i, row in enumerate(candidate.get("diagrams", []))},
            "q000": candidate["self_test"]}


def review_schema(candidate, unit):
    fact = object_schema({"passed": {"type": "boolean"}, "reason": TEXT,
        "source_keys": {"type": "array", "uniqueItems": True, "items": {"enum": unit["source_ids"]}}})
    coverage = object_schema({"passed": {"type": "boolean"}, "reason": TEXT,
        "paragraph_indices": {"type": "array", "uniqueItems": True,
            "items": {"type": "integer", "minimum": 0,
                      "maximum": max(0, len(candidate.get("paragraphs", [])) - 1)}}})
    retention = object_schema({"passed": {"type": "boolean"}, "reason": TEXT})
    old = unit.get("existing_section", {}).get("paragraphs", [])
    return object_schema({"facts": object_schema({k: fact for k in claims_of(candidate)}),
        "coverage": object_schema({p["point_id"]: coverage for p in unit["gap_sources"]}),
        "retention": object_schema({f"old{i:03d}": retention for i in range(len(old))})})


def validate_review(value, candidate, unit):
    jsonschema.validate(value, review_schema(candidate, unit))
    for row in value["facts"].values():
        require(not row["passed"] or bool(row["source_keys"]), "positive_fact_actual_support")
    for row in value["coverage"].values():
        require(not row["passed"] or bool(row["paragraph_indices"]), "positive_coverage_actual_paragraph")


def passed(review):
    return all(row["passed"] for group in review.values() for row in group.values())


def assemble(inputs, plan, candidates):
    require(set(candidates) == {u["unit"] for u in plan["units"]}, "every_unit_written")
    book = copy.deepcopy(inputs["book"])
    sections = {s["id"]: s for c in book["chapters"] for s in c["sections"]}
    additions = {}
    for unit in plan["units"]:
        candidate = check_candidate(candidates[unit["unit"]], unit)
        target = unit["target"]
        if "self_test" in target:
            book["quiz"][int(target["self_test"][1:])] = without_sources(candidate["self_test"])
            continue
        content = {"paragraphs": [p["text"] for p in candidate["paragraphs"]],
                   "diagrams": [without_sources(g) for g in candidate["diagrams"]]}
        if "section" in target:
            require(target["section"] in sections, "real_replacement_section")
            sections[target["section"]].update(content)
        else:
            key = "addition-" + unit["unit"]
            additions.setdefault(target["after_chapter"], []).append({"id": key,
                "heading": unit["heading"], "sections": [{"id": key + "-section",
                "heading": unit["heading"], **content}]})
        book["quiz"].append(without_sources(candidate["self_test"]))
    book["chapters"] = [*additions.get("start", []), *[
        row for c in book["chapters"] for row in [c, *additions.get(c["id"], [])]]]
    article.validate_book(book)
    return book


class WritingJob(planmod.GapPlanJob):
    def __init__(self, store, inputs, plan, key, binding, tokenizer, model):
        from tokenizers import Tokenizer
        self.inputs, self.tokenizer = inputs, Tokenizer.from_file(str(tokenizer))
        DraftJob.__init__(self, store, {"sources": inputs["sources"]}, model, inputs["model_digest"], 21600,
            pipeline=POLICY, job_directory="textbook-gap-write-jobs", max_attempts=2,
            recipe_extra={"plan": key, "plan_bindings": binding, "parent_bindings": inputs["bindings"],
                "module_sha256": sha(Path(__file__).read_bytes()), "plan_module_sha256": sha(Path(planmod.__file__).read_bytes()),
                "tokenizer_sha256": sha(Path(tokenizer).read_bytes()), "prompts": [WRITE, REVIEW, COVERAGE, READ],
                "maximum_semantic_repairs_per_unit": 1, "maximum_context": planmod.MAX_CONTEXT,
                "old_budgets_reset": False})
        self.plan = plan

    def progress(self, state, stage, **extra):
        self.current = stage
        value = {**self.identity, "job": self.key, "parent": self.inputs["parent"],
            "job_folder": str(self.root), "state": state, "stage": stage, "updated": time.time(),
            "reader_publication_allowed": False, "source_deletion_allowed": False, **extra}
        atomic_json(self.root / "progress.json", value)
        atomic_json(self.store.root / "wiki/textbook-gap-write-current.json", value)


def write_unit(job, unit):
    packet = {"sources": {k: job.inputs["sources"][k] for k in unit["source_ids"]}}
    context = {"article_context": job.inputs["book"], "unit": unit}
    schema = writing_schema(unit)
    candidate = job.stage(unit["unit"] + "-write", WRITE, schema, context,
                          lambda v: check_candidate(v, unit), evidence_packet=packet)
    review = None
    for round_number in range(2):
        review = job.stage(unit["unit"] + f"-review-{round_number}", REVIEW,
            review_schema(candidate, unit), {"unit": {k: v for k, v in unit.items() if k != "complete_sources"},
                                            "candidate": claims_of(candidate), "complete_sources": unit["complete_sources"]},
            lambda v: validate_review(v, candidate, unit), evidence_packet=packet)
        if passed(review):
            break
        if round_number == 0:
            candidate = job.stage(unit["unit"] + "-repair", WRITE, schema,
                {**context, "prior_candidate": candidate, "negative_review": review},
                lambda v: check_candidate(v, unit), evidence_packet=packet)
    proof = {"unit": unit["unit"], "candidate": candidate, "review": review,
             "checks_passed": passed(review), "reader_publication_allowed": False}
    atomic_json(job.root / (unit["unit"] + "-private-result.json"), proof)
    return candidate, proof


def source_schema(refs, body):
    choice = {"anyOf": [object_schema({"kind": {"const": "covered"}, "reason": TEXT,
        "paragraph_ids": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": list(body)}}}),
        object_schema({"kind": {"enum": ["missing", "non_teaching"]}, "reason": TEXT})]}
    return object_schema({"sources": object_schema({k: copy.deepcopy(choice) for k in refs})})


def run(root, key, tokenizer, *, model="qwen3.5:9b"):
    store = TextbookStore(root)
    store.checkpoint()
    inputs, plan, binding = load_plan(root, key)
    require(local_model_digest(model) == inputs["model_digest"], "verified_local_model")
    with schema_lease(store.root / "wiki/textbook-gap-write.lock", timeout=0):
        job = WritingJob(store, inputs, plan, key, binding, tokenizer, model)
        parent_binding = store.root / "wiki/textbook-gap-plan-jobs" / key / "writing-binding.json"
        expected = {"job": job.key, "manifest_sha256": sha((job.root / "manifest.json").read_bytes())}
        require(not parent_binding.exists() or read(parent_binding) == expected, "existing_plan_writing_budget_binding")
        if not parent_binding.exists():
            atomic_json(parent_binding, expected)
        try:
            candidates, proofs = {}, {}
            for unit in plan["units"]:
                candidate, proof = write_unit(job, unit)
                candidates[unit["unit"]], proofs[unit["unit"]] = candidate, proof
                job.progress("writing_units", unit["unit"] + "-done", units_written=len(candidates),
                             total_units=len(plan["units"]), unit_checks_passed=sum(p["checks_passed"] for p in proofs.values()))
            book = assemble(inputs, plan, candidates)
            atomic_json(job.root / "expanded-reading.json", book)
            (job.root / "expanded-reading.html").write_text(article.standalone_html(book), encoding="utf-8")
            body = planmod.body_index(book)[0]
            coverage = {}
            refs = list(inputs["sources"])
            for number, start in enumerate(range(0, len(refs), 24)):
                selected = refs[start:start + 24]
                schema = source_schema(selected, body)
                value = job.stage(f"whole-sources-{number:03d}", COVERAGE, schema,
                    {"article": planmod.labelled_book(book), "sources": planmod.whole_sources(inputs["sources"], selected)},
                    lambda v, schema=schema: jsonschema.validate(v, schema),
                    evidence_packet={"sources": {k: inputs["sources"][k] for k in selected}})
                coverage.update(value["sources"])
            schema = object_schema({k: object_schema({"passed": {"type": "boolean"}, "reason": TEXT})
                                    for k in ["organisation", "depth", "coherence", "figures", "self_tests"]})
            reading = job.stage("whole-reading", READ, schema, {"article": book}, lambda v: jsonschema.validate(v, schema))
            audit = {"article_sha256": sha(canonical(book)), "unit_proofs": proofs,
                     "source_coverage": coverage, "reading": reading, "all_prior_negative_reviews": plan["all_prior_negative_reviews"]}
            atomic_json(job.root / "expanded-audit.json", audit)
            result = {"state": "expanded_draft_checks_passed_scope_pending" if all(p["checks_passed"] for p in proofs.values())
                      and not any(v["kind"] == "missing" for v in coverage.values()) and all(v["passed"] for v in reading.values())
                      else "expanded_draft_requires_changes", "job": job.key, "plan": key,
                "article_sha256": sha(canonical(book)), "body_characters": sum(map(len, body.values())),
                "paragraphs": len(body), "chapters": len(book["chapters"]), "units_written": len(candidates),
                "units_with_issues": sum(not p["checks_passed"] for p in proofs.values()),
                "all_sources_checked": len(coverage), "missing_sources": sum(v["kind"] == "missing" for v in coverage.values()),
                "reading_issues": sum(not v["passed"] for v in reading.values()),
                "scope_verified": False, "reader_publication_allowed": False, "source_deletion_allowed": False, "goal_complete": False}
            atomic_json(job.root / "result.json", result)
            job.progress(result["state"], "complete", **{k: v for k, v in result.items() if k not in
                         ["state", "job", "reader_publication_allowed", "source_deletion_allowed"]})
            return result
        except Exception as exc:
            msg = str(exc)
            job.progress("paused" if isinstance(exc, InterruptedError) else "stopped", job.current,
                error_type=type(exc).__name__, reason_code=msg if re.fullmatch(r"[a-zA-Z0-9_-]{1,180}", msg) else None,
                error_sha256=sha(msg.encode()))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan-job")
    parser.add_argument("--parent-job")
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--wait-plan", type=int, default=7200)
    parser.add_argument("command", choices=["worker", "guard"])
    args = parser.parse_args()
    if args.command == "worker":
        print(json.dumps(run(args.root, args.plan_job, args.tokenizer)))
        return
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded
    store = TextbookStore(args.root)
    with schema_lease(store.root / "wiki/textbook-gap-write-dispatch.lock", timeout=0):
        deadline = time.monotonic() + args.wait_plan
        while True:
            store.checkpoint()
            current = store.root / "wiki/textbook-gap-plan-current.json"
            state = read(current) if current.exists() else None
            if state and (not args.parent_job or state["parent"] == args.parent_job):
                if state["stage"] == "complete" and not planmod.identity_live(state):
                    key = state["job"]
                    break
                require(planmod.identity_live(state), "plan_stopped_before_completion")
            require(time.monotonic() < deadline, "plan_wait_timeout_no_restart")
            atomic_json(store.root / "wiki/textbook-gap-write-dispatch.json", {
                "state": "waiting_for_plan", "parent": args.parent_job, "pid": os.getpid(),
                "created": psutil.Process().create_time(), "updated": time.time()})
            time.sleep(5)
        guardpath = store.root / "review-experiment-thermal-guard.json"
        while guardpath.exists() and planmod.identity_live(read(guardpath)):
            store.checkpoint()
            require(time.monotonic() < deadline, "plan_guard_exit_wait_timeout")
            time.sleep(1)
        guarded(store.root, [sys.executable, "-m", "wechat_decrypt_tool.course_textbook_gap_write",
            "--root", str(store.root), "--plan-job", key, "--tokenizer", str(args.tokenizer.resolve()), "worker"],
            maximum_temperature=70, maximum_power=200, guard_name="review-experiment-thermal-guard",
            sampler=lambda: checked_sample(store.root))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        msg = str(exc)
        print(json.dumps({"state": "stopped", "error_type": type(exc).__name__,
            "reason_code": msg if re.fullmatch(r"[a-zA-Z0-9_-]{1,180}", msg) else None,
            "error_sha256": sha(msg.encode())}))
        raise SystemExit(1) from None
