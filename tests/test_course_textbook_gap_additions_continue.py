import copy
import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from test_course_textbook_gap_additions import packet
from wechat_decrypt_tool import course_textbook_gap_additions_continue as module


def test_quotes_are_exact_program_input_and_do_not_change_semantic_decisions():
    value, targets, candidate, review = packet()
    schema = module.original.candidate_schema(value["book"], targets, value["sources"])
    fixed = module.fixed_quotes(schema, targets, value["sources"])
    assert schema != fixed
    assert "const" not in schema["properties"]["targets"]["properties"]["gap1"]["properties"]["source_quote"]
    for ref in targets:
        candidate["targets"][ref]["source_quote"] = value["sources"][ref]["text"][:400]
    jsonschema.validate(candidate, fixed)
    changed = copy.deepcopy(candidate)
    changed["targets"]["gap1"]["source_quote"] = "A fabricated quotation"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(changed, fixed)
    review["paragraphs"]["add000"]["fully_explained"] = False
    assert module.original.resolved(candidate, review) == set()


def test_recovery_binding_cannot_be_replaced_to_refresh_attempts(tmp_path):
    path = tmp_path / "binding.json"
    binding = {"parent": "a" * 32, "repair_slot": "additions-repair", "maximum_structural_attempts_per_slot": 2}
    module.bind_once(path, binding)
    before = path.read_bytes()
    module.bind_once(path, binding)
    with pytest.raises(ValueError, match="frozen_recovery_binding_unchanged"):
        module.bind_once(path, {**binding, "maximum_structural_attempts_per_slot": 3})
    assert path.read_bytes() == before


def test_exhausted_repair_stage_never_enters_gpu_or_resets_files(tmp_path):
    job = object.__new__(module.ContinuationJob)
    job.root = tmp_path
    job.store = SimpleNamespace(checkpoint=lambda: None)
    job.range_notice = ""
    job.max_attempts = 2
    job.budget = lambda *args: None
    job.stage_input = lambda *args, **kwargs: ({}, "frozen")
    attempts = tmp_path / "attempts"
    attempts.mkdir()
    for number in (1, 2):
        (attempts / f"additions-repair-{number:02}.json").write_text(json.dumps({"validation_error": "requires_test"}))
    original = {p.name: p.read_bytes() for p in attempts.iterdir()}
    with pytest.raises(ValueError, match="wiki_stage_attempt_budget_exhausted_additions-repair"):
        job.stage("additions-repair", "test", {}, {}, lambda v: None)
    assert {p.name: p.read_bytes() for p in attempts.iterdir()} == original


@pytest.mark.parametrize("positive", [True, False])
def test_only_existing_repair_and_final_review_slots_are_used(tmp_path, monkeypatch, positive):
    value, targets, candidate, review = packet()
    for ref in targets:
        candidate["targets"][ref]["source_quote"] = value["sources"][ref]["text"][:400]
        review["targets"][ref]["source_quote"] = candidate["targets"][ref]["source_quote"]
    review["paragraphs"]["add000"]["fully_explained"] = positive
    key, writer = "a" * 32, "b" * 32
    folder = tmp_path / "wiki/textbook-gap-addition-jobs" / key
    folder.mkdir(parents=True)
    module.atomic_json(folder / "manifest.json", {"recipe": {"extra": {"writer": writer, "writer_bindings": {}}}})
    monkeypatch.setattr(module, "TextbookStore", lambda _: SimpleNamespace(root=tmp_path, checkpoint=lambda: None))
    monkeypatch.setattr(module, "prepare", lambda *args: (
        value, value["book"], {"reading": {"coherence": {"passed": False}}}, targets, {},
        module.fixed_quotes(module.original.candidate_schema(value["book"], targets, value["sources"]), targets, value["sources"]), {}))
    monkeypatch.setattr(module, "backup_inputs", lambda *args: None)
    calls = []

    class Job:
        def __init__(self, *args):
            self.root, self.key = folder, key

        def stage(self, stage, prompt, schema, context, validate):
            calls.append(stage)
            result = candidate if stage == "additions-repair" else review
            jsonschema.validate(result, schema)
            validate(result)
            return result

        def progress(self, *args, **kwargs):
            pass

    monkeypatch.setattr(module, "ContinuationJob", Job)
    result = module.run(tmp_path, key, Path("unused-tokenizer"))
    assert calls == ["additions-repair", "additions-review-1"]
    assert result["resolved_targets"] == (2 if positive else 0)
    assert result["original_write_budget_reset"] is False
    assert result["semantic_acceptance"] is result["reader_publication_allowed"] is False
    assert result["goal_complete"] is result["source_deletion_allowed"] is False
    assert not (folder / "additions-write.json").exists()
