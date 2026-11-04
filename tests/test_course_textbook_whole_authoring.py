import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_whole_authoring as module
from wechat_decrypt_tool.course_library import canonical


class CharacterTokenizer:
    def encode(self, text):
        return SimpleNamespace(ids=range(len(text)))


def fixture(text="Synthetic complete source", count=1):
    inventory = {"sources": {"O00000": {"kind": "frame", "text": text}},
        "attachment_scope": {"unresolved": True, "selected_notebooks": []}}
    points = {f"P{i:04d}": {"label": f"Synthetic point {i}", "teaching": "Synthetic teaching",
        "category": "concept", "citations": ["O00000"]} for i in range(count)}
    ledger = {"points": points, "pending_source_cards": {}}
    plan = {"chapters": ["N"], "nodes": {"N": {"heading": "Synthetic chapter", "purpose": "Synthetic purpose",
        "children": list(reversed(points))}}}
    return inventory, ledger, plan


def test_full_original_longer_than_old_limit_reaches_writer_unchanged():
    text = "def synthetic():\r\n    return 'literal whitespace'\n" + "x" * 12500
    inventory, ledger, plan = fixture(text)
    before = copy.deepcopy(inventory)
    units = module.writing_units(plan, ledger, inventory, CharacterTokenizer())
    context = module.writing_context(units[0]["point_ids"], ledger, inventory)
    assert len(text) > 10000
    assert context["sources"]["O00000"]["text"] == text
    assert inventory == before


def test_oversized_full_context_stops_instead_of_truncating_original():
    inventory, ledger, plan = fixture("x" * 20000)
    with pytest.raises(ValueError, match="complete_single_point_context_fits_no_truncation"):
        module.writing_units(plan, ledger, inventory, CharacterTokenizer())
    assert inventory["sources"]["O00000"]["text"] == "x" * 20000


def test_units_preserve_concept_order_and_every_assigned_point_once():
    inventory, ledger, plan = fixture(count=module.SECTION_POINTS + 1)
    units = module.writing_units(plan, ledger, inventory, CharacterTokenizer())
    assert len(units) == 2
    assert [key for unit in units for key in unit["point_ids"]] == list(reversed(ledger["points"]))


def test_reading_review_retains_large_sections_and_each_complete_boundary():
    units, sections = [], {}
    for index in range(3):
        key = f"s{index}"
        units.append({"id": key, "chapter": {"heading": "Synthetic chapter"}})
        sections[key] = {"heading": "Synthetic section", "paragraphs": [{"text": str(index) * 16000}],
            "diagrams": [], "quiz": {"question": "Synthetic question", "citations": ["O00000"]}}
    blocks = module.reading_blocks(units, sections, CharacterTokenizer())
    assert len(blocks) == 2
    assert any({"s0", "s1"} <= set(block) for block in blocks)
    assert any({"s1", "s2"} <= set(block) for block in blocks)
    for block in blocks:
        for key, row in block.items():
            assert row["paragraphs"] == [sections[key]["paragraphs"][0]["text"]]


def test_read_only_ledger_reconstruction_never_invokes_model_stage(tmp_path):
    class OriginalJob:
        root = tmp_path

        def stage_input(self, *args, **kwargs):
            return {}, "a" * 64

        def stage(self, *args, **kwargs):
            pytest.fail("Verification must never call the original model stage")

    data = {"synthetic": True}
    path = tmp_path / "retained.json"
    path.write_text(json.dumps({"input_sha256": "a" * 64, "data": data,
        "data_sha256": module.whole.sha(canonical(data))}), encoding="utf-8")
    original = path.read_bytes()
    assert module.ReadOnlyJob(OriginalJob()).stage("retained", "prompt", {}, {}, lambda row: None) == data
    assert path.read_bytes() == original


def test_read_only_ledger_reconstruction_rejects_different_request(tmp_path):
    original = SimpleNamespace(root=tmp_path, stage_input=lambda *args, **kwargs: ({}, "b" * 64))
    data = {"synthetic": True}
    (tmp_path / "retained.json").write_text(json.dumps({"input_sha256": "a" * 64, "data": data,
        "data_sha256": module.whole.sha(canonical(data))}), encoding="utf-8")
    with pytest.raises(ValueError, match="unchanged_actual_ledger_request"):
        module.ReadOnlyJob(original).stage("retained", "prompt", {}, {}, lambda row: None)


def role_job(tmp_path):
    job = module.AuthoringJob.__new__(module.AuthoringJob)
    job.root, job.model, job.model_digest = tmp_path, "qwen3.5:9b", "a" * 64
    job.models = {"qwen3.5:9b": "a" * 64, "course-defiant:q4": "b" * 64}
    job.stage_input = lambda *args, **kwargs: ({}, job.model_digest)
    return job


