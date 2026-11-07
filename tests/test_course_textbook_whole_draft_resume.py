import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_whole_draft_resume as module
from wechat_decrypt_tool.course_library import canonical


def fixture():
    inventory = {"sources": {key: {"kind": "speech", "text": "Synthetic full original " + key} for key in ["O00000", "O00001"]}}
    working = {"points": {"P0": {"citations": ["O00000"]}, "P1": {"citations": ["O00001"]}}, "pending_source_cards": {}}
    candidate = {"heading": "Synthetic heading", "objective": "Synthetic objective",
        "overview": {"text": "Synthetic introduction", "citations": ["O00000"]},
        "takeaway": {"text": "Synthetic conclusion", "citations": ["O00000"]},
        "paragraphs": [{"text": "Synthetic explanation one", "citations": ["O00000"]},
            {"text": "Synthetic explanation two", "citations": ["O00000"]}],
        "coverage": {"P0": {"paragraphs": [0], "reason": "Synthetic mapping"}, "P1": {"paragraphs": [1], "reason": "Synthetic mapping"}},
        "diagrams": [{"kind": "flow", "title": "Synthetic invalid graph", "caption": "Synthetic caption",
            "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
            "edges": [{"from": "a", "to": "unknown", "label": "Synthetic edge"}], "citations": ["O00000"]}],
        "quiz": {"question": "Synthetic question", "answer": "Synthetic answer", "explanation": "Synthetic explanation",
            "criteria": ["Synthetic criterion"], "citations": ["O00000"]}}
    return inventory, working, candidate


def test_proposals_preserve_prose_mappings_and_original_invalid_diagram():
    inventory, working, candidate = fixture()
    before = copy.deepcopy(candidate)
    proposed, changes, invalid = module.normalize(candidate, list(working["points"]), working, inventory)
    assert candidate == before
    assert len(changes) == 1 and proposed["paragraphs"][1]["citations"] == ["O00000", "O00001"]
    assert proposed["coverage"] == candidate["coverage"] and proposed["quiz"] == candidate["quiz"]
    assert [p["text"] for p in proposed["paragraphs"]] == [p["text"] for p in candidate["paragraphs"]]
    assert not proposed["diagrams"] and invalid[0]["graph"] == candidate["diagrams"][0]
    module.writing.validate_section(proposed, list(working["points"]), working, inventory)


@pytest.mark.parametrize("changed", ["scope", "mapping", "empty", "no_defect"])
def test_recovery_never_invents_sources_or_repairs_content_or_mapping(changed):
    inventory, working, candidate = fixture()
    if changed == "scope":
        candidate["paragraphs"][0]["citations"] = ["outside"]
    elif changed == "mapping":
        candidate["coverage"]["P0"]["paragraphs"] = [99]
    elif changed == "empty":
        candidate["paragraphs"][0]["text"] = " "
    else:
        candidate["diagrams"] = []
        candidate["paragraphs"][1]["citations"] = ["O00001"]
    with pytest.raises((ValueError, module.jsonschema.ValidationError)):
        module.normalize(candidate, list(working["points"]), working, inventory)


def history(tmp_path, monkeypatch):
    inventory, working, candidate = fixture()
    name = "section-0000-write"
    job = SimpleNamespace(root=tmp_path, key="a" * 32, models={"qwen3.5:9b": "b" * 64})
    context = {"assigned_points": working["points"], "sources": inventory["sources"]}
    schema = module.writing.section_schema(list(working["points"]), working)
    request = {"prompt": "Synthetic exact original prompt", "schema": schema, "binding": "c" * 64}
    attempts = tmp_path / "attempts"
    attempts.mkdir()
    previous = ""
    review_error = module.reviewable.repair.ERROR
    for number in [1, 2]:
        record = {"stage": name, "attempt": number, "input_sha256": request["binding"],
            "model_digest": job.models["qwen3.5:9b"], "error_type": "ValueError", "validation_error": review_error,
            "raw_response": json.dumps(candidate)}
        messages = [{"role": "system", "content": request["prompt"]},
            {"role": "user", "content": json.dumps({**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        record["request_sha256"] = module.writing.whole.sha(canonical(messages))
        (attempts / f"{name}-{number:02}.json").write_text(json.dumps(record), "utf-8")
        previous = record["validation_error"]
    monkeypatch.setattr(module, "capture_request", lambda *args: request)
    monkeypatch.setattr(module.writing.whole.editor.e, "verify_response", lambda *args: None)
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path / "user")
    return job, name, schema, context, {"sources": inventory["sources"]}, working, inventory


def test_recovery_uses_earliest_complete_candidate_without_positive_model_cache(tmp_path, monkeypatch):
    args = history(tmp_path, monkeypatch)
    originals = {p: p.read_bytes() for p in (tmp_path / "attempts").iterdir()}
    result = module.recover_stage(*args)
    assert result["receipt"]["selected_attempt"] == 1
    assert result["receipt"]["model_calls"] == 0 and not result["receipt"]["semantic_acceptance"]
    assert not result["receipt"]["reader_publication_allowed"]
    assert not (tmp_path / (args[1] + ".json")).exists()
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    assert module.recover_stage(*args) == result


@pytest.mark.parametrize("changed", ["request", "input", "model", "accepted", "missing_attempt", "used_review"])
def test_changed_history_or_previously_used_roles_prevent_new_recovery(tmp_path, monkeypatch, changed):
    args = history(tmp_path, monkeypatch)
    p = tmp_path / "attempts/section-0000-write-01.json"
    value = json.loads(p.read_bytes())
    if changed == "missing_attempt":
        (p.parent / "section-0000-write-02.json").unlink()
    elif changed == "used_review":
        (p.parent / "section-0000-review-01.json").write_text("{}", "utf-8")
    else:
        key = {"request": "request_sha256", "input": "input_sha256", "model": "model_digest", "accepted": "accepted_structure"}[changed]
        value[key] = True if changed == "accepted" else "changed"
        p.write_text(json.dumps(value), "utf-8")
    with pytest.raises(ValueError):
        module.recover_stage(*args)
    assert not module.artifact_path(args[0], args[1]).exists()


def test_recovery_artifact_tampering_is_rejected_without_replacing_originals(tmp_path, monkeypatch):
    args = history(tmp_path, monkeypatch)
    module.recover_stage(*args)
    path = module.artifact_path(args[0], args[1])
    value = json.loads(path.read_bytes())
    value["data"]["paragraphs"][0]["text"] = "Unauthorized replacement"
    path.write_text(json.dumps(value), "utf-8")
    with pytest.raises(ValueError, match="unchanged_explicit_recovery"):
        module.recover_stage(*args)
    assert not (tmp_path / "section-0000-write.json").exists()
