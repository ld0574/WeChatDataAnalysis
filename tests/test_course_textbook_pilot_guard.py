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
