import copy
import json
from types import SimpleNamespace

import pytest

import test_course_textbook_final_section_edit as fixture
from wechat_decrypt_tool import course_textbook_final_section_citations as m


def failed_proposal(tmp_path, monkeypatch):
    edit = m.edit
    root, _, _, data, _, args, original = fixture.prepared(tmp_path, monkeypatch)
    data = copy.deepcopy(data)
    extra = {"kind": "speech", "text": "Synthetic adjacent original, not primary support."}
    data["context"]["sources"]["O00001"] = extra
    data["inventory"]["sources"]["O00001"] = extra
    data["ledger"]["points"]["P1"] = {"label": "Synthetic second point", "teaching": extra["text"],
        "category": "concept", "citations": ["O00001"]}
    data["context"]["assigned_points"] = copy.deepcopy(data["ledger"]["points"])
    data["ids"] = ["P0", "P1"]
    data["schema"] = edit.candidate_schema(data["ids"], data["ledger"], data["context"]["sources"])
    monkeypatch.setattr(edit, "source_inputs", lambda *_a, **_k: copy.deepcopy(data))
    key = edit.prepare(root, "b" * 32, args[2], args[3])["job"]
    folder = root / "wiki/final-editorial-section-jobs" / key
    value = fixture.candidate()
    for row in value["paragraphs"]:
        row["citations"] = ["O00001"]
    value["coverage"]["P1"] = {"paragraphs": [1], "reason": "Synthetic second point explanation"}
    monkeypatch.setattr(fixture, "candidate", lambda: copy.deepcopy(value))
    calls = fixture.backend(monkeypatch)
    with pytest.raises(ValueError, match="attempt_budget_exhausted_editorial-write"):
        edit.worker(root, key)
    edit.atomic_json(folder / "guard-started.json", {"pid": 999999, "created": 0})
    edit.atomic_json(folder / "guard-progress.json", {"state": "worker_exited", "returncode": 1})
    edit.atomic_json(folder / "guard-failure.json", {"state": "stopped", "reason_code": "synthetic_guard_failure"})
    reviews(monkeypatch, calls=calls)
    return root, folder, key, calls, original


def reviews(monkeypatch, *, calls=None, negative=False, cpu=False):
    import httpx

    calls = [] if calls is None else calls
    state = {}

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, json):
            state["request"] = json
            calls.append(copy.deepcopy(json))
            value = {"paragraphs": {str(i): {"passed": True, "reason": "Synthetic evidence"} for i in range(3)},
                "coverage": {key: {"passed": not negative, "reason": "Synthetic evidence"} for key in ("P0", "P1")},
                "diagrams": {}, **{key: {"passed": True, "reason": "Synthetic evidence"}
                    for key in ("overview", "takeaway", "quiz", "readability")}}
            if negative:
                value["coverage"]["P1"]["passed"] = True
            response = {"done": True, "done_reason": "stop", "eval_count": 50, "prompt_eval_count": 2,
                "eval_duration": 1_000_000_000, "message": {"content": __import__("json").dumps(value)}}
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: response)

        def get(self, url):
            request = state["request"]
            return SimpleNamespace(json=lambda: {"models": [{"digest": "synthetic-digest-" + request["model"],
                "context_length": request["options"]["num_ctx"], "size_vram": 0 if cpu else 12345}]})

    monkeypatch.setattr(httpx, "Client", Client)
    return calls


def sealed(folder):
    names = ["failure.json", "progress.json", "guard-failure.json", "guard-progress.json",
        "attempts/editorial-write-01.json", "recipe.json", "inputs.json"]
    return {name: (folder / name).read_bytes() for name in names}


