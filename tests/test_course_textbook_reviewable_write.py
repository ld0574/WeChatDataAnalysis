import copy
import json

import pytest

from wechat_decrypt_tool import course_textbook_authoring as author
from wechat_decrypt_tool import course_textbook_composition as composition
from wechat_decrypt_tool import course_textbook_provisional_continue as provisional
from wechat_decrypt_tool import course_textbook_reviewable_write as module
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_authoring import archive as archive
from test_course_textbook_composition import disputed_archive as disputed_archive
from test_course_textbook_provisional_continue import failed_review
from test_course_textbook_section_repair import stopped_section


def mark_exited(folder):
    path = folder / "progress.json"
    value = json.loads(path.read_bytes())
    value.update(pid=9999999, created=1)
    atomic_json(path, value)


def test_citation_proposal_requires_real_final_review_and_keeps_failed_writes(
    request, monkeypatch
):
    root, key, _, parent, _, calls, folder, _ = stopped_section(request, monkeypatch)
    attempts = {p: p.read_bytes() for p in (folder / "attempts").iterdir()}
    before = len(calls)
    result = module.recover(root, key, parent["job"], folder.name, "section-0000-write")
    assert len(calls) == before + 2
    assert not result["semantic_repair_used"]
    assert not list((folder / "attempts").glob("section-0000-repair-0*.json"))
    assert result["citation_paragraphs_proposed"] > 0
    assert all(path.read_bytes() == raw for path, raw in attempts.items())
    before_cache = json.loads(
        (folder / "section-0000-review-before-reviewable-promotion.json").read_bytes()
    )
    after_cache = json.loads((folder / "section-0000-review.json").read_bytes())
    final_cache = json.loads((folder / "section-0000-repair-review.json").read_bytes())
    assert before_cache["input_sha256"] != after_cache["input_sha256"]
    assert after_cache["input_sha256"] == final_cache["input_sha256"]
    result = author.run(root, key, parent["job"], protocol=composition)
    assert result["job"] == folder.name
    assert (folder / "private-article.json").is_file()
    assert not (folder / "reading.json").exists()


def test_content_failure_uses_exactly_one_original_repair_then_final_review(
    request, monkeypatch
):
    root, key, _, parent, _, calls, folder, original = stopped_section(
        request, monkeypatch
    )

    def reply(self, messages, *, schema):
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if self.current == "section-0000-review":
            first = next(iter(value["coverage"]))
            value["coverage"][first] = {
                "passed": False,
                "reason": "Preserve this exact fixture condition in the explanation.",
            }
        if messages[0]["content"] == composition.PROMPTS[author.REPAIR_PROMPT]:
            for row in value["coverage"].values():
                row["reason"] = "Fixture mapping."
        return json.dumps(value), metrics

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", reply)
    before = len(calls)
    result = module.recover(root, key, parent["job"], folder.name, "section-0000-write")
    assert result["semantic_repair_used"] and len(calls) == before + 3
    repairs = [
        context
        for prompt, context in calls[before:]
        if prompt == composition.PROMPTS[author.REPAIR_PROMPT]
    ]
    assert len(repairs) == 1
    failed = [
        row for row in repairs[0]["review"]["coverage"].values() if not row["passed"]
    ]
    assert (
        failed[0]["reason"]
        == "Preserve this exact fixture condition in the explanation."
    )
    assert repairs[0]["sources"]
    assert len(list((folder / "attempts").glob("section-0000-repair-0*.json"))) == 1


def test_failed_final_audit_cannot_be_promoted_or_reset_on_restart(
    request, monkeypatch
):
    root, key, _, parent, _, calls, folder, original = stopped_section(
        request, monkeypatch
    )

    def reply(self, messages, *, schema):
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if self.current == "section-0000-repair-review":
            value["quiz"] = {
                "passed": False,
                "reason": "The fixture answer is still unsupported.",
            }
        return json.dumps(value), metrics

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", reply)
    for attempt in range(2):
        before = len(calls)
        with pytest.raises(ValueError, match="passing_final_factual_review"):
            module.recover(root, key, parent["job"], folder.name, "section-0000-write")
        assert len(calls) == before + (2 if attempt == 0 else 0)
        assert not (folder / "section-0000-write.json").exists()
        assert not (folder / "section-0000-reviewable-promotion.json").exists()
        mark_exited(folder)


