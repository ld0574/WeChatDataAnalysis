import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_visible_context as module


def fixture():
    inventory = {"sources": {key: {"kind": "speech", "text": "Synthetic full original " + key}
        for key in ["O00000", "O00001", "O00002", "O00003", "O00004"]}}
    working = {"points": {"U0": {"citations": ["O00001"]}},
        "pending_source_cards": {"U0": {"reference": "O00001", "neighbors": ["O00000"]}}}
    packet = module.ORIGINAL_PACKET(["U0"], working, inventory)
    context = {**module.writing.whole.context_for(inventory, list(packet["sources"])),
        "assigned_points": working["points"], "chapter": "Synthetic chapter"}
    cited = {"text": "Synthetic independent prose", "citations": ["O00002"]}
    candidate = {"kind": "teaching", "heading": "Synthetic heading", "objective": "Synthetic objective",
        "overview": copy.deepcopy(cited), "takeaway": copy.deepcopy(cited), "paragraphs": [copy.deepcopy(cited)],
        "coverage": {"U0": {"teaching": True, "paragraphs": [0], "reason": "Synthetic coverage"}}, "diagrams": [],
        "quiz": {"question": "Synthetic question", "answer": "Synthetic answer", "explanation": "Synthetic explanation",
            "criteria": ["Synthetic criterion"], "citations": ["O00002"]}}
    return inventory, working, packet, context, candidate


def test_visible_context_keeps_teaching_prose_mapping_and_original_request():
    inventory, working, packet, context, candidate = fixture()
    before = copy.deepcopy((packet, context, candidate))
    scope = module.visible_scope(["U0"], working, inventory, context, packet)
    assert set(scope["expanded"]["sources"]) == {"O00000", "O00001", "O00002"}
    with pytest.raises(ValueError, match="scoped_original_citations"):
        module.ORIGINAL_VALIDATE(candidate, ["U0"], working, inventory)
    proposed, changes, invalid = module.normalize(candidate, ["U0"], working, inventory, scope)
    assert proposed["kind"] == "teaching" and proposed["coverage"] == candidate["coverage"]
    assert proposed["paragraphs"][0]["text"] == candidate["paragraphs"][0]["text"]
    assert proposed["paragraphs"][0]["citations"] == ["O00001", "O00002"]
    assert len(changes) == 1 and not invalid
    assert (packet, context, candidate) == before
    assert module.writing.composition.original_packet is module.ORIGINAL_PACKET


@pytest.mark.parametrize("changed", ["summary", "extra", "missing", "packet", "points"])
def test_only_complete_context_actually_sent_can_extend_scope(changed):
    inventory, working, packet, context, _ = fixture()
    if changed == "summary":
        context["context_sources"]["O00002"]["text"] = "Synthetic summary, not full original"
    elif changed == "extra":
        context["context_sources"]["O00004"] = inventory["sources"]["O00004"]
    elif changed == "missing":
        context["context_sources"] = {}
    elif changed == "packet":
        packet["sources"]["O00002"] = inventory["sources"]["O00002"]
    else:
        context["assigned_points"] = {}
    with pytest.raises(ValueError):
        module.visible_scope(["U0"], working, inventory, context, packet)


@pytest.mark.parametrize("changed", ["unknown", "duplicate", "empty", "mapping", "blank", "disposition", "hidden_graph_ref"])
def test_scope_fix_does_not_hide_other_defects(changed):
    inventory, working, packet, context, candidate = fixture()
    scope = module.visible_scope(["U0"], working, inventory, context, packet)
    if changed == "unknown":
        candidate["quiz"]["citations"] = ["O00004"]
    elif changed == "duplicate":
        candidate["quiz"]["citations"] *= 2
    elif changed == "empty":
        candidate["quiz"]["citations"] = []
    elif changed == "mapping":
        candidate["coverage"]["U0"]["paragraphs"] = [99]
    elif changed == "blank":
        candidate["paragraphs"][0]["text"] = " "
    elif changed == "disposition":
        candidate["kind"] = "non_teaching"
    else:
        candidate["diagrams"] = [{"kind": "flow", "title": "Synthetic graph", "caption": "Synthetic caption",
            "nodes": [{"id": "a", "label": "A"}], "edges": [{"from": "a", "to": "missing", "label": "Invalid"}],
            "citations": ["O00004"]}]
    with pytest.raises((ValueError, module.jsonschema.ValidationError)):
        module.normalize(candidate, ["U0"], working, inventory, scope)
    assert module.writing.composition.original_packet is module.ORIGINAL_PACKET


