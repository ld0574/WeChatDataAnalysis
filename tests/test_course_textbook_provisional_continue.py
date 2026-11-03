import json

import pytest

from wechat_decrypt_tool import course_textbook_authoring as author
from wechat_decrypt_tool import course_textbook_composition as composition
from wechat_decrypt_tool import course_textbook_provisional_continue as module
from wechat_decrypt_tool import course_textbook_section_repair as repair
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_authoring import archive as archive
from test_course_textbook_composition import disputed_archive as disputed_archive
from test_course_textbook_section_repair import stopped_section


def failed_review(request, monkeypatch):
    root, key, _, parent, _, calls, folder, original = stopped_section(
        request, monkeypatch
    )

    def reply(self, messages, *, schema):
        raw, _ = original(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] == composition.PROMPTS[author.REPAIR_PROMPT]:
            context = json.loads(messages[1]["content"])
            first = value["paragraphs"][0]
            first["citations"] = [
                next(ref for ref in context["sources"] if ref not in first["citations"])
            ]
        elif messages[0]["content"] == composition.PROMPTS[author.REVIEW_PROMPT]:
            first = next(iter(value["coverage"]))
            value["coverage"][first]["passed"] = False
        return json.dumps(value), {
            "model_digest": "e" * 64,
            "done_reason": "stop",
            "prompt_eval_count": 100,
            "eval_count": 100,
            "context_length": 16384,
            "size_vram": 1024,
        }

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", reply)
    with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
        repair.recover(root, key, parent["job"], folder.name, "section-0000-write")
    progress_path = folder / "progress.json"
    progress = json.loads(progress_path.read_bytes())
    progress.update(pid=9999999, created=1)
    atomic_json(progress_path, progress)
    with pytest.raises(ValueError, match="passing_unannotated_factual_review"):
        repair.recover(
            root,
            key,
            parent["job"],
            folder.name,
            "section-0000-write",
            confirm_citations=True,
        )
    progress = json.loads(progress_path.read_bytes())
    progress.update(pid=9999999, created=1)
    atomic_json(progress_path, progress)
    monkeypatch.setattr(composition.AuthoringJob, "generate_response", original)
    return root, key, parent, calls, folder


def test_remaining_unused_slots_finish_private_manuscript_without_accepting_failure(
    request, monkeypatch
):
    root, key, parent, calls, folder = failed_review(request, monkeypatch)
    originals = {
        p: p.read_bytes() for p in (folder / "attempts").glob("section-0000-*.json")
    }
    result = module.run(root, key, parent["job"], folder.name, "section-0000")
    assert result["state"] == "article_requires_editorial_repair"
    assert result["quality_issues"] >= 3
    assert not result["automatic_local_article_review_passed"]
    private = repair.saved(folder / "private-article.json")["data"]
    assert private["unaccepted_sections"]["section-0000"]["accepted"] is False
    assert not (folder / "reading.json").exists()
    assert not (folder / "reading.html").exists()
    assert not (folder / "section-0000-write.json").exists()
    assert not (folder / "section-0000-repair.json").exists()
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    progress = json.loads((folder / "progress.json").read_bytes())
    assert progress["draft_sections_processed"] == result["sections"]
    assert progress["completed_sections"] == result["sections"] - 1
    assert progress["sections_retained_unaccepted"] == 1
    count = len(calls)
    progress.update(pid=9999999, created=1)
    atomic_json(folder / "progress.json", progress)
    assert module.run(root, key, parent["job"], folder.name, "section-0000") == result
    assert len(calls) == count
    with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
        author.run(root, key, parent["job"], protocol=composition)
    assert len(calls) == count


def test_continuation_contract_cannot_be_rebound_to_hide_the_failed_body(
    request, monkeypatch
):
    root, key, parent, calls, folder = failed_review(request, monkeypatch)
    module.prepare(root, key, parent["job"], folder.name, "section-0000")
    path = folder / "section-0000-provisional-continuation.json"
    value = json.loads(path.read_bytes())
    value["candidate_sha256"] = "0" * 64
    atomic_json(path, value)
    count = len(calls)
    with pytest.raises(ValueError, match="unchanged_continuation_contract"):
        module.prepare(root, key, parent["job"], folder.name, "section-0000")
    assert len(calls) == count
