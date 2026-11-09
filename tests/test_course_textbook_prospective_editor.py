import copy
import json
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_prospective_editor as m
from test_course_textbook_final_section_edit import candidate, prepared


def wire():
    value = candidate()
    value["paragraph_count"] = len(value["paragraphs"])
    for paragraph in value["paragraphs"]:
        refs = paragraph.pop("citations")
        paragraph["point_sources"] = [{"point": "P0", "sources": refs}]
    del value["coverage"]["P0"]["paragraphs"]
    return value


def setup(tmp_path, monkeypatch):
    root, folder, recipe, data, key, args, original = prepared(tmp_path, monkeypatch)
    m.bind_prepared(root, key)
    return root, folder, recipe, data, key, args, original


def backend(monkeypatch, *, negative=False, invalid=False):
    import httpx

    calls, active = [], {}

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, json):
            request = json
            active["request"] = request
            calls.append(copy.deepcopy(request))
            if request["model"] == "qwen3.5:9b":
                value = wire()
                if invalid:
                    value["paragraphs"][0]["point_sources"][0]["sources"] = ["not_visible"]
                else:
                    jsonschema.validate(value, request["format"])
            else:
                value = {"paragraphs": {str(i): {"passed": True, "reason": "Synthetic evidence"} for i in range(3)},
                    "coverage": {"P0": {"passed": not negative, "reason": "Synthetic evidence"}}, "diagrams": {},
                    **{key: {"passed": True, "reason": "Synthetic evidence"} for key in ("overview", "takeaway", "quiz", "readability")}}
            response = {"done": True, "done_reason": "stop", "eval_count": 50, "prompt_eval_count": 2,
                "eval_duration": 1_000_000_000, "message": {"content": __import__("json").dumps(value)}}
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: response)

        def get(self, url):
            request = active["request"]
            return SimpleNamespace(json=lambda: {"models": [{"digest": "synthetic-digest-" + request["model"],
                "context_length": request["options"]["num_ctx"], "size_vram": 12345}]})

    monkeypatch.setattr(httpx, "Client", Client)
    return calls


def test_first_editorial_role_uses_actual_wire_schema_and_independent_complete_review(tmp_path, monkeypatch):
    root, folder, _, data, key, _, original = setup(tmp_path, monkeypatch)
    before = original.read_bytes()
    calls = backend(monkeypatch)
    result = m.worker(root, key)
    assert [r["model"] for r in calls] == ["qwen3.5:9b", "course-defiant:q4"]
    first = json.loads(calls[0]["messages"][1]["content"])
    second = json.loads(calls[1]["messages"][1]["content"])
    assert first["sources"] == second["sources"] == data["context"]["sources"]
    assert "original_negative_review" not in second
    assert m.NOTICE in calls[0]["messages"][0]["content"]
    assert second["point_source_assignments_are_factual_evidence"] is False
    assert second["candidate"] == candidate()
    assert result["section_editorial_review_passed"] and result["original_parent_issues_retained"] == 9
    assert result["reader_publication_allowed"] is False and original.read_bytes() == before
    raw = m.whole.read(folder / "attempts/editorial-write-01.json")["raw_response"]
    assert json.loads(raw) == wire()
    assert m.whole.read(folder / "point-source-candidate-derivation.json")["semantic_acceptance"] is False


def test_projection_uses_only_model_chosen_sources_and_preserves_every_prose_field(tmp_path, monkeypatch):
    _, _, _, data, _, _, _ = setup(tmp_path, monkeypatch)
    raw = wire()
    actual, proof = m.compile_candidate(raw, data)
    assert actual == candidate() and raw == wire()
    assert proof["paragraph_mapping"] == {"P0": [0, 1, 2]}
    assert proof["citations_selected_by_model_not_added_by_program"] is True


