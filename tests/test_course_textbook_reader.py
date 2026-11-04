import copy
import json

import pytest
from fastapi.testclient import TestClient

from wechat_decrypt_tool import course_textbook_reader as reader
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_textbook_article import reading_book
from wechat_decrypt_tool.course_textbook_evidence import inventory, sha


def sealed(path, data):
    atomic_json(path, {"data": data, "data_sha256": sha(canonical(data))})


@pytest.fixture
def publication(tmp_path):
    course, job, ledger_job = "a" * 24, "b" * 32, "c" * 32
    private_marker = "PRIVATE_RAW_SOURCE_AND_PATH"
    value = inventory(
        {
            "id": course,
            "source_export_sha256": "d" * 64,
            "evidence": {
                "one": {"kind": "speech", "text": private_marker, "start": 0, "end": 1}
            },
        },
        lesson_artifact="e" * 64,
    )
    invkey = sha(canonical(value))
    folder = tmp_path / "wiki/textbook-evidence" / invkey
    folder.mkdir(parents=True)
    atomic_json(folder / "manifest.json", value)
    ref = next(iter(value["sources"]))
    ledger = {
        "job": ledger_job,
        "lesson": course,
        "inventory_sha256": invkey,
        "points": {
            "P0": {
                "label": "Fixture principle",
                "teaching": "A condition governs its action.",
                "category": "concept",
                "citations": [ref],
            }
        },
        "source_dispositions": {ref: {"points": ["P0"], "non_teaching_reason": ""}},
        "pages": [{"page": 0, "points": ["P0"], "local_review_passed": True}],
        "issues": [],
        "automatic_local_evidence_review_passed": True,
        "whole_lesson_knowledge_coverage_verified": False,
        "semantic_accuracy_verified": False,
        "reader_article_published": False,
        "source_deletion_allowed": False,
    }
    dest = tmp_path / "wiki/textbook-keyed-jobs" / ledger_job
    dest.mkdir(parents=True)
    sealed(dest / "ledger.json", ledger)
    claim = {
        "text": "An authored explanation of fixture conditions and consequences.",
        "citations": [ref],
        "raw_text": private_marker,
    }
    graph = {
        "kind": "flow",
        "title": "Fixture relationship",
        "caption": "An authored explanation of the dependency.",
        "nodes": [
            {"id": "condition", "label": "Check applicability"},
            {"id": "action", "label": "Apply operation"},
        ],
        "edges": [
            {"from": "condition", "to": "action", "label": "eligible transition"}
        ],
        "citations": [ref],
        "image_sha256": private_marker,
    }
    private = {
        "course_id": course,
        "title": "Understanding fixture dependencies",
        "introduction": [claim],
        "objectives": ["Explain when an operation applies."],
        "prerequisites": [],
        "chapters": [
            {
                "id": "chapter-a",
                "heading": "Read the dependency",
                "sections": [
                    {
                        "id": "section-a",
                        "heading": "Conditions and consequences",
                        "paragraphs": [claim],
                        "diagrams": [graph],
                    }
                ],
            }
        ],
        "takeaways": [claim],
        "quiz": [
            {
                "question": "When can the fixture operation apply?",
                "answer": "After its condition holds.",
                "explanation": "The condition governs applicability.",
                "criteria": ["Explain the dependency."],
                "citations": [ref],
            }
        ],
        "related_courses": [],
        "inventory_sha256": invkey,
        "ledger_job": ledger_job,
        "private_coverage": {"P0": {"section": "section-a", "paragraphs": [0]}},
    }
    result = {
        "job": job,
        "state": "reading_draft_ready_for_actual_review",
        "quality_issues": 0,
        "automatic_local_article_review_passed": True,
        "source_deletion_allowed": False,
        "inventory_sha256": invkey,
        "ledger_job": ledger_job,
    }
    authoring = tmp_path / "wiki/textbook-authoring-jobs" / job
    authoring.mkdir(parents=True)
    for name, data in [
        ("result", result),
        ("private-article", private),
        ("quality-issues", []),
    ]:
        sealed(authoring / (name + ".json"), data)
    atomic_json(authoring / "reading.json", reading_book(private))
    (tmp_path / "library").mkdir()
    atomic_json(
        tmp_path / "library/catalog.json",
        {
            "lessons": [
                {
                    "id": course,
                    "title": "Old source title",
                    "collection": "Synthetic learning",
                    "artifact": private_marker,
                    "relative_path": private_marker,
                    "state": "draft_available",
                },
                {
                    "id": "f" * 24,
                    "title": "An unfinished course",
                    "collection": "Synthetic learning",
                    "artifact": private_marker,
                    "state": "draft_available",
                },
            ]
        },
    )
    return tmp_path, job, course, authoring, private


