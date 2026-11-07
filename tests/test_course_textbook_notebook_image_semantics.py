import copy
import json

import jsonschema
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from wechat_decrypt_tool import course_textbook_notebook_image_semantics as m
from wechat_decrypt_tool.course_knowledge import atomic_json


def context_fixture():
    return {"classroom_sources": {"O00000": {"kind": "speech", "text": "Synthetic full original"}}}


def proposal_fixture():
    return {"observations": [{"description": "Synthetic visible relation", "box": [10, 20, 900, 980]}],
        "interpretation": "Synthetic diagram meaning", "relation": {"kind": "same_lesson_teaching",
            "reason": "Synthetic direct connection", "lesson_source_ids": ["O00000"]},
        "ambiguities": [], "ocr_disagreements": []}


@pytest.mark.parametrize("change", ["none", "unknown_ref", "missing_ref", "false_ref", "empty_box", "reversed_box", "blank", "no_observation"])
def test_proposal_requires_real_references_visible_observations_and_valid_boxes(change):
    value = proposal_fixture()
    if change == "unknown_ref":
        value["relation"]["lesson_source_ids"] = ["O99999"]
    elif change == "missing_ref":
        value["relation"]["lesson_source_ids"] = []
    elif change == "false_ref":
        value["relation"]["kind"] = "unresolved"
    elif change == "empty_box":
        value["observations"][0]["box"] = [0, 0, 0, 1]
    elif change == "reversed_box":
        value["observations"][0]["box"] = [900, 100, 10, 200]
    elif change == "blank":
        value["interpretation"] = " "
    elif change == "no_observation":
        value["observations"] = []
    if change == "none":
        m.validate_proposal(value, context_fixture())
    else:
        with pytest.raises((ValueError, jsonschema.ValidationError)):
            m.validate_proposal(value, context_fixture())


def test_native_pixel_reinspection_keeps_negative_checks_and_same_model_limitation():
    proposal = proposal_fixture()
    audit = {"checks": {key: True for key in m.CHECKS}, "reason": "Synthetic inspection"}
    m.validate_audit(audit)
    supported = m.compile_image(proposal, audit)
    assert supported["state"] == "supported_with_same_model_limitation"
    assert supported["different_model_pixel_review"] is False
    assert supported["article_coverage_verified"] is False and supported["reader_publication_allowed"] is False
    for key in m.CHECKS:
        negative = copy.deepcopy(audit)
        negative["checks"][key] = False
        result = m.compile_image(proposal, negative)
        assert result["state"] == "unresolved" and result["separate_same_model_pixel_review"] == negative
    proposal["relation"] = {"kind": "unresolved", "reason": "Synthetic uncertainty", "lesson_source_ids": []}
    assert m.compile_image(proposal, audit)["state"] == "unresolved"


