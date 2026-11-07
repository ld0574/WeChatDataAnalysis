import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_private_practice as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_practice import book, questions


def raw_questions():
    value = questions()
    for row in value["questions"]:
        for support in row["support"]:
            support.pop("quote")
    return value


class FakeJob:
    def __init__(self, reviews):
        self.calls, self.reviews = [], iter(reviews)

    def stage(self, name, prompt, schema, context, validate):
        self.calls.append(name)
        value = next(self.reviews) if prompt == m.build.REVIEW else raw_questions()
        validate(value)
        return copy.deepcopy(value)


def review(passed):
    return {"Q1": {"passed": passed, "reason": "Synthetic independent judgment"},
        "Q2": {"passed": True, "reason": "Synthetic independent judgment"}}


def test_private_pipeline_reuses_original_roles_and_program_binds_sources():
    job = FakeJob([review(True)])
    raw, value, writer, reviewer = m.stages(job, book())
    assert job.calls == ["choices-write", "choices-review"]
    assert (writer, reviewer) == tuple(job.calls)
    assert raw == raw_questions()
    assert value["questions"][0]["support"][0]["quote"] == book()["chapters"][0]["sections"][0]["paragraphs"][0]


def test_negative_review_uses_one_original_repair_and_keeps_final_negative_result():
    job = FakeJob([review(False), review(False)])
    with pytest.raises(ValueError, match="independent_review_not_passed"):
        m.stages(job, book())
    assert job.calls == ["choices-write", "choices-review", "choices-repair", "choices-repair-review"]


def test_repair_that_passes_does_not_call_an_extra_semantic_round():
    job = FakeJob([review(False), review(True)])
    _, _, writer, reviewer = m.stages(job, book())
    assert (writer, reviewer) == ("choices-repair", "choices-repair-review")
    assert len(job.calls) == 4


def test_artifacts_stay_private_and_are_version_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(m.build.practice, "store", lambda *a, **k: pytest.fail("public practice publication"))
    raw = raw_questions()
    value = m.build.compile_candidate(raw, book())
    job = SimpleNamespace(root=tmp_path / "wiki/textbook-choice-quiz-jobs" / ("a" * 32),
        key="a" * 32, whole_key=m.digest(book()), models={"qwen3.5:9b": "b" * 64, "course-defiant:q4": "c" * 64})
    job.root.mkdir(parents=True)
    for name in ["choices-write", "choices-review"]:
        atomic_json(job.root / (name + ".json"), {"synthetic": True})
    result = m.artifacts("d" * 32, job, raw, value, "choices-write", "choices-review")
    assert result["questions"] == 2 and not result["reader_publication_allowed"]
    assert not (tmp_path / "wiki/textbook-reading").exists()
    private = m.whole.read(job.root / "private-practice.json")
    assert private["article_sha256"] == m.digest(book())
    assert private["questions"] == value["questions"]
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    assert m.artifacts("d" * 32, job, raw, value, "choices-write", "choices-review", read_only=True) == result
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    changed = copy.deepcopy(value)
    changed["questions"][0]["explanation"] = "Changed synthetic explanation"
    with pytest.raises(ValueError, match="immutable_private_artifact"):
        m.artifacts("d" * 32, job, raw, changed, "choices-write", "choices-review")


def test_readonly_cannot_create_an_artifact(tmp_path):
    with pytest.raises(ValueError, match="required_existing_private_artifact"):
        m.retained(tmp_path / "absent/proof.json", {}, read_only=True)
    assert not list(tmp_path.iterdir())


