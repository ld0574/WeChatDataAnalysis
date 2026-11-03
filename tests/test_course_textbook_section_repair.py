import copy
import json
from contextlib import contextmanager

import pytest

from wechat_decrypt_tool import course_textbook_authoring as author
from wechat_decrypt_tool import course_textbook_composition as composition
from wechat_decrypt_tool import course_textbook_section_repair as module
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_authoring import archive as archive
from test_course_textbook_authoring import review_value
from test_course_textbook_composition import disputed_archive as disputed_archive
from test_course_textbook_composition import inference


def stopped_section(request, monkeypatch):
    root, key, inventory, parent, _, _ = request.getfixturevalue("disputed_archive")
    _, working = composition.load_inputs(root, key, parent["job"])
    calls = inference(monkeypatch, working)
    original = composition.AuthoringJob.generate_response

    @contextmanager
    def slot(self, stage):
        self.progress("generating", stage)
        yield

    def missing_support(self, messages, *, schema):
        raw, _ = original(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] == composition.PROMPTS[author.WRITE_PROMPT]:
            first = value["paragraphs"][0]
            scoped = json.loads(messages[1]["content"])["sources"]
            other = next(ref for ref in scoped if ref not in first["citations"])
            first["citations"] = [other]
        return json.dumps(value), {
            "model_digest": "e" * 64,
            "done_reason": "stop",
            "prompt_eval_count": 100,
            "eval_count": 100,
            "context_length": 16384,
            "size_vram": 1024,
        }

    monkeypatch.setattr(composition.AuthoringJob, "slot", slot)
    monkeypatch.setattr(composition.AuthoringJob, "generate_response", missing_support)
    with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
        author.run(root, key, parent["job"], protocol=composition)
    folder = next((root / "wiki/textbook-composition-jobs").iterdir())
    progress = json.loads((folder / "progress.json").read_bytes())
    progress.update(pid=9999999, created=1)
    atomic_json(folder / "progress.json", progress)
    monkeypatch.setattr(composition.AuthoringJob, "generate_response", original)
    return root, key, inventory, parent, working, calls, folder, original


def test_existing_semantic_repair_is_reviewed_then_same_writer_resumes(
    request, monkeypatch
):
    root, key, _, parent, _, calls, folder, _ = stopped_section(request, monkeypatch)
    originals = {
        p: p.read_bytes()
        for p in (folder / "attempts").glob("section-0000-write-*.json")
    }
    count = len(calls)
    result = module.recover(root, key, parent["job"], folder.name, "section-0000-write")
    assert len(calls) == count + 2
    assert result["state"] == "existing_section_repair_ready_to_resume"
    assert not result["semantic_repair_budget_refreshed"]
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    prefix = folder / "section-0000"
    review = json.loads(prefix.with_name(prefix.name + "-review.json").read_bytes())
    repair_review = json.loads(
        prefix.with_name(prefix.name + "-repair-review.json").read_bytes()
    )
    assert review["data"] == repair_review["data"]
    assert review["input_sha256"] == repair_review["input_sha256"]
    result = author.run(root, key, parent["job"], protocol=composition)
    assert result["job"] == folder.name
    assert (folder / "private-article.json").is_file()
    assert not (folder / "reading.json").exists()
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    assert len(list((folder / "attempts").glob("section-0000-write-*.json"))) == 2
    assert len(list((folder / "attempts").glob("section-0000-repair-0*.json"))) == 1


def test_failing_semantic_repair_is_never_promoted_or_given_another_budget(
    request, monkeypatch
):
    root, key, _, parent, _, calls, folder, original = stopped_section(
        request, monkeypatch
    )

    def failed_review(self, messages, *, schema):
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] == composition.PROMPTS[author.REVIEW_PROMPT]:
            value["quiz"] = {
                "passed": False,
                "reason": "Fixture answer remains unsupported.",
            }
        return json.dumps(value), metrics

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", failed_review)
    with pytest.raises(ValueError, match="passing_existing_repair_semantic_review"):
        module.recover(root, key, parent["job"], folder.name, "section-0000-write")
    assert not (folder / "section-0000-write.json").exists()
    assert not (folder / "section-0000-review.json").exists()
    count = len(calls)
    progress = json.loads((folder / "progress.json").read_bytes())
    progress.update(pid=9999999, created=1)
    atomic_json(folder / "progress.json", progress)
    with pytest.raises(ValueError, match="passing_existing_repair_semantic_review"):
        module.recover(root, key, parent["job"], folder.name, "section-0000-write")
    assert len(calls) == count