def test_writer_and_reviewer_use_independent_pinned_models(tmp_path, monkeypatch):
    job, calls = role_job(tmp_path), []

    def generate(self, *args, **kwargs):
        calls.append((self.model, self.model_digest))
        return {"synthetic": True}

    monkeypatch.setattr(module.whole.Job, "stage", generate)
    monkeypatch.setattr(module.whole.editor.e, "checked_cache", lambda *args: ({"synthetic": True}, job.model_digest))
    job.stage("section-0000-write", module.author.WRITE_PROMPT, {}, {}, lambda row: None)
    job.stage("section-0000-review", module.author.REVIEW_PROMPT, {}, {}, lambda row: None)
    assert calls == [("qwen3.5:9b", "a" * 64), ("course-defiant:q4", "b" * 64)]
    assert job.model == "qwen3.5:9b" and job.model_digest == "a" * 64
    assert module.whole.read(tmp_path / "section-0000-review.proof.json")["model"] == "course-defiant:q4"


def test_failed_review_restores_writer_identity_and_creates_no_positive_proof(tmp_path, monkeypatch):
    job = role_job(tmp_path)

    def fail(*args, **kwargs):
        raise ValueError("requires_synthetic_failure")

    monkeypatch.setattr(module.whole.Job, "stage", fail)
    with pytest.raises(ValueError, match="requires_synthetic_failure"):
        job.stage("section-0000-review", module.author.REVIEW_PROMPT, {}, {}, lambda row: None)
    assert job.model == "qwen3.5:9b" and job.model_digest == "a" * 64
    assert not (tmp_path / "section-0000-review.proof.json").exists()


def test_actual_context_budget_accounts_for_reserved_output_and_rejects_overflow(tmp_path):
    job = role_job(tmp_path)
    job.tokenizer = CharacterTokenizer()
    count, output, capacity = job.budget([{"content": "x" * 50000}], "section-0000-write")
    assert count + output < capacity <= 65536
    with pytest.raises(ValueError, match="actual_complete_stage_context_fits"):
        job.budget([{"content": "x" * 66000}], "section-0000-write")


def test_section_reviews_do_not_publish_article_or_erase_prior_negative_findings():
    negative = [{"kind": "section_semantic_review", "review": {"passed": False}}]
    original = copy.deepcopy(negative)
    result = module.final_issues({"attachment_scope": {"unresolved": True}},
        {"chapters": [{"sections": [{"diagrams": []}]}]}, negative)
    assert negative == original
    assert negative[0] in result
    assert {row["kind"] for row in result} >= {"complete_original_source_to_actual_article_review_required",
        "item_specific_courseware_reference_and_visual_scope_required", "missing_supported_explanatory_diagram"}


def pending(root, state="preparing_complete_original_knowledge"):
    path = root / "wiki/whole-source-ledger-current.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"state": state, "job": "a" * 32, "inventory": "b" * 64, "pid": 123,
        "created": 1.0, "processing_groups": 25, "completed_groups": 0}
    module.atomic_json(path, row)
    return path, row


def test_waiter_follows_the_exact_live_worker_until_confirmed_terminal_success(tmp_path):
    path, row = pending(tmp_path)
    slept = []

    def live(identity):
        return identity["pid"] == 123 and not slept

    def finish(seconds):
        slept.append(seconds)
        module.atomic_json(path, {**row, "state": "ledger_ready_for_conceptual_reorganization", "completed_groups": 25})
        module.atomic_json(tmp_path / "wiki-revision-thermal-guard.json",
            {"state": "worker_exited", "returncode": 0, "pid": 456, "created": 2.0})

    module.wait_for_ledger(tmp_path, "b" * 64, "a" * 32, live=live, sleep=finish, checkpoint=lambda: None)
    assert slept == [10]
    status = module.whole.read(tmp_path / "wiki/whole-source-authoring-dispatch-current.json")
    assert status["model_calls_started_by_dispatcher"] == 0
    assert status["ledger_worker"] == {"pid": 123, "created": 1.0}


def test_missing_worker_with_running_status_is_not_treated_as_success_or_restarted(tmp_path):
    pending(tmp_path)
    with pytest.raises(ValueError, match="missing_worker_without_terminal_success"):
        module.wait_for_ledger(tmp_path, "b" * 64, "a" * 32, live=lambda row: False, checkpoint=lambda: None)


def test_completed_negative_ledger_can_be_composed_without_resetting_its_result(tmp_path):
    path, _ = pending(tmp_path, "ledger_requires_semantic_repair")
    original = path.read_bytes()
    module.atomic_json(tmp_path / "wiki-revision-thermal-guard.json",
        {"state": "worker_exited", "returncode": 0, "pid": 456, "created": 2.0})
    module.wait_for_ledger(tmp_path, "b" * 64, "a" * 32, live=lambda row: False, checkpoint=lambda: None)
    assert path.read_bytes() == original


