import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_pilot_guard as module


def fixture(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    binding = {"synthetic": "unchanged prepared execution"}
    key = module.pilot.whole.sha(module.pilot.canonical(binding))[:32]
    folder = root / "wiki/textbook-keyed-jobs" / key
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"id": key, "binding": binding}), "utf-8")
    monkeypatch.setattr(module.pilot.whole, "TextbookStore", lambda root: SimpleNamespace(checkpoint=lambda: None))
    monkeypatch.setattr(module.pilot.whole, "load", lambda *args: {})
    monkeypatch.setattr(module.pilot, "make_binding", lambda *args: binding)
    return root


def test_supported_guard_keeps_original_worker_budget_and_thermal_limits(tmp_path, monkeypatch):
    root = fixture(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(module, "guarded", lambda *args, **kwargs: calls.append((args, kwargs)))
    module.run(root, "a" * 64, tmp_path / "tokenizer.json")
    (args, kwargs), = calls
    assert "wechat_decrypt_tool.course_textbook_ledger_pilot" in args[1]
    assert args[1][-1] == "worker" and "--complete" not in args[1]
    assert kwargs["guard_name"] == "review-experiment-thermal-guard"
    assert kwargs["maximum_temperature"] == 70 and kwargs["maximum_power"] == 200
    assert "resume" not in kwargs


def test_live_previous_guard_is_preserved(tmp_path, monkeypatch):
    root = fixture(tmp_path, monkeypatch)
    prior = root / "review-experiment-thermal-guard.json"
    prior.write_text('{"pid":123,"created":456}', "utf-8")
    before = prior.read_bytes()
    monkeypatch.setattr(module.pilot, "whole_identity_live", lambda value: True)
    monkeypatch.setattr(module, "guarded", lambda *args, **kwargs: pytest.fail("must not restart a live guard"))
    with pytest.raises(ValueError, match="prior_experiment_guard_exited"):
        module.run(root, "a" * 64, tmp_path / "tokenizer.json")
    assert prior.read_bytes() == before


def test_continuation_checks_trial_before_launch_and_keeps_same_worker(tmp_path, monkeypatch):
    root = fixture(tmp_path, monkeypatch)
    calls, checks = [], []
    monkeypatch.setattr(module, "trial_ready", lambda *args: checks.append(args))
    monkeypatch.setattr(module, "guarded", lambda *args, **kwargs: calls.append((args, kwargs)))
    module.run(root, "a" * 64, tmp_path / "tokenizer.json", complete=True)
    assert len(checks) == 1 and checks[0][0] == root
    command = calls[0][0][1]
    assert "wechat_decrypt_tool.course_textbook_ledger_pilot" in command
    assert command[-2:] == ["--complete", "worker"]


def test_failed_trial_guard_prevents_continuation(tmp_path, monkeypatch):
    root = fixture(tmp_path, monkeypatch)
    prior = root / "review-experiment-thermal-guard.json"
    prior.write_text('{"pid":123,"created":456,"state":"worker_exited","returncode":1}', "utf-8")
    monkeypatch.setattr(module.pilot, "whole_identity_live", lambda value: False)
    monkeypatch.setattr(module, "guarded", lambda *args, **kwargs: pytest.fail("failed trial must not continue"))
    with pytest.raises(ValueError, match="trial_guard_success_required"):
        module.run(root, "a" * 64, tmp_path / "tokenizer.json", complete=True)


def trial_fixture(tmp_path):
    inventory = {"sources": {"O00000": {"kind": "speech", "text": "Synthetic full original"}}, "pages": [["O00000"]]}
    key = "a" * 32
    folder = tmp_path / "wiki/textbook-keyed-jobs" / key
    folder.mkdir(parents=True)
    stage = folder / "actual-stage.json"
    stage.write_text('{"synthetic":"retained response"}', "utf-8")
    candidate = {"points": [], "excluded_sources": [{"reference": "O00000", "reason": "Synthetic nonteaching"}]}
    audit = {"reviews": [], "missing_points": [], "excluded_sources": [{"reference": "O00000", "valid": True,
        "reason": "Synthetic independent check"}]}
    data = {"candidate": candidate, "audit": audit}
    group = {"data": data, "data_sha256": module.pilot.whole.sha(module.pilot.canonical(data)),
        "input_sha256": module.pilot.whole.sha(module.pilot.canonical(module.pilot.whole.context_for(inventory, ["O00000"]))),
        "stage_files": {str(stage): module.pilot.whole.sha(stage.read_bytes())}}
    (folder / "group-0000.json").write_text(json.dumps(group), "utf-8")
    (folder / "trial-result.json").write_text(json.dumps({"job": key,
        "inventory": module.pilot.whole.sha(module.pilot.canonical(inventory)),
        "state": "first_role_batch_completed_quality_and_throughput_review_pending", "completed_groups": 1,
        "unresolved_groups": 0, "reader_article_published": False}), "utf-8")
    return inventory, key, folder, stage, group


def test_actual_trial_gate_preserves_files(tmp_path):
    inventory, key, folder, _, _ = trial_fixture(tmp_path)
    before = {p: p.read_bytes() for p in folder.iterdir()}
    module.trial_ready(tmp_path, key, inventory)
    assert all(p.read_bytes() == raw for p, raw in before.items())


@pytest.mark.parametrize("changed", ["source", "stage", "missing_stage", "audit", "already_finished"])
def test_trial_gate_rejects_changed_or_negative_completed_groups(tmp_path, changed):
    inventory, key, folder, stage, group = trial_fixture(tmp_path)
    if changed == "source":
        inventory["sources"]["O00000"]["text"] = "Changed complete original"
    elif changed == "stage":
        stage.write_text("changed", "utf-8")
    elif changed == "missing_stage":
        stage.unlink()
    elif changed == "audit":
        group["data"]["audit"]["excluded_sources"][0]["valid"] = False
        group["data_sha256"] = module.pilot.whole.sha(module.pilot.canonical(group["data"]))
        (folder / "group-0000.json").write_text(json.dumps(group), "utf-8")
    else:
        (folder / "ledger.json").write_text("{}", "utf-8")
    with pytest.raises(ValueError):
        module.trial_ready(tmp_path, key, inventory)
