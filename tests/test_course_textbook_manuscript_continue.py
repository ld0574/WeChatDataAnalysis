import json

import pytest

from wechat_decrypt_tool import course_textbook_authoring as author
from wechat_decrypt_tool import course_textbook_composition as composition
from wechat_decrypt_tool import course_textbook_manuscript_continue as module
from wechat_decrypt_tool import course_textbook_provisional_continue as provisional
from wechat_decrypt_tool import course_textbook_reviewable_write as reviewable
from wechat_decrypt_tool import course_textbook_section_repair as repair
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_authoring import archive as archive
from test_course_textbook_composition import disputed_archive as disputed_archive
from test_course_textbook_provisional_continue import failed_review

METRICS = {
    "model_digest": "e" * 64,
    "done_reason": "stop",
    "prompt_eval_count": 100,
    "eval_count": 100,
    "context_length": 16384,
    "size_vram": 1024,
}


def schema_response(original):
    def reply(self, messages, *, schema):
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if "paragraphs" in value:
            fields = schema["properties"]["coverage"]["properties"]
            for key, row in value["coverage"].items():
                if fields[key]["properties"]["reason"].get("maxLength") == 24:
                    row["reason"] = "Fixture mapping."
        return json.dumps(value), metrics or METRICS

    return reply


def exited(folder):
    path = folder / "progress.json"
    value = json.loads(path.read_bytes())
    value.update(pid=9999999, created=1)
    atomic_json(path, value)


def missing_primary(value, context):
    paragraph = value["paragraphs"][0]
    paragraph["citations"] = [
        next(ref for ref in context["sources"] if ref not in paragraph["citations"])
    ]


def preflight_failed_second(request, monkeypatch):
    root, key, parent, calls, folder = failed_review(request, monkeypatch)
    original = schema_response(composition.AuthoringJob.generate_response)

    def write_failure(self, messages, *, schema):
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] == composition.PROMPTS[author.WRITE_PROMPT]:
            missing_primary(value, json.loads(messages[1]["content"]))
        return json.dumps(value), metrics or METRICS

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", write_failure)
    with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
        provisional.run(root, key, parent["job"], folder.name, "section-0000")
    exited(folder)

    def repair_preflight(self, messages, *, schema):
        if messages[0]["content"] == composition.PROMPTS[author.REPAIR_PROMPT]:
            raise ValueError("requires_textbook_authoring_smaller_complete_context")
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] == composition.PROMPTS[author.REVIEW_PROMPT]:
            first = next(iter(value["coverage"]))
            value["coverage"][first]["passed"] = False
        return json.dumps(value), metrics or METRICS

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", repair_preflight)
    with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
        reviewable.recover(
            root,
            key,
            parent["job"],
            folder.name,
            "section-0001-write",
            unaccepted="section-0000",
        )
    exited(folder)
    monkeypatch.setattr(composition.AuthoringJob, "generate_response", original)
    return root, key, parent, calls, folder


def test_terminal_preflight_slots_are_not_reset_and_private_manuscript_finishes(
    request, monkeypatch
):
    root, key, parent, calls, folder = preflight_failed_second(request, monkeypatch)
    originals = {path: path.read_bytes() for path in (folder / "attempts").iterdir()}
    result = module.run(root, key, parent["job"], folder.name, "section-0000")
    assert result["state"] == "article_requires_editorial_repair"
    assert not result["automatic_local_article_review_passed"]
    private = repair.saved(folder / "private-article.json")["data"]
    assert set(private["unaccepted_sections"]) == {"section-0000", "section-0001"}
    assert all(
        row["accepted"] is False for row in private["unaccepted_sections"].values()
    )
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert not list((folder / "attempts").glob("section-0001-repair-review-*.json"))
    assert not (folder / "section-0001-write.json").exists()
    assert not (folder / "section-0001-repair.json").exists()
    assert not (folder / "reading.json").exists()
    assert not (folder / "reading.html").exists()
    receipt = json.loads(
        (folder / "private-manuscript-continuation/section-0001.json").read_bytes()
    )
    assert set(receipt["deferred_stages"]) == {
        "section-0001-repair",
        "section-0001-repair-review",
    }
    assert all(
        not row["model_call_performed"] for row in receipt["deferred_stages"].values()
    )
    count = len(calls)
    exited(folder)
    assert module.run(root, key, parent["job"], folder.name, "section-0000") == result
    assert len(calls) == count
    assert all(path.read_bytes() == raw for path, raw in originals.items())


def test_oversized_unused_repair_does_not_consume_either_original_attempt(
    request, monkeypatch
):
    root, key, parent, calls, folder = failed_review(request, monkeypatch)
    original = schema_response(composition.AuthoringJob.generate_response)
    stages = []

    def failed_second_review(self, messages, *, schema):
        stages.append(self.current)
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] == composition.PROMPTS[author.REVIEW_PROMPT]:
            value["quiz"]["passed"] = False
        return json.dumps(value), metrics or METRICS

    monkeypatch.setattr(
        composition.AuthoringJob, "generate_response", failed_second_review
    )
    protocol = module.protocol_for(
        root, key, parent["job"], folder.name, "section-0000"
    )
    measure = protocol.AuthoringJob.request_size

    def oversized(self, name, *args):
        return 35001 if name == "section-0001-repair" else measure(self, name, *args)

    monkeypatch.setattr(protocol.AuthoringJob, "request_size", oversized)
    result = author.run(root, key, parent["job"], protocol=protocol)
    assert result["state"] == "article_requires_editorial_repair"
    assert "section-0001-review" in stages
    assert not any(stage.startswith("section-0001-repair") for stage in stages)
    assert not list((folder / "attempts").glob("section-0001-repair-*.json"))
    assert not (folder / "reading.json").exists()


