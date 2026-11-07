from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import psutil
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from wechat_decrypt_tool import course_pdf_ocr
from wechat_decrypt_tool import course_textbook_whole_courseware as module


def inputs():
    member = "a" * 64
    inventory = {"lesson": "synthetic", "lesson_artifact": "b" * 64, "sources": {
        "O00000": {"kind": "speech", "text": "alpha example boundary", "origin": {"start": 0, "end": 10}},
        "O00001": {"kind": "frame", "text": "alpha diagram", "origin": {"start": 5, "end": 6}},
        "O00002": {"kind": "document", "text": "alpha\n" * 1500, "origin": {"member_sha256": member, "cell": 0}},
        "O00003": {"kind": "document", "text": "alpha output", "origin": {"member_sha256": member, "cell": 0}},
        "O00004": {"kind": "document", "text": "alpha condition", "origin": {"member_sha256": member, "cell": 1}},
    }}
    sidecar = {"proof": {"inventory_sha256": module.digest(inventory)}, "visuals": [{"id": "V00000",
        "notebook_sha256": member, "cell": 0, "direct_text_source_ids": ["O00002"], "raw_image_sha256": "c" * 64,
        "existing_ocr": {"text": "Synthetic diagram draft"}}], "unresolved": []}
    return inventory, sidecar


def test_every_complete_item_once_and_visual_drafts_stay_separate():
    inventory, sidecar = inputs()
    plan = module.make_plan(inventory, sidecar)
    allocated = [ref for row in plan["groups"] for ref in row["targets"]]
    assert allocated == ["O00002", "O00003", "O00004"]
    assert plan["text_characters"] == sum(len(row["text"]) for row in inventory["sources"].values() if row["kind"] == "document")
    assert plan["visual_targets_requiring_separate_review"] == 1
    assert plan["whole_notebook_association_verified"] is False
    context = module.context_for(inventory, sidecar, plan["groups"][0])
    assert context["targets"]["O00002"]["text"] == "alpha\n" * 1500
    assert context["context_courseware"]["O00004"] == inventory["sources"]["O00004"]
    assert context["visual_ocr_context"]["V00000"]["semantics_verified"] is False
    assert context["retrieval"]["ranking_establishes_affiliation"] is False


def test_unknown_source_kind_or_changed_visual_parent_stops():
    inventory, sidecar = inputs()
    sidecar["proof"]["inventory_sha256"] = "changed"
    with pytest.raises(ValueError, match="actual_visual_sidecar_inventory"):
        module.make_plan(inventory, sidecar)
    inventory["sources"]["other"] = {"kind": "unhandled", "text": "keep me"}
    sidecar["proof"]["inventory_sha256"] = module.digest(inventory)
    with pytest.raises(ValueError, match="all_inventory_source_kinds_accounted_for"):
        module.make_plan(inventory, sidecar)


def test_packing_reduces_calls_without_changing_any_literal_item_or_context():
    inventory, sidecar = inputs()
    original = module.make_plan(inventory, sidecar)
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    packed = module.pack_plan(inventory, sidecar, original, tokenizer)
    assert len(packed["groups"]) == 1
    assert packed["notebook_cells"] == 2
    assert packed["complete_cell_scopes"] == original["groups"]
    group = packed["groups"][0]
    context = module.context_for(inventory, sidecar, group)
    for cell in original["groups"]:
        literal = module.context_for(inventory, sidecar, cell)
        for key in ("targets", "context_courseware", "classroom_sources", "visual_ocr_context"):
            assert all(context[key][ref] == row for ref, row in literal[key].items())
    assert group["targets"] == [ref for cell in original["groups"] for ref in cell["targets"]]


def test_packing_rejects_oversized_single_scope_without_trimming():
    inventory, sidecar = inputs()
    original = module.make_plan(inventory, sidecar)
    tokenizer = SimpleNamespace(encode=lambda _: SimpleNamespace(ids=[0] * 70000))
    with pytest.raises(ValueError, match="complete_single_cell_scope_fits"):
        module.pack_plan(inventory, sidecar, original, tokenizer)
    assert inventory["sources"]["O00002"]["text"] == "alpha\n" * 1500


def candidate(context, kind="same_lesson_teaching"):
    ids = list(context["classroom_sources"])[:1] if kind in module.KINDS[:2] else []
    return {ref: {"kind": kind, "reason": "Synthetic relation", "lesson_source_ids": ids} for ref in context["targets"]}


