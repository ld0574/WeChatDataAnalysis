import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_ledger_pilot as module
from wechat_decrypt_tool.course_library import canonical


def batch_fixture(tmp_path, monkeypatch, *, always_negative=False):
    sources = {f"O{n:05}": {"kind": "speech", "text": "Literal long original\r\n" + str(n) * 18001} for n in range(3)}
    inventory = {"sources": sources, "pages": [[ref] for ref in sources]}
    cache, generated = {}, []

    def checked(job, name, prompt, schema, context, selected, normalize, *, cached_only=False):
        assert context["sources"] == {ref: {"kind": row["kind"], "text": row["text"]} for ref, row in selected.items()}
        assert all(row["text"] == sources[ref]["text"] for ref, row in selected.items())
        if cached_only and name not in cache:
            raise ValueError("whole_sources_completed_group_stage_missing")
        if name not in cache:
            generated.append((job, name))
            if name.endswith("review"):
                valid = not always_negative and not (name == "group-0001-review")
                data = {"reviews": {}, "missing_points": [], "excluded_sources": {
                    ref: {"valid": valid, "reason": "Synthetic independent judgment"} for ref in selected}}
            else:
                data = {ref: {"kind": "non_teaching", "reason": "Synthetic non-teaching scope"} for ref in selected}
            cache[name] = data
        return normalize(cache[name]), tmp_path / (name + ".json")

    monkeypatch.setattr(module.whole, "checked_stage", checked)
    return inventory, cache, generated


def test_three_intact_groups_are_scheduled_by_role_and_reuse_exact_cached_stages(tmp_path, monkeypatch):
    inventory, _, calls = batch_fixture(tmp_path, monkeypatch)
    original = copy.deepcopy(inventory)
    outputs = module.process_batch("extractor", "reviewer", inventory, [0, 1, 2])
    assert inventory == original and list(outputs) == [0, 1, 2]
    assert calls == [("extractor", f"group-{n:04}-extract") for n in range(3)] + [
        ("reviewer", f"group-{n:04}-review") for n in range(3)] + [
        ("extractor", "group-0001-repair"), ("reviewer", "group-0001-repair-review")]
    assert all(module.whole.audit_passed(value[0]["audit"]) for value in outputs.values())
    before = len(calls)
    assert module.process_batch("extractor", "reviewer", inventory, [0, 1, 2]) == outputs
    assert len(calls) == before


def test_remaining_negative_reviews_keep_one_repair_budget_and_are_not_reasked(tmp_path, monkeypatch):
    inventory, _, calls = batch_fixture(tmp_path, monkeypatch, always_negative=True)
    outputs = module.process_batch("extractor", "reviewer", inventory, [0, 1, 2])
    assert len(calls) == 12
    assert all(not module.whole.audit_passed(value[0]["audit"]) for value in outputs.values())
    module.process_batch("extractor", "reviewer", inventory, [0, 1, 2])
    assert len(calls) == 12


def test_missing_completed_stage_is_not_regenerated_under_a_new_attempt(tmp_path, monkeypatch):
    inventory, cache, calls = batch_fixture(tmp_path, monkeypatch)
    module.process_batch("extractor", "reviewer", inventory, [0, 1, 2])
    before = list(calls)
    del cache["group-0000-extract"]
    with pytest.raises(ValueError, match="completed_group_stage_missing"):
        module.process_batch("extractor", "reviewer", inventory, [0, 1, 2], cached_groups={0, 1, 2})
    assert calls == before


def test_pause_stops_the_next_stage_without_silently_skipping_a_group(tmp_path, monkeypatch):
    inventory, _, calls = batch_fixture(tmp_path, monkeypatch)
    checks = []

    def checkpoint():
        checks.append(True)
        if len(checks) == 3:
            raise InterruptedError("textbook_paused_progress_preserved")

    with pytest.raises(InterruptedError):
        module.process_batch("extractor", "reviewer", inventory, [0, 1, 2], checkpoint=checkpoint)
    assert [name for _, name in calls] == ["group-0000-extract", "group-0001-extract"]


