import copy
from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_whole_coverage as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_authoring import section_value
from test_course_textbook_whole_authoring import CharacterTokenizer, fixture


def manuscript(text="Only integer inputs are allowed, input 3 returns 9."):
    inventory, working, plan = fixture(text)
    inventory["lesson"] = "a" * 24
    working["job"] = "b" * 32
    plan["title"] = "Synthetic full article"
    units = m.writing.writing_units(plan, working, inventory, CharacterTokenizer())
    sections = {unit["id"]: section_value(unit["point_ids"], working) for unit in units}
    private = m.writing.author.assemble(inventory, working, plan, units, sections)
    return inventory, working, private


def new_states(ids):
    return {cid: {"kind": "unresolved", "resolved": False, "teaching_detected": False,
        "classification_conflict": False, "article_items": [], "assessments": []} for cid in ids}


@pytest.mark.parametrize("text", ["", " \r\n ", "a(x,y);\nreturn x+y", "1.25 is literal. Next?", "条件，示例。限制；尾部"])
def test_whole_record_clause_inventory_preserves_every_original_character_and_offset(text):
    inventory = {"sources": {"O00000": {"kind": "speech", "text": text}, "O00001": {"kind": "frame", "text": "Second complete record"}}}
    units = m.source_units(inventory)
    for ref, source in inventory["sources"].items():
        pieces = [source["text"][row["start"]:row["end"]] for row in units.values() if row["reference"] == ref]
        assert "".join(pieces) == source["text"]
        assert all(row["sha256"] == m.writing.whole.sha(source["text"][row["start"]:row["end"]].encode())
            for row in units.values() if row["reference"] == ref)


def test_full_article_content_is_indexed_but_quizzes_cannot_cover_missing_body_teaching():
    inventory, _, private = manuscript()
    items = m.article_items(private, inventory)
    assert sum(row["kind"] == "paragraph" for row in items.values()) == sum(len(s["paragraphs"]) for c in private["chapters"] for s in c["sections"])
    assert any(row["kind"] == "diagram" for row in items.values())
    assert {"introduction", "takeaway"} <= {row["kind"] for row in items.values()}
    assert all("quiz" not in row["kind"] for row in items.values())


def test_plan_preserves_long_complete_records_and_allocates_each_clause_once():
    inventory, working, private = manuscript("x" * 10500)
    plan = m.make_plan(inventory, private, working, CharacterTokenizer())
    assert plan["source_characters"] == 10500
    items = m.article_items(private, inventory)
    for batch in plan["batches"]:
        for block in batch["article_blocks"]:
            context = m.context_for(inventory, plan["units"], batch["clauses"], items, block)
            assert context["sources"]["O00000"]["text"] == "x" * 10500
            assert m.tokens(CharacterTokenizer(), m.REVIEW, context) <= m.INPUT_TOKENS
    flat = [cid for batch in plan["batches"] for cid in batch["clauses"]]
    assert flat == list(plan["units"]) and len(flat) == len(set(flat))


def test_token_bounded_blocks_keep_all_complete_body_items_available(monkeypatch):
    inventory, working, private = manuscript()
    section = private["chapters"][0]["sections"][0]
    template = copy.deepcopy(section["paragraphs"][0])
    section["paragraphs"] = [{**template, "text": "Synthetic complete paragraph " + str(i) + " " + "v" * 900} for i in range(12)]
    monkeypatch.setattr(m, "INPUT_TOKENS", 4200)
    plan = m.make_plan(inventory, private, working, CharacterTokenizer())
    items = m.article_items(private, inventory)
    assert any(len(batch["article_blocks"]) > 1 for batch in plan["batches"])
    for batch in plan["batches"]:
        flat = [aid for block in batch["article_blocks"] for aid in block]
        assert len(flat) == len(items) and set(flat) == set(items)
        for block in batch["article_blocks"]:
            context = m.context_for(inventory, plan["units"], batch["clauses"], items, block)
            assert m.tokens(CharacterTokenizer(), m.REVIEW, context) <= 4200
            assert all(context["article"][aid]["content"] == items[aid]["content"] for aid in block)