def test_same_proposal_uses_only_unused_original_review_preserves_all_failed_writer_bytes(tmp_path, monkeypatch):
    root, folder, key, calls, original = failed_proposal(tmp_path, monkeypatch)
    before, parent = sealed(folder), original.read_bytes()
    prepared = m.prepare(root, key)
    assert prepared["new_model_calls"] == 0 and prepared["citation_paragraphs"] == 3
    proof = m.edit.whole.read(folder / "citation-review/proposal.json")
    old = json.loads(m.edit.whole.read(folder / "attempts/editorial-write-01.json")["raw_response"])
    assert proof["candidate"]["coverage"] == old["coverage"]
    assert [p["text"] for p in proof["candidate"]["paragraphs"]] == [p["text"] for p in old["paragraphs"]]
    result = m.worker(root, key)
    assert result["section_editorial_review_passed"] and not result["reader_publication_allowed"]
    assert [r["model"] for r in calls] == ["qwen3.5:9b", "course-defiant:q4"]
    actual = json.loads(calls[-1]["messages"][1]["content"])
    assert actual["citation_metadata_is_unproved"] and "original_negative_review" not in actual
    assert actual["sources"] == json.loads(calls[0]["messages"][1]["content"])["sources"]
    assert sealed(folder) == before and original.read_bytes() == parent
    assert not (folder / "editorial-write.json").exists()
    assert not (root / "wiki/textbook-reading/catalog.json").exists()
    with pytest.raises(ValueError, match="once_no_new_recipe"):
        m.prepare(root, key)
    with pytest.raises(ValueError, match="worker_once_no_role_refresh"):
        m.worker(root, key)
    assert len(calls) == 2


def test_negative_review_stays_negative_without_rewrite_or_second_review(tmp_path, monkeypatch):
    root, folder, key, _, _ = failed_proposal(tmp_path, monkeypatch)
    calls = reviews(monkeypatch, negative=True)
    m.prepare(root, key)
    result = m.worker(root, key)
    assert result["negative_checks"] == 1 and not result["section_editorial_review_passed"]
    assert len(calls) == 1 and result["new_writer_calls"] == result["automatic_semantic_repairs"] == 0
    assert (folder / "failure.json").exists()
    with pytest.raises(ValueError, match="once_no_role_refresh"):
        m.worker(root, key)


def test_readonly_rebuild_reuses_exact_review_schema_requests_and_responses(tmp_path, monkeypatch):
    root, folder, key, _, _ = failed_proposal(tmp_path, monkeypatch)
    m.prepare(root, key)
    m.worker(root, key)
    before = {p: p.read_bytes() for p in folder.rglob("*.json")}
    monkeypatch.setattr(m.ReviewJob, "generate_response", lambda *_a, **_k: pytest.fail("unexpected inference"))
    monkeypatch.setattr(m.ReviewJob, "slot", lambda *_a, **_k: pytest.fail("unexpected GPU"))
    assert m.verify(root, key)["new_model_calls"] == 0
    assert all(p.read_bytes() == raw for p, raw in before.items())


@pytest.mark.parametrize("change", ["original_request", "decode_schema", "different_gate", "mapping", "prose"])
def test_request_schema_original_gate_and_all_prose_and_mapping_are_bound(tmp_path, monkeypatch, change):
    root, folder, key, _, _ = failed_proposal(tmp_path, monkeypatch)
    if change in {"mapping", "prose"}:
        m.prepare(root, key)
        path = folder / "citation-review/proposal.json"
        data = m.edit.whole.read(path)
        if change == "mapping":
            data["candidate"]["coverage"]["P0"]["paragraphs"] = [1]
        else:
            data["candidate"]["paragraphs"][0]["text"] = "Changed synthetic prose"
        m.edit.atomic_json(path, data)
        with pytest.raises(ValueError, match="unchanged_metadata"):
            m.load(root, key)
    else:
        path = folder / "attempts/editorial-write-01.json" if change != "decode_schema" else next(folder.glob("response-*.json"))
        data = m.edit.whole.read(path)
        field = {"original_request": "request_sha256", "decode_schema": "schema_sha256", "different_gate": "validation_error"}[change]
        data[field] = "changed-synthetic-value"
        m.edit.atomic_json(path, data)
        with pytest.raises(ValueError):
            m.prepare(root, key)
        assert not (folder / "citation-review").exists()


def test_failed_reviewer_consumes_same_single_role_and_retains_cpu_failure(tmp_path, monkeypatch):
    root, folder, key, _, _ = failed_proposal(tmp_path, monkeypatch)
    calls = reviews(monkeypatch, cpu=True)
    m.prepare(root, key)
    with pytest.raises(ValueError, match="attempt_budget_exhausted_editorial-review"):
        m.worker(root, key)
    assert len(calls) == 1 and (folder / "citation-review/failure.json").exists()
    assert len(list((folder / "attempts").glob("editorial-review-*.json"))) == 1
    with pytest.raises(ValueError, match="once_no_role_refresh"):
        m.worker(root, key)