def test_publish_exposes_only_projection_and_never_promotes_unfinished_notes(
    publication,
):
    root, job, course, _, _ = publication
    status = reader.publish(root, job)
    store = reader.Reader(root)
    catalog = store.catalog()
    assert catalog["requested"] == 2 and catalog["reading_drafts"] == 1
    assert catalog["lessons"][0]["title"] == "Understanding fixture dependencies"
    assert catalog["lessons"][1]["state"] == "organizing"
    with pytest.raises(reader.ArticleNotReady):
        store.article("f" * 24)
    public = json.dumps([catalog, store.article(course), store.search("condition")])
    for forbidden in (
        "PRIVATE_RAW_SOURCE",
        "raw_text",
        "citations",
        "image_sha256",
        "relative_path",
        "private_coverage",
        "ledger_job",
        "inventory_sha256",
    ):
        assert forbidden not in public
    assert status["state"] == "review_draft" and not status["source_deletion_allowed"]
    assert not store.search("PRIVATE_RAW_SOURCE")
    assert store.search("eligible transition")[0]["section_id"] == "section-a"
    assert store.search("After its condition")[0]["section_id"] == "reading-self-test"


@pytest.mark.parametrize(
    "mutation",
    [
        "failed_review",
        "issues",
        "wrong_inventory",
        "missing_coverage",
        "dirty_export",
        "changed_checksum",
    ],
)
def test_publication_refuses_incomplete_or_inconsistent_proof(publication, mutation):
    root, job, _, authoring, private = publication
    if mutation == "failed_review":
        saved = json.loads((authoring / "result.json").read_bytes())["data"]
        saved["automatic_local_article_review_passed"] = False
        sealed(authoring / "result.json", saved)
    elif mutation == "issues":
        sealed(authoring / "quality-issues.json", [{"kind": "fixture unresolved"}])
    elif mutation == "wrong_inventory":
        private["inventory_sha256"] = "0" * 64
        sealed(authoring / "private-article.json", private)
    elif mutation == "missing_coverage":
        private["private_coverage"] = {}
        sealed(authoring / "private-article.json", private)
    elif mutation == "dirty_export":
        book = reading_book(private)
        book["raw_text"] = "PRIVATE_RAW_SOURCE"
        atomic_json(authoring / "reading.json", book)
    else:
        saved = json.loads((authoring / "private-article.json").read_bytes())
        saved["data"]["title"] = "Changed after checksum"
        atomic_json(authoring / "private-article.json", saved)
    with pytest.raises(ValueError):
        reader.publish(root, job)
    assert not (root / "wiki/textbook-reading/catalog.json").exists()


