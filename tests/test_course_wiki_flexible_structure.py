"""Material-shaped topics retain complete originals and legacy attempt identities."""

import copy
import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import jsonschema
import pytest

from test_course_wiki_allocation import proposal
from test_course_wiki_batch import pipeline as batch_pipeline
from test_course_wiki_evidence import (
    archive as source_archive,
    long_evidence as source_long_evidence,
    published as source_published,
)
from test_course_wiki_synthesis import model
from wechat_decrypt_tool import course_wiki_allocation as allocation
from wechat_decrypt_tool import course_wiki_batch as batch
from wechat_decrypt_tool import course_wiki_limitations_repair as limitations
from wechat_decrypt_tool import course_wiki_planning as planning
from wechat_decrypt_tool import course_wiki_snapshot as snapshot
from wechat_decrypt_tool import course_wiki_synthesis as synthesis
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_knowledge import GpuBusyError, atomic_json
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_lesson_exports import digest
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki import Wiki, validate_article

archive = source_archive
published = source_published
long_evidence = source_long_evidence
pipeline = batch_pipeline


def flexible_proposal(context, count=2):
    value = proposal(context)
    value["chapters"] = [
        {
            "heading": f"Synthetic chapter {i}",
            "focus": f"Explain complete synthetic learning task {i}",
            "section": context["eligible"][i % len(context["eligible"])],
        }
        for i in range(count)
    ]
    value["exercises"] = [
        {"chapter": i % count, "focus": f"Synthetic distinct assessment task {i}"}
        for i in range(max(3, count))
    ]
    return value


def planned(long_evidence, monkeypatch, count=2, flexible=True):
    store, lesson, partition = long_evidence
    reader = ReadOnlyArchive(store.root)
    prepared = allocation.create(reader, partition["partition"])
    calls = []

    @contextmanager
    def slot(*args):
        yield

    def chat(name, messages, *, schema):
        assert messages[0]["content"] == (
            planning.FLEXIBLE_ALLOCATION_PROMPT
            if flexible
            else planning.ALLOCATION_PROMPT
        )
        context = json.loads(messages[1]["content"])
        value = flexible_proposal(context, count) if flexible else proposal(context)
        jsonschema.validate(value, schema)
        calls.append(context)
        return json.dumps(value)

    monkeypatch.setattr(planning, "slot", slot)
    monkeypatch.setattr(planning, "local_chat", chat)
    monkeypatch.setattr(planning, "local_model_digest", lambda _: "synthetic-weights")
    args = dict(
        allocation=prepared["allocation"],
        allocation_scope=0,
        flexible_structure=flexible,
    )
    result = planning.propose(reader, lesson["id"], **args)
    plan = json.loads(
        (reader.root / "wiki/planning-jobs" / result["job"] / "plan.json").read_bytes()
    )
    return reader, lesson, prepared, calls, args, result, plan


@pytest.mark.parametrize("count", [2, 3, 5])
def test_complete_originals_and_explicit_assessments_are_frozen_and_resumable(
    long_evidence, monkeypatch, count
):
    reader, lesson, prepared, calls, args, result, plan = planned(
        long_evidence, monkeypatch, count
    )
    actual = allocation.scope(reader.root, prepared["allocation"], 0, lesson["id"])
    assert calls[0]["evidence_pages"] == actual["context"]["evidence_pages"]
    assert calls[0]["original_section"] == actual["context"]["original_section"]
    assert plan["structure_contract"] == synthesis.FLEXIBLE_STRUCTURE
    assert len(plan["chapters"]) == count
    assert len(plan["exercises"]) == max(3, count)
    packet, _, chapters = synthesis.prepare_plan(reader.root, plan)
    assert packet["quiz_scopes"] == [
        sorted(chapters[e["chapter"]]["sources"]) for e in plan["exercises"]
    ]
    assert not result["full_course_coverage_verified"]
    assert planning.propose(reader, lesson["id"], **args) == result
    assert len(calls) == 1


@pytest.mark.parametrize(
    "change,error",
    [
        ("unassessed", "assessment_for_every_chapter"),
        ("repeated", "distinct_assessment_goals"),
        ("outside", "invalid_quiz_chapter"),
        ("boolean", "invalid_quiz_chapter"),
        ("too_few", "3_to_5_questions"),
    ],
)
def test_assessment_shape_is_rejected_before_writing(
    long_evidence, monkeypatch, change, error
):
    reader, _, _, _, _, _, plan = planned(long_evidence, monkeypatch)
    if change == "unassessed":
        for e in plan["exercises"]:
            e["chapter"] = 0
    elif change == "repeated":
        plan["exercises"][1]["focus"] = plan["exercises"][0]["focus"]
    elif change == "outside":
        plan["exercises"][0]["chapter"] = 2
    elif change == "boolean":
        plan["exercises"][0]["chapter"] = True
    else:
        plan["exercises"].pop()
    with pytest.raises(ValueError, match=error):
        synthesis.prepare_plan(reader.root, plan)


