import copy
import json

import pytest

from wechat_decrypt_tool import course_textbook_working_preview as preview
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_textbook_evidence import sha

JOB = "a" * 32
LESSON = "b" * 24
PRIVATE = "PRIVATE_ORIGINAL_NOT_READING_CONTENT"


def store(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(data))


def saved(path, data):
    store(path, {"data": data, "data_sha256": sha(canonical(data))})


def authored():
    claim = {
        "text": "An independently written explanation <script>alert(1)</script>.",
        "citations": [PRIVATE],
    }
    return {
        "heading": "A concept",
        "paragraphs": [claim],
        "overview": claim,
        "takeaway": claim,
        "objective": "Understand the concept",
        "diagrams": [
            {
                "kind": "flow",
                "title": "An explanatory diagram",
                "caption": "A caption",
                "nodes": [
                    {"id": "a", "label": "Input"},
                    {"id": "b", "label": "Result"},
                ],
                "edges": [{"from": "a", "to": "b", "label": "produces"}],
                "citations": [PRIVATE],
                "private_original": PRIVATE,
            }
        ],
        "quiz": {
            "question": "How?",
            "answer": "The authored answer",
            "explanation": "Because",
            "criteria": ["Explain the connection"],
            "citations": [PRIVATE],
            "raw_transcript": PRIVATE,
        },
        "coverage": {"X": {"reason": PRIVATE, "paragraphs": [0]}},
        "raw_transcript": PRIVATE,
    }


@pytest.fixture
def job(tmp_path):
    folder = tmp_path / "wiki/textbook-composition-jobs" / JOB
    store(folder / "progress.json", {"stage": "section-0001-write", "pid": 12})
    saved(
        folder / "private-composition-inputs.json",
        {"lesson": LESSON, "private": PRIVATE},
    )
    saved(folder / "concept-plan.json", {"title": "An authored lesson"})
    saved(
        folder / "writing-allocation.json",
        [
            {
                "id": f"section-{i:04d}",
                "chapter_id": "chapter-0000",
                "chapter": {"heading": "A coherent chapter"},
                "concept": {"heading": "The next concept"},
            }
            for i in range(2)
        ],
    )
    candidate = authored()
    store(
        folder / "private-manuscript-continuation/section-0000.json",
        {
            "candidate": candidate,
            "candidate_sha256": sha(canonical(candidate)),
            "context": {"sources": PRIVATE},
            "accepted": False,
        },
    )
    return tmp_path, folder


def test_unfinished_snapshot_is_clean_and_does_not_modify_or_publish(job):
    root, folder = job
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    result = preview.write_preview(root, JOB)
    page = (root / "wiki/working-previews" / JOB / "working-draft.html").read_text(
        "utf-8"
    )
    receipt = json.loads(
        (root / "wiki/working-previews" / JOB / "snapshot.json").read_bytes()
    )
    assert PRIVATE not in page
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "工作稿 · 未完成 · 未验收" in page
    assert "已保留正文 1/2" in page
    assert "尚未写完" in page
    assert "<svg" in page and "The authored answer" in page
    assert result["final_article_ready"] is False
    assert receipt["quality_acceptance_changed"] is False
    assert not (root / "wiki/textbook-reading").exists()
    assert not (folder / "reading.json").exists()
    assert before == {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_tampered_retained_body_is_rejected(job):
    root, folder = job
    path = folder / "private-manuscript-continuation/section-0000.json"
    value = json.loads(path.read_bytes())
    value["candidate"]["paragraphs"][0]["text"] = "An unbound substitution"
    store(path, value)
    with pytest.raises(ValueError, match="changed_retained_candidate"):
        preview.snapshot(root, JOB)
    assert not (root / "wiki/working-previews").exists()


def test_partial_fields_with_invalid_graph_are_not_laundered_as_valid(job):
    root, folder = job
    value = authored()
    value["diagrams"][0]["edges"][0]["to"] = "unknown"
    store(
        folder / "section-0000-partial-fields-private-result.json",
        {
            "candidate": value,
            "candidate_sha256": sha(canonical(value)),
            "accepted": False,
        },
    )
    page, receipt = preview.snapshot(root, JOB)
    assert receipt["withheld_invalid_diagrams"] == 1
    assert "<svg" not in page
    assert receipt["sections"]["section-0000"] == "unaccepted_working_draft"
    assert PRIVATE not in page


def test_provisional_draft_must_match_its_exact_preserved_attempt(job):
    root, folder = job
    value = authored()
    attempt = folder / "attempts/section-0001-repair-01.json"
    store(attempt, {"raw_response": json.dumps(value), "accepted_structure": False})
    store(
        folder / "section-0001-provisional-continuation.json",
        {
            "candidate_sha256": sha(canonical(value)),
            "original_repair_attempts": {attempt.name: sha(attempt.read_bytes())},
        },
    )
    page, receipt = preview.snapshot(root, JOB)
    assert receipt["draft_sections_retained"] == 2
    assert "已保留正文 2/2" in page
    mutated = copy.deepcopy(value)
    mutated["quiz"]["answer"] = "Changed"
    store(attempt, {"raw_response": json.dumps(mutated)})
    with pytest.raises(ValueError, match="changed_original_attempt"):
        preview.snapshot(root, JOB)
