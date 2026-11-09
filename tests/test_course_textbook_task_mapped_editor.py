import copy
import json
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_task_mapped_editor as m
from test_course_textbook_final_section_edit import candidate, prepared


def wire():
    value = candidate()
    value["paragraph_count"] = len(value["paragraphs"])
    for paragraph in value["paragraphs"]:
        del paragraph["citations"]
    for point in value["coverage"].values():
        point["paragraph_sources"] = {
            str(i): ["O00000"] for i in point.pop("paragraphs")
        }
    return value


def setup(tmp_path, monkeypatch):
    root, folder, recipe, data, key, args, original = prepared(tmp_path, monkeypatch)
    m.bind_prepared(root, key)
    return root, folder, recipe, data, key, args, original


def backend(monkeypatch, *, negative=False, invalid=False, cpu=False):
    import httpx

    calls, state = [], {}

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, json):
            request = json
            calls.append(copy.deepcopy(request))
            state["request"] = request
            if request["model"] == "qwen3.5:9b":
                value = wire()
                if invalid:
                    del value["coverage"]["P0"]
                else:
                    jsonschema.validate(value, request["format"])
            else:
                value = {
                    "paragraphs": {
                        str(i): {"passed": True, "reason": "Synthetic evidence"}
                        for i in range(3)
                    },
                    "coverage": {
                        "P0": {"passed": not negative, "reason": "Synthetic evidence"}
                    },
                    "diagrams": {},
                    **{
                        key: {"passed": True, "reason": "Synthetic evidence"}
                        for key in ("overview", "takeaway", "quiz", "readability")
                    },
                }
            response = {
                "done": True,
                "done_reason": "stop",
                "eval_count": 50,
                "prompt_eval_count": 2,
                "eval_duration": 1_000_000_000,
                "message": {"content": __import__("json").dumps(value)},
            }
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: response)

        def get(self, url):
            request = state["request"]
            return SimpleNamespace(
                json=lambda: {
                    "models": [
                        {
                            "digest": "synthetic-digest-" + request["model"],
                            "context_length": request["options"]["num_ctx"],
                            "size_vram": 0 if cpu else 12345,
                        }
                    ]
                }
            )

    monkeypatch.setattr(httpx, "Client", Client)
    return calls


def test_actual_decode_requires_all_task_maps_and_review_uses_originals(
    tmp_path, monkeypatch
):
    root, folder, _, data, key, _, original = setup(tmp_path, monkeypatch)
    before = original.read_bytes()
    calls = backend(monkeypatch)
    result = m.worker(root, key)
    assert [r["model"] for r in calls] == ["qwen3.5:9b", "course-defiant:q4"]
    first = json.loads(calls[0]["messages"][1]["content"])
    review = json.loads(calls[1]["messages"][1]["content"])
    assert first["sources"] == review["sources"] == data["context"]["sources"]
    assert "original_negative_review" not in review
    assert m.NOTICE in calls[0]["messages"][0]["content"]
    assert review["task_source_assignments_are_factual_evidence"] is False
    assert review["candidate"] == candidate()
    assert (
        result["section_editorial_review_passed"]
        and result["original_parent_issues_retained"] == 9
    )
    assert (
        result["reader_publication_allowed"] is False
        and original.read_bytes() == before
    )
    assert (
        json.loads(
            m.whole.read(folder / "attempts/editorial-write-01.json")["raw_response"]
        )
        == wire()
    )
    assert (
        m.whole.read(folder / "task-mapped-candidate-derivation.json")[
            "semantic_acceptance"
        ]
        is False
    )


def test_separate_task_sources_only_attach_to_model_chosen_paragraphs(
    tmp_path, monkeypatch
):
    _, _, _, data, _, _, _ = setup(tmp_path, monkeypatch)
    data["ids"].append("P1")
    data["ledger"]["points"]["P1"] = {
        **data["ledger"]["points"]["P0"],
        "citations": ["O00001"],
    }
    data["context"]["sources"]["O00001"] = {
        "kind": "speech",
        "text": "A second synthetic original",
    }
    data["inventory"]["sources"]["O00001"] = data["context"]["sources"]["O00001"]
    data["schema"] = m.original.candidate_schema(
        data["ids"], data["ledger"], data["context"]["sources"]
    )
    raw = wire()
    raw["coverage"]["P0"]["paragraph_sources"] = {"0": ["O00000"]}
    raw["coverage"]["P1"] = {
        "reason": "Synthetic explanation",
        "paragraph_sources": {"1": ["O00001"], "2": ["O00001"]},
    }
    before = copy.deepcopy(raw)
    actual, proof = m.compile_candidate(raw, data)
    assert raw == before
    assert [p["text"] for p in actual["paragraphs"]] == [
        p["text"] for p in raw["paragraphs"]
    ]
    assert [p["citations"] for p in actual["paragraphs"]] == [
        ["O00000"],
        ["O00001"],
        ["O00001"],
    ]
    assert proof["paragraph_mapping"] == {"P0": [0], "P1": [1, 2]}
    assert (
        proof["citations_and_mappings_selected_by_model_not_added_by_program"] is True
    )