@pytest.mark.parametrize("first_flexible", [False, True])
def test_started_original_scope_cannot_switch_strategy_or_reset_budget(
    long_evidence, monkeypatch, first_flexible
):
    reader, lesson, _, calls, args, _, _ = planned(
        long_evidence, monkeypatch, flexible=first_flexible
    )
    before = {
        p.relative_to(reader.root): p.read_bytes()
        for p in reader.root.glob("wiki/planning-jobs/**/*")
        if p.is_file()
    }
    args["flexible_structure"] = not first_flexible
    with pytest.raises(ValueError, match="started_scope_structure_strategy_changed"):
        planning.propose(reader, lesson["id"], **args)
    assert len(calls) == 1
    assert all((reader.root / p).read_bytes() == b for p, b in before.items())


@pytest.mark.parametrize("count", [2, 3, 5])
def test_schema_binds_exercise_index_to_actual_chapter_count(count):
    context = {"eligible": ["page-0", "page-1"]}
    value = flexible_proposal(context, count)
    schema = planning.proposal_schema(context["eligible"], flexible=True)
    jsonschema.validate(value, schema)
    value["exercises"][0]["chapter"] = count
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, schema)
    value = flexible_proposal(context, count)
    value["chapters"][0]["section"] = "page-unknown"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, schema)
    skipped = dict(
        decision="skip",
        title="",
        introduction="",
        chapters=[],
        exercises=[],
        reason="gap",
    )
    jsonschema.validate(skipped, schema)
    assert (
        "exercises"
        not in planning.proposal_schema(context["eligible"])["oneOf"][0]["properties"]
    )


def fixed_model(monkeypatch):
    calls = model(monkeypatch)
    original = writer.local_chat

    def fixed(name, messages, schema):
        value = json.loads(original(name, messages, schema))
        if "fixed_question" in json.loads(messages[1]["content"]):
            value["quiz"].pop("question")
        return json.dumps(value)

    monkeypatch.setattr(writer, "local_chat", fixed)
    return calls


def test_two_chapter_article_keeps_body_citation_and_limitation_gates_and_restores(
    long_evidence, monkeypatch
):
    reader, _, _, _, _, _, plan = planned(long_evidence, monkeypatch)
    calls = fixed_model(monkeypatch)
    draft = synthesis.generate(reader, plan, fixed_assessments=True)
    publication = Wiki(reader.root).article(draft["id"])
    article, packet = publication["article"], publication["packet"]
    assert len(article["sections"]) == 2 and len(article["quiz"]) == 3
    assert all(len(s["paragraphs"]) >= 2 for s in article["sections"])
    assert article["pitfalls"]
    assert all(
        c["schema"]["properties"]["heading"]["enum"] == [c["stage"]]
        for c in calls
        if c["stage"].startswith("Synthetic chapter")
    )
    changed = copy.deepcopy(article)
    changed["sections"][0]["paragraphs"].pop()
    with pytest.raises(ValueError, match="two_developed_paragraphs"):
        validate_article(changed, packet)
    changed = copy.deepcopy(article)
    for section in changed["sections"]:
        for paragraph in section["paragraphs"]:
            paragraph["text"] = "short"
    with pytest.raises(ValueError, match="body"):
        validate_article(changed, packet)
    changed_packet = copy.deepcopy(packet)
    changed_packet["quiz_scopes"][0] = ["unassigned"]
    with pytest.raises(ValueError, match="assessment_scope_changed"):
        synthesis.validate_scopes(article, changed_packet)
    for field in ("quiz", "paragraph", "image", "code"):
        changed = copy.deepcopy(article)
        out = next(r for r in packet["sources"] if r not in packet["chapter_scopes"][0])
        if field == "quiz":
            changed["quiz"][0]["citations"] = [out]
        elif field == "paragraph":
            changed["sections"][0]["paragraphs"][0]["citations"] = [out]
        elif field == "image":
            changed["sections"][0]["image"] = out
        else:
            changed["sections"][0]["code_citations"] = [out]
        with pytest.raises(ValueError, match="citation_outside_assigned_scope"):
            synthesis.validate_scopes(changed, packet)
    backup = snapshot.create(
        reader.root, reader.root.parent / "flex-backup", reserve_gib=0
    )
    restored = snapshot.restore(
        backup, reader.root.parent / "flex-restored", reserve_gib=0
    )
    assert Wiki(restored).article(draft["id"]) == publication
    checked = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("wiki_snapshot_isolated_reader.py")),
            str(restored),
            str(reader.root),
            str(Path(snapshot.__file__).parent),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert json.loads(checked.stdout)["original_root_access_attempts"] == 0