def cached_job(tmp_path):
    (tmp_path / "knowledge.sqlite3").touch()
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    token_path = tmp_path / "tokenizer.json"
    tokenizer.save(str(token_path))
    inventory = {"sources": context_fixture()["classroom_sources"]}
    binding = {"model_digest": "a" * 64}
    job = m.Job(tmp_path, inventory, {}, {}, token_path, binding)
    image_path = tmp_path / "document-visuals/teaching-images/synthetic/image.png"
    image_path.parent.mkdir(parents=True)
    image = b"Synthetic native image bytes retained exactly"
    image_path.write_bytes(image)
    job.active_image = {"image": image_path.relative_to(tmp_path).as_posix(), "image_sha256": m.whole.sha(image)}
    name, prompt, schema, context = "V00000-describe", m.DESCRIBE, m.schema_for(context_fixture()), context_fixture()
    actual_schema, binding_sha = job.stage_input(prompt, schema, context)
    value = proposal_fixture()
    messages = [{"role": "system", "content": prompt + job.range_notice},
        {"role": "user", "content": json.dumps({**context, "previous_validation_failure": ""}, ensure_ascii=False)}]
    expected = job.text_tokens(messages)
    metrics = {"model_digest": job.model_digest, "done_reason": "stop", "text_content_tokens": expected,
        "prompt_eval_count": expected + 100, "eval_count": 100, "eval_duration": 10000,
        "measured_context": m.OPTIONS["num_ctx"], "size_vram": 1024}
    record = {"stage": name, "attempt": 1, "input_sha256": binding_sha, "model_digest": job.model_digest,
        "request_sha256": m.digest(messages), "raw_response": json.dumps(value), "model_metrics": metrics,
        "accepted_structure": True}
    response = {"message": {"content": record["raw_response"]},
        **{key: metrics[key] for key in ["done_reason", "prompt_eval_count", "eval_count", "eval_duration"]}}
    receipt = {"stage": name, "text_request_sha256": m.digest(messages),
        "wire_request_sha256": m.digest(m.wire_messages(messages, image)), "schema_sha256": m.digest(actual_schema),
        "image_sha256": m.whole.sha(image), "response": response, "metrics": metrics,
        "loaded_model": {"digest": job.model_digest, "context_length": m.OPTIONS["num_ctx"], "size_vram": 1024}}
    (job.root / "attempts").mkdir()
    atomic_json(job.root / "attempts" / (name + "-01.json"), record)
    atomic_json(job.root / "vision-response-synthetic.json", receipt)
    atomic_json(job.root / (name + ".json"), {"input_sha256": binding_sha, "data": value, "data_sha256": m.digest(value)})
    job.read_only = True
    return job, name, prompt, schema, context, value, receipt, image_path


def test_cache_reconstruction_checks_actual_native_image_wire_and_gpu_without_mutation_or_inference(tmp_path, monkeypatch):
    job, name, prompt, schema, context, value, _, _ = cached_job(tmp_path)
    monkeypatch.setattr(job, "generate_response", lambda *args, **kwargs: pytest.fail("Unexpected inference"))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    assert job.stage(name, prompt, schema, context, lambda row: m.validate_proposal(row, context)) == value
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize("change", ["image", "wire", "text_request", "schema", "response", "metrics", "owner", "server_metrics", "cache", "attempt", "model", "context"])
def test_native_request_or_response_tampering_cannot_reconstruct(tmp_path, change):
    job, name, prompt, schema, context, value, receipt, image_path = cached_job(tmp_path)
    if change == "image":
        image_path.write_bytes(b"Different synthetic image")
    elif change == "wire":
        receipt["wire_request_sha256"] = "changed"
    elif change == "text_request":
        receipt["text_request_sha256"] = "changed"
    elif change == "schema":
        receipt["schema_sha256"] = "changed"
    elif change == "response":
        receipt["response"]["message"]["content"] = "{}"
    elif change == "metrics":
        receipt["metrics"]["eval_count"] = 999
    elif change == "owner":
        receipt["loaded_model"]["size_vram"] = 0
    elif change == "server_metrics":
        receipt["response"]["prompt_eval_count"] = 1
    elif change == "cache":
        value["interpretation"] = "Changed synthetic interpretation"
        row = json.loads((job.root / (name + ".json")).read_bytes())
        row.update(data=value, data_sha256=m.digest(value))
        atomic_json(job.root / (name + ".json"), row)
    elif change in {"attempt", "model"}:
        path = job.root / "attempts" / (name + "-01.json")
        row = json.loads(path.read_bytes())
        row["attempt" if change == "attempt" else "model_digest"] = 9 if change == "attempt" else "b" * 64
        atomic_json(path, row)
    else:
        context["extra_synthetic_source"] = "Changed complete input"
    atomic_json(job.root / "vision-response-synthetic.json", receipt)
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        job.stage(name, prompt, schema, context, lambda row: m.validate_proposal(row, context))


def test_read_only_missing_stage_cannot_generate_or_acquire_gpu(tmp_path):
    job, name, prompt, schema, context, _, _, _ = cached_job(tmp_path)
    (job.root / (name + ".json")).unlink()
    with pytest.raises(ValueError, match="existing_stage_without_inference"):
        job.stage(name, prompt, schema, context, lambda value: None)
    with pytest.raises(ValueError, match="inference_forbidden"):
        job.generate_response([], schema={})
    with pytest.raises(ValueError, match="gpu_acquisition_forbidden"):
        job.slot(name)