@pytest.mark.parametrize("large", ["source", "paragraph"])
def test_oversized_indivisible_complete_inputs_stop_without_clipping(monkeypatch, large):
    inventory, working, private = manuscript("x" * 6000 if large == "source" else "Short source.")
    if large == "paragraph":
        private["chapters"][0]["sections"][0]["paragraphs"][0]["text"] = "v" * 8000
    monkeypatch.setattr(m, "INPUT_TOKENS", 3000)
    before = copy.deepcopy((inventory, private))
    with pytest.raises(ValueError, match="complete_single_source_and_article_item_fit"):
        m.make_plan(inventory, private, working, CharacterTokenizer())
    assert (inventory, private) == before


def group_fixture():
    inventory = {"sources": {"O00000": {"text": "Condition, example, limit."}, "O00001": {"text": "Another source."}}}
    units = m.source_units(inventory)
    ids = list(units)
    row = {"kind": "covered", "clauses": ids[:3], "article_items": ["A0"], "reason": "Synthetic complete support"}
    return units, ids, {"groups": [row, {"kind": "non_teaching", "clauses": ids[3:], "article_items": [], "reason": "Synthetic administration"}]}


@pytest.mark.parametrize("changed", ["omission", "duplicate", "invented_clause", "invented_article", "cross_source", "noncontiguous", "blank", "non_teaching_mapping"])
def test_compact_output_cannot_omit_duplicate_or_invent_clause_decisions(changed):
    units, ids, value = group_fixture()
    m.validate_groups(value, ids, ["A0"], units)
    if changed == "omission":
        value["groups"][0]["clauses"].pop()
    elif changed == "duplicate":
        value["groups"].append(copy.deepcopy(value["groups"][0]))
    elif changed == "invented_clause":
        value["groups"][0]["clauses"][0] = "C9999999"
    elif changed == "invented_article":
        value["groups"][0]["article_items"] = ["invented"]
    elif changed == "cross_source":
        value["groups"] = [{**value["groups"][0], "clauses": ids}]
    elif changed == "noncontiguous":
        value["groups"] = [{**value["groups"][0], "clauses": [ids[0], ids[2]]},
            {**value["groups"][0], "clauses": [ids[1]]}, value["groups"][1]]
    elif changed == "blank":
        value["groups"][0]["reason"] = " "
    else:
        value["groups"][1]["article_items"] = ["A0"]
    with pytest.raises((ValueError, m.jsonschema.ValidationError)):
        m.validate_groups(value, ids, ["A0"], units)


def test_an_omitted_condition_cannot_be_hidden_by_a_covered_example():
    units, ids, _ = group_fixture()
    states = new_states(ids[:2])
    groups = {"G0": {"kind": "covered", "clauses": ids[:2], "article_items": ["A0"], "reason": "Synthetic candidate groups condition with example"}}
    audit = {"groups": {"G0": {"passed": False, "reason": "Synthetic body has example but omits integer-only condition"}}}
    m.merge_assessments(states, groups, audit, "first-body")
    assert all(not row["resolved"] for row in states.values())
    assert all(row["teaching_detected"] for row in states.values())
    rejected_exclusion = {"G0": {**groups["G0"], "kind": "non_teaching", "article_items": []}}
    m.merge_assessments(states, rejected_exclusion, {"groups": {"G0": {"passed": True, "reason": "Synthetic conflicting exclusion"}}}, "second-body")
    assert all(row["classification_conflict"] and not row["resolved"] for row in states.values())
    m.merge_assessments(states, groups, {"groups": {"G0": {"passed": True, "reason": "Synthetic later actual explanation contains both"}}}, "third-body")
    assert all(row["resolved"] and row["kind"] == "covered" for row in states.values())
    assert all(len(row["assessments"]) == 3 for row in states.values())