def allocation_batch(pipeline, monkeypatch):
    p = pipeline
    monkeypatch.setattr(
        allocation,
        "create",
        lambda store, partition: {
            "allocation": partition,
            "video": "video-0",
            "lesson_sha256": digest(canonical(p.lessons["video-0"])),
            "scopes": 2,
        },
    )
    key = batch.prepare_allocations(p.store, ["a" * 32])["batch"]
    return key


def test_batch_intent_recovers_gpu_wait_and_passes_both_constrained_checks(
    pipeline, monkeypatch
):
    p = pipeline
    key = allocation_batch(p, monkeypatch)
    original = batch.propose
    checks = batch.review
    seen = []

    def busy(*a, **kw):
        assert kw["flexible_structure"] is True
        raise GpuBusyError("synthetic busy")

    monkeypatch.setattr(batch, "propose", busy)
    assert batch.run(p.store, key, flexible_new_topics=True)["state"] == "deferred_gpu"

    def resumed(*a, **kw):
        assert kw["flexible_structure"] is True
        return original(*a, **kw)

    def check(*a, **kw):
        assert kw["constrained_evidence"] is True
        seen.append(True)
        return checks(*a, **kw)

    monkeypatch.setattr(batch, "propose", resumed)
    monkeypatch.setattr(batch, "review", check)
    monkeypatch.setattr(batch, "omissions", check)
    result = batch.run(p.store, key, max_items=1)
    assert [r["state"] for r in result["courses"].values()] == [
        "draft_checks_complete",
        "pending",
    ]
    assert len(seen) == 2
    folder, _ = batch.manifest(p.store.root, key)
    assert len(list((folder / "topic-structure-intents").glob("*.json"))) == 1


@pytest.mark.parametrize("change", ["missing", "tamper", "switch", "disable_checks"])
def test_batch_rejects_changed_intents_before_models(pipeline, monkeypatch, change):
    p = pipeline
    key = allocation_batch(p, monkeypatch)

    def busy(*a, **kw):
        raise GpuBusyError("synthetic busy")

    original = batch.propose
    monkeypatch.setattr(batch, "propose", busy)
    batch.run(p.store, key, flexible_new_topics=True)
    folder, _ = batch.manifest(p.store.root, key)
    intent = next((folder / "topic-structure-intents").glob("*.json"))
    if change == "missing":
        intent.unlink()
    elif change == "tamper":
        value = json.loads(intent.read_bytes())
        value["planning_strategy"] = "changed"
        atomic_json(intent, value)
    monkeypatch.setattr(batch, "propose", original)
    result = batch.run(
        p.store,
        key,
        max_items=1,
        flexible_new_topics=False if change == "switch" else None,
        constrained_new_checks=False if change == "disable_checks" else None,
    )
    assert next(iter(result["courses"].values()))["state"] == "needs_review"
    assert not p.calls


def test_orphan_intent_recovers_but_started_legacy_cannot_opt_in(pipeline, monkeypatch):
    p = pipeline
    key = allocation_batch(p, monkeypatch)
    folder, recipe = batch.manifest(p.store.root, key)
    spec = recipe["courses"][0]
    row = {"state": "pending"}
    assert batch.topic_structure(folder, key, spec, row, True)
    recovered = {"state": "pending"}
    assert batch.topic_structure(folder, key, spec, recovered, None)
    assert recovered == row
    with pytest.raises(ValueError, match="unstarted_item"):
        batch.topic_structure(
            folder, key, recipe["courses"][1], {"state": "paused"}, True
        )
    assert not p.calls


def test_terminal_legacy_rows_unchanged_and_default_stays_legacy(pipeline, monkeypatch):
    p = pipeline
    key = allocation_batch(p, monkeypatch)
    original = batch.propose

    def legacy(*a, **kw):
        assert "flexible_structure" not in kw
        return original(*a, **kw)

    monkeypatch.setattr(batch, "propose", legacy)
    result = batch.run(p.store, key)
    before = list(p.calls)
    assert (
        batch.run(p.store, key, flexible_new_topics=True)["courses"]
        == result["courses"]
    )
    assert p.calls == before
    folder, _ = batch.manifest(p.store.root, key)
    assert not (folder / "topic-structure-intents").exists()


def test_guard_forwards_flexible_strategy(pipeline, monkeypatch):
    commands = []
    monkeypatch.setattr(
        batch, "gpu_guard", lambda root, command, **kw: commands.append((command, kw))
    )
    monkeypatch.setattr(
        batch.sys,
        "argv",
        [
            "batch",
            "--root",
            str(pipeline.store.root),
            "guard",
            "--batch",
            "a" * 32,
            "--max-items",
            "1",
            "--flexible-new-topics",
        ],
    )
    batch.main()
    assert "--flexible-new-topics" in commands[0][0]
    assert commands[0][1]["maximum_power"] == 200
    assert commands[0][1]["maximum_temperature"] == 70
    assert not pipeline.calls


