import copy
import json
import os

import psutil
import pytest

from wechat_decrypt_tool import course_textbook_authoring as author
from wechat_decrypt_tool import course_textbook_composition as composition
from wechat_decrypt_tool import course_textbook_deferred_repair as module
from wechat_decrypt_tool import course_textbook_manuscript_continue as continuation
from wechat_decrypt_tool import course_textbook_post_repair_review as post
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_textbook_evidence import sha
from test_course_textbook_authoring import archive as archive
from test_course_textbook_composition import disputed_archive as disputed_archive
from test_course_textbook_manuscript_continue import exited, schema_response
from test_course_textbook_provisional_continue import failed_review


def exit_progress(path):
    value = json.loads(path.read_bytes())
    value.update(pid=9999999, created=1)
    atomic_json(path, value)


def deferred_manuscript(request, monkeypatch):
    root, key, parent, calls, folder = failed_review(request, monkeypatch)
    original = schema_response(composition.AuthoringJob.generate_response)

    def negative_initial(self, messages, *, schema):
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if self.current == "section-0001-review":
            value["quiz"]["passed"] = False
            value["quiz"]["reason"] = "Complete failed factual review reason."
        return json.dumps(value), metrics

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", negative_initial)
    protocol = continuation.protocol_for(
        root, key, parent["job"], folder.name, "section-0000"
    )
    measure = protocol.AuthoringJob.request_size

    def oversized(self, name, *args):
        return 35001 if name == "section-0001-repair" else measure(self, name, *args)

    monkeypatch.setattr(protocol.AuthoringJob, "request_size", oversized)
    author.run(root, key, parent["job"], protocol=protocol)
    exited(folder)
    post.run(root, folder.name)
    exit_progress(folder / "post-repair-review-progress.json")
    monkeypatch.setattr(composition.AuthoringJob, "generate_response", original)
    return root, folder, calls, original


def test_original_unused_roles_repair_without_rewriting_history_or_accepting_other_failures(
    request, monkeypatch
):
    root, folder, calls, original = deferred_manuscript(request, monkeypatch)
    immutable = {
        p: p.read_bytes() for p in folder.rglob("*.json") if p.name != module.CONTRACT
    }
    count = len(calls)
    result = module.run(root, folder.name)
    assert len(calls) == count + 2
    assert result["adopted_sections"] == ["section-0001"]
    assert result["unaccepted_sections_remaining"] == 1
    assert result["original_budget_refreshed"] is False
    assert result["reader_publication_allowed"] is False
    assert result["whole_lesson_coverage_verified"] is False
    assert all(path.read_bytes() == data for path, data in immutable.items())
    assert not (folder / "reading.json").exists()
    exit_progress(folder / "deferred-original-repair-progress.json")
    private = module.verified_output(root, folder.name)
    assert set(private["unaccepted_sections"]) == {"section-0000"}
    assert len(calls) == count + 2
    contract = json.loads((folder / module.CONTRACT).read_bytes())
    readonly, inventory, working, plan, units, sections, _, _ = module.prepare(
        root, folder.name
    )
    context, _, _, _ = module.section_request(
        readonly, "section-0001", 1, units, plan, sections, working, inventory
    )
    source = post.context_for(1, units, plan, sections, working, inventory)
    assert context["sources"] == source["sources"]
    assert context["assigned_points"] == source["assigned_points"]
    assert (
        "Complete failed factual review reason." == context["review"]["quiz"]["reason"]
    )
    assert "chapter_scope" not in context and "previous_reading_section" not in context
    assert len(contract["eligible"]) == 1
    # Self-consistent summaries and hashes cannot replace actual model results.
    forged = copy.deepcopy(private)
    forged["chapters"][0]["sections"][0]["paragraphs"][0]["text"] += " Forged change."
    author.save(folder, module.OUTPUT.removesuffix(".json"), forged)
    summary = post.saved(folder / "deferred-original-repair-result.json")["data"]
    summary["private_article_sha256"] = sha(canonical(forged))
    author.save(folder, "deferred-original-repair-result", summary)
    with pytest.raises(ValueError, match="exact_result_from_real_calls"):
        module.verified_output(root, folder.name)


def test_negative_final_review_remains_unaccepted_and_is_not_reasked(
    request, monkeypatch
):
    root, folder, calls, original = deferred_manuscript(request, monkeypatch)

    def unsupported(self, messages, *, schema):
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if self.current == "section-0001-repair-review":
            value["quiz"]["passed"] = False
        return json.dumps(value), metrics

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", unsupported)
    result = module.run(root, folder.name)
    assert result["adopted_sections"] == []
    assert result["unaccepted_sections_remaining"] == 2
    attempts = {p: p.read_bytes() for p in (folder / "attempts").glob("*.json")}
    count = len(calls)
    assert module.run(root, folder.name) == result
    assert len(calls) == count
    assert all(p.read_bytes() == raw for p, raw in attempts.items())
    exit_progress(folder / "deferred-original-repair-progress.json")
    assert len(module.verified_output(root, folder.name)["unaccepted_sections"]) == 2


def test_two_malformed_calls_exhaust_original_role_across_restarts(
    request, monkeypatch
):
    root, folder, calls, original = deferred_manuscript(request, monkeypatch)
    invocations = []

    def malformed(self, messages, *, schema):
        invocations.append(self.current)
        return "{}", None

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", malformed)
    result = module.run(root, folder.name)
    assert invocations == ["section-0001-repair"] * 2
    assert result["adopted_sections"] == []
    assert not (folder / "section-0001-repair-review.json").exists()
    assert module.run(root, folder.name) == result
    assert len(invocations) == 2
    exit_progress(folder / "deferred-original-repair-progress.json")
    assert len(module.verified_output(root, folder.name)["unaccepted_sections"]) == 2


def test_live_writer_cannot_allocate_deferred_repair_contract(request, monkeypatch):
    root, folder, calls, original = deferred_manuscript(request, monkeypatch)
    state = json.loads((folder / "progress.json").read_bytes())
    state.update(pid=os.getpid(), created=psutil.Process().create_time())
    atomic_json(folder / "progress.json", state)
    count = len(calls)
    with pytest.raises(ValueError, match="writer_exited"):
        module.run(root, folder.name)
    assert not (folder / module.CONTRACT).exists()
    assert len(calls) == count


def test_still_oversized_complete_request_is_skipped_without_truncation_or_calls(
    request, monkeypatch
):
    root, folder, calls, original = deferred_manuscript(request, monkeypatch)
    measure = module.request_size

    def oversized(*args):
        _, binding = measure(*args)
        return 35001, binding

    monkeypatch.setattr(module, "request_size", oversized)
    count = len(calls)
    result = module.run(root, folder.name)
    assert result["eligible"] == 0
    assert result["adopted_sections"] == []
    assert len(calls) == count
    assert not list((folder / "attempts").glob("section-0001-repair-*.json"))
    contract = json.loads((folder / module.CONTRACT).read_bytes())
    assert (
        contract["skipped"]["section-0001"]
        == "complete_compact_request_still_exceeds_limit"
    )