def test_search_uses_both_models_and_can_find_real_teaching_in_a_later_block():
    inventory, working, private = manuscript()
    plan = m.make_plan(inventory, private, working, CharacterTokenizer())
    items = m.article_items(private, inventory)
    ids = plan["batches"][0]["clauses"]
    blocks = [[key] for key in list(items)[:2]]
    calls = []

    class Job:
        tokenizer = CharacterTokenizer()

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            calls.append((name, prompt, copy.deepcopy(context), copy.deepcopy(kwargs)))
            if name.endswith("-review"):
                kind = "missing" if len(calls) == 1 else "covered"
                value = {"groups": [{"kind": kind, "clauses": ids, "article_items": list(context["article"]) if kind == "covered" else [], "reason": "Synthetic block-specific decision"}]}
            else:
                assert context["groups"]
                value = {"groups": {gid: {"passed": True, "reason": "Synthetic independent complete check"} for gid in context["groups"]}}
            validate(value)
            return value

    result = m.review_batch(Job(), inventory, plan, items, {"clauses": ids, "article_blocks": blocks}, 0)
    assert len(calls) == 4 and all(row["resolved"] and row["kind"] == "covered" for row in result.values())
    assert [prompt for _, prompt, _, _ in calls] == [m.REVIEW, m.VERIFY, m.REVIEW, m.VERIFY]
    assert all(context["sources"]["O00000"]["text"] == inventory["sources"]["O00000"]["text"] for _, _, context, _ in calls)


def test_complete_all_non_teaching_result_conflicts_with_confirmed_teaching_and_keeps_prior_issues():
    inventory, working, private = manuscript()
    plan = m.make_plan(inventory, private, working, CharacterTokenizer())
    states = new_states(plan["units"])
    for row in states.values():
        row.update(resolved=True, kind="non_teaching")
    issues = [{"kind": "section_semantic_review", "review": {"passed": False, "reason": "Synthetic prior unresolved judgment"}}]
    parent = {"summary": {"job": "d" * 32}, "issues": issues}
    result = m.compile_result(parent, plan, SimpleNamespace(key="e" * 32), states)
    assert result["classification_conflicts"] == ["O00000"]
    assert not result["automatic_original_coverage_complete"]
    assert result["inherited_manuscript_issues"] == issues
    assert not result["reader_publication_allowed"] and not result["semantic_accuracy_verified"]
    assert "coverage" not in m.public_result(result) and "inherited_manuscript_issues" not in m.public_result(result)


def test_supervisor_stop_is_preserved_without_input_or_model_load(tmp_path, monkeypatch):
    stop = tmp_path / "supervisor-stop.flag"
    stop.write_text("Synthetic preserved stop", "utf-8")
    monkeypatch.setattr(m.writing, "TextbookStore", lambda root: SimpleNamespace(root=tmp_path, checkpoint=lambda: None))
    monkeypatch.setattr(m, "inputs", lambda *args, **kwargs: pytest.fail("Stopped dispatch must not prepare or allocate a model role"))
    with pytest.raises(InterruptedError, match="supervisor_stop_preserved"):
        m.prepared(tmp_path, "a" * 32, tmp_path / "tokenizer.json", "continuation", "section-0007")
    assert stop.read_text("utf-8") == "Synthetic preserved stop"


def test_after_writer_waits_for_exact_live_identity_then_rejects_actual_failure_without_restart(tmp_path, monkeypatch):
    key = "a" * 32
    folder = tmp_path / "wiki/textbook-whole-authoring-jobs" / key
    folder.mkdir(parents=True)
    writer, guard = {"pid": 100, "created": 1.0}, {"pid": 200, "created": 2.0}
    row = {**writer, "job": key, "state": "generating", "stage": "section-0015-write", "completed_sections": 15, "total_sections": 16}
    atomic_json(folder / "progress.json", row)
    atomic_json(tmp_path / "wiki-revision-thermal-guard.json", {**guard, "state": "running"})
    monkeypatch.setattr(m.writing, "TextbookStore", lambda root: SimpleNamespace(root=tmp_path, checkpoint=lambda: None))
    monkeypatch.setattr(m, "prepared", lambda *args: pytest.fail("A failed predecessor cannot create a new coverage budget"))
    waited = []

    def sleep(seconds):
        waited.append(seconds)
        atomic_json(folder / "progress.json", {**row, "state": "stopped"})

    with pytest.raises(ValueError, match="original_task_not_failed_or_paused"):
        m.after_writer(tmp_path, key, tmp_path / "tokenizer.json", "continuation", "section-0007", writer, guard,
            live=lambda identity: True, sleep=sleep)
    assert waited == [10]