def test_interrupted_cache_promotion_resumes_without_repeating_a_model_call(
    request, monkeypatch
):
    root, key, _, parent, _, calls, folder, _ = stopped_section(request, monkeypatch)
    real = module.atomic_json
    interrupted = []

    def crash(path, value):
        if (
            path.name == "section-0000-review.json"
            and "recovery_sha256" in value
            and not interrupted
        ):
            interrupted.append(True)
            raise OSError("fixture promotion interruption")
        return real(path, value)

    monkeypatch.setattr(module, "atomic_json", crash)
    with pytest.raises(OSError):
        module.recover(root, key, parent["job"], folder.name, "section-0000-write")
    assert (folder / "section-0000-write.json").is_file()
    before = len(calls)
    mark_exited(folder)
    result = module.recover(root, key, parent["job"], folder.name, "section-0000-write")
    assert result["state"] == "reviewed_section_ready_to_resume"
    assert len(calls) == before


def test_failed_guidance_is_lossless_and_source_proposal_does_not_change_body(
    request, monkeypatch
):
    _, _, inventory, _, working, _, folder, _ = stopped_section(request, monkeypatch)
    candidate = json.loads(
        json.loads((folder / "attempts/section-0000-write-01.json").read_bytes())[
            "raw_response"
        ]
    )
    old = copy.deepcopy(candidate)
    proposed, changes = module.propose_citations(
        candidate, list(candidate["coverage"]), working, inventory
    )
    assert changes and candidate == old
    for before, after in zip(
        candidate["paragraphs"], proposed["paragraphs"], strict=True
    ):
        assert before["text"] == after["text"]
    guidance = {
        "a": {"passed": True, "reason": "Long explanation of a passed check."},
        "b": {
            "passed": False,
            "reason": "Exact failed reason and numeric constraint 42.",
        },
    }
    assert module.repair_guidance(guidance)["b"] == guidance["b"]
    baseline = composition.section_schema(list(candidate["coverage"]), working)
    constrained = module.repair_schema(list(candidate["coverage"]), working)
    for key, row in constrained["properties"]["coverage"]["properties"].items():
        assert row["properties"]["reason"]["maxLength"] == 24
        row["properties"]["reason"] = baseline["properties"]["coverage"]["properties"][
            key
        ]["properties"]["reason"]
    assert constrained == baseline


def test_other_structure_errors_never_reach_a_model_review(request, monkeypatch):
    root, key, _, parent, _, calls, folder, _ = stopped_section(request, monkeypatch)
    for path in (folder / "attempts").glob("section-0000-write-*.json"):
        record = json.loads(path.read_bytes())
        value = json.loads(record["raw_response"])
        value["diagrams"][0]["edges"][0]["to"] = "unknown"
        record["raw_response"] = json.dumps(value)
        atomic_json(path, record)
    before = len(calls)
    with pytest.raises(ValueError, match="unknown_or_unconnected_node"):
        module.recover(root, key, parent["job"], folder.name, "section-0000-write")
    assert len(calls) == before


def test_context_after_an_unaccepted_section_matches_original_write_and_resumes(
    request, monkeypatch
):
    root, key, parent, calls, folder = failed_review(request, monkeypatch)
    original = composition.AuthoringJob.generate_response

    def fail_next_write(self, messages, *, schema):
        raw, _ = original(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] == composition.PROMPTS[author.WRITE_PROMPT]:
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

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", fail_next_write)
    with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
        provisional.run(root, key, parent["job"], folder.name, "section-0000")
    mark_exited(folder)
    monkeypatch.setattr(composition.AuthoringJob, "generate_response", original)
    protocol = provisional.protocol_for(
        root, key, parent["job"], folder.name, "section-0000"
    )
    old = {p: p.read_bytes() for p in (folder / "attempts").glob("section-0000-*.json")}
    module.recover(
        root,
        key,
        parent["job"],
        folder.name,
        "section-0001-write",
        unaccepted="section-0000",
    )
    result = author.run(root, key, parent["job"], protocol=protocol)
    assert result["state"] == "article_requires_editorial_repair"
    assert (folder / "private-article.json").is_file()
    assert not (folder / "reading.json").exists()
    assert all(path.read_bytes() == raw for path, raw in old.items())