def history(tmp_path, monkeypatch):
    inventory, working, packet, context, candidate = fixture()
    name = "section-0005-write"
    job = SimpleNamespace(root=tmp_path, key="a" * 32, models={"qwen3.5:9b": "b" * 64})
    schema = module.writing.section_schema(["U0"], working)
    request = {"prompt": "Synthetic authenticated original prompt", "schema": schema, "binding": "c" * 64}
    attempts = tmp_path / "attempts"
    attempts.mkdir()
    previous = ""
    for number in [1, 2]:
        value = copy.deepcopy(candidate)
        if number == 2:
            value["quiz"]["citations"] = ["O00004"]
        record = {"stage": name, "attempt": number, "input_sha256": request["binding"],
            "model_digest": job.models["qwen3.5:9b"], "error_type": "ValueError", "validation_error": "Synthetic original error",
            "raw_response": json.dumps(value)}
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user",
            "content": json.dumps({**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        record["request_sha256"] = module.digest(messages)
        (attempts / f"{name}-{number:02}.json").write_text(json.dumps(record), "utf-8")
        previous = record["validation_error"]

    def capture(*args):
        assert args[-1] == packet  # Never extend historical writer evidence.
        return request

    monkeypatch.setattr(module.draft, "capture_request", capture)
    monkeypatch.setattr(module.writing.whole.editor.e, "verify_response", lambda *args: None)
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path / "user")
    return job, name, schema, context, packet, working, inventory


def test_authenticated_history_uses_first_candidate_and_original_budgets(tmp_path, monkeypatch):
    args = history(tmp_path, monkeypatch)
    registry = module.Recovery([args[1]])
    before = {p: p.read_bytes() for p in (tmp_path / "attempts").iterdir()}
    with registry.hooks():
        result = module.draft.recover_stage(*args)
        assert module.draft.recover_stage(*args) == result
        module.writing.validate_section(result["data"], ["U0"], args[-2], args[-1])
    receipt = result["receipt"]
    assert receipt["selected_attempt"] == 1 and len(receipt["rejected_candidates"]) == 1
    extension = receipt[module.FIELD]
    assert extension["actual_context_sha256"] == module.digest(args[3])
    assert extension["original_evidence_packet_sha256"] == module.digest(args[4])
    assert extension["adapter_sha256"] == module.writing.whole.sha(module.Path(module.__file__).read_bytes())
    assert extension["additional_original_sha256"] == {"O00002": module.digest(args[-1]["sources"]["O00002"])}
    assert extension["model_calls"] == 0 and not extension["original_budgets_reset"] and not extension["semantic_acceptance"]
    assert not (tmp_path / (args[1] + ".json")).exists()
    assert all(p.read_bytes() == raw for p, raw in before.items())


@pytest.mark.parametrize("changed", ["request", "model", "input", "accepted", "used_review"])
def test_invalid_original_history_cannot_receive_recovery(tmp_path, monkeypatch, changed):
    args = history(tmp_path, monkeypatch)
    p = tmp_path / "attempts/section-0005-write-01.json"
    value = json.loads(p.read_bytes())
    if changed == "used_review":
        (p.parent / "section-0005-review-01.json").write_text("{}", "utf-8")
    else:
        key = {"request": "request_sha256", "model": "model_digest", "input": "input_sha256", "accepted": "accepted_structure"}[changed]
        value[key] = True if changed == "accepted" else "changed"
        p.write_text(json.dumps(value), "utf-8")
    with module.Recovery([args[1]]).hooks(), pytest.raises(ValueError):
        module.draft.recover_stage(*args)
    assert not module.draft.artifact_path(args[0], args[1]).exists()


@pytest.mark.parametrize("stage", ["section-0000-write", "section-0001-write", "section-0002-write"])
def test_legacy_recovery_receipts_pass_through_without_new_fields(tmp_path, monkeypatch, stage):
    job = SimpleNamespace(root=tmp_path)
    path = module.draft.artifact_path(job, stage)
    path.parent.mkdir()
    original = {"receipt": {"policy": "Synthetic frozen old policy"}, "data": {"unchanged": True}}
    path.write_text(json.dumps(original), "utf-8")
    monkeypatch.setattr(module.refs, "candidate_from_history", lambda *args: copy.deepcopy(original))
    before = path.read_bytes()
    assert module.Recovery([]).candidate_from_history(job, stage, {}, {}, {}, {}, {}) == original
    assert path.read_bytes() == before


def test_only_prepared_original_followup_roles_get_extended_evidence():
    inventory, working, packet, context, _ = fixture()
    registry = module.Recovery([])
    job = SimpleNamespace(key="synthetic")
    scope = module.visible_scope(["U0"], working, inventory, context, packet)
    registry.scopes[job.key, "section-0005"] = scope
    kwargs = {"evidence_packet": packet}
    review = module.writing.author.REVIEW_PROMPT
    for suffix in ("review", "repair", "repair-review"):
        prompt = module.writing.author.REPAIR_PROMPT if suffix == "repair" else review
        actual = registry.followup_packet(job, "section-0005-" + suffix, prompt, {**context, "candidate": {}}, kwargs)
        assert actual == {"evidence_packet": scope["expanded"]}
    assert registry.followup_packet(job, "section-0005-write", module.writing.author.WRITE_PROMPT, context, kwargs) is kwargs
    assert registry.followup_packet(job, "section-0004-review", review, context, kwargs) is kwargs
    with pytest.raises(ValueError, match="unchanged_followup"):
        registry.followup_packet(job, "section-0005-review", review, {**context, "chapter": "Changed"}, kwargs)
    assert kwargs["evidence_packet"] == packet


def test_hooks_and_packet_scope_are_restored_after_failure():
    original = module.draft.candidate_from_history, module.writing.validate_section, module.draft.BASE_JOB, module.writing.AuthoringJob
    with pytest.raises(RuntimeError):
        with module.Recovery([]).hooks():
            raise RuntimeError("Synthetic failure")
    assert (module.draft.candidate_from_history, module.writing.validate_section, module.draft.BASE_JOB, module.writing.AuthoringJob) == original


def test_frozen_capture_can_call_scoped_stage_on_the_original_job_instance(monkeypatch):
    job = object.__new__(module.ORIGINAL_JOB)
    job.key = "synthetic"
    observed = []
    monkeypatch.setattr(module.ORIGINAL_JOB, "stage", lambda self, *args, **kwargs: observed.append(self))
    with module.Recovery([]).hooks():
        module.draft.BASE_JOB.stage(job, "concept-plan", "Synthetic prompt", {}, {}, lambda value: None)
    assert observed == [job]