def test_after_writer_rejects_recycled_pid_or_unexpected_parent_without_inference(tmp_path, monkeypatch):
    key = "a" * 32
    folder = tmp_path / "wiki/textbook-whole-authoring-jobs" / key
    folder.mkdir(parents=True)
    atomic_json(folder / "progress.json", {"pid": 100, "created": 999.0, "job": key})
    monkeypatch.setattr(m.writing, "TextbookStore", lambda root: SimpleNamespace(root=tmp_path, checkpoint=lambda: None))
    with pytest.raises(ValueError, match="exact_original_writer_identity"):
        m.after_writer(tmp_path, key, tmp_path / "tokenizer.json", "continuation", "section-0007",
            {"pid": 100, "created": 1.0}, {"pid": 200, "created": 2.0}, live=lambda identity: True, sleep=lambda _: pytest.fail("Wrong identity must not wait"))


def real_stage_job(tmp_path, monkeypatch, *, malformed=False):
    inventory, working, private = manuscript()
    plan = m.make_plan(inventory, private, working, CharacterTokenizer())
    job = m.CoverageJob.__new__(m.CoverageJob)
    job.tokenizer, job.models = CharacterTokenizer(), {"qwen3.5:9b": "a" * 64, "course-defiant:q4": "b" * 64}
    job.plan, job.completed_batches, job.completed_clauses = plan, 0, 0
    store = SimpleNamespace(root=tmp_path, checkpoint=lambda: None)
    m.DraftJob.__init__(job, store, {"sources": inventory["sources"]}, "course-defiant:q4", "b" * 64, 10,
        pipeline="synthetic-whole-coverage", job_directory="whole-coverage-test-jobs", max_attempts=2)
    monkeypatch.setattr("wechat_decrypt_tool.course_pdf_ocr.local_model_digest", lambda model: job.models[model])
    calls = []

    @contextmanager
    def slot(name):
        job.progress("generating", name)
        yield

    def generate(messages, *, schema):
        context = json.loads(messages[1]["content"])
        calls.append((job.model, copy.deepcopy(context)))
        if malformed:
            value = {}
        elif job.model == "course-defiant:q4":
            value = {"groups": [{"kind": "covered", "clauses": list(context["clauses"]),
                "article_items": list(context["article"]), "reason": "Synthetic supported full explanation"}]}
        else:
            value = {"groups": {key: {"passed": True, "reason": "Synthetic independent full check"} for key in context["groups"]}}
        raw = json.dumps(value)
        metrics = {"model_digest": job.model_digest, "done_reason": "stop", "size_vram": 1,
            "requested_context": 65536, "measured_context": 65536, "expected_content_tokens": 20,
            "prompt_eval_count": 20, "reserved_output": 4096}
        atomic_json(job.root / f"response-{len(calls)}.json", {"stage": job.current, "request_sha256": m.digest(messages),
            "schema_sha256": m.digest(schema), "response": {"message": {"content": raw}}, "metrics": metrics})
        return raw, metrics

    job.slot, job.generate_response = slot, generate
    return job, inventory, plan, m.article_items(private, inventory), calls


