import copy
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_ready_first_coverage as m


@pytest.fixture
def completed():
    key = "a" * 32
    inventory, private, book = {"sources": {"S0": {"text": "Synthetic complete source"}}}, {"full": "Synthetic private article"}, {"body": "Synthetic book"}
    actual = {"summary": {"job": key, "inventory": m.digest(inventory), "verified_units": 4, "total_units": 4,
        "model_calls": 0, "unresolved_issues": 3}, "private": private, "inventory": inventory, "book": book,
        "working": {"points": ["Synthetic complete obligation"]}, "issues": ["Synthetic unresolved"] * 3,
        "proof": {"files": {}, "private_article_sha256": m.digest(private), "article_sha256": m.digest(book)}}
    restore = {"state": "ready_guard_exit_inputs_independently_restored", "first_manuscript_fully_reconstructed": True,
        "first_writer": key, "first_proof_sha256": m.digest(actual["proof"]), "first_verified_units": 4,
        "first_total_units": 4, "first_unresolved_issues": 3, "original_root_reads": 0, "model_calls": 0}
    guard = {"state": "worker_exited", "returncode": 0, "pid": 999999999, "created": 0}
    return key, actual, restore, guard


def test_requires_entire_same_actual_restored_manuscript_and_retains_every_issue(completed):
    key, actual, restore, guard = completed
    before = copy.deepcopy(completed)
    m.check_complete(actual, key, actual["proof"], restore, guard)
    assert completed == before


@pytest.mark.parametrize("change", ["prefix", "missing_body", "another_restore", "cleared_issues", "live_guard", "failed_guard"])
def test_incomplete_unrestored_or_running_results_cannot_receive_first_coverage(completed, monkeypatch, change):
    key, actual, restore, guard = completed
    if change == "prefix":
        actual["summary"]["verified_units"] = 3
    elif change == "missing_body":
        actual["private"] = None
    elif change == "another_restore":
        restore["first_proof_sha256"] = "different"
    elif change == "cleared_issues":
        actual["issues"].pop()
    elif change == "live_guard":
        monkeypatch.setattr(m.first.coverage.writing, "identity_live", lambda row: True)
    else:
        guard["returncode"] = 1
    with pytest.raises(ValueError):
        m.check_complete(actual, key, actual["proof"], restore, guard)


def test_scoped_provider_restores_frozen_original_functions_after_failure(monkeypatch):
    parent, binding = m.first.parent, m.first.bindings
    monkeypatch.setattr(m, "manuscript", lambda *args: {"synthetic": True})
    with pytest.raises(RuntimeError):
        with m.installed():
            assert m.first.parent("root", "key", m.PROVIDER, "batch") == {"synthetic": True}
            assert m.first.bindings()["original"] == binding()
            raise RuntimeError("Synthetic exception")
    assert m.first.parent is parent
    assert m.first.bindings is binding


def test_other_original_providers_still_use_the_unchanged_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(m.first, "parent", lambda *args: calls.append(args) or "synthetic_original")
    with m.installed():
        assert m.first.parent("root", "key", "index-reviewed", None) == "synthetic_original"
    assert calls == [("root", "key", "index-reviewed", None)]


def test_existing_first_coverage_budget_rejected_before_snapshot_or_any_role(tmp_path, monkeypatch):
    key = "a" * 32
    target = tmp_path / "wiki/textbook-whole-authoring-jobs" / key / m.first.coverage.CONTRACT
    m.retained(target, {"original": "synthetic_existing_budget"})
    monkeypatch.setattr(m.guard, "checkpoint", lambda root: None)
    monkeypatch.setattr(m, "capture", lambda *args: pytest.fail("Existing budget must not be recaptured"))
    with pytest.raises(ValueError, match="no_existing_coverage_budget"):
        m.prepare(tmp_path, key, "b" * 32, tmp_path / "tokenizer")
    assert m.whole.read(target) == {"original": "synthetic_existing_budget"}
    assert not m.snapshot_path(tmp_path, key).exists()


def test_snapshot_load_checks_complete_source_body_and_every_original_byte(tmp_path, completed):
    key, actual, _, _ = completed
    batch = "b" * 32
    source = tmp_path / "wiki/original-proof.json"
    m.retained(source, {"synthetic": "original bytes"})
    actual["proof"]["files"] = {source.relative_to(tmp_path).as_posix(): m.whole.sha(source.read_bytes())}
    value = {"policy": m.POLICY, "parent_writer": key, "batch": batch, "code": m.bindings(),
        "manuscript": actual, "manuscript_sha256": m.digest(actual)}
    m.retained(m.snapshot_path(tmp_path, key), value)
    assert m.manuscript(tmp_path, key, batch) == actual
    source.write_text("Changed synthetic bytes", encoding="utf-8")
    with pytest.raises(ValueError, match="all_original_authenticated_bytes_unchanged"):
        m.manuscript(tmp_path, key, batch)


def test_snapshot_cannot_change_body_while_updating_only_the_outer_hash(tmp_path, completed):
    key, actual, _, _ = completed
    batch = "b" * 32
    actual["private"] = {"changed": "Synthetic altered private body"}
    m.retained(m.snapshot_path(tmp_path, key), {"policy": m.POLICY, "parent_writer": key, "batch": batch,
        "code": m.bindings(), "manuscript": actual, "manuscript_sha256": m.digest(actual)})
    with pytest.raises(ValueError, match="complete_inventory_body_and_all_issues"):
        m.manuscript(tmp_path, key, batch)


def test_missing_independent_restore_prevents_worker_before_any_coverage_role(tmp_path, monkeypatch):
    bundle = SimpleNamespace(folder=tmp_path, contract={"parent_writer": "a" * 32},
        job=SimpleNamespace(key="b" * 32), plan={"complete": "Synthetic full plan"})
    monkeypatch.setattr(m, "load", lambda *args, **kwargs: bundle)
    monkeypatch.setattr(m.first, "run", lambda *args: pytest.fail("No model role before independent input restore"))
    with pytest.raises(FileNotFoundError):
        m.worker(tmp_path, "a" * 32, tmp_path / "tokenizer")
    m.retained(tmp_path / "independent-input-restore.json", {"state": "wrong_future_result"})
    with pytest.raises(ValueError, match="actual_independent_full_input_restore"):
        m.worker(tmp_path, "a" * 32, tmp_path / "tokenizer")