def test_http_read_search_and_export_are_clean_and_unknown_paths_do_not_expose_sources(
    publication,
):
    root, job, course, _, _ = publication
    reader.publish(root, job)
    client = TestClient(reader.create_app(root))
    for path in [
        "/",
        "/api/catalog",
        "/api/article/" + course,
        "/api/search?q=condition",
        "/read/" + course,
        "/export/" + course,
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert "PRIVATE_RAW_SOURCE" not in response.text
        assert "private_coverage" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "<details>" in client.get("/read/" + course).text
    assert "<svg " in client.get("/read/" + course).text
    assert (
        'attachment; filename="course-'
        in client.get("/export/" + course).headers["content-disposition"]
    )
    for path in [
        "/api/article/" + "f" * 24,
        "/api/article/not-an-id",
        "/library/api/lesson/" + course,
        "/wiki/textbook-authoring-jobs/" + job + "/private-article.json",
    ]:
        assert client.get(path).status_code == 404
    html = client.get("/", params={"q": '<script>alert("fixture")</script>'}).text
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_corrupt_objects_fail_closed_even_after_a_successful_cached_request(
    publication,
):
    root, job, course, _, private = publication
    status = reader.publish(root, job)
    client = TestClient(reader.create_app(root))
    assert client.get("/api/article/" + course).status_code == 200
    obj = root / "wiki/textbook-reading/objects" / (status["artifact"] + ".json")
    book = reading_book(private)
    book["title"] = "Corrupted reading object"
    obj.write_bytes(canonical(book))
    assert client.get("/api/article/" + course).status_code == 503
    # Even a rehashed dirty object must satisfy the strict public contract.
    book["raw_text"] = "PRIVATE_RAW_SOURCE"
    raw = canonical(book)
    new = sha(raw)
    obj.with_name(new + ".json").write_bytes(raw)
    index = reader.Reader(root).index()
    index["lessons"][course]["artifact"] = new
    atomic_json(root / "wiki/textbook-reading/catalog.json", index)
    assert client.get("/api/article/" + course).status_code == 503


def test_new_versions_keep_history_and_revision_identity(publication):
    root, job, course, folder, private = publication
    old = reader.publish(root, job)
    original = copy.deepcopy(private)
    private["title"] = "A revised fixture article"
    sealed(folder / "private-article.json", private)
    atomic_json(folder / "reading.json", reading_book(private))
    current = reader.publish(root, job)
    assert current["artifact"] != old["artifact"]
    index = reader.Reader(root).index()
    assert index["history"] == [
        {"course_id": course, "artifact": old["artifact"], "state": "review_draft"}
    ]
    assert json.loads(
        (
            root / "wiki/textbook-reading/objects" / (old["artifact"] + ".json")
        ).read_bytes()
    ) == reading_book(original)
    assert reader.publish(root, job) == current
    assert len(reader.Reader(root).index()["history"]) == 1


@pytest.fixture
def reviewed_revision(publication):
    from wechat_decrypt_tool.course_textbook_edition_editor import (
        apply,
        labelled_article,
        operations,
    )
    from wechat_decrypt_tool.course_textbook_reviewed_draft import ROLE, STAGE

    root, _, course, _, private = publication
    parent, writer = "1" * 32, "2" * 32
    before = reading_book(private)
    parent_path = root / "wiki/lesson-retained-addition-jobs" / parent / "reading.json"
    parent_path.parent.mkdir(parents=True)
    atomic_json(parent_path, before)
    ref = next(iter(private["chapters"][0]["sections"][0]["paragraphs"][0]["citations"]))
    candidate = {
        "paragraph_changes": [{
            "kind": "append", "section_id": "section-a",
            "text": "An independently checked **additional explanation**.", "citations": [ref],
        }],
        "quiz_changes": [],
    }
    book = apply(before, candidate)
    writer_folder = root / "wiki/lesson-contextual-repair-jobs" / writer
    writer_folder.mkdir(parents=True)
    atomic_json(writer_folder / "manifest.json", {"fixture": True})
    binding = {
        str(parent_path): sha(parent_path.read_bytes()),
        "original_writer": writer,
        "original_writer_files": {"manifest.json": sha((writer_folder / "manifest.json").read_bytes())},
        "proposed_article_sha256": sha(canonical(book)),
    }
    recipe = {"extra": {"role": ROLE, "binding": binding}, "packet": {"sources": {ref: {}}}}
    recipe_digest = sha(canonical(recipe))
    job = recipe_digest[:32]
    folder = root / "wiki/lesson-contextual-repair-jobs" / job
    folder.mkdir(parents=True)
    atomic_json(folder / "manifest.json", {"id": job, "recipe_sha256": recipe_digest, "recipe": recipe})
    atomic_json(writer_folder / "retained-actual-unique-prose-position-mapped-review-binding.json", {
        "reviewer": job, "new_writing_calls": 0,
        "manifest_sha256": sha((folder / "manifest.json").read_bytes()),
    })
    editorial = {
        "operations": {"edit000": {"verdict": "accepted"}},
        "prior_issues": {"old": {"verdict": "resolved"}},
        "reading": {"readability": {"verdict": "passed"}},
    }
    coverage = {ref: {"kind": "covered", "paragraph_ids": ["p000", "p001"]}}
    review = {"editorial": editorial, "source_check": {"sources": coverage}}
    atomic_json(folder / "private-inputs.json", {
        "binding": binding, "schema": {"type": "object"},
        "context": {"proposed_article": labelled_article(book), "operations": operations(candidate),
                    "source_check_targets": [ref]},
    })
    atomic_json(folder / "private-final-checks.json", {"binding": binding, "review": editorial, "source_coverage": coverage})
    atomic_json(folder / "private-reviewed-proposal.json", {
        "selection_binding": binding, "candidate": candidate, "review": review, "proposed_article": book,
    })
    raw = json.dumps(review)
    metrics = {"done_reason": "stop", "size_vram": 1024, "requested_context": 4096,
               "measured_context": 4096, "expected_content_tokens": 100,
               "prompt_eval_count": 100, "reserved_output": 100}
    atomic_json(folder / (STAGE + ".json"), {
        "data": review, "data_sha256": sha(canonical(review)), "input_sha256": "3" * 64,
    })
    (folder / "attempts").mkdir()
    atomic_json(folder / "attempts" / (STAGE + "-01.json"), {
        "accepted_structure": True, "input_sha256": "3" * 64,
        "raw_response": raw, "request_sha256": "4" * 64, "model_metrics": metrics,
    })
    atomic_json(folder / "response-01.json", {
        "stage": STAGE, "request_sha256": "4" * 64,
        "response": {"message": {"content": raw}}, "metrics": metrics,
    })
    atomic_json(folder / "reading.json", book)
    atomic_json(folder / "result.json", {
        "job": job, "writer": writer, "parent": parent,
        "state": "retained_visual_additions_independently_checked", "all_operations_accepted": True,
        "new_writing_calls": 0, "source_deletion_allowed": False, "semantic_acceptance": False,
        "scope_verified": False, "reader_publication_allowed": False,
        "missing_source_inputs": 0, "remaining_prior_issues": 0, "reading_issues": 0,
        "article_sha256": sha(canonical(book)), "audited_source_inputs": 1,
        "body_characters": sum(map(len, book["chapters"][0]["sections"][0]["paragraphs"])),
        "chapters": 1, "self_tests": 1, "actual_added_paragraphs": 1,
        "actual_added_characters": len(candidate["paragraph_changes"][0]["text"]),
    })
    return root, job, course, folder, parent_path


def test_reviewed_revision_reaches_directory_search_and_rich_reading(reviewed_revision):
    root, job, course, _, _ = reviewed_revision
    status = reader.publish_reviewed(root, job)
    assert status["state"] == "review_draft" and not status["source_deletion_allowed"]
    client = TestClient(reader.create_app(root))
    assert client.get("/api/catalog").json()["reading_drafts"] == 1
    assert client.get("/api/search", params={"q": "additional explanation"}).json()["results"]
    page = client.get("/read/" + course)
    assert page.status_code == 200
    assert "<strong>additional explanation</strong>" in page.text
    assert "待验收" in page.text and "课件归属仍在核对" in page.text
    assert "PRIVATE_RAW_SOURCE" not in page.text
    export = client.get("/export/" + course).text
    assert "<strong>additional explanation</strong>" in export and 'src="' not in export
    assert client.get("/wiki/textbook-reading/receipts/" + status["artifact"] + ".json").status_code == 404


@pytest.mark.parametrize("mutation", ["source", "receipt", "cache", "rehash_book", "final_claim"])
def test_reviewed_publication_rejects_changed_evidence_or_unverified_success(reviewed_revision, mutation):
    from wechat_decrypt_tool.course_textbook_reviewed_draft import STAGE

    root, job, _, folder, parent_path = reviewed_revision
    if mutation == "source":
        parent_path.write_bytes(parent_path.read_bytes() + b" ")
    elif mutation == "receipt":
        (folder / "response-01.json").unlink()
    elif mutation == "cache":
        path = folder / (STAGE + ".json")
        value = json.loads(path.read_bytes())
        value["data"]["editorial"]["reading"]["readability"]["verdict"] = "failed"
        value["data_sha256"] = sha(canonical(value["data"]))
        atomic_json(path, value)
    else:
        result = json.loads((folder / "result.json").read_bytes())
        if mutation == "final_claim":
            result["semantic_acceptance"] = True
        else:
            book = json.loads((folder / "reading.json").read_bytes())
            book["chapters"][0]["sections"][0]["paragraphs"][-1] = "An unreviewed assertion."
            result["article_sha256"] = sha(canonical(book))
            atomic_json(folder / "reading.json", book)
        atomic_json(folder / "result.json", result)
    with pytest.raises(ValueError):
        reader.publish_reviewed(root, job)
    assert not reader.Reader(root).index()["lessons"]