def test_flexible_contract_cannot_be_applied_to_nonallocated_sources(
    long_evidence, monkeypatch
):
    reader, _, _, _, _, _, plan = planned(long_evidence, monkeypatch)
    for selections in [
        plan["introduction"],
        *(c["selections"] for c in plan["chapters"]),
    ]:
        for selected in selections:
            selected.pop("evidence_partition", None)
            selected.pop("evidence_page", None)
    with pytest.raises(ValueError):
        synthesis.prepare_plan(reader.root, plan)
    with pytest.raises(ValueError, match="requires_allocated_originals"):
        planning.propose(reader, "unused", flexible_structure=True)


def test_new_final_limitation_failure_retains_three_attempt_budget(
    long_evidence, monkeypatch
):
    reader, _, _, _, _, _, plan = planned(long_evidence, monkeypatch)
    calls = fixed_model(monkeypatch)
    original = writer.local_chat

    def missing(name, messages, schema):
        value = json.loads(original(name, messages, schema))
        if len(json.loads(messages[1]["content"]).get("previous_questions", [])) == 2:
            value["pitfalls"] = []
        return json.dumps(value)

    monkeypatch.setattr(writer, "local_chat", missing)
    before = None
    for _ in range(2):
        with pytest.raises(ValueError, match="attempt_budget_exhausted_self-check-2"):
            synthesis.generate(reader, plan, fixed_assessments=True)
        retained = {
            p.relative_to(reader.root): p.read_bytes()
            for p in reader.root.glob("wiki/jobs/*/attempts/self-check-2-*.json")
        }
        assert len(retained) == 3
        if before is not None:
            assert retained == before
        before = retained
    assert sum(c["stage"] == "quiz-2" for c in calls) == 3
    assert not Wiki(reader.root).catalog()["articles"]


def test_flexible_pause_keeps_same_intent_and_resumes(pipeline, monkeypatch):
    p = pipeline
    key = allocation_batch(p, monkeypatch)
    original = batch.generate
    monkeypatch.setattr(
        batch,
        "generate",
        lambda *a, **kw: (_ for _ in ()).throw(InterruptedError("pause")),
    )
    with pytest.raises(InterruptedError):
        batch.run(p.store, key, flexible_new_topics=True)
    folder, _ = batch.manifest(p.store.root, key)
    path = next((folder / "topic-structure-intents").glob("*.json"))
    before = path.read_bytes()
    monkeypatch.setattr(batch, "generate", original)
    assert batch.run(p.store, key, max_items=1)["state"] == "slice_finished"
    assert path.read_bytes() == before


@pytest.mark.parametrize("value", [1, "true", []])
def test_invalid_strategy_options_do_not_call_models(pipeline, value):
    key = batch.prepare(pipeline.store, limit=1)["batch"]
    with pytest.raises(ValueError, match="invalid_topic_structure_option"):
        batch.run(pipeline.store, key, flexible_new_topics=value)
    assert not pipeline.calls


@pytest.mark.parametrize("pinned", [False, True])
def test_limitation_restore_accepts_exact_old_or_fixed_heading_only(tmp_path, pinned):
    schema = {"type": "object", "properties": {"heading": {"type": "string"}}}
    saved_schema = copy.deepcopy(schema)
    if pinned:
        saved_schema["properties"]["heading"]["enum"] = ["Frozen heading"]
    scoped = {"sources": {"s:1": {"text": "Synthetic retained source"}}}
    recipe = {"model_digest": "frozen", "original_range_notice": "Retained notice"}
    context = {"sources": scoped["sources"]}
    data = {"heading": "Frozen heading"}
    binding = digest(
        canonical(
            {
                "prompt": "promptRetained notice",
                "schema": writer.evidence_schema(saved_schema, scoped),
                "context": context,
                "model_digest": "frozen",
            }
        )
    )
    atomic_json(
        tmp_path / "chapter-0.json",
        {
            "input_sha256": binding,
            "data_sha256": digest(canonical(data)),
            "data": data,
        },
    )
    before = (tmp_path / "chapter-0.json").read_bytes()
    assert (
        limitations.checkpoint(
            tmp_path,
            "chapter-0",
            recipe,
            "prompt",
            schema,
            context,
            scoped,
            lambda v: None,
            heading="Frozen heading",
        )
        == data
    )
    assert (tmp_path / "chapter-0.json").read_bytes() == before
    with pytest.raises(ValueError, match="original_checkpoint_changed"):
        limitations.checkpoint(
            tmp_path,
            "chapter-0",
            recipe,
            "changed prompt",
            schema,
            context,
            scoped,
            lambda v: None,
            heading="Frozen heading",
        )
