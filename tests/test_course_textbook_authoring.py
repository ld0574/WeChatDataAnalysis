import copy
import json
from contextlib import contextmanager

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_authoring as author
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_textbook_evidence import inventory, sha


def group_value(cards, groups=1):
    return {
        "title": "An independently organized fixture article",
        "groups": [
            {
                "heading": f"Conceptual chapter {i}",
                "purpose": f"Explain dependency {i}.",
            }
            for i in range(groups)
        ],
        "assignments": {
            key: {"group": i % groups, "order": i // groups}
            for i, key in enumerate(reversed(cards))
        },
    }


def section_value(ids, ledger):
    paragraphs = [
        {
            "text": f"An authored explanation of the fixture principle {key} and its conditions.",
            "citations": ledger["points"][key]["citations"],
        }
        for key in ids
    ]
    return {
        "heading": "Develop the fixture principle " + ids[0],
        "paragraphs": paragraphs,
        "diagrams": [
            {
                "kind": "flow",
                "title": "Apply the fixture condition",
                "caption": "The fixture condition determines the applicable action.",
                "nodes": [
                    {"id": "condition", "label": "Check the condition"},
                    {"id": "action", "label": "Apply the operation"},
                ],
                "edges": [
                    {"from": "condition", "to": "action", "label": "when satisfied"}
                ],
                "citations": paragraphs[0]["citations"],
            }
        ],
        "overview": paragraphs[0],
        "takeaway": paragraphs[-1],
        "objective": "Explain the fixture condition and its consequence.",
        "quiz": {
            "question": "When is the fixture operation applicable?",
            "answer": "When the fixture condition is satisfied.",
            "explanation": "The action depends on the condition.",
            "criteria": ["State the condition and explain its dependency."],
            "citations": paragraphs[0]["citations"],
        },
        "coverage": {
            key: {
                "paragraphs": [i],
                "reason": "The complete fixture dependency is explained.",
            }
            for i, key in enumerate(ids)
        },
    }


@pytest.fixture
def archive(tmp_path):
    (tmp_path / "knowledge.sqlite3").touch()
    value = inventory(
        {
            "id": "a" * 24,
            "source_export_sha256": "b" * 64,
            "evidence": {
                str(i): {
                    "kind": "speech",
                    "text": f"PRIVATE_FIXTURE_ORIGINAL_{i}: condition {i} permits action {i}. "
                    * 4,
                    "start": i,
                    "end": i + 1,
                }
                for i in range(4)
            },
        },
        lesson_artifact="c" * 64,
    )
    key = sha(canonical(value))
    folder = tmp_path / "wiki/textbook-evidence" / key
    folder.mkdir(parents=True)
    atomic_json(folder / "manifest.json", value)
    points, pages, dispositions = {}, [], {}
    for number, refs in enumerate(value["pages"]):
        ids = []
        for index, ref in enumerate(refs):
            point = f"P{number:04d}_{index:03d}"
            points[point] = {
                "label": "Fixture condition " + point,
                "teaching": "The fixture explicitly connects a condition to an action.",
                "category": "concept",
                "citations": [ref],
            }
            dispositions[ref] = {"points": [point], "non_teaching_reason": ""}
            ids.append(point)
        pages.append({"page": number, "points": ids, "local_review_passed": True})
    ledger_job = "d" * 32
    ledger = {
        "job": ledger_job,
        "inventory_sha256": key,
        "lesson": value["lesson"],
        "points": points,
        "source_dispositions": dispositions,
        "pages": pages,
        "issues": [],
        "automatic_local_evidence_review_passed": True,
        "whole_lesson_knowledge_coverage_verified": False,
        "semantic_accuracy_verified": False,
        "reader_article_published": False,
        "source_deletion_allowed": False,
    }
    target = tmp_path / "wiki/textbook-keyed-jobs" / ledger_job
    target.mkdir(parents=True)
    author.save(target, "ledger", ledger)
    return tmp_path, key, value, ledger


def test_grouping_requires_every_input_once_in_nonempty_ordered_groups():
    cards = {"first": {}, "second": {}, "third": {}}
    good = group_value(cards, 2)
    assert author.grouped(good, cards, final=True)[0]["children"] == ["third", "first"]
    for mutation in ("omission", "invented", "unused", "order_collision"):
        value = copy.deepcopy(good)
        if mutation == "omission":
            value["assignments"].pop("first")
        elif mutation == "invented":
            value["assignments"]["extra"] = value["assignments"]["first"]
        elif mutation == "unused":
            value["assignments"]["second"]["group"] = 0
        else:
            value["assignments"]["first"]["order"] = 0
        with pytest.raises((ValueError, jsonschema.ValidationError)):
            author.grouped(value, cards, final=True)


def test_hierarchical_planning_preserves_all_points_without_fixed_chapter_count(
    archive, monkeypatch
):
    _, _, _, ledger = archive
    monkeypatch.setattr(author, "PLAN_CHARACTERS", 420)

    class Job:
        calls = []

        def stage(self, name, prompt, schema, context, validate):
            self.calls.append(context)
            result = group_value(context["items"])
            validate(result)
            return result

    job = Job()
    plan = author.organize(job, ledger)
    allocated = author.plan_points(plan, ledger["points"])
    assert sorted(key for chapter in allocated for key in chapter) == sorted(
        ledger["points"]
    )
    assert len(job.calls) > 1
    assert job.calls[-1]["mode"] == "chapters"
    assert len(plan["chapters"]) == 1
    for mutation in ("omitted", "cycle", "duplicate", "orphan"):
        value = copy.deepcopy(plan)
        root = value["chapters"][0]
        if mutation == "omitted":
            value["nodes"][root]["children"].pop()
        elif mutation == "cycle":
            value["nodes"][root]["children"] = [root]
        elif mutation == "duplicate":
            value["chapters"].append(root)
        else:
            value["nodes"]["orphan"] = {"children": []}
        with pytest.raises(ValueError):
            author.plan_points(value, ledger["points"])


def fixture_plan(ledger):
    return {
        "title": "Fixture conceptual article",
        "chapters": ["concept"],
        "nodes": {
            "concept": {
                "heading": "Understand conditions and consequences",
                "purpose": "Explain the fixture dependency.",
                "children": list(reversed(ledger["points"])),
            }
        },
    }


def test_writing_splits_whole_points_and_includes_every_complete_original(
    archive, monkeypatch
):
    _, _, value, ledger = archive
    monkeypatch.setattr(author, "WRITE_CHARACTERS", 850)
    units = author.writing_units(fixture_plan(ledger), ledger, value)
    assert len(units) > 1
    assert [key for unit in units for key in unit["point_ids"]] == list(
        reversed(ledger["points"])
    )
    for unit in units:
        context = author.writing_context(unit["point_ids"], ledger, value)
        packet = author.original_packet(unit["point_ids"], ledger, value)
        for ref, source in packet["sources"].items():
            assert context["sources"][ref]["text"] == value["sources"][ref]["text"]
            assert source == value["sources"][ref]
    monkeypatch.setattr(author, "WRITE_CHARACTERS", 1)
    with pytest.raises(ValueError, match="complete_point_context_too_large"):
        author.writing_units(fixture_plan(ledger), ledger, value)
    with pytest.raises(ValueError, match="smaller_single_card"):
        author.chunks({"one": {"text": "long" * 100}}, 10)


def test_section_coverage_must_exist_in_body_and_cite_its_own_originals(archive):
    _, _, value, ledger = archive
    ids = list(ledger["points"])[:2]
    good = section_value(ids, ledger)
    author.validate_section(good, ids, ledger, value)
    for mutation in ("omitted", "nonexistent", "unrelated", "foreign", "no_criteria"):
        bad = copy.deepcopy(good)
        if mutation == "omitted":
            bad["coverage"].pop(ids[0])
        elif mutation == "nonexistent":
            bad["coverage"][ids[0]]["paragraphs"] = [999]
        elif mutation == "unrelated":
            bad["coverage"][ids[0]]["paragraphs"] = [1]
        elif mutation == "foreign":
            bad["paragraphs"][0]["citations"] = ["invented"]
        else:
            bad["quiz"]["criteria"] = []
        with pytest.raises((ValueError, jsonschema.ValidationError)):
            author.validate_section(bad, ids, ledger, value)


def test_lower_planning_partitions_do_not_force_reading_sections(archive):
    _, _, value, ledger = archive
    ids = list(ledger["points"])
    plan = {
        "title": "Fixture article",
        "chapters": ["root"],
        "nodes": {
            "root": {
                "heading": "One learning question",
                "purpose": "Combine its explanations.",
                "children": ["leaf-b", "leaf-a"],
            },
            "leaf-a": {
                "heading": "Earlier planning batch",
                "purpose": "Internal only.",
                "children": ids[:2],
            },
            "leaf-b": {
                "heading": "Later planning batch",
                "purpose": "Internal only.",
                "children": ids[2:],
            },
        },
    }
    units = author.writing_units(plan, ledger, value)
    assert len(units) == 1
    assert units[0]["point_ids"] == ids[2:] + ids[:2]
    assert units[0]["concept"]["heading"] == "One learning question"


def test_all_review_items_are_mandatory_and_negative_verdicts_stay_negative(archive):
    _, _, _, ledger = archive
    ids = list(ledger["points"])
    section = section_value(ids, ledger)
    schema = author.review_schema(section, ids)
    good = review_value(schema)
    author.validate_review(good, schema)
    assert all(author.verdicts(good))
    good["coverage"][ids[-1]]["passed"] = False
    assert not all(author.verdicts(good))
    good["coverage"].pop(ids[-1])
    with pytest.raises(jsonschema.ValidationError):
        author.validate_review(good, schema)


def review_value(schema):
    if "passed" in schema["properties"]:
        return {
            "passed": True,
            "reason": "Fixture review performed against complete input.",
        }
    return {key: review_value(child) for key, child in schema["properties"].items()}


def fake_inference(monkeypatch, ledger, *, semantic_failure=False):
    calls = []
    monkeypatch.setattr(
        "wechat_decrypt_tool.course_pdf_ocr.local_model_digest", lambda model: "e" * 64
    )

    @contextmanager
    def slot(self, stage):
        yield

    def response(self, messages, *, schema):
        context = json.loads(messages[1]["content"])
        prompt = messages[0]["content"]
        calls.append((prompt, context))
        if prompt == author.GROUP_PROMPT:
            result = group_value(context["items"], min(2, len(context["items"])))
        elif prompt in (author.WRITE_PROMPT, author.REPAIR_PROMPT):
            result = section_value(list(context["assigned_points"]), ledger)
        else:
            result = review_value(schema)
            if semantic_failure and prompt == author.REVIEW_PROMPT:
                result["quiz"]["passed"] = False
                result["quiz"]["reason"] = (
                    "Fixture question still needs an application task."
                )
        return json.dumps(result), None

    monkeypatch.setattr(author.AuthoringJob, "slot", slot)
    monkeypatch.setattr(author.AuthoringJob, "generate_response", response)
    return calls


def test_complete_run_reads_originals_resumes_without_calls_and_exports_only_reading_data(
    archive, monkeypatch
):
    root, key, value, ledger = archive
    calls = fake_inference(monkeypatch, ledger)
    result = author.run(root, key, ledger["job"])
    assert result["state"] == "reading_draft_ready_for_actual_review"
    assert not result["reader_article_published"]
    assert not result["semantic_accuracy_verified"]
    assert not result["source_deletion_allowed"]
    assert not result["attachment_scope_resolved"]
    folder = root / "wiki/textbook-authoring-jobs" / result["job"]
    private = json.loads((folder / "private-article.json").read_bytes())["data"]
    assert set(private["private_coverage"]) == set(ledger["points"])
    reading = (folder / "reading.json").read_text("utf-8")
    html = (folder / "reading.html").read_text("utf-8")
    for forbidden in (
        "PRIVATE_FIXTURE_ORIGINAL",
        "citations",
        "private_coverage",
        "inventory_sha256",
        "attachment_scope",
    ):
        assert forbidden not in reading
        assert forbidden not in html
    writes = [ctx for prompt, ctx in calls if prompt == author.WRITE_PROMPT]
    assert writes
    for context in writes:
        refs = {
            ref
            for point in context["assigned_points"].values()
            for ref in point["citations"]
        }
        assert set(context["sources"]) == refs
        for ref in refs:
            assert context["sources"][ref]["text"] == value["sources"][ref]["text"]
    before = len(calls)
    assert author.run(root, key, ledger["job"]) == result
    assert len(calls) == before
    monkeypatch.setattr(
        "wechat_decrypt_tool.course_pdf_ocr.local_model_digest", lambda model: "f" * 64
    )
    with pytest.raises(ValueError, match="preserve_bound_authoring_budget"):
        author.run(root, key, ledger["job"])
    assert len(calls) == before


def test_unresolved_semantics_use_one_repair_and_withhold_reading_exports(
    archive, monkeypatch
):
    root, key, _, ledger = archive
    calls = fake_inference(monkeypatch, ledger, semantic_failure=True)
    result = author.run(root, key, ledger["job"])
    assert result["state"] == "article_requires_editorial_repair"
    assert result["quality_issues"] == result["sections"]
    assert (
        sum(prompt == author.REPAIR_PROMPT for prompt, _ in calls) == result["sections"]
    )
    folder = root / "wiki/textbook-authoring-jobs" / result["job"]
    assert not (folder / "reading.json").exists()
    assert not (folder / "reading.html").exists()
    assert (folder / "private-article.json").is_file()
    previous = len(calls)
    author.run(root, key, ledger["job"])
    assert len(calls) == previous


def test_unfinished_or_changed_ledger_cannot_start_writing(archive):
    root, key, _, ledger = archive
    path = root / "wiki/textbook-keyed-jobs" / ledger["job"] / "ledger.json"
    ledger["issues"] = [{"page": 0, "kind": "fixture unresolved semantic issue"}]
    ledger["automatic_local_evidence_review_passed"] = False
    author.save(path.parent, "ledger", ledger)
    with pytest.raises(ValueError, match="resolved_ledger_semantic_issues"):
        author.load_inputs(root, key, ledger["job"])
    saved = json.loads(path.read_bytes())
    saved["data"]["points"][next(iter(ledger["points"]))]["teaching"] = (
        "Changed after checksum."
    )
    atomic_json(path, saved)
    with pytest.raises(ValueError, match="unchanged_completed_ledger"):
        author.load_inputs(root, key, ledger["job"])
    with pytest.raises(ValueError, match="valid_completed_ledger_job"):
        author.load_inputs(root, key, "../foreign")


def test_structural_attempt_budget_is_persistent_across_worker_restarts(
    archive, monkeypatch
):
    root, key, _, ledger = archive
    calls = fake_inference(monkeypatch, ledger)
    successful = author.AuthoringJob.generate_response

    def invalid(self, messages, *, schema):
        successful(self, messages, schema=schema)
        return "{}", None

    monkeypatch.setattr(author.AuthoringJob, "generate_response", invalid)
    for expected_calls in (2, 2):
        with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
            author.run(root, key, ledger["job"])
        assert len(calls) == expected_calls
    attempts = list((root / "wiki/textbook-authoring-jobs").glob("*/attempts/*.json"))
    assert len(attempts) == 2
    assert all(
        json.loads(path.read_bytes())["validation_error"] == "ValidationError"
        for path in attempts
    )


def test_reading_blocks_cover_every_complete_section_and_every_transition(
    archive, monkeypatch
):
    _, _, value, ledger = archive
    monkeypatch.setattr(author, "WRITE_CHARACTERS", 850)
    units = author.writing_units(fixture_plan(ledger), ledger, value)
    sections = {unit["id"]: section_value(unit["point_ids"], ledger) for unit in units}
    monkeypatch.setattr(author, "READ_CHARACTERS", 3000)
    blocks = author.reading_blocks(units, sections)
    assert len(blocks) > 1
    assert {key for block in blocks for key in block} == set(sections)
    keys = list(sections)
    assert all(
        any(first in block and second in block for block in blocks)
        for first, second in zip(keys, keys[1:])
    )
    for block in blocks:
        for key, section in block.items():
            assert section["paragraphs"] == [
                row["text"] for row in sections[key]["paragraphs"]
            ]
    monkeypatch.setattr(author, "READ_CHARACTERS", 10)
    with pytest.raises(ValueError, match="complete_reading_section_too_large"):
        author.reading_blocks(units, sections)
