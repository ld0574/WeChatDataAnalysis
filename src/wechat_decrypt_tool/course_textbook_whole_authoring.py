"""Conceptual articles from verified complete-record ledgers and local models.

Scheduling groups never become article chapters. The existing ledger's requests,
responses and budgets are verified read-only before any authoring budget starts.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
import sys
import time
from pathlib import Path

import psutil
import jsonschema
from tokenizers import Tokenizer

from . import course_textbook_authoring as author
from . import course_textbook_composition as composition
from . import course_textbook_whole_sources as whole
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_pdf_ocr import local_model_digest
from .course_textbook_article import reading_book, standalone_html
from .course_textbook_ledger import TextbookStore, validate_result
from .course_wiki import object_schema
from .course_wiki_writer import DraftJob

POLICY = "complete-record-conceptual-article-independent-review-v1"
PLAN_CHARACTERS = 32000
WRITE_INPUT_TOKENS = 18000
READ_INPUT_TOKENS = 42000
SECTION_POINTS = 48
NOTICE = """
本次 sources 和 context_sources 是未截断的完整原始记录，调度组不是文章章节。
知识账本只帮助定位教学，不是独立事实依据；一切断言必须由实际原文支持。
按知识依赖独立重组，归并重复讲解，保留真实条件、步骤、例子和限制，不拼接来源摘要。
candidate_not_verified 的课件尚待关联审核，不得声称老师在本课实际讲过全部课件内容。
不得把内部编号、审核状态、逐字识别稿、时间窗口和待处理指令写成读者正文。
"""


def require(value, code):
    if not value:
        raise ValueError("requires_whole_authoring_" + code)


def bindings():
    names = ["course_textbook_whole_authoring", "course_textbook_authoring",
        "course_textbook_article", "course_textbook_composition", "course_textbook_whole_sources", "course_wiki_writer",
        "course_textbook_gap_plan", "course_textbook_gap_final_review", "course_wiki_text_inference"]
    return {name: whole.sha(Path(__file__).with_name(name + ".py").read_bytes()) for name in names}


class ReadOnlyJob:
    """Reconstruct accepted requests without inference or changing old progress."""

    def __init__(self, job):
        self.job, self.root = job, job.root

    def stage_input(self, *args, **kwargs):
        return self.job.stage_input(*args, **kwargs)

    def stage(self, name, prompt, schema, context, validate, **kwargs):
        saved = whole.read(self.root / (name + ".json"))
        expected = self.stage_input(prompt, schema, context, **kwargs)[1]
        require(saved["input_sha256"] == expected
            and saved["data_sha256"] == whole.sha(canonical(saved["data"])), "unchanged_actual_ledger_request")
        validate(saved["data"])
        return saved["data"]


def load_inputs(root, inventory_key, ledger_job, tokenizer):
    root = Path(root).resolve()
    require(re.fullmatch(r"[a-f0-9]{32}", ledger_job), "ledger_job_id")
    inventory = whole.load(root, inventory_key)
    folder = root / "wiki/textbook-keyed-jobs" / ledger_job
    manifest = whole.read(folder / "manifest.json")
    binding = manifest["binding"]
    require(manifest["id"] == ledger_job == whole.sha(canonical(binding))[:32]
        and binding["inventory"] == inventory_key, "actual_complete_record_ledger")
    require(binding["module_sha256"] == whole.module_bindings()
        and binding["tokenizer_sha256"] == whole.sha(Path(tokenizer).read_bytes()), "original_ledger_code_and_tokenizer")
    parent = root / "wiki/textbook-evidence" / inventory["parent_inventory_sha256"]
    with schema_lease(parent / "ledger-worker.lock", timeout=0):
        require(whole.read(parent / "whole-source-ledger-execution.json") == binding, "original_ledger_budget")
        saved = whole.read(folder / "ledger.json")
        ledger = saved["data"]
        require(saved["data_sha256"] == whole.sha(canonical(ledger)), "ledger_checksum")
        validate_result(ledger, inventory, ledger_job)
        result = whole.read(folder / "result.json")
        require(result["job"] == ledger_job and result["inventory"] == inventory_key
            and result["completed_groups"] == len(inventory["pages"])
            and result["state"] == ledger["state"], "terminal_complete_ledger")
        roles = [ReadOnlyJob(whole.Job(TextbookStore(root), inventory, binding, model, tokenizer))
            for model in ["qwen3.5:9b", "course-defiant:q4"]]
        outputs = []
        for group, refs in enumerate(inventory["pages"]):
            context = whole.context_for(inventory, refs)
            selected = {ref: inventory["sources"][ref] for ref in refs}
            data, used = whole.process_group(*roles, context, selected, f"group-{group:04d}", cached_only=True)
            proof = {"input_sha256": whole.sha(canonical(context)), "data": data,
                "data_sha256": whole.sha(canonical(data)),
                "stage_files": {str(path): whole.sha(path.read_bytes()) for path in used}}
            require(whole.read(folder / f"group-{group:04d}.json") == proof, "actual_completed_group_proof")
            outputs.append((data["candidate"], data["audit"]))
        require(whole.compile_ledger(inventory, ledger_job, outputs) == ledger, "entire_ledger_reconstructed")
    return inventory, working_inputs(inventory, ledger)


def working_inputs(inventory, ledger):
    """Keep negative reviews; quarantine disputed claims and write from originals."""
    scope = set()
    for issue in ledger["issues"]:
        audit = issue["review"]
        ids = ledger["pages"][issue["group"]]["points"]
        for row in audit["reviews"]:
            if row["verdict"] != "supported":
                scope.update(ledger["points"][ids[row["point"]]]["citations"])
        scope.update(ref for row in audit["missing_points"] for ref in row["citations"])
        scope.update(row["reference"] for row in audit["excluded_sources"] if not row["valid"])
    while True:
        quarantined = {key for key, point in ledger["points"].items() if scope.intersection(point["citations"])}
        expanded = scope | {ref for key in quarantined for ref in ledger["points"][key]["citations"]}
        if expanded == scope:
            break
        scope = expanded
    require(scope <= set(inventory["sources"]), "pending_original_sources_exist")
    points = {key: copy.deepcopy(point) for key, point in ledger["points"].items() if key not in quarantined}
    cards = {}
    for number, ref in enumerate(sorted(scope)):
        key = f"U{number:05d}"
        require(key not in points, "unique_pending_original_task")
        points[key] = {"label": f"完整来源教学整理任务 {number + 1}", "teaching": inventory["sources"][ref]["text"],
            "category": "context", "citations": [ref]}
        cards[key] = {"reference": ref, "neighbors": list(whole.context_for(inventory, [ref])["context_sources"])}
    return {"policy": POLICY, "job": ledger["job"], "points": points, "pending_source_cards": cards,
        "confirmed_point_count": len(points) - len(cards), "parent_ledger_sha256": whole.sha(canonical(ledger)),
        "quarantined_point_keys": sorted(quarantined), "inherited_issues": copy.deepcopy(ledger["issues"]),
        "source_revision_budget_refreshed": False, "reader_article_published": False}


def tokens(tokenizer, value):
    return len(tokenizer.encode(json.dumps(value, ensure_ascii=False)).ids)


def writing_context(ids, ledger, inventory):
    refs = list(composition.original_packet(ids, ledger, inventory)["sources"])
    return {**whole.context_for(inventory, refs),
        "assigned_points": {key: ledger["points"][key] for key in ids},
        "pending_source_cards": {key: ledger["pending_source_cards"][key] for key in ids if key in ledger["pending_source_cards"]},
        "pending_cards_are_verified_facts": False,
        "attachment_scope": inventory["attachment_scope"]}


def section_schema(ids, ledger):
    teaching = composition.section_schema(ids, ledger)
    if not set(ids) <= set(ledger["pending_source_cards"]):
        return teaching
    teaching["properties"]["kind"] = {"const": "teaching"}
    teaching["required"].append("kind")
    exclusion = object_schema({"kind": {"const": "non_teaching"},
        "coverage": object_schema({key: object_schema({"teaching": {"const": False},
            "paragraphs": {"type": "array", "maxItems": 0}, "reason": author.TEXT}) for key in ids})})
    return {"oneOf": [teaching, exclusion]}


def validate_section(value, ids, ledger, inventory):
    jsonschema.validate(value, section_schema(ids, ledger))
    if value.get("kind") == "non_teaching":
        for row in value["coverage"].values():
            author.nonempty(row["reason"])
        return
    composition.validate_section({key: row for key, row in value.items() if key != "kind"}, ids, ledger, inventory)


def review_schema(candidate, ids):
    if candidate.get("kind") == "non_teaching":
        return object_schema({"coverage": object_schema({key: author.VERDICT for key in ids}), "exclusion": author.VERDICT})
    return author.review_schema(candidate, ids)


def organize(job, ledger):
    cards = {key: {field: point[field] for field in ("label", "teaching", "category")}
        for key, point in ledger["points"].items()}
    require(bool(cards), "real_teaching_points")
    nodes, depth = {}, 0
    while True:
        batches = author.chunks(cards, PLAN_CHARACTERS)
        final, following = len(batches) == 1, {}
        for index, batch in enumerate(batches):
            value = job.stage(f"concepts-{depth:02d}-{index:04d}", author.GROUP_PROMPT,
                author.grouping_schema(batch, final=final), {"items": batch, "mode": "chapters" if final else "concepts"},
                lambda row, batch=batch: author.grouped(row, batch, final=final))
            for number, group in enumerate(author.grouped(value, batch, final=final)):
                key = f"N{depth:02d}_{index:04d}_{number:02d}"
                nodes[key] = group
                following[key] = {field: group[field] for field in ("heading", "purpose")}
        if final:
            plan = {"title": value["title"], "chapters": list(following), "nodes": nodes}
            author.plan_points(plan, ledger["points"])
            return plan
        require(len(following) < len(cards), "finite_concept_plan_compression")
        cards, depth = following, depth + 1


def writing_units(plan, ledger, inventory, tokenizer):
    units = []
    for index, (chapter, ids) in enumerate(zip(plan["chapters"], author.plan_points(plan, ledger["points"]), strict=True)):
        current = []

        def add():
            units.append({"id": f"section-{len(units):04d}", "chapter_id": f"chapter-{index:03d}",
                "chapter": {k: plan["nodes"][chapter][k] for k in ("heading", "purpose")},
                "concept": {k: plan["nodes"][chapter][k] for k in ("heading", "purpose")}, "point_ids": list(current)})

        for key in ids:
            proposed = [*current, key]
            if current and (len(proposed) > SECTION_POINTS
                    or tokens(tokenizer, writing_context(proposed, ledger, inventory)) > WRITE_INPUT_TOKENS):
                add()
                current = []
            current.append(key)
            require(tokens(tokenizer, writing_context(current, ledger, inventory)) <= WRITE_INPUT_TOKENS,
                "complete_single_point_context_fits_no_truncation")
        if current:
            add()
    require([key for unit in units for key in unit["point_ids"]]
        == [key for ids in author.plan_points(plan, ledger["points"]) for key in ids], "every_conceptual_point_allocated_once")
    return units


def reading_blocks(units, sections, tokenizer):
    result, current = [], {}
    for unit in units:
        section = sections[unit["id"]]
        card = {"chapter": unit["chapter"]["heading"], "heading": section["heading"],
            "paragraphs": [row["text"] for row in section["paragraphs"]],
            "diagrams": [{k: v for k, v in graph.items() if k != "citations"} for graph in section["diagrams"]],
            "quiz": {k: v for k, v in section["quiz"].items() if k != "citations"}}
        key = unit["id"]
        require(tokens(tokenizer, {key: card}) <= READ_INPUT_TOKENS, "entire_reading_section_fits")
        if current and tokens(tokenizer, {**current, key: card}) > READ_INPUT_TOKENS:
            result.append(current)
            last = next(reversed(current))
            current = {last: current[last]}
        require(tokens(tokenizer, {**current, key: card}) <= READ_INPUT_TOKENS, "entire_reading_boundary_fits")
        current[key] = card
    if current:
        result.append(current)
    return result


class AuthoringJob(whole.Job):
    def __init__(self, store, inventory, ledger, tokenizer):
        self.tokenizer = Tokenizer.from_file(str(tokenizer))
        self.whole_key, self.ledger_job = whole.sha(canonical(inventory)), ledger["job"]
        self.models = {name: local_model_digest(name) for name in ["qwen3.5:9b", "course-defiant:q4"]}
        DraftJob.__init__(self, store, {"sources": inventory["sources"]}, "qwen3.5:9b",
            self.models["qwen3.5:9b"], 21600, pipeline=POLICY, job_directory="textbook-whole-authoring-jobs",
            max_attempts=2, recipe_extra={"inventory_sha256": self.whole_key,
                "working_inputs_sha256": whole.sha(canonical(ledger)), "parent_ledger_sha256": ledger["parent_ledger_sha256"],
                "models": self.models,
                "tokenizer_sha256": whole.sha(Path(tokenizer).read_bytes()), "module_sha256": bindings(),
                "prompts": [*composition.PROMPTS.values(), NOTICE],
                "limits": [PLAN_CHARACTERS, WRITE_INPUT_TOKENS, READ_INPUT_TOKENS, SECTION_POINTS],
                "maximum_semantic_repairs_per_section": 1, "old_budgets_reset": False})
        self.section_count, self.finished_sections = 0, 0
        self.point_count, self.pending_count = ledger["confirmed_point_count"], len(ledger["pending_source_cards"])

    def budget(self, messages, stage):
        count = sum(len(self.tokenizer.encode(row["content"]).ids) for row in messages)
        output = 12288 if stage.endswith(("-write", "-repair")) else 8192
        context = max(32768, math.ceil((count + output + 1024) / 16384) * 16384)
        require(context <= 65536, "actual_complete_stage_context_fits")
        return count, output, context

    def stage(self, name, prompt, schema, context, validate, **kwargs):
        require(prompt in {author.GROUP_PROMPT, author.WRITE_PROMPT, author.REVIEW_PROMPT,
            author.REPAIR_PROMPT, author.READ_PROMPT}, "declared_stage_role")
        model = "course-defiant:q4" if prompt in {author.REVIEW_PROMPT, author.READ_PROMPT} else "qwen3.5:9b"
        previous = self.model, self.model_digest
        self.model, self.model_digest = model, self.models[model]
        try:
            prompt = composition.PROMPTS[prompt] + NOTICE + """