@pytest.mark.parametrize("defect", ["wrong_source", "unknown_point", "wrong_count", "missing_point", "duplicate_prose"])
def test_bad_structural_support_never_becomes_an_accepted_coverage_mapping(defect, tmp_path, monkeypatch):
    _, _, _, data, _, _, _ = setup(tmp_path, monkeypatch)
    raw = wire()
    if defect == "wrong_source":
        raw["paragraphs"][0]["point_sources"][0]["sources"] = ["O99999"]
    elif defect == "unknown_point":
        raw["paragraphs"][0]["point_sources"][0]["point"] = "not_assigned"
    elif defect == "wrong_count":
        raw["paragraph_count"] = 2
    elif defect == "missing_point":
        raw["paragraphs"][0]["point_sources"] = []
    else:
        raw["paragraphs"][1]["text"] = raw["paragraphs"][0]["text"]
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        m.compile_candidate(raw, data)


def test_non_teaching_pending_point_cannot_be_assigned_to_a_teaching_paragraph(tmp_path, monkeypatch):
    _, _, _, data, _, _, _ = setup(tmp_path, monkeypatch)
    data["ids"].append("U0")
    data["ledger"]["points"]["U0"] = copy.deepcopy(data["ledger"]["points"]["P0"])
    data["ledger"]["pending_source_cards"]["U0"] = {"reference": "O00000", "neighbors": []}
    data["schema"] = m.original.candidate_schema(data["ids"], data["ledger"], data["context"]["sources"])
    raw = wire()
    raw["coverage"]["U0"] = {"teaching": False, "reason": "Synthetic administrative text"}
    actual, _ = m.compile_candidate(raw, data)
    assert actual["coverage"]["U0"]["paragraphs"] == []
    raw["paragraphs"][0]["point_sources"].append({"point": "U0", "sources": ["O00000"]})
    with pytest.raises(jsonschema.ValidationError):
        m.compile_candidate(raw, data)


def test_negative_review_stays_negative_without_second_write_or_review(tmp_path, monkeypatch):
    root, folder, _, _, key, args, _ = setup(tmp_path, monkeypatch)
    calls = backend(monkeypatch, negative=True)
    result = m.worker(root, key)
    assert not result["section_editorial_review_passed"] and result["negative_checks"] == 1
    with pytest.raises(ValueError, match="all_original_editorial_roles_unused"):
        m.worker(root, key)
    with pytest.raises(ValueError, match="all_original_editorial_roles_unused"):
        m.bind_prepared(root, key)
    with pytest.raises(ValueError, match="editorial_budget_already_reserved"):
        m.prepare(*args)
    assert len(calls) == 2
    assert m.whole.read(folder / "point-source-result.json")["negative_checks"] == 1


def test_single_failed_proposal_consumes_its_original_attempt_and_retains_failure(tmp_path, monkeypatch):
    root, folder, _, _, key, _, _ = setup(tmp_path, monkeypatch)
    calls = backend(monkeypatch, invalid=True)
    with pytest.raises(ValueError):
        m.worker(root, key)
    assert len(calls) == 1 and (folder / "point-source-failure.json").is_file()
    assert (folder / "attempts/editorial-write-01.json").is_file()
    with pytest.raises(ValueError, match="all_original_editorial_roles_unused"):
        m.worker(root, key)


def test_readonly_reconstruction_uses_the_same_actual_wire_and_never_calls_inference(tmp_path, monkeypatch):
    root, folder, _, _, key, _, _ = setup(tmp_path, monkeypatch)
    backend(monkeypatch)
    m.worker(root, key)
    before = {p: p.read_bytes() for p in folder.rglob("*.json")}
    monkeypatch.setattr(m.Job, "generate_response", lambda *args: pytest.fail("Readonly inference forbidden"))
    monkeypatch.setattr(m.Job, "slot", lambda *args: pytest.fail("Readonly GPU forbidden"))
    assert m.verify(root, key)["new_model_calls"] == 0
    assert all(p.read_bytes() == raw for p, raw in before.items())


@pytest.mark.parametrize("name", ["point-source-preflight.json", "point-source-write-request.json", "point-source-candidate-derivation.json"])
def test_changed_preflight_or_projection_is_rejected(name, tmp_path, monkeypatch):
    root, folder, _, _, key, _, _ = setup(tmp_path, monkeypatch)
    backend(monkeypatch)
    m.worker(root, key)
    m.atomic_json(folder / name, {"changed": "synthetic"})
    with pytest.raises(ValueError):
        m.verify(root, key)


def test_code_bindings_are_stable_for_cli():
    from pathlib import Path

    assert Path(m.__file__).stem in m.bindings()["entrypoints"]