def test_private_loader_uses_complete_authentication_without_reader_publication(tmp_path, monkeypatch):
    parent = "a" * 32
    value = {"book": book(), "summary": {"job": parent, "verified_units": 2, "total_units": 2},
        "proof": {"article_sha256": m.digest(book())}}
    calls = []

    def reconstruct(*args, **kwargs):
        calls.append((args, kwargs))
        return value

    monkeypatch.setattr(m.continuation.snapshot, "reconstruct", reconstruct)
    monkeypatch.setattr(m.build.Reader, "article", lambda *a: pytest.fail("published reader required"))
    assert m.manuscript(tmp_path, parent, Path("tokenizer.json"), "original", "section-0007") == value
    assert calls[0][1] == {"complete": True}
    value["summary"]["verified_units"] = 1
    with pytest.raises(ValueError, match="complete_actual_manuscript"):
        m.manuscript(tmp_path, parent, Path("tokenizer.json"), "original", "section-0007")


def configured_job(tmp_path, monkeypatch):
    tokenizer = tmp_path / "tokenizer.json"
    tokenizer.write_text("{}", encoding="utf-8")
    fake_tokenizer = SimpleNamespace(encode=lambda text: SimpleNamespace(ids=[0] * max(1, len(text) // 3)))
    monkeypatch.setattr(m.build, "Tokenizer", SimpleNamespace(from_file=lambda *a: fake_tokenizer))
    monkeypatch.setattr(m.build, "local_model_digest", lambda name: ("b" if name == "qwen3.5:9b" else "c") * 64)
    store = SimpleNamespace(root=tmp_path, checkpoint=lambda: None)
    original = m.build.Job(store, book(), m.digest(book()), tokenizer)
    job = m.ReadOnlyJob(store, book(), m.digest(book()), tokenizer, original.root)
    return job, tokenizer


def accepted_cache(job, name, prompt, schema, context, data):
    model = "course-defiant:q4" if prompt == m.build.REVIEW else "qwen3.5:9b"
    previous = job.model, job.model_digest
    job.model, job.model_digest = model, job.models[model]
    actual_schema, binding = job.stage_input(prompt, schema, context)
    job.model, job.model_digest = previous
    messages = [{"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps({**context, "previous_validation_failure": ""}, ensure_ascii=False)}]
    metrics = {"model_digest": job.models[model], "done_reason": "stop", "size_vram": 1,
        "prompt_eval_count": 20, "expected_content_tokens": 20, "requested_context": 1024,
        "measured_context": 1024, "reserved_output": 16}
    row = {"stage": name, "attempt": 1, "input_sha256": binding, "model_digest": job.models[model],
        "request_sha256": m.digest(messages), "raw_response": json.dumps(data), "model_metrics": metrics,
        "accepted_structure": True}
    (job.root / "attempts").mkdir(exist_ok=True)
    atomic_json(job.root / "attempts" / (name + "-01.json"), row)
    atomic_json(job.root / ("response-" + name + ".json"), {"stage": name, "request_sha256": row["request_sha256"],
        "schema_sha256": m.digest(actual_schema), "response": {"message": {"content": row["raw_response"]}}, "metrics": metrics})
    atomic_json(job.root / (name + ".json"), {"input_sha256": binding, "data": data, "data_sha256": m.digest(data)})


def cache_fixture(tmp_path, monkeypatch):
    job, _ = configured_job(tmp_path, monkeypatch)
    name, prompt, context = "choices-write", m.build.WRITE, {"sections": m.build.practice.source_sections(book()), "existing_exercises": book()["quiz"]}
    raw = raw_questions()
    accepted_cache(job, name, prompt, m.build.MODEL_SCHEMA, context, raw)
    return job, name, prompt, context, raw


def test_readonly_reconstructs_real_request_gpu_receipt_and_schema_without_writes(tmp_path, monkeypatch):
    job, name, prompt, context, raw = cache_fixture(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    with m.continuation.exact_role_cache():
        assert job.stage(name, prompt, m.build.MODEL_SCHEMA, context, lambda v: m.build.compile_candidate(v, book())) == raw
    assert job.authenticated_stages == {name}
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}


@pytest.mark.parametrize("field", ["request_sha256", "model_digest", "schema_sha256", "attempt", "missing"])
def test_readonly_rejects_tampered_or_missing_requests_without_inference(tmp_path, monkeypatch, field):
    job, name, prompt, context, _ = cache_fixture(tmp_path, monkeypatch)
    path = job.root / ("response-" + name + ".json" if field == "schema_sha256" else "attempts/" + name + "-01.json")
    if field == "missing":
        (job.root / (name + ".json")).unlink()
    else:
        row = m.whole.read(path)
        row[field] = 2 if field == "attempt" else "f" * 64
        atomic_json(path, row)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    with m.continuation.exact_role_cache(), pytest.raises(ValueError):
        job.stage(name, prompt, m.build.MODEL_SCHEMA, context, lambda v: m.build.compile_candidate(v, book()))
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}


def test_full_private_workflow_and_readonly_reconstruction_never_publish(tmp_path, monkeypatch):
    job, tokenizer = configured_job(tmp_path, monkeypatch)
    monkeypatch.setattr(m.build, "TextbookStore", lambda root: SimpleNamespace(root=Path(root).resolve(), checkpoint=lambda: None))
    parent = "d" * 32
    private = {"book": book(), "proof": {"article_sha256": m.digest(book())}, "issues": [{"kind": "synthetic_pending_issue"}],
        "summary": {"job": parent, "verified_units": 2, "total_units": 2}}
    monkeypatch.setattr(m, "manuscript", lambda *a: copy.deepcopy(private))
    monkeypatch.setattr(m.whole, "backup", lambda *a: None)
    monkeypatch.setattr(m.build.practice, "store", lambda *a: pytest.fail("unexpected publication"))
    monkeypatch.setattr(m.build.Reader, "article", lambda *a: pytest.fail("unexpected public input"))
    source = {"sections": m.build.practice.source_sections(book()), "existing_exercises": book()["quiz"]}
    raw = raw_questions()
    accepted_cache(job, "choices-write", m.build.WRITE, m.build.MODEL_SCHEMA, source, raw)
    candidate = m.build.compile_candidate(raw, book())
    accepted_cache(job, "choices-review", m.build.REVIEW, m.build.review_schema(candidate),
        {"sections": source["sections"], "candidate": m.build.review_candidate(candidate)}, review(True))
    result = m.run(tmp_path, parent, tokenizer, "original")
    assert result["state"] == "private_choice_practice_reviewed_not_published"
    assert not (tmp_path / "wiki/textbook-reading").exists()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    verified = m.run(tmp_path, parent, tokenizer, "original", read_only=True)
    assert verified["model_calls"] == 0 and verified["authenticated_stages"] == 2
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    changed = m.whole.read(job.root / "private-practice.json")
    changed["article_sha256"] = "f" * 64
    atomic_json(job.root / "private-practice.json", changed)
    with pytest.raises(ValueError, match="immutable_private_artifact"):
        m.run(tmp_path, parent, tokenizer, "original", read_only=True)


@pytest.mark.parametrize("blocking", ["live_guard", "supervisor_stop"])
def test_guard_preserves_existing_stop_or_healthy_guard(tmp_path, monkeypatch, blocking):
    from wechat_decrypt_tool import course_gpu_guard

    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki/private-choice-practice-dispatch.lock").write_bytes(b"0")
    path = tmp_path / "review-experiment-thermal-guard.json"
    atomic_json(path, {"state": "running", "pid": 123, "created": 456})
    if blocking == "supervisor_stop":
        (tmp_path / "supervisor-stop.flag").write_text("Synthetic retained stop", encoding="utf-8")
    monkeypatch.setattr(m.build, "TextbookStore", lambda root: SimpleNamespace(checkpoint=lambda: None))
    monkeypatch.setattr(m.continuation.writing, "identity_live", lambda value: True)
    monkeypatch.setattr(course_gpu_guard, "run", lambda *a, **k: pytest.fail("guard started despite a live guard or stop"))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="existing_review_guard_not_live|supervisor_stop_preserved"):
        m.guard(tmp_path, "a" * 32, Path("tokenizer.json"), "original", "section-0007")
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