def test_actual_stage_binds_both_model_identities_requests_schemas_and_gpu_receipts_and_resumes_without_calls(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = real_stage_job(tmp_path, monkeypatch)
    first = m.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
    before = {path: path.read_bytes() for path in (job.root / "attempts").glob("*.json")}
    assert [model for model, _ in calls] == ["course-defiant:q4", "qwen3.5:9b"]
    assert all(row["resolved"] for row in first.values())
    assert m.review_batch(job, inventory, plan, items, plan["batches"][0], 0) == first
    assert len(calls) == 2 and all(path.read_bytes() == raw for path, raw in before.items())


def test_failed_structure_budget_is_not_refreshed_by_a_restart(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = real_stage_job(tmp_path, monkeypatch, malformed=True)
    for _ in range(2):
        with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
            m.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
        assert len(calls) == 2


def test_changed_actual_decoding_schema_cannot_authenticate_a_cached_coverage_judgment(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = real_stage_job(tmp_path, monkeypatch)
    m.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
    path = job.root / "response-1.json"
    value = json.loads(path.read_bytes())
    value["schema_sha256"] = "f" * 64
    atomic_json(path, value)
    with pytest.raises(ValueError, match="actual_coverage_decoding_schema"):
        m.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
    assert len(calls) == 2


def test_many_distinct_groups_get_finite_complete_verification_subbatches(monkeypatch):
    inventory, working, private = manuscript("one, two, three, four, five, six, seven, eight, nine, ten, eleven, twelve, thirteen, fourteen, fifteen, sixteen, seventeen.")
    plan = m.make_plan(inventory, private, working, CharacterTokenizer())
    items = m.article_items(private, inventory)
    verified = []

    class Job:
        tokenizer = CharacterTokenizer()

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            if name.endswith("-review"):
                value = {"groups": [{"kind": "covered", "clauses": [cid], "article_items": list(context["article"]),
                    "reason": "Synthetic distinct teaching decision"} for cid in context["clauses"]]}
            else:
                assert len(context["groups"]) <= m.MAX_VERIFY_GROUPS
                verified.extend(context["groups"])
                value = {"groups": {gid: {"passed": True, "reason": "Synthetic complete independent check"} for gid in context["groups"]}}
            validate(value)
            return value

    result = m.review_batch(Job(), inventory, plan, items, plan["batches"][0], 0)
    assert len(verified) == 17 and len(set(verified)) == 17
    assert all(row["resolved"] for row in result.values())
    assert len({row["assessments"][0]["verification_stage"] for row in result.values()}) == 2


def test_after_writer_starts_one_coverage_run_only_after_both_exact_predecessors_finish_cleanly(tmp_path, monkeypatch):
    key = "a" * 32
    folder = tmp_path / "wiki/textbook-whole-authoring-jobs" / key
    folder.mkdir(parents=True)
    writer, original_guard = {"pid": 100, "created": 1.0}, {"pid": 200, "created": 2.0}
    row = {**writer, "job": key, "state": "generating", "stage": "section-0015-write", "completed_sections": 15, "total_sections": 16}
    atomic_json(folder / "progress.json", row)
    atomic_json(tmp_path / "wiki-revision-thermal-guard.json", {**original_guard, "state": "running"})
    live_states, calls = {100: True, 200: True}, []
    coverage_folder = tmp_path / "coverage-result"
    coverage_folder.mkdir()
    job = SimpleNamespace(key="b" * 32, root=coverage_folder)

    def sleep(seconds):
        calls.append("wait")
        if live_states[100]:
            live_states[100] = False
            atomic_json(folder / "progress.json", {**row, "state": "conceptual_article_requires_actual_source_and_editorial_review",
                "stage": "private_article_assembled", "completed_sections": 16})
        else:
            live_states[200] = False
            atomic_json(tmp_path / "wiki-revision-thermal-guard.json", {**original_guard, "state": "worker_exited", "returncode": 0})

    def prepared(*args):
        assert not any(live_states.values())
        calls.append("prepare")
        return {"issues": []}, {"sources": 2, "clauses": 5, "batches": [1]}, job, tmp_path / "proof.json", tmp_path / "contract.json"

    def guard(*args):
        calls.append("guard")
        atomic_json(coverage_folder / "coverage-result.json", {"state": "whole_manuscript_requires_coverage_or_classification_repair",
            "unresolved_clauses": 1, "automatic_original_coverage_complete": False, "coverage": {"synthetic_private": True}})
        atomic_json(tmp_path / "wiki-revision-thermal-guard.json", {"state": "worker_exited", "returncode": 0})

    monkeypatch.setattr(m.writing, "TextbookStore", lambda root: SimpleNamespace(root=tmp_path, checkpoint=lambda: None))
    monkeypatch.setattr(m, "prepared", prepared)
    monkeypatch.setattr(m, "backup", lambda *args: None)
    monkeypatch.setattr(m, "guard", guard)
    result = m.after_writer(tmp_path, key, tmp_path / "tokenizer.json", "continuation", "section-0007", writer, original_guard,
        live=lambda identity: live_states[identity["pid"]], sleep=sleep)
    assert calls == ["wait", "wait", "prepare", "guard"]
    assert result["unresolved_clauses"] == 1 and "coverage" not in result
