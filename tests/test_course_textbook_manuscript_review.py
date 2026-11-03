import copy
import json
from contextlib import contextmanager

import pytest

from wechat_decrypt_tool import course_textbook_authoring as author
from wechat_decrypt_tool import course_textbook_manuscript_review as module
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_authoring import archive as archive
from test_course_textbook_authoring import fixture_plan, section_value


@pytest.fixture
def manuscript(request):
    root, key, inventory, ledger = request.getfixturevalue("archive")
    plan = fixture_plan(ledger)
    units = author.writing_units(plan, ledger, inventory)
    sections = {unit["id"]: section_value(unit["point_ids"], ledger) for unit in units}
    private = author.assemble(inventory, ledger, plan, units, sections)
    parent = "d" * 32
    folder = root / "wiki/textbook-composition-jobs" / parent
    folder.mkdir(parents=True)
    author.save(folder, "private-article", private)
    author.save(
        folder,
        "result",
        {"job": parent, "inventory_sha256": key, "reader_article_published": False},
    )
    atomic_json(
        folder / "progress.json",
        {"stage": "private_article_assembled", "pid": 9999999, "created": 1},
    )
    return root, parent, inventory, private


def test_plan_covers_every_original_clause_and_every_body_paragraph(manuscript):
    _, _, inventory, private = manuscript
    plan = module.make_plan(inventory, private)
    original = module.coverage.plan(inventory)
    records = module.paragraphs(private, inventory)
    assert plan["clause_count"] == original["clauses"]
    for ref, parts in plan["sources"].items():
        assert [key for part in parts for key in part["clauses"]] == list(
            original["units"][ref]
        )
        for part in parts:
            ordered = [pid for block in part["article_blocks"] for pid in block]
            assert len(ordered) == len(records) and set(ordered) == set(records)
            priorities = [ref in records[pid]["citations"] for pid in ordered]
            assert priorities == sorted(priorities, reverse=True)
            context = module.base_context(
                inventory, ref, original["units"][ref], part["clauses"]
            )
            for block in part["article_blocks"]:
                actual = module.with_paragraphs(context, records, block)
                assert actual["target"] == inventory["sources"][ref]["text"]
                assert module.context_size(actual) <= module.CONTEXT_CHARACTERS
    changed = copy.deepcopy(private)
    changed["chapters"][0]["sections"][0]["paragraphs"][0]["text"] += (
        " Changed condition."
    )
    assert (
        module.make_plan(inventory, changed)["article_sha256"] != plan["article_sha256"]
    )
    assert (
        not plan["reader_publication_allowed"] and not plan["source_deletion_allowed"]
    )


def test_large_complete_paragraph_is_rejected_without_clipping(manuscript, monkeypatch):
    _, _, inventory, private = manuscript
    monkeypatch.setattr(module, "CONTEXT_CHARACTERS", 3000)
    private["chapters"][0]["sections"][0]["paragraphs"][0]["text"] = "complete " * 1000
    with pytest.raises(ValueError, match="complete_single_body_paragraph_fits_context"):
        module.make_plan(inventory, private)


def test_fallback_search_cannot_erase_previously_detected_teaching(manuscript):
    _, _, inventory, private = manuscript
    records = module.paragraphs(private, inventory)
    ref = next(iter(inventory["sources"]))
    units = module.coverage.clauses(inventory["sources"][ref]["text"])
    cid = next(iter(units))
    pids = list(records)[:3]

    class Job:
        calls = []

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            self.calls.append(context)
            number = len(self.calls)
            row = {
                "teaching": number != 2,
                "covered": number == 3,
                "paragraph_ids": [pids[2]] if number == 3 else [],
                "reason": "Fixture detects a condition and searches its complete explanation.",
            }
            value = {"clauses": {cid: row}}
            validate(value)
            return value

    job = Job()
    result = module.review_partition(
        job,
        inventory,
        records,
        ref,
        units,
        {"clauses": [cid], "article_blocks": [[pid] for pid in pids]},
        "fixture",
    )
    assert len(job.calls) == 3
    assert result[cid]["teaching"] and result[cid]["covered"]
    assert result[cid]["paragraph_ids"] == [pids[2]]
    assert len(result[cid]["assessments"]) == 3


def fake_inference(monkeypatch, *, malformed=False, non_teaching=False):
    calls = []
    monkeypatch.setattr(
        "wechat_decrypt_tool.course_pdf_ocr.local_model_digest", lambda _: "e" * 64
    )

    @contextmanager
    def slot(self, stage):
        self.progress("generating", stage)
        yield

    def chat(model, messages, *, schema):
        context = json.loads(messages[1]["content"])
        calls.append(context)
        if malformed:
            return "{}", None
        value = {
            "clauses": {
                key: {
                    "teaching": not non_teaching,
                    "covered": not non_teaching,
                    "paragraph_ids": []
                    if non_teaching
                    else [next(iter(context["paragraphs"]))],
                    "reason": "Fixture body explains the condition.",
                }
                for key in context["clauses"]
            }
        }
        return json.dumps(value), None

    monkeypatch.setattr(module.ReviewJob, "slot", slot)
    monkeypatch.setattr(module, "chat", chat)
    return calls


def test_actual_report_is_private_and_resumes_without_duplicate_calls(
    manuscript, monkeypatch
):
    root, parent, inventory, _ = manuscript
    calls = fake_inference(monkeypatch)
    result = module.run(root, parent)
    assert result["automatic_original_coverage_complete"]
    assert result["sources"] == len(inventory["sources"])
    assert result["clauses"] == module.coverage.plan(inventory)["clauses"]
    assert "coverage" not in result and "missing" not in result
    assert (
        not result["reader_publication_allowed"]
        and not result["semantic_accuracy_verified"]
    )
    folder = root / "wiki/manuscript-coverage-jobs" / result["job"]
    attempts = {p: p.read_bytes() for p in (folder / "attempts").iterdir()}
    count = len(calls)
    assert module.run(root, parent) == result
    assert len(calls) == count
    assert all(p.read_bytes() == data for p, data in attempts.items())
    assert not (folder / "reading.json").exists()


def test_bad_output_budget_survives_restart(manuscript, monkeypatch):
    root, parent, _, _ = manuscript
    calls = fake_inference(monkeypatch, malformed=True)
    for _ in range(2):
        with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
            module.run(root, parent)
        assert len(calls) == 2


def test_confirmed_teaching_cannot_all_be_excluded_as_non_teaching(
    manuscript, monkeypatch
):
    root, parent, inventory, _ = manuscript
    fake_inference(monkeypatch, non_teaching=True)
    result = module.run(root, parent)
    assert result["missing_clauses"] == 0
    assert result["sources_with_classification_conflicts"] == len(inventory["sources"])
    assert not result["automatic_original_coverage_complete"]
    assert result["state"] == "manuscript_requires_original_coverage_repair"


def test_parent_must_be_finished_and_saved_inputs_unchanged(manuscript):
    root, parent, _, _ = manuscript
    folder = root / "wiki/textbook-composition-jobs" / parent
    path = folder / "progress.json"
    progress = json.loads(path.read_bytes())
    progress["stage"] = "section-0000-write"
    atomic_json(path, progress)
    with pytest.raises(ValueError, match="assembled_parent_article"):
        module.inputs(root, parent)
    progress["stage"] = "private_article_assembled"
    atomic_json(path, progress)
    article_path = folder / "private-article.json"
    article = json.loads(article_path.read_bytes())
    article["data"]["title"] += " modified"
    atomic_json(article_path, article)
    with pytest.raises(ValueError, match="unchanged_saved_data"):
        module.inputs(root, parent)