@pytest.mark.parametrize("change", ["gpu", "context", "model", "truncation", "text", "headroom", "output"])
def test_vision_gpu_metrics_reject_incomplete_input_or_cpu_fallback(tmp_path, change):
    job, _, _, _, _, _, receipt, _ = cached_job(tmp_path)
    metrics = copy.deepcopy(receipt["metrics"])
    expected = metrics["text_content_tokens"]
    if change == "gpu":
        metrics["size_vram"] = 0
    elif change == "context":
        metrics["measured_context"] = 16384
    elif change == "model":
        metrics["model_digest"] = "changed"
    elif change == "truncation":
        metrics["done_reason"] = "length"
    elif change == "text":
        metrics["prompt_eval_count"] = expected - 10
    elif change == "headroom":
        metrics["prompt_eval_count"] = m.OPTIONS["num_ctx"] - 1
    else:
        metrics["eval_count"] = m.OPTIONS["num_predict"] + 1
    with pytest.raises(ValueError):
        m.validate_metrics(metrics, job.model_digest, expected)


def test_no_duplicate_images_are_injected_into_wire_request():
    original = [{"role": "system", "content": "Synthetic"}, {"role": "user", "content": "Synthetic"}]
    wire = m.wire_messages(original, b"pixels")
    assert "images" not in original[1] and len(wire[1]["images"]) == 1
    with pytest.raises(ValueError):
        m.wire_messages(wire, b"pixels")


def test_context_preserves_complete_ocr_and_neighbor_text_without_inventing_lesson_affiliation(monkeypatch):
    inventory = {"lesson": "synthetic", "lesson_artifact": "a" * 64, "sources": {
        "O00000": {"kind": "speech", "text": "Complete synthetic classroom", "origin": {"start": 0, "end": 10}},
        "O00001": {"kind": "document", "text": "full notebook " * 5000, "origin": {"member_sha256": "b" * 64, "cell": 0}},
        "O00002": {"kind": "document", "text": "Unrelated notebook", "origin": {"member_sha256": "c" * 64, "cell": 0}}}}
    visual = {"id": "V00000", "notebook_sha256": "b" * 64, "cell": 1, "raw_image_sha256": "d" * 64,
        "existing_ocr": {"image_sha256": "e" * 64, "text": "Complete OCR draft " * 5000}}

    def rank(lessons, queries, **kwargs):
        assert lessons["synthetic"]["sources"]["O00000"]["text"] == inventory["sources"]["O00000"]["text"]
        assert queries["V00000"]["complete_notebook_context"] == inventory["sources"]["O00001"]["text"]
        assert queries["V00000"]["existing_complete_ocr_draft"] == visual["existing_ocr"]["text"]
        return {"V00000": {"courses": [{"lesson": "synthetic", "source_ids": ["O00000"]}],
            "ranking_establishes_affiliation": False}}

    monkeypatch.setattr(m.courseware.retrieval, "rank", rank)
    value = m.contexts(inventory, {"visuals": [visual]})["V00000"]
    assert value["notebook_context"] == {"O00001": inventory["sources"]["O00001"]}
    assert value["existing_ocr_draft"] == visual["existing_ocr"]["text"]
    assert value["existing_ocr_is_ground_truth"] is False
    assert value["article_coverage_reviewed"] is False


def test_read_only_constructor_refuses_missing_manifest_without_creating_job_files(tmp_path):
    (tmp_path / "knowledge.sqlite3").touch()
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    token_path = tmp_path / "tokenizer.json"
    tokenizer.save(str(token_path))
    with pytest.raises(ValueError, match="existing_manifest_without_writes"):
        m.Job(tmp_path, {"sources": context_fixture()["classroom_sources"]}, {}, {}, token_path,
            {"model_digest": "a" * 64}, read_only=True)
    assert not (tmp_path / "wiki").exists()