def review(proposal):
    return {ref: {"checks": {name: True for name in module.CHECKS[row["kind"]]}, "reason": "Synthetic review"}
        for ref, row in proposal.items()}


@pytest.mark.parametrize("kind", module.KINDS)
def test_each_typed_relation_requires_its_own_independent_checks(kind):
    inventory, sidecar = inputs()
    context = module.context_for(inventory, sidecar, module.make_plan(inventory, sidecar)["groups"][0])
    proposal = candidate(context, kind)
    audit = review(proposal)
    result = module.compile_group(proposal, audit, context)
    assert all(row["kind"] == kind for row in result.values())
    ref = next(iter(proposal))
    audit[ref]["checks"][next(iter(audit[ref]["checks"]))] = False
    result = module.compile_group(proposal, audit, context)
    assert result[ref]["kind"] == "unresolved"
    assert result[ref]["proposal"]["kind"] == kind
    assert result[ref]["article_coverage_verified"] is False


def test_missing_item_unknown_lesson_id_and_empty_positive_support_are_rejected():
    inventory, sidecar = inputs()
    context = module.context_for(inventory, sidecar, module.make_plan(inventory, sidecar)["groups"][0])
    value = candidate(context)
    for fault in ("missing", "unknown", "empty"):
        wrong = copy.deepcopy(value)
        ref = next(iter(wrong))
        if fault == "missing":
            wrong.pop(ref)
        else:
            wrong[ref]["lesson_source_ids"] = ["not-in-context"] if fault == "unknown" else []
        with pytest.raises(jsonschema.ValidationError):
            module.compile_group(wrong, review(value), context)


def test_no_retrieval_match_cannot_be_positive_or_prove_non_teaching():
    context = {"targets": {"D": {"text": "still teaching"}}, "classroom_sources": {}}
    value = {"D": {"kind": "unresolved", "reason": "No evidence", "lesson_source_ids": []}}
    assert module.compile_group(value, review(value), context)["D"]["kind"] == "unresolved"
    value["D"]["kind"] = "same_lesson_teaching"
    with pytest.raises(jsonschema.ValidationError):
        module.compile_group(value, review(value), context)


class Store:
    def __init__(self, root):
        self.root = root

    def checkpoint(self):
        pass


@pytest.fixture
def simulated_job(tmp_path, monkeypatch):
    """Exercise real stage/cache code with explicitly synthetic inference receipts."""
    inventory, sidecar = inputs()
    plan = module.make_plan(inventory, sidecar)
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer_path = tmp_path / "tokenizer.json"
    tokenizer.save(str(tokenizer_path))
    digests = {"qwen3.5:9b": "d" * 64, "course-defiant:q4": "e" * 64}
    config = {"inventory": "f" * 64, "models": digests}
    monkeypatch.setattr(course_pdf_ocr, "local_model_digest", digests.__getitem__)
    calls = []

    @contextmanager
    def slot(self, stage):
        self.progress("generating", stage)
        yield

    def generate(self, messages, *, schema):
        context = json.loads(messages[1]["content"])
        value = review(context["proposal"]) if self.current.endswith("-verify") else candidate(context)
        if getattr(self, "force_invalid", False):
            value = {}
        calls.append({"stage": self.current, "model": self.model, "context": context})
        expected, output, capacity = self.budget(messages, self.current)
        raw = json.dumps(value)
        metrics = {"done_reason": "stop", "size_vram": 1, "requested_context": capacity, "measured_context": capacity,
            "expected_content_tokens": expected, "prompt_eval_count": expected, "reserved_output": output,
            "eval_count": 1, "eval_duration": 1, "model_digest": self.model_digest}
        module.atomic_json(self.root / f"response-{len(calls)}.json", {"stage": self.current,
            "request_sha256": module.digest(messages), "schema_sha256": module.digest(schema),
            "response": {"message": {"content": raw}}, "metrics": metrics})
        return raw, metrics

    monkeypatch.setattr(module.Job, "slot", slot)
    monkeypatch.setattr(module.Job, "generate_response", generate)
    job = module.Job(Store(tmp_path), inventory, sidecar, plan, config, tokenizer_path)
    return SimpleNamespace(job=job, calls=calls, inventory=inventory, sidecar=sidecar,
        plan=plan, config=config, tokenizer=tokenizer_path)


