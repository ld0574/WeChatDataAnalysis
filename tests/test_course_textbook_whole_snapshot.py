import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_whole_snapshot as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_authoring import section_value
from test_course_textbook_whole_authoring import CharacterTokenizer


def cache_fixture(tmp_path, monkeypatch, *, previous_failure=False):
    name, prompt = "section-0000-review", m.writing.author.REVIEW_PROMPT
    data, schema, context = {"synthetic": True}, {"type": "object"}, {"synthetic_context": "Synthetic complete original"}
    packet = {"sources": {"O00000": {"kind": "speech", "text": "Synthetic complete original"}}}
    job = SimpleNamespace(root=tmp_path, key="a" * 32, models={"qwen3.5:9b": "b" * 64, "course-defiant:q4": "c" * 64},
        max_attempts=2, packet=packet)
    binding = "d" * 64
    request = {"prompt": "Synthetic exact transformed prompt", "schema": schema, "binding": binding}
    monkeypatch.setattr(m.draft, "capture_request", lambda *args: copy.deepcopy(request))
    (tmp_path / "attempts").mkdir()
    previous = ""
    for number in range(1, 3 if previous_failure else 2):
        accepted = number == (2 if previous_failure else 1)
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
            {**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        metrics = {"model_digest": job.models["course-defiant:q4"], "done_reason": "stop", "size_vram": 1,
            "prompt_eval_count": 20, "expected_content_tokens": 20, "requested_context": 1024,
            "measured_context": 1024, "reserved_output": 16}
        record = {"stage": name, "attempt": number, "input_sha256": binding, "model_digest": job.models["course-defiant:q4"],
            "request_sha256": m.sha(messages), "raw_response": json.dumps(data), "model_metrics": metrics,
            "accepted_structure": accepted, "validation_error": "Synthetic retained failure" if not accepted else None}
        atomic_json(tmp_path / "attempts" / f"{name}-{number:02}.json", record)
        atomic_json(tmp_path / f"response-{number}.json", {"stage": name, "request_sha256": record["request_sha256"],
            "response": {"message": {"content": record["raw_response"]}}, "metrics": metrics})
        previous = record["validation_error"]
    atomic_json(tmp_path / (name + ".json"), {"input_sha256": binding, "data": data, "data_sha256": m.sha(data)})
    atomic_json(tmp_path / (name + ".proof.json"), {"stage": name, "model": "course-defiant:q4",
        "model_digest": job.models["course-defiant:q4"], "input_sha256": binding, "data_sha256": m.sha(data)})
    reader = m.ReadOnlyStages(job, {}, {}, m.visible.Recovery([]))
    return reader, name, prompt, schema, context, data


@pytest.mark.parametrize("previous_failure", [False, True])
def test_cache_authenticates_real_requests_and_receipts_without_any_mutation(tmp_path, monkeypatch, previous_failure):
    reader, name, prompt, schema, context, data = cache_fixture(tmp_path, monkeypatch, previous_failure=previous_failure)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    assert reader.stage(name, prompt, schema, context, lambda value: None) == data
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert reader.stages == [name] and not reader.recoveries


@pytest.mark.parametrize("changed", ["request", "response_model", "model", "proof", "context", "budget", "attempt_order"])
def test_modified_request_model_proof_or_budget_cannot_authenticate(tmp_path, monkeypatch, changed):
    reader, name, prompt, schema, context, _ = cache_fixture(tmp_path, monkeypatch)
    path = tmp_path / "attempts" / (name + "-01.json")
    value = json.loads(path.read_bytes())
    if changed == "proof":
        path = tmp_path / (name + ".proof.json")
        value = json.loads(path.read_bytes())
        value["model"] = "qwen3.5:9b"
    elif changed == "context":
        context["synthetic_context"] = "Changed actual original"
    elif changed == "budget":
        reader.job.max_attempts = 0
    elif changed == "response_model":
        value["model_metrics"]["model_digest"] = "f" * 64
        response = json.loads((tmp_path / "response-1.json").read_bytes())
        response["metrics"] = value["model_metrics"]
        atomic_json(tmp_path / "response-1.json", response)
    else:
        field = {"request": "request_sha256", "model": "model_digest", "attempt_order": "attempt"}[changed]
        value[field] = 2 if changed == "attempt_order" else "f" * 64
    atomic_json(path, value)
    with pytest.raises(ValueError):
        reader.stage(name, prompt, schema, context, lambda value: None)


def test_missing_stage_never_calls_a_model_or_fabricates_cache(tmp_path):
    job = SimpleNamespace(root=tmp_path, packet={})
    reader = m.ReadOnlyStages(job, {}, {}, m.visible.Recovery([]))
    with pytest.raises(ValueError, match="required_existing_stage_no_generation"):
        reader.stage("section-0000-write", m.writing.author.WRITE_PROMPT, {}, {}, lambda value: None)
    assert not list(tmp_path.iterdir())


def test_explicit_recovery_is_reconstructed_and_never_upgraded_to_model_cache(tmp_path):
    name = "section-0000-write"
    job = SimpleNamespace(root=tmp_path)
    artifact = {"data": {"synthetic": True}, "receipt": {"original_write_attempts": {}}}
    path = m.draft.artifact_path(job, name)
    path.parent.mkdir()
    atomic_json(path, artifact)
    registry = SimpleNamespace(candidate_from_history=lambda *args: copy.deepcopy(artifact))
    reader = m.ReadOnlyStages(job, {}, {}, registry)
    assert reader.stage(name, m.writing.author.WRITE_PROMPT, {}, {}, lambda value: None, evidence_packet={}) == artifact["data"]
    assert reader.recoveries == [name] and not (tmp_path / (name + ".json")).exists()
    atomic_json(tmp_path / (name + ".json"), {})
    with pytest.raises(ValueError, match="unchanged_explicit_recovery"):
        reader.stage(name, m.writing.author.WRITE_PROMPT, {}, {}, lambda value: None, evidence_packet={})


def assembled_fixture(tmp_path, *, negative=False):
    inventory = {"lesson": "a" * 24, "sources": {"E000000": {"kind": "speech", "text": "Synthetic full original"}},
        "attachment_scope": {"unresolved": True, "selected_notebooks": []}}
    working = {"job": "b" * 32, "points": {"P0": {"label": "Synthetic point", "teaching": "Synthetic explanation",
        "category": "concept", "citations": ["E000000"]}}, "pending_source_cards": {}, "confirmed_point_count": 1,
        "parent_ledger_sha256": "c" * 64, "inherited_issues": [], "quarantined_point_keys": []}
    plan = {"title": "Synthetic title", "chapters": ["N"], "nodes": {"N": {"heading": "Synthetic chapter",
        "purpose": "Synthetic purpose", "children": ["P0"]}}}
    units = [{"id": "section-0000", "chapter_id": "chapter-000", "chapter": {"heading": "Synthetic chapter", "purpose": "Synthetic purpose"},
        "point_ids": ["P0"]}]
    sections = {"section-0000": section_value(["P0"], working)}
    private = m.writing.author.assemble(inventory, working, plan, units, sections)
    private.update(private_non_teaching_units={}, parent_ledger_sha256=working["parent_ledger_sha256"],
        inherited_ledger_issues=[], quarantined_point_keys=[], writing_budget_refreshed=False)
    issues = ([{"kind": "section_semantic_review", "section": "section-0000", "review": {"passed": False}}] if negative else [])
    expected_issues = m.writing.final_issues(inventory, private, copy.deepcopy(issues))
    book = m.writing.reading_book(private)

    def verdict(schema):
        props = schema["properties"]
        if "passed" in props:
            return {"passed": True, "reason": "Synthetic independent reading check"}
        return {k: verdict(v) for k, v in props.items()}

    job = SimpleNamespace(root=tmp_path, key="d" * 32, tokenizer=CharacterTokenizer(),
        stage=lambda name, prompt, schema, context, validate: verdict(schema))
    reader = m.ReadOnlyStages(job, inventory, working, m.visible.Recovery([]))
    m.writing.author.save(tmp_path, "private-article", private)
    m.writing.author.save(tmp_path, "quality-issues", expected_issues)
    atomic_json(tmp_path / "result.json", {"state": "conceptual_article_requires_actual_source_and_editorial_review", "job": job.key,
        "inventory": m.sha(inventory), "ledger_job": working["job"], "chapters": 1, "sections": 1, "knowledge_points": 1,
        "pending_original_tasks": 0, "remaining_reviews": len(expected_issues), "article_sha256": m.sha(book),
        "reader_article_published": False, "source_deletion_allowed": False, "goal_complete": False})
    (tmp_path / "private-reading.html").write_bytes(m.writing.standalone_html(book).encode())
    return reader, units, plan, sections, {}, issues, book, expected_issues


@pytest.mark.parametrize("negative", [False, True])
def test_whole_article_is_reassembled_with_all_negative_and_required_reviews(tmp_path, negative):
    reader, units, plan, sections, excluded, issues, book, expected = assembled_fixture(tmp_path, negative=negative)
    private, actual, pending = m.assemble(reader, units, plan, sections, excluded, issues)
    assert actual == book and pending == expected
    assert private["writing_budget_refreshed"] is False
    assert any(row["kind"] == "complete_original_source_to_actual_article_review_required" for row in pending)


@pytest.mark.parametrize("changed", ["body", "issues", "result", "html"])
def test_changed_article_unresolved_issues_summary_or_html_are_rejected(tmp_path, changed):
    reader, units, plan, sections, excluded, issues, _, _ = assembled_fixture(tmp_path)
    if changed == "html":
        (tmp_path / "private-reading.html").write_text("Changed", "utf-8")
    elif changed == "result":
        v = json.loads((tmp_path / "result.json").read_bytes())
        v["reader_article_published"] = True
        atomic_json(tmp_path / "result.json", v)
    elif changed == "issues":
        m.writing.author.save(tmp_path, "quality-issues", [])
    else:
        value = reader.saved("private-article")
        value["chapters"][0]["sections"][0]["paragraphs"][0]["text"] = "Changed"
        m.writing.author.save(tmp_path, "private-article", value)
    with pytest.raises(ValueError):
        m.assemble(reader, units, plan, sections, excluded, issues)


def test_complete_snapshot_rejects_an_unfinished_live_parent_before_any_model_or_source_load(tmp_path, monkeypatch):
    key = "a" * 32
    folder = tmp_path / "wiki/textbook-whole-authoring-jobs" / key
    folder.mkdir(parents=True)
    atomic_json(folder / "progress.json", {"stage": "section-0006-review", "state": "generating"})
    monkeypatch.setattr(m.writing, "load_inputs", lambda *args: pytest.fail("unfinished article must not start a coverage snapshot"))
    with pytest.raises(ValueError, match="assembled_exited_writer"):
        m.reconstruct(tmp_path, key, tmp_path / "tokenizer.json", complete=True)