def test_waiter_preserves_pause_and_never_automatically_resumes(tmp_path):
    pending(tmp_path)

    def paused():
        raise InterruptedError("textbook_paused_progress_preserved")

    with pytest.raises(InterruptedError, match="textbook_paused_progress_preserved"):
        module.wait_for_ledger(tmp_path, "b" * 64, "a" * 32, live=lambda row: True, checkpoint=paused)


def test_reused_pid_cannot_start_authoring_for_a_different_worker(tmp_path):
    path, row = pending(tmp_path)

    def replace(seconds):
        module.atomic_json(path, {**row, "created": 99.0})

    with pytest.raises(ValueError, match="unchanged_pending_worker_identity"):
        module.wait_for_ledger(tmp_path, "b" * 64, "a" * 32, live=lambda row: True, sleep=replace, checkpoint=lambda: None)


def test_disputed_claims_are_quarantined_and_complete_originals_become_explicit_tasks():
    inventory, initial, _ = fixture("Synthetic complete original")
    inventory["sources"]["O00001"] = {"kind": "speech", "text": "Synthetic adjacent original"}
    initial["points"]["P0000"]["citations"] = ["O00000", "O00001"]
    ledger = {"job": "a" * 32, "points": initial["points"], "pages": [{"page": 0, "points": ["P0000"]}],
        "issues": [{"group": 0, "review": {"reviews": [], "missing_points": [],
            "excluded_sources": [{"reference": "O00000", "valid": False, "reason": "Synthetic dispute"}]}}]}
    original = copy.deepcopy(ledger)
    working = module.working_inputs(inventory, ledger)
    assert ledger == original
    assert working["quarantined_point_keys"] == ["P0000"]
    assert working["confirmed_point_count"] == 0 and len(working["pending_source_cards"]) == 2
    assert working["inherited_issues"] == original["issues"]
    assert not working["source_revision_budget_refreshed"]
    for key, card in working["pending_source_cards"].items():
        assert working["points"][key]["teaching"] == inventory["sources"][card["reference"]]["text"]
    context = module.writing_context(list(working["points"]), working, inventory)
    assert context["pending_cards_are_verified_facts"] is False


def test_supported_ledger_stays_free_of_synthetic_pending_tasks():
    inventory, initial, _ = fixture()
    ledger = {"job": "a" * 32, "points": initial["points"], "pages": [{"points": ["P0000"]}], "issues": []}
    working = module.working_inputs(inventory, ledger)
    assert working["confirmed_point_count"] == 1
    assert working["points"] == ledger["points"]
    assert not working["pending_source_cards"]


def test_pure_non_teaching_tasks_require_no_fabricated_article_or_quiz():
    inventory, _, _ = fixture()
    working = {"points": {"U00000": {"citations": ["O00000"]}},
        "pending_source_cards": {"U00000": {"reference": "O00000", "neighbors": []}}}
    candidate = {"kind": "non_teaching", "coverage": {"U00000": {
        "teaching": False, "paragraphs": [], "reason": "Synthetic administrative source"}}}
    module.validate_section(candidate, ["U00000"], working, inventory)
    assert "paragraphs" not in candidate and "quiz" not in candidate
    assert "coverage" in module.review_schema(candidate, ["U00000"])["properties"]


def test_confirmed_teaching_cannot_be_discarded_as_an_administrative_only_unit():
    inventory, ledger, _ = fixture()
    candidate = {"kind": "non_teaching", "coverage": {"P0000": {
        "teaching": False, "paragraphs": [], "reason": "Synthetic invalid dismissal"}}}
    with pytest.raises(module.jsonschema.ValidationError):
        module.validate_section(candidate, ["P0000"], ledger, inventory)


def test_dispatch_pause_is_persisted_without_private_text_or_automatic_restart(tmp_path, monkeypatch):
    (tmp_path / "pause.flag").write_text("synthetic retained pause", encoding="utf-8")

    def paused(*args, **kwargs):
        raise InterruptedError("textbook_paused_progress_preserved")

    monkeypatch.setattr(module, "wait_for_ledger", paused)
    monkeypatch.setattr(module, "guard", lambda *args: pytest.fail("Paused tasks cannot start the guard"))
    with pytest.raises(InterruptedError):
        module.after_ledger(tmp_path, "b" * 64, "a" * 32, tmp_path / "tokenizer.json")
    status = module.whole.read(tmp_path / "wiki/whole-source-authoring-dispatch-current.json")
    assert status["state"] == "paused"
    assert status["reason_code"] == "textbook_paused_progress_preserved"
    assert len(list((tmp_path / "wiki/whole-authoring-dispatch-failures").glob("*.json"))) == 1
    assert (tmp_path / "pause.flag").read_text("utf-8") == "synthetic retained pause"
