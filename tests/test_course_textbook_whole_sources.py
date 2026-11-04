import copy

import pytest

from wechat_decrypt_tool import course_textbook_whole_sources as module
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_textbook_evidence import inventory, sha


def fixture():
    text = "def example():\r\n    return 'literal whitespace'\n" * 160
    return inventory({"id": "a" * 24, "source_export_sha256": "b" * 64,
        "evidence": {"long": {"kind": "frame", "text": text, "start": 0, "end": 0},
            "next": {"kind": "speech", "text": "Explain the complete operation.", "start": 1, "end": 2}}},
        lesson_artifact="c" * 64)


def test_full_record_is_restored_without_fragmenting_long_code_or_altering_parent():
    parent = fixture()
    original = copy.deepcopy(parent)
    value = module.build(sha(canonical(parent)), parent)
    assert len(parent["sources"]) > len(value["sources"])
    assert value["sources"]["O00000"] == parent["originals"][0]
    assert value["original_characters"] == parent["original_characters"]
    assert value["originals"] == parent["originals"]
    assert parent == original
    assert value["attachment_scope"]["unresolved"]
    assert not value["reader_payload"]


def test_oversized_original_is_an_intact_group_not_silently_sliced():
    rows = {"a": {"text": "a" * 10}, "b": {"text": "b" * 80}, "c": {"text": "c"}}
    assert module.groups_for(rows, 20) == [["a"], ["b"], ["c"]]
    assert rows["b"]["text"] == "b" * 80


def test_context_contains_entire_adjacent_original_and_never_makes_it_a_target():
    parent = fixture()
    value = module.build(sha(canonical(parent)), parent)
    context = module.context_for(value, ["O00001"])
    assert set(context["sources"]) == {"O00001"}
    assert context["context_sources"]["O00000"]["text"] == parent["originals"][0]["text"]


@pytest.mark.parametrize("change", ["text", "allocation", "parent"])
def test_changed_records_or_allocation_are_rejected(change):
    parent = fixture()
    value = module.build(sha(canonical(parent)), parent)
    if change == "text":
        value["originals"][0]["text"] += "changed"
    elif change == "allocation":
        value["pages"][0].pop()
    else:
        value["parent_inventory_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        module.validate(value, parent)


def test_incomplete_processing_groups_cannot_compile_a_finished_ledger():
    parent = fixture()
    value = module.build(sha(canonical(parent)), parent)
    with pytest.raises(ValueError, match="every_group_finished"):
        module.compile_ledger(value, "d" * 32, [])


def test_negative_review_is_preserved_in_compiled_ledger():
    parent = fixture()
    value = module.build(sha(canonical(parent)), parent)
    refs = value["pages"][0]
    candidate = {"points": [], "excluded_sources": [{"reference": ref, "reason": "Synthetic non-teaching fixture"} for ref in refs]}
    audit = {"reviews": [], "missing_points": [], "excluded_sources": [
        {"reference": ref, "valid": False, "reason": "Synthetic retained dispute"} for ref in refs]}
    result = module.compile_ledger(value, "d" * 32, [(candidate, audit)])
    assert result["state"] == "ledger_requires_semantic_repair"
    assert result["issues"] and not result["automatic_local_evidence_review_passed"]
    assert set(result["source_dispositions"]) == set(value["sources"])


def test_review_point_keys_match_the_required_schema_without_modifying_candidate():
    candidate = {"points": [{"label": "Synthetic point"}], "excluded_sources": []}
    original = copy.deepcopy(candidate)
    context = {"sources": {"O00000": {"text": "Synthetic text"}}, "context_sources": {}}
    result = module.review_context(context, candidate)
    assert result["candidate"]["points"] == {"0": candidate["points"][0]}
    assert result["sources"] == context["sources"]
    assert candidate == original


def test_finished_group_reuse_rejects_a_different_actual_request(tmp_path, monkeypatch):
    class CachedJob:
        root = tmp_path

        def stage_input(self, *args, **kwargs):
            return {}, "a" * 64

        def stage(self, *args, **kwargs):
            return {"synthetic": True}

    (tmp_path / "cached.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module.editor.e, "checked_cache", lambda *args: ({"synthetic": True}, "b" * 64))
    with pytest.raises(ValueError, match="actual_stage_request_binding"):
        module.checked_stage(CachedJob(), "cached", "prompt", {}, {}, {}, lambda row: row, cached_only=True)


def test_completed_group_never_regenerates_a_missing_cached_stage(tmp_path):
    class CachedJob:
        root = tmp_path

        def stage_input(self, *args, **kwargs):
            pytest.fail("Missing completed stages must stop before generating any new request")

    with pytest.raises(ValueError, match="completed_group_stage_missing"):
        module.checked_stage(CachedJob(), "missing", "prompt", {}, {}, {}, lambda row: row, cached_only=True)


def test_unresolved_review_stops_after_the_one_bound_semantic_repair(tmp_path, monkeypatch):
    refs = {"O00000": {"kind": "speech", "text": "Synthetic source"}}
    candidate = {"points": [], "excluded_sources": [{"reference": "O00000", "reason": "Synthetic exclusion"}]}
    audit = {"reviews": [], "missing_points": [], "excluded_sources": [
        {"reference": "O00000", "valid": False, "reason": "Synthetic unresolved judgment"}]}
    calls = []

    def stage(job, name, prompt, schema, context, selected, normalize, *, cached_only=False):
        calls.append(name)
        return (audit if name.endswith("review") else candidate), tmp_path / (name + ".json")

    monkeypatch.setattr(module, "checked_stage", stage)
    data, used = module.process_group(None, None, {"sources": refs}, refs, "group-0000")
    assert len(calls) == len(used) == 4
    assert calls[-1] == "group-0000-repair-review"
    assert data["audit"] == audit and not module.audit_passed(data["audit"])


@pytest.mark.parametrize("message", ["Synthetic private words: never public", "SyntheticPrivateIdentifier"])
def test_arbitrary_failure_text_is_only_hashed(message):
    status = module.failure_status(ValueError(message))
    assert status["state"] == "stopped" and status["reason_code"] is None
    assert status["error_sha256"] == sha(message.encode())


def test_machine_coded_disk_pause_preserves_safe_reason_and_pause_state():
    status = module.failure_status(InterruptedError("textbook_32gib_disk_reserve_reached"))
    assert status["state"] == "paused"
    assert status["reason_code"] == "textbook_32gib_disk_reserve_reached"