若当前全部任务确为非教学，允许 kind=non_teaching 并逐项给出内部排除判断，不编造正文、图或自测。
独立复核必须对照完整原文判断每项排除是否成立；明确非教学不是完成有教学文章的捷径。
"""
            expected = self.stage_input(prompt, schema, context, **kwargs)[1]
            value = super().stage(name, prompt, schema, context, validate, **kwargs)
            actual, binding = whole.editor.e.checked_cache(self.root, name)
            require(actual == value and expected == binding, "actual_article_stage_request")
            proof = {"stage": name, "model": model, "model_digest": self.model_digest,
                "input_sha256": expected, "data_sha256": whole.sha(canonical(value))}
            path = self.root / (name + ".proof.json")
            require(not path.exists() or whole.read(path) == proof, "immutable_article_stage_proof")
            atomic_json(path, proof)
            return value
        finally:
            self.model, self.model_digest = previous

    def progress(self, state, stage, **extra):
        self.current = stage
        value = {**self.identity, "state": state, "stage": stage, "job": self.key,
            "inventory": self.whole_key, "ledger_job": self.ledger_job, "updated": time.time(),
            "current_model": self.model, "completed_sections": self.finished_sections,
            "total_sections": self.section_count, "knowledge_points": self.point_count,
            "pending_original_tasks": self.pending_count,
            "reader_article_published": False, "source_deletion_allowed": False, **extra}
        atomic_json(self.root / "progress.json", value)
        atomic_json(self.store.root / "wiki/whole-source-authoring-current.json", value)


def backup_job(job):
    dest = Path.home() / "Documents/CourseKnowledgeBackups/whole-source-articles" / job.key
    for path in [*job.root.rglob("*.json"), *[Path(__file__).with_name(name + ".py") for name in bindings()]]:
        if path.name != "progress.json":
            relative = path.relative_to(job.root) if path.is_relative_to(job.root) else Path("code") / path.name
            whole.backup(path, dest / relative)


def final_issues(inventory, private, issues):
    result = copy.deepcopy(issues)
    result.append({"kind": "complete_original_source_to_actual_article_review_required"})
    if inventory["attachment_scope"].get("unresolved", True):
        result.append({"kind": "item_specific_courseware_reference_and_visual_scope_required"})
    if not any(section["diagrams"] for chapter in private["chapters"] for section in chapter["sections"]):
        result.append({"kind": "missing_supported_explanatory_diagram"})
    return result


def run(root, inventory_key, ledger_job, tokenizer):
    root = Path(root).resolve()
    inventory, ledger = load_inputs(root, inventory_key, ledger_job, tokenizer)
    store = TextbookStore(root)
    store.checkpoint()
    with schema_lease(root / "wiki/textbook-whole-record-evidence" / inventory_key / "authoring-worker.lock", timeout=0):
        job = AuthoringJob(store, inventory, ledger, tokenizer)
        contract = root / "wiki/textbook-keyed-jobs" / ledger_job / "whole-source-authoring-execution.json"
        binding = {"inventory": inventory_key, "ledger_sha256": whole.sha(canonical(ledger)), "job": job.key}
        require(not contract.exists() or whole.read(contract) == binding, "same_original_authoring_budget")
        atomic_json(contract, binding)
        (job.root / "failures").mkdir(exist_ok=True)
        try:
            job.progress("planning_conceptual_article", "concept_plan")
            author.save(job.root, "private-working-inputs", ledger)
            backup_job(job)
            plan = organize(job, ledger)
            author.save(job.root, "concept-plan", plan)
            units = writing_units(plan, ledger, inventory, job.tokenizer)
            author.save(job.root, "writing-allocation", units)
            job.section_count = len(units)
            sections, issues, excluded_units = {}, [], {}
            for number, unit in enumerate(units):
                ids, prefix = unit["point_ids"], unit["id"]
                packet = composition.original_packet(ids, ledger, inventory)
                previous = next(reversed(sections)) if sections else None
                context = {**writing_context(ids, ledger, inventory), "chapter": unit["chapter"],
                    "concept": unit["concept"], "article_outline": [plan["nodes"][key] for key in plan["chapters"]],
                    "diagram_preferred": not sections or units[number - 1]["chapter_id"] != unit["chapter_id"],
                    "previous_reading_section": sections[previous] if previous else None}

                def write(suffix, prompt, extra=None):
                    return job.stage(prefix + suffix, prompt, section_schema(ids, ledger),
                        {**context, **(extra or {})}, lambda row: validate_section(row, ids, ledger, inventory),
                        evidence_packet=packet)

                def review(candidate, suffix):
                    schema = review_schema(candidate, ids)
                    return job.stage(prefix + suffix, author.REVIEW_PROMPT, schema, {**context, "candidate": candidate},
                        lambda row: author.validate_review(row, schema), evidence_packet=packet)

                candidate = write("-write", author.WRITE_PROMPT)
                audit = review(candidate, "-review")
                if not all(author.verdicts(audit)):
                    candidate = write("-repair", author.REPAIR_PROMPT, {"candidate": candidate, "review": audit})
                    audit = review(candidate, "-repair-review")
                if not all(author.verdicts(audit)):
                    issues.append({"kind": "section_semantic_review", "section": prefix, "review": audit})
                if candidate.get("kind") == "non_teaching":
                    excluded_units[prefix] = {"candidate": candidate, "audit": audit}
                else:
                    sections[prefix] = {key: row for key, row in candidate.items() if key != "kind"}
                job.finished_sections += 1
                job.progress("authoring_conceptual_article", prefix, unresolved_sections=len(issues))
                backup_job(job)
            actual_units = [unit for unit in units if unit["id"] in sections]
            require(bool(actual_units), "actual_teaching_article_content")
            for number, block in enumerate(reading_blocks(actual_units, sections, job.tokenizer)):
                audit = job.stage(f"reading-{number:04d}", author.READ_PROMPT, author.READ_REVIEW,
                    {"complete_reading_sections": block}, lambda row: author.validate_review(row, author.READ_REVIEW))
                if not all(author.verdicts(audit)):
                    issues.append({"kind": "whole_reading_review", "block": number, "review": audit})
            excluded_keys = {key for unit in units if unit["id"] in excluded_units for key in unit["point_ids"]}
            article_ledger = {**ledger, "points": {key: row for key, row in ledger["points"].items() if key not in excluded_keys}}
            private = author.assemble(inventory, article_ledger, plan, actual_units, sections)
            require(set(private["private_coverage"]) | excluded_keys == set(ledger["points"]), "all_writing_targets_accounted_for")
            private.update(private_non_teaching_units=excluded_units,
                parent_ledger_sha256=ledger["parent_ledger_sha256"], inherited_ledger_issues=ledger["inherited_issues"],
                quarantined_point_keys=ledger["quarantined_point_keys"], writing_budget_refreshed=False)
            issues = final_issues(inventory, private, issues)
            author.save(job.root, "private-article", private)
            author.save(job.root, "quality-issues", issues)
            book = reading_book(private)
            (job.root / "private-reading.html").write_text(standalone_html(book), encoding="utf-8")
            result = {"state": "conceptual_article_requires_actual_source_and_editorial_review", "job": job.key,
                "inventory": inventory_key, "ledger_job": ledger_job, "chapters": len(book["chapters"]),
                "sections": len(actual_units), "knowledge_points": ledger["confirmed_point_count"],
                "pending_original_tasks": len(ledger["pending_source_cards"]), "remaining_reviews": len(issues),
                "article_sha256": whole.sha(canonical(book)), "reader_article_published": False,
                "source_deletion_allowed": False, "goal_complete": False}
            atomic_json(job.root / "result.json", result)
            backup_job(job)
            job.progress(result["state"], "private_article_assembled", remaining_reviews=len(issues))
            return result
        except Exception as exc:
            status = whole.failure_status(exc)
            atomic_json(job.root / "failures" / (str(time.time_ns()) + ".json"), status)
            job.progress(status["state"], job.current, **{k: v for k, v in status.items() if k != "state"})
            raise


def identity_live(row):
    try:
        return abs(psutil.Process(row["pid"]).create_time() - row["created"]) < .01
    except psutil.NoSuchProcess:
        return False


def guard(root, inventory_key, ledger_job, tokenizer):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded

    root = Path(root).resolve()
    load_inputs(root, inventory_key, ledger_job, tokenizer)
    with schema_lease(root / "wiki/whole-source-authoring-dispatch.lock", timeout=0):
        previous = root / "wiki-revision-thermal-guard.json"
        if previous.exists():
            old = whole.read(previous)
            require(not identity_live(old) and old["state"] == "worker_exited" and old.get("returncode") == 0,
                "previous_wiki_guard_cleanly_exited")
            whole.backup(previous, root / "wiki/whole-authoring-guard-history" / (whole.sha(previous.read_bytes()) + ".json"))
        guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root),
            "--inventory", inventory_key, "--ledger-job", ledger_job, "--tokenizer", str(Path(tokenizer).resolve()), "worker"],
            maximum_temperature=70, maximum_power=200, guard_name="wiki-revision-thermal-guard",
            sampler=lambda: checked_sample(root))


def wait_for_ledger(root, inventory_key, ledger_job, *, live=identity_live, sleep=time.sleep, checkpoint=None):
    """Observe this exact live worker; never restart it or infer a terminal state."""
    root = Path(root).resolve()
    checkpoint = checkpoint or TextbookStore(root).checkpoint
    current = root / "wiki/whole-source-ledger-current.json"
    first = whole.read(current)
    require(first["job"] == ledger_job and first["inventory"] == inventory_key, "same_pending_ledger")
    identity = {key: first[key] for key in ("pid", "created")}
    progress = root / "wiki/whole-source-authoring-dispatch-current.json"
    while True:
        checkpoint()
        row = whole.read(current)
        require(row["job"] == ledger_job and row["inventory"] == inventory_key
            and all(row[key] == value for key, value in identity.items()), "unchanged_pending_worker_identity")
        if row["state"] in {"paused", "stopped"}:
            raise ValueError("requires_whole_authoring_ledger_not_ready_preserve_negative_result")
        if not live(identity):
            require(row["state"] in {"ledger_ready_for_conceptual_reorganization", "ledger_requires_semantic_repair"},
                "missing_worker_without_terminal_success")
            old_guard = whole.read(root / "wiki-revision-thermal-guard.json")
            if not live(old_guard):
                require(old_guard["state"] == "worker_exited" and old_guard.get("returncode") == 0,
                    "ledger_guard_confirmed_success")
                return
        atomic_json(progress, {"state": "waiting_for_exact_ledger_worker", "inventory": inventory_key,
            "ledger_job": ledger_job, "ledger_worker": identity, "pid": psutil.Process().pid,
            "created": psutil.Process().create_time(), "updated": time.time(),
            "completed_groups": row.get("completed_groups", 0), "processing_groups": row["processing_groups"],
            "model_calls_started_by_dispatcher": 0, "reader_article_published": False})
        sleep(10)


def after_ledger(root, inventory_key, ledger_job, tokenizer):
    root = Path(root).resolve()
    path = root / "wiki/whole-source-authoring-dispatch-current.json"
    failures = root / "wiki/whole-authoring-dispatch-failures"
    failures.mkdir(parents=True, exist_ok=True)
    with schema_lease(root / "wiki/whole-source-authoring-waiter.lock", timeout=0):
        try:
            wait_for_ledger(root, inventory_key, ledger_job)
            guard(root, inventory_key, ledger_job, tokenizer)
            state = whole.read(root / "wiki-revision-thermal-guard.json")
            require(state["state"] == "worker_exited" and state.get("returncode") == 0,
                "article_guard_did_not_finish_successfully")
            atomic_json(path, {"state": "private_article_task_finished_not_published",
                "inventory": inventory_key, "ledger_job": ledger_job, "updated": time.time(),
                "reader_article_published": False, "source_deletion_allowed": False, "goal_complete": False})
        except Exception as exc:
            status = {**whole.failure_status(exc), "inventory": inventory_key,
                "ledger_job": ledger_job, "updated": time.time(), "reader_article_published": False}
            atomic_json(failures / (str(time.time_ns()) + ".json"), status)
            atomic_json(path, status)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--ledger-job", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("command", choices=["preflight", "worker", "guard", "after-ledger"])
    args = parser.parse_args()
    if args.command == "preflight":
        inventory, ledger = load_inputs(args.root, args.inventory, args.ledger_job, args.tokenizer)
        print(json.dumps({"state": "complete_ledger_reconstructed_read_only", "original_records": len(inventory["sources"]),
            "knowledge_points": ledger["confirmed_point_count"], "pending_original_tasks": len(ledger["pending_source_cards"]),
            "model_calls": 0, "authoring_budget_started": False}))
    elif args.command == "worker":
        print(json.dumps(run(args.root, args.inventory, args.ledger_job, args.tokenizer)))
    elif args.command == "guard":
        guard(args.root, args.inventory, args.ledger_job, args.tokenizer)
    else:
        after_ledger(args.root, args.inventory, args.ledger_job, args.tokenizer)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps(whole.failure_status(exc)))
        sys.exit(1)
