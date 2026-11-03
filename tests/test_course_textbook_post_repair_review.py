import json
import os

import psutil
import pytest

from wechat_decrypt_tool import course_textbook_authoring as author
from wechat_decrypt_tool import course_textbook_composition as composition
from wechat_decrypt_tool import course_textbook_manuscript_continue as continuation
from wechat_decrypt_tool import course_textbook_post_repair_review as module
from wechat_decrypt_tool import course_textbook_section_repair as repair
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_authoring import archive as archive
from test_course_textbook_composition import disputed_archive as disputed_archive
from test_course_textbook_manuscript_continue import (
    exited,
    missing_primary,
    schema_response,
)
from test_course_textbook_provisional_continue import failed_review


def failed_manuscript(request, monkeypatch, *, broken_latest=False):
    root, key, parent, calls, folder = failed_review(request, monkeypatch)
    original = schema_response(composition.AuthoringJob.generate_response)
    repair_calls = []

    def reply(self, messages, *, schema):
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] in {
            composition.PROMPTS[author.WRITE_PROMPT],
            composition.PROMPTS[author.REPAIR_PROMPT],
        }:
            missing_primary(value, json.loads(messages[1]["content"]))
            if messages[0]["content"] == composition.PROMPTS[author.REPAIR_PROMPT]:
                repair_calls.append(1)
                if broken_latest and len(repair_calls) == 2:
                    value["diagrams"][0]["edges"][0]["to"] = "unknown"
        elif self.current == "section-0001-review":
            value["quiz"]["passed"] = False
        return json.dumps(value), metrics

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", reply)
    continuation.run(root, key, parent["job"], folder.name, "section-0000")
    exited(folder)
    return root, key, calls, folder, reply


def test_only_unused_original_post_repair_role_can_adopt_a_private_candidate(
    request, monkeypatch
):
    root, key, calls, folder, reply = failed_manuscript(request, monkeypatch)
    original_article = (folder / "private-article.json").read_bytes()
    original_result = (folder / "result.json").read_bytes()
    attempts = {path: path.read_bytes() for path in (folder / "attempts").iterdir()}
    count = len(calls)
    result = module.run(root, folder.name)
    assert len(calls) == count + 1
    assert result["adopted_sections"] == ["section-0001"]
    assert result["skipped_sections"] == {
        "section-0000": "original_post_repair_role_already_used"
    }
    assert result["unaccepted_sections_remaining"] == 1
    assert result["semantic_accuracy_verified"] is False
    assert result["whole_lesson_coverage_verified"] is False
    assert result["reader_publication_allowed"] is False
    updated = repair.saved(folder / module.OUTPUT)["data"]
    assert set(updated["unaccepted_sections"]) == {"section-0000"}
    assert (folder / "private-article.json").read_bytes() == original_article
    assert (folder / "result.json").read_bytes() == original_result
    assert all(path.read_bytes() == data for path, data in attempts.items())
    assert not (folder / "section-0001-write.json").exists()
    assert not (folder / "section-0001-repair.json").exists()
    assert not (folder / "reading.json").exists()
    assert not (folder / "reading.html").exists()
    count = len(calls)
    assert module.run(root, folder.name) == result
    assert len(calls) == count
    assert all(path.read_bytes() == data for path, data in attempts.items())


def test_failed_real_factual_review_does_not_adopt_or_repeat_it(request, monkeypatch):
    root, key, calls, folder, reply = failed_manuscript(request, monkeypatch)

    def unsupported(self, messages, *, schema):
        raw, metrics = reply(self, messages, schema=schema)
        value = json.loads(raw)
        if self.current == "section-0001-repair-review":
            value["quiz"]["passed"] = False
        return json.dumps(value), metrics

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", unsupported)
    result = module.run(root, folder.name)
    assert result["adopted_sections"] == []
    assert set(result["failed_sections"]) == {"section-0001"}
    assert result["unaccepted_sections_remaining"] == 2
    original = repair.saved(folder / "private-article.json")["data"]
    updated = repair.saved(folder / module.OUTPUT)["data"]
    assert updated["chapters"] == original["chapters"]
    count = len(calls)
    assert module.run(root, folder.name) == result
    assert len(calls) == count
    assert (
        len(list((folder / "attempts").glob("section-0001-repair-review-*.json"))) == 1
    )


def test_bad_schema_uses_original_two_attempts_and_cannot_refresh_on_restart(
    request, monkeypatch
):
    root, key, calls, folder, reply = failed_manuscript(request, monkeypatch)
    invocations = []

    def malformed(self, messages, *, schema):
        invocations.append(self.current)
        return "{}", None

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", malformed)
    result = module.run(root, folder.name)
    assert invocations == ["section-0001-repair-review"] * 2
    assert result["exhausted_stages"] == ["section-0001"]
    assert result["adopted_sections"] == []
    assert result["unaccepted_sections_remaining"] == 2
    paths = list((folder / "attempts").glob("section-0001-repair-review-*.json"))
    assert len(paths) == 2
    before = {path: path.read_bytes() for path in paths}
    assert module.run(root, folder.name) == result
    assert len(invocations) == 2
    assert all(path.read_bytes() == data for path, data in before.items())