def ledger_fixture(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    tokenizer = tmp_path / "tokenizer.json"
    tokenizer.write_text("{}", "utf-8")
    parent = module.evidence.inventory({"id": "a" * 24, "source_export_sha256": "b" * 64,
        "evidence": {str(n): {"kind": "speech", "text": str(n) * 12000, "start": n, "end": n + 1} for n in range(4)}}, lesson_artifact="c" * 64)
    parent_key = module.whole.sha(canonical(parent))
    inventory = module.whole.build(parent_key, parent)
    inventory["throughput_trial"] = {"policy": module.POLICY, "role_batch_groups": module.BATCH_GROUPS,
        "module_sha256": module.whole.sha(module.Path(module.__file__).read_bytes()), "all_courseware_still_requires_specific_association": True}
    inventory.update(group_character_limit=module.GROUP_CHARACTERS, pages=module.whole.groups_for(inventory["sources"], module.GROUP_CHARACTERS))
    module.whole.validate(inventory, parent)
    monkeypatch.setattr(module.whole, "local_model_digest", lambda model: "d" * 64 if model == "qwen3.5:9b" else "e" * 64)
    binding = module.make_binding(inventory, tokenizer)
    key = module.whole.sha(canonical(binding))[:32]
    folder = root / "wiki/textbook-keyed-jobs" / key
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"id": key, "binding": binding}), "utf-8")
    parent_folder = root / "wiki/textbook-evidence" / parent_key
    parent_folder.mkdir(parents=True)
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path / "user")
    monkeypatch.setattr(module.whole, "load", lambda *args: inventory)
    monkeypatch.setattr(module.whole, "TextbookStore", lambda path: SimpleNamespace(root=path, checkpoint=lambda: None))

    def job(store, inventory, binding, model, tokenizer):
        target = store.root / "fake-jobs" / model.replace(":", "-")
        target.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(root=target, key=model)

    monkeypatch.setattr(module.whole, "Job", job)

    def results(extractor, reviewer, inventory, numbers, **kwargs):
        out = {}
        for n in numbers:
            refs = inventory["pages"][n]
            candidate = {"points": [], "excluded_sources": [{"reference": ref, "reason": "Synthetic exclusion"} for ref in refs]}
            audit = {"reviews": [], "missing_points": [], "excluded_sources": [{"reference": ref, "valid": True, "reason": "Synthetic independent audit"} for ref in refs]}
            out[n] = ({"candidate": candidate, "audit": audit}, [])
        return out

    monkeypatch.setattr(module, "process_batch", results)
    return root, inventory, binding, folder, tokenizer, parent_folder


def test_partial_trial_never_produces_a_complete_ledger_or_reader_payload(tmp_path, monkeypatch):
    root, inventory, _, folder, tokenizer, _ = ledger_fixture(tmp_path, monkeypatch)
    result = module.run(root, module.whole.sha(canonical(inventory)), tokenizer)
    assert result["completed_groups"] == 3 and result["full_lesson_groups"] == 4
    assert result["partial_trial_only"] and not result["speedup_proven"]
    assert not (folder / "ledger.json").exists() and not (folder / "result.json").exists()
    assert not result["reader_article_published"] and not result["source_deletion_allowed"]


def test_completion_reuses_the_same_execution_contract_and_preserves_trial_result(tmp_path, monkeypatch):
    root, inventory, binding, folder, tokenizer, parent = ledger_fixture(tmp_path, monkeypatch)
    inventory_key = module.whole.sha(canonical(inventory))
    module.run(root, inventory_key, tokenizer)
    trial_before = (folder / "trial-result.json").read_bytes()
    contract = parent / "whole-source-ledger-execution.json"
    before = contract.read_bytes()
    result = module.run(root, inventory_key, tokenizer, complete=True)
    assert result["completed_groups"] == result["full_lesson_groups"] == 4
    assert contract.read_bytes() == before and json.loads(before) == binding
    assert (folder / "trial-result.json").read_bytes() == trial_before
    ledger = json.loads((folder / "ledger.json").read_bytes())["data"]
    module.whole.validate_result(ledger, inventory, result["job"])
    assert set(ledger["source_dispositions"]) == set(inventory["sources"])
    assert not ledger["reader_article_published"]