@pytest.mark.parametrize(
    "defect",
    [
        "missing_task",
        "empty_confirmed_map",
        "unknown_task",
        "wrong_primary_source",
        "out_of_actual_range",
        "wrong_count",
        "empty_source",
        "duplicate_source",
        "orphan_body",
        "duplicate_body",
    ],
)
def test_incomplete_or_fabricated_structure_is_rejected(defect, tmp_path, monkeypatch):
    _, _, _, data, _, _, _ = setup(tmp_path, monkeypatch)
    raw = wire()
    links = raw["coverage"]["P0"]["paragraph_sources"]
    if defect == "missing_task":
        del raw["coverage"]["P0"]
    elif defect == "empty_confirmed_map":
        links.clear()
    elif defect == "unknown_task":
        raw["coverage"]["foreign"] = copy.deepcopy(raw["coverage"]["P0"])
    elif defect == "wrong_primary_source":
        links["0"] = ["O99999"]
    elif defect == "out_of_actual_range":
        links["3"] = ["O00000"]
    elif defect == "wrong_count":
        raw["paragraph_count"] = 2
    elif defect == "empty_source":
        links["0"] = []
    elif defect == "duplicate_source":
        links["0"] = ["O00000", "O00000"]
    elif defect == "orphan_body":
        del links["2"]
    else:
        raw["paragraphs"][2]["text"] = raw["paragraphs"][0]["text"]
    with pytest.raises((jsonschema.ValidationError, ValueError)):
        m.compile_candidate(raw, data)


def test_pending_exclusion_requires_empty_map_and_teaching_requires_actual_map(
    tmp_path, monkeypatch
):
    _, _, _, data, _, _, _ = setup(tmp_path, monkeypatch)
    data["ids"].append("U0")
    data["ledger"]["points"]["U0"] = copy.deepcopy(data["ledger"]["points"]["P0"])
    data["ledger"]["pending_source_cards"]["U0"] = {
        "reference": "O00000",
        "neighbors": [],
    }
    data["schema"] = m.original.candidate_schema(
        data["ids"], data["ledger"], data["context"]["sources"]
    )
    raw = wire()
    raw["coverage"]["U0"] = {
        "teaching": False,
        "reason": "Synthetic administrative content",
        "paragraph_sources": {},
    }
    assert m.compile_candidate(raw, data)[0]["coverage"]["U0"]["paragraphs"] == []
    raw["coverage"]["U0"]["paragraph_sources"] = {"0": ["O00000"]}
    with pytest.raises(jsonschema.ValidationError):
        m.compile_candidate(raw, data)
    raw["coverage"]["U0"].update(teaching=True, paragraph_sources={})
    with pytest.raises(jsonschema.ValidationError):
        m.compile_candidate(raw, data)


def test_negative_review_is_terminal_and_preserves_old_failures_and_budget(
    tmp_path, monkeypatch
):
    root, folder, _, _, key, args, original = setup(tmp_path, monkeypatch)
    before = original.read_bytes()
    calls = backend(monkeypatch, negative=True)
    result = m.worker(root, key)
    assert (
        result["negative_checks"] == 1 and not result["section_editorial_review_passed"]
    )
    with pytest.raises(ValueError, match="all_original_editorial_roles_unused"):
        m.worker(root, key)
    with pytest.raises(ValueError, match="all_original_editorial_roles_unused"):
        m.bind_prepared(root, key)
    with pytest.raises(ValueError, match="editorial_budget_already_reserved"):
        m.prepare(*args)
    assert len(calls) == 2 and original.read_bytes() == before
    assert m.whole.read(folder / "task-mapped-result.json")["negative_checks"] == 1


@pytest.mark.parametrize("reason", ["missing_task", "cpu"])
def test_single_failed_write_is_retained_without_budget_refresh(
    reason, tmp_path, monkeypatch
):
    root, folder, _, _, key, _, _ = setup(tmp_path, monkeypatch)
    calls = backend(monkeypatch, invalid=reason == "missing_task", cpu=reason == "cpu")
    with pytest.raises(ValueError):
        m.worker(root, key)
    assert len(calls) == 1 and (folder / "task-mapped-failure.json").is_file()
    assert (folder / "attempts/editorial-write-01.json").is_file()
    assert not (folder / "task-mapped-result.json").exists()
    with pytest.raises(ValueError, match="all_original_editorial_roles_unused"):
        m.worker(root, key)


def test_existing_first_use_format_cannot_be_replaced(tmp_path, monkeypatch):
    root, folder, _, _, key, _, _ = prepared(tmp_path, monkeypatch)
    m.prior.bind_prepared(root, key)
    with pytest.raises(ValueError, match="no_other_reserved_wire_format"):
        m.bind_prepared(root, key)
    assert not (folder / m.CONTRACT).exists()


def test_readonly_result_replay_checks_exact_actual_requests_without_gpu(
    tmp_path, monkeypatch
):
    root, folder, _, _, key, _, _ = setup(tmp_path, monkeypatch)
    backend(monkeypatch)
    m.worker(root, key)
    before = {p: p.read_bytes() for p in folder.rglob("*.json")}
    monkeypatch.setattr(
        m.Job, "generate_response", lambda *args: pytest.fail("Readonly inference")
    )
    monkeypatch.setattr(m.Job, "slot", lambda *args: pytest.fail("Readonly GPU"))
    assert m.verify(root, key)["new_model_calls"] == 0
    assert all(p.read_bytes() == value for p, value in before.items())


@pytest.mark.parametrize(
    "name",
    [
        "task-mapped-preflight.json",
        "task-mapped-write-request.json",
        "task-mapped-candidate-derivation.json",
        m.CONTRACT,
    ],
)
def test_modified_execution_or_mapping_is_not_accepted(name, tmp_path, monkeypatch):
    root, folder, _, _, key, _, _ = setup(tmp_path, monkeypatch)
    backend(monkeypatch)
    m.worker(root, key)
    m.atomic_json(folder / name, {"changed": "Synthetic modification"})
    with pytest.raises(ValueError):
        m.verify(root, key)


def test_cli_bindings_use_stable_module_paths():
    from pathlib import Path

    assert Path(m.__file__).stem in m.bindings()["entrypoints"]