def test_primary_citation_failure_is_retained_but_not_called_an_accepted_section(
    request, monkeypatch
):
    root, key, parent, calls, folder = failed_review(request, monkeypatch)
    original = schema_response(composition.AuthoringJob.generate_response)

    def missing(self, messages, *, schema):
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] == composition.PROMPTS[author.WRITE_PROMPT]:
            missing_primary(value, json.loads(messages[1]["content"]))
        return json.dumps(value), metrics or METRICS

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", missing)
    result = module.run(root, key, parent["job"], folder.name, "section-0000")
    assert result["state"] == "article_requires_editorial_repair"
    assert len(list((folder / "attempts").glob("section-0001-write-*.json"))) == 2
    assert not (folder / "section-0001-write.json").exists()
    private = repair.saved(folder / "private-article.json")["data"]
    assert private["unaccepted_sections"]["section-0001"]["accepted"] is False
    progress = json.loads((folder / "progress.json").read_bytes())
    assert progress["completed_sections"] == result["sections"] - 2
    assert progress["sections_retained_unaccepted"] == 2
    assert not (folder / "reading.json").exists()


def test_non_citation_structure_failure_cannot_become_a_manuscript_section(
    request, monkeypatch
):
    root, key, parent, calls, folder = failed_review(request, monkeypatch)
    original = schema_response(composition.AuthoringJob.generate_response)

    def invalid(self, messages, *, schema):
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] == composition.PROMPTS[author.WRITE_PROMPT]:
            value["diagrams"][0]["edges"][0]["to"] = "unknown"
        return json.dumps(value), metrics or METRICS

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", invalid)
    with pytest.raises(ValueError, match="complete_failed_write"):
        module.run(root, key, parent["job"], folder.name, "section-0000")
    assert not (folder / "private-article.json").exists()
    assert not (folder / "reading.json").exists()
    assert not list((folder / "private-manuscript-continuation").glob("section-*.json"))


def test_receipt_tampering_is_rejected_before_new_calls(request, monkeypatch):
    root, key, parent, calls, folder = preflight_failed_second(request, monkeypatch)
    module.run(root, key, parent["job"], folder.name, "section-0000")
    exited(folder)
    path = folder / "private-manuscript-continuation/section-0001.json"
    receipt = json.loads(path.read_bytes())
    receipt["candidate"]["paragraphs"][0]["text"] = "Changed fixture."
    atomic_json(path, receipt)
    count = len(calls)
    with pytest.raises(ValueError, match="unchanged_private_candidate_and_context"):
        module.run(root, key, parent["job"], folder.name, "section-0000")
    assert len(calls) == count


def test_real_original_repair_and_review_can_resolve_a_retained_citation_failure(
    request, monkeypatch
):
    root, key, parent, calls, folder = failed_review(request, monkeypatch)
    original = schema_response(composition.AuthoringJob.generate_response)

    def respond(self, messages, *, schema):
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] == composition.PROMPTS[author.WRITE_PROMPT]:
            missing_primary(value, json.loads(messages[1]["content"]))
        elif self.current == "section-0001-review":
            value["quiz"]["passed"] = False
        return json.dumps(value), metrics or METRICS

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", respond)
    result = module.run(root, key, parent["job"], folder.name, "section-0000")
    private = repair.saved(folder / "private-article.json")["data"]
    assert set(private["unaccepted_sections"]) == {"section-0000"}
    receipt = json.loads(
        (folder / "private-manuscript-continuation/section-0001.json").read_bytes()
    )
    assert receipt["resolved_with_original_roles"] is True
    assert len(list((folder / "attempts").glob("section-0001-repair-0*.json"))) == 1
    assert (
        len(list((folder / "attempts").glob("section-0001-repair-review-*.json"))) == 1
    )
    assert result["state"] == "article_requires_editorial_repair"
    assert not (folder / "reading.json").exists()


def test_only_unstarted_sections_get_shorter_internal_mapping_reasons(
    request, monkeypatch
):
    root, key, parent, calls, folder = failed_review(request, monkeypatch)
    protocol = module.protocol_for(
        root, key, parent["job"], folder.name, "section-0000"
    )
    inventory, working = composition.load_inputs(root, key, parent["job"])
    units = repair.saved(folder / "writing-allocation.json")["data"]
    assert protocol.section_schema(
        units[0]["point_ids"], working
    ) == composition.section_schema(units[0]["point_ids"], working)
    original = composition.section_schema(units[1]["point_ids"], working)
    projected = protocol.section_schema(units[1]["point_ids"], working)
    assert projected != original
    for key, row in projected["properties"]["coverage"]["properties"].items():
        assert row["properties"]["reason"]["maxLength"] == 24
        row["properties"]["reason"] = original["properties"]["coverage"]["properties"][
            key
        ]["properties"]["reason"]
    assert projected == original
