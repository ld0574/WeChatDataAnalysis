import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_whole_disposition_mapping as m
from wechat_decrypt_tool.course_knowledge import atomic_json

from test_course_textbook_visible_context import fixture


def failed_history(tmp_path, monkeypatch):
    inventory, working, packet, context, original = fixture()
    original["coverage"]["U0"]["teaching"] = False
    scope = m.continuation.scope_for(["U0"], working, inventory, context, packet)
    schema = m.continuation.grammar.bind(m.writing.section_schema(["U0"], working), scope["expanded"])
    job = SimpleNamespace(root=tmp_path, key="a" * 32, models={"qwen3.5:9b": "b" * 64})
    request = {"prompt": "Synthetic exact prompt", "schema": schema, "binding": "c" * 64}
    monkeypatch.setattr(m.draft, "capture_request", lambda *args: copy.deepcopy(request))
    name, previous = "section-0014-write", ""
    descriptor = {"actual_context_sha256": m.digest(context), "actual_schema_sha256": m.digest(schema),
        "actual_visible_packet_sha256": m.digest(scope["expanded"])}
    atomic_json(tmp_path / (name + "-continuation-request.json"), descriptor)
    (tmp_path / "attempts").mkdir()
    for number in [1, 2]:
        candidate = copy.deepcopy(original)
        candidate["paragraphs"][0]["text"] += " " + str(number)
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
            {**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        metrics = {"model_digest": job.models["qwen3.5:9b"], "done_reason": "stop", "size_vram": 1,
            "requested_context": 1024, "measured_context": 1024, "expected_content_tokens": 20,
            "prompt_eval_count": 20, "reserved_output": 16}
        row = {"stage": name, "attempt": number, "input_sha256": request["binding"],
            "model_digest": job.models["qwen3.5:9b"], "request_sha256": m.digest(messages),
            "raw_response": json.dumps(candidate), "model_metrics": metrics,
            "error_type": "ValueError", "validation_error": m.ERROR}
        atomic_json(tmp_path / "attempts" / f"{name}-{number:02d}.json", row)
        atomic_json(tmp_path / f"response-{number}.json", {"stage": name, "request_sha256": row["request_sha256"],
            "schema_sha256": m.digest(schema), "response": {"message": {"content": row["raw_response"]}}, "metrics": metrics})
        previous = m.ERROR
    proof = {"verified_units": 14, "files": {}}
    return job, name, schema, context, scope["expanded"], working, inventory, proof, tmp_path / "prefix.json"


def test_projection_retains_actual_prose_dispositions_and_history_without_fabricating_a_cache(tmp_path, monkeypatch):
    args = failed_history(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    value = m.artifact(*args)
    assert value["receipt"]["selected_attempt"] == 1
    assert value["data"]["paragraphs"][0]["text"].endswith(" 1")
    assert value["data"]["coverage"]["U0"]["teaching"] is False
    assert value["data"]["coverage"]["U0"]["paragraphs"] == []
    assert value["receipt"]["mapping_proposals"][0]["original_paragraphs"] == [0]
    assert value["receipt"]["mapping_exclusions_independently_verified"] is False
    assert value["receipt"]["model_calls"] == 0
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert not (tmp_path / "section-0014-write.json").exists()


@pytest.mark.parametrize("change", ["request", "input", "model", "error", "accepted", "schema", "response", "missing"])
def test_original_failed_budget_model_response_and_schema_must_authenticate(tmp_path, monkeypatch, change):
    args = failed_history(tmp_path, monkeypatch)
    path = tmp_path / "attempts/section-0014-write-01.json"
    row = json.loads(path.read_bytes())
    if change == "missing":
        path.rename(tmp_path / "retained-original-attempt.json")
    elif change in {"schema", "response"}:
        target = tmp_path / "response-1.json"
        value = json.loads(target.read_bytes())
        if change == "schema":
            value["schema_sha256"] = "f" * 64
        else:
            value["response"]["message"]["content"] = "Changed synthetic model output"
        atomic_json(target, value)
    else:
        key = {"request": "request_sha256", "input": "input_sha256", "model": "model_digest",
            "error": "validation_error", "accepted": "accepted_structure"}[change]
        row[key] = True if change == "accepted" else "Changed synthetic identity"
        atomic_json(path, row)
    with pytest.raises(ValueError):
        m.artifact(*args)


@pytest.mark.parametrize("change", ["already_empty", "teaching", "confirmed", "unknown_source"])
def test_metadata_proposal_cannot_reclassify_teaching_or_hide_another_defect(change):
    inventory, working, packet, context, value = fixture()
    value["coverage"]["U0"]["teaching"] = False
    scope = m.continuation.scope_for(["U0"], working, inventory, context, packet)
    if change == "already_empty":
        value["coverage"]["U0"]["paragraphs"] = []
    elif change == "teaching":
        value["coverage"]["U0"]["teaching"] = True
    elif change == "confirmed":
        working = {**working, "pending_source_cards": {}}
    else:
        value["paragraphs"][0]["citations"] = ["O00004"]
    before = copy.deepcopy(value)
    with pytest.raises(ValueError):
        m.propose(value, ["U0"], working, inventory, scope)
    assert value == before


def test_hooks_restore_original_runtime_even_on_failure():
    before = m.continuation.job_type, m.visible.Recovery
    with pytest.raises(RuntimeError), m.hooks():
        assert m.continuation.job_type is m.job_type
        assert m.visible.Recovery is m.Recovery
        raise RuntimeError("Synthetic failure")
    assert (m.continuation.job_type, m.visible.Recovery) == before


def test_only_failed_write_uses_projection_and_original_negative_followup_is_preserved(monkeypatch):
    calls = []

    def factory(*args, read_only):
        class Base:
            def stage(self, name, prompt, schema, context, validate, **kwargs):
                calls.append((read_only, name, context))
                return {"passed": False}

            def progress(self, state, stage, **extra):
                return self.finished_sections
        return Base

    monkeypatch.setattr(m, "ORIGINAL_JOB_TYPE", factory)
    cls = m.job_type(None, None, None, read_only=False)
    job = cls.__new__(cls)
    job.mapping_targets = {"section-0014": {"receipt": {"citation_proposals": [],
        "mapping_proposals": [{"point": "U0", "original_paragraphs": [0], "proposed_paragraphs": []}], "prefix_verified_units": 14}}}
    assert job.stage("section-0014-write", "", {}, {}, lambda _: None) == {"passed": False}
    assert calls[-1][0] is True
    assert job.stage("section-0014-review", "", {}, {}, lambda _: None) == {"passed": False}
    assert calls[-1][0] is False
    assert calls[-1][2]["mapping_proposals_are_factual_evidence"] is False
    job.finished_sections = 2
    assert job.progress("waiting_gpu", "section-0014-review") == 14
    assert job.finished_sections == 2
    job.mapping_targets = {}
    assert job.progress("waiting_gpu", "another") == 2


@pytest.mark.parametrize("fails", [False, True])
def test_successor_observer_preserves_its_prior_failure_and_restores_hooks(tmp_path, monkeypatch, fails):
    from wechat_decrypt_tool import course_textbook_whole_coverage as coverage

    key = "a" * 32
    folder = tmp_path / "wiki"
    folder.mkdir()
    old_path = folder / (key + "-whole-coverage-after-writer.json")
    atomic_json(old_path, {"pid": 100, "created": 1.0, "state": "stopped"})
    original_bytes = old_path.read_bytes()
    monkeypatch.setattr(m.writing, "identity_live", lambda value: False)
    before = coverage.guard, m.continuation.job_type, m.visible.Recovery
    calls = []

    def observer(*args):
        assert coverage.guard is m.coverage_guard
        assert m.continuation.job_type is m.job_type
        assert m.visible.Recovery is m.Recovery
        calls.append(args)
        if fails:
            raise ValueError("Synthetic observer failure")
        return {"state": "finite_result_requires_review"}

    monkeypatch.setattr(coverage, "after_writer", observer)
    if fails:
        with pytest.raises(ValueError, match="Synthetic observer failure"):
            m.after_writer(tmp_path, key, tmp_path / "tokenizer.json", "section-0007", {"pid": 1}, {"pid": 2})
    else:
        assert m.after_writer(tmp_path, key, tmp_path / "tokenizer.json", "section-0007", {"pid": 1}, {"pid": 2}) == {
            "state": "finite_result_requires_review"}
    assert len(calls) == 1
    assert (coverage.guard, m.continuation.job_type, m.visible.Recovery) == before
    assert old_path.read_bytes() == original_bytes
    assert [path.read_bytes() for path in (folder / "disposition-mapping-observer-history").glob("*.json")] == [original_bytes]


def test_new_coverage_cannot_start_after_a_failed_writer_guard(tmp_path, monkeypatch):
    (tmp_path / "wiki").mkdir()
    atomic_json(tmp_path / "wiki-revision-thermal-guard.json", {"pid": 100, "created": 1.0,
        "state": "worker_exited", "returncode": 1})
    monkeypatch.setattr(m.writing, "identity_live", lambda value: False)
    monkeypatch.setattr(m.writing, "TextbookStore", lambda root: SimpleNamespace(root=root, checkpoint=lambda: None))
    with pytest.raises(ValueError, match="coverage_requires_clean_completed_writer_guard"):
        m.coverage_guard(tmp_path, "a" * 32, tmp_path / "tokenizer.json", "continuation", "section-0007")