def test_complete_oversized_request_is_skipped_without_trimming_or_calling(
    request, monkeypatch
):
    root, key, calls, folder, reply = failed_manuscript(request, monkeypatch)
    monkeypatch.setattr(module, "CONTEXT_LIMIT", 1)
    count = len(calls)
    result = module.run(root, folder.name)
    assert len(calls) == count
    assert result["eligible"] == 0
    assert (
        result["skipped_sections"]["section-0001"]
        == "complete_original_review_request_exceeds_limit"
    )
    assert result["adopted_sections"] == []
    assert not list((folder / "attempts").glob("section-0001-repair-review-*.json"))


def test_live_writer_cannot_be_reviewed_in_parallel(request, monkeypatch):
    root, key, calls, folder, reply = failed_manuscript(request, monkeypatch)
    path = folder / "progress.json"
    state = json.loads(path.read_bytes())
    state.update(pid=os.getpid(), created=psutil.Process().create_time())
    atomic_json(path, state)
    count = len(calls)
    with pytest.raises(ValueError, match="writer_exited"):
        module.run(root, folder.name)
    assert len(calls) == count
    assert not (folder / module.CONTRACT).exists()


def test_changed_repair_attempt_is_rejected_instead_of_rebinding_a_review(
    request, monkeypatch
):
    root, key, calls, folder, reply = failed_manuscript(request, monkeypatch)
    module.run(root, folder.name)
    execution = json.loads((folder / module.CONTRACT).read_bytes())
    path = folder / "attempts" / execution["eligible"]["section-0001"]["attempt"]
    value = json.loads(path.read_bytes())
    value["raw_response"] += " "
    atomic_json(path, value)
    count = len(calls)
    with pytest.raises(ValueError, match="unchanged_repair_attempt"):
        module.run(root, folder.name)
    assert len(calls) == count


def test_invalid_latest_repair_cannot_mask_an_earlier_reviewable_repair(
    request, monkeypatch
):
    root, key, calls, folder, reply = failed_manuscript(
        request, monkeypatch, broken_latest=True
    )
    before = {p: p.read_bytes() for p in (folder / "attempts").iterdir()}
    result = module.run(root, folder.name)
    assert result["adopted_sections"] == ["section-0001"]
    execution = json.loads((folder / module.CONTRACT).read_bytes())
    assert (
        execution["eligible"]["section-0001"]["attempt"]
        == "section-0001-repair-01.json"
    )
    assert "section-0001-repair-02.json" in execution["invalid_candidate_attempts"]
    assert all(p.read_bytes() == value for p, value in before.items())


def passing_write_manuscript(request, monkeypatch, *, passing=True):
    root, key, parent, calls, folder = failed_review(request, monkeypatch)
    original = schema_response(composition.AuthoringJob.generate_response)

    def reply(self, messages, *, schema):
        if messages[0]["content"] == composition.PROMPTS[author.REPAIR_PROMPT]:
            return "{}", None
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] == composition.PROMPTS[author.WRITE_PROMPT]:
            missing_primary(value, json.loads(messages[1]["content"]))
        elif self.current == "section-0001-review" and not passing:
            value["quiz"]["passed"] = False
        return json.dumps(value), metrics

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", reply)
    continuation.run(root, key, parent["job"], folder.name, "section-0000")
    exited(folder)
    return root, key, calls, folder, reply


def test_passing_pre_reviewed_write_gets_only_citation_proposal_and_original_post_review(
    request, monkeypatch
):
    root, key, calls, folder, reply = passing_write_manuscript(request, monkeypatch)
    originals = {p: p.read_bytes() for p in (folder / "attempts").iterdir()}
    private = repair.saved(folder / "private-article.json")["data"]
    count = len(calls)
    result = module.run(root, folder.name)
    assert len(calls) == count + 1
    assert result["adopted_sections"] == ["section-0001"]
    execution = json.loads((folder / module.CONTRACT).read_bytes())
    assert execution["eligible"]["section-0001"]["candidate_kind"] == "write"
    updated = repair.saved(folder / module.OUTPUT)["data"]
    before = next(
        s
        for c in private["chapters"]
        for s in c["sections"]
        if s["id"] == "section-0001"
    )
    after = next(
        s
        for c in updated["chapters"]
        for s in c["sections"]
        if s["id"] == "section-0001"
    )
    assert [p["text"] for p in before["paragraphs"]] == [
        p["text"] for p in after["paragraphs"]
    ]
    assert before["coverage"] == after["coverage"]
    assert before["diagrams"] == after["diagrams"]
    assert before["quiz"] == after["quiz"]
    assert before["paragraphs"] != after["paragraphs"]
    assert not list((folder / "attempts").glob("section-0001-repair-0*.json"))
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert module.run(root, folder.name) == result
    assert len(calls) == count + 1


def test_negative_pre_review_cannot_be_reasked_as_a_citation_only_write(
    request, monkeypatch
):
    root, key, calls, folder, reply = passing_write_manuscript(
        request, monkeypatch, passing=False
    )
    count = len(calls)
    result = module.run(root, folder.name)
    assert result["adopted_sections"] == []
    assert result["eligible"] == 0
    assert len(calls) == count
    assert not list((folder / "attempts").glob("section-0001-repair-review-*.json"))