def test_machine_preflight_does_not_repair_the_candidate_or_mask_other_errors(
    request, monkeypatch
):
    _, _, inventory, _, working, _, folder, _ = stopped_section(request, monkeypatch)
    candidate = json.loads(
        json.loads((folder / "attempts/section-0000-write-01.json").read_bytes())[
            "raw_response"
        ]
    )
    original = copy.deepcopy(candidate)
    ids = list(candidate["coverage"])
    audit, missing = module.machine_audit(candidate, ids, working, inventory)
    assert candidate == original and missing
    assert not any(author.verdicts(audit))
    candidate["diagrams"][0]["edges"][0]["to"] = "unknown"
    with pytest.raises(ValueError, match="unknown_or_unconnected_node"):
        module.machine_audit(candidate, ids, working, inventory)


def test_citations_require_a_complete_passing_factual_review(request, monkeypatch):
    _, _, inventory, _, working, _, folder, _ = stopped_section(request, monkeypatch)
    candidate = json.loads(
        json.loads((folder / "attempts/section-0000-write-01.json").read_bytes())[
            "raw_response"
        ]
    )
    original = copy.deepcopy(candidate)
    ids = list(candidate["coverage"])
    audit = review_value(author.review_schema(candidate, ids))
    annotated, changes = module.annotate_reviewed_candidate(
        candidate, audit, ids, working, inventory
    )
    assert changes and candidate == original
    assert [p["text"] for p in annotated["paragraphs"]] == [
        p["text"] for p in candidate["paragraphs"]
    ]
    assert annotated["coverage"] == candidate["coverage"]
    audit["coverage"][ids[0]]["passed"] = False
    with pytest.raises(ValueError, match="passing_unannotated_factual_review"):
        module.annotate_reviewed_candidate(candidate, audit, ids, working, inventory)


@pytest.mark.parametrize("passing", [True, False])
def test_exhausted_rewrite_uses_only_reserved_factual_reviews(
    request, monkeypatch, passing
):
    root, key, _, parent, _, calls, folder, original = stopped_section(
        request, monkeypatch
    )

    def bad_repair(self, messages, *, schema):
        raw, _ = original(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] == composition.PROMPTS[author.REPAIR_PROMPT]:
            context = json.loads(messages[1]["content"])
            first = value["paragraphs"][0]
            first["citations"] = [
                next(ref for ref in context["sources"] if ref not in first["citations"])
            ]
        return json.dumps(value), {
            "model_digest": "e" * 64,
            "done_reason": "stop",
            "prompt_eval_count": 100,
            "eval_count": 100,
            "context_length": 16384,
            "size_vram": 1024,
        }

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", bad_repair)
    with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
        module.recover(root, key, parent["job"], folder.name, "section-0000-write")
    failures = {
        p: p.read_bytes() for p in (folder / "attempts").glob("section-0000-*.json")
    }
    progress = json.loads((folder / "progress.json").read_bytes())
    progress.update(pid=9999999, created=1)
    atomic_json(folder / "progress.json", progress)

    def factual_review(self, messages, *, schema):
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if not passing:
            first = next(iter(value["coverage"]))
            value["coverage"][first]["passed"] = False
        return json.dumps(value), metrics

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", factual_review)
    before = len(calls)
    if passing:
        result = module.recover(
            root,
            key,
            parent["job"],
            folder.name,
            "section-0000-write",
            confirm_citations=True,
        )
        assert result["state"] == "existing_section_repair_ready_to_resume"
        assert len(calls) == before + 2
        author.run(root, key, parent["job"], protocol=composition)
        assert (folder / "private-article.json").is_file()
    else:
        with pytest.raises(ValueError, match="passing_unannotated_factual_review"):
            module.recover(
                root,
                key,
                parent["job"],
                folder.name,
                "section-0000-write",
                confirm_citations=True,
            )
        assert len(calls) == before + 1
        assert not (folder / "section-0000-write.json").exists()
        assert not (folder / "section-0000-repair.json").exists()
    assert all(p.read_bytes() == raw for p, raw in failures.items())
    assert len(list((folder / "attempts").glob("section-0000-repair-0*.json"))) == 2