def test_real_role_caches_are_independent_and_reconstruct_without_inference(simulated_job):
    data = simulated_job
    first = module.review_group(data.job, data.inventory, data.sidecar, data.plan["groups"][0])
    assert len(data.calls) == 2
    assert [c["model"] for c in data.calls] == ["qwen3.5:9b", "course-defiant:q4"]
    assert data.calls[1]["context"]["proposal"]
    reader = module.ReadOnlyJob(data.job.store, data.inventory, data.sidecar, data.plan, data.config,
        data.tokenizer, expected_root=data.job.root)
    second = module.review_group(reader, data.inventory, data.sidecar, data.plan["groups"][0])
    assert first == second
    assert len(data.calls) == 2
    (data.job.root / "cell-0000-propose.json").unlink()
    with pytest.raises(ValueError, match="existing_stage_without_inference"):
        module.review_group(reader, data.inventory, data.sidecar, data.plan["groups"][0])
    assert len(data.calls) == 2


@pytest.mark.parametrize("fault", ["request", "schema", "gpu"])
def test_tampered_actual_request_schema_or_gpu_receipt_is_rejected(simulated_job, fault):
    data = simulated_job
    module.review_group(data.job, data.inventory, data.sidecar, data.plan["groups"][0])
    path = data.job.root / ("attempts/cell-0000-propose-01.json" if fault == "request" else "response-1.json")
    value = module.whole.read(path)
    if fault == "request":
        value["request_sha256"] = "changed"
    elif fault == "schema":
        value["schema_sha256"] = "changed"
    else:
        value["metrics"]["size_vram"] = 0
    module.atomic_json(path, value)
    with pytest.raises(ValueError):
        module.review_group(data.job, data.inventory, data.sidecar, data.plan["groups"][0])
    assert len(data.calls) == 2


def test_context_capacity_failure_consumes_no_attempt(simulated_job):
    data = simulated_job
    data.job.tokenizer = SimpleNamespace(encode=lambda value: SimpleNamespace(ids=[0] * 70000))
    with pytest.raises(ValueError, match="complete_untruncated_context_fits"):
        module.review_group(data.job, data.inventory, data.sidecar, data.plan["groups"][0])
    assert data.calls == []
    assert not (data.job.root / "attempts").exists()


def test_two_failed_attempts_remain_exhausted_without_reset(simulated_job):
    data = simulated_job
    data.job.force_invalid = True
    for _ in range(2):
        with pytest.raises(ValueError, match="wiki_stage_attempt_budget_exhausted_cell-0000-propose"):
            module.review_group(data.job, data.inventory, data.sidecar, data.plan["groups"][0])
        assert len(data.calls) == 2
    assert len(list((data.job.root / "attempts").glob("cell-0000-propose-*.json"))) == 2
    assert not (data.job.root / "cell-0000-propose.json").exists()


def test_result_preserves_negative_judgments_and_separate_visual_scope(simulated_job):
    data = simulated_job
    findings = {}
    for group in data.plan["groups"]:
        findings.update(module.review_group(data.job, data.inventory, data.sidecar, group))
    findings["O00003"]["kind"] = "unresolved"
    result = module.result_for(data.job, data.plan, findings)
    assert result["relations"]["unresolved"] == 1
    assert result["all_text_item_relations_resolved"] is False
    assert result["visual_relations_requiring_separate_review"] == 1
    assert result["whole_notebook_association_verified"] is False
    assert result["reader_publication_allowed"] is False


def test_guard_refuses_a_verified_live_owner(tmp_path, monkeypatch):
    (tmp_path / "wiki").mkdir()
    previous = tmp_path / "review-experiment-thermal-guard.json"
    value = {"pid": os.getpid(), "created": psutil.Process().create_time(), "state": "running"}
    previous.write_text(json.dumps(value))
    monkeypatch.setattr(module, "checkpoint", lambda *_: None)
    with pytest.raises(ValueError, match="review_guard_slot_actually_free"):
        module.guard(tmp_path, "f" * 64, Path("not-used"))
    assert json.loads(previous.read_text()) == value
    assert not (tmp_path / "wiki/courseware-relations-guard-history").exists()
