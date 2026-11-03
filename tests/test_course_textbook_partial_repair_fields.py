import json

import pytest

from wechat_decrypt_tool import course_textbook_composition as composition
from wechat_decrypt_tool import course_textbook_partial_repair_fields as module
from wechat_decrypt_tool.course_knowledge import ModelOutputTruncatedError
from test_course_textbook_authoring import archive as archive
from test_course_textbook_composition import disputed_archive as disputed_archive
from test_course_textbook_diagram_quarantine import stopped_invalid_diagram
from test_course_textbook_manuscript_continue import exited


def truncated_manuscript(request, monkeypatch, *, missing_body_field=False):
    root, key, parent, calls, folder = stopped_invalid_diagram(
        request, monkeypatch, invalid_mapping=True
    )
    original = composition.AuthoringJob.generate_response

    def reply(self, messages, *, schema):
        raw, metrics = original(self, messages, schema=schema)
        if self.current == "section-0001-repair":
            value = json.loads(raw)
            body = {key: row for key, row in value.items() if key != "coverage"}
            if missing_body_field:
                del body["quiz"]
            raw = json.dumps(body)[:-1] + ', "coverage": {'
            raise ModelOutputTruncatedError(
                {
                    "message": {"content": raw},
                    "done_reason": "length",
                    "eval_count": 4000,
                    "prompt_eval_count": 100,
                }
            )
        return raw, metrics

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", reply)
    return root, key, parent, calls, folder


def test_complete_prose_is_retained_with_missing_metadata_explicitly_unaccepted(
    request, monkeypatch
):
    root, key, parent, calls, folder = truncated_manuscript(request, monkeypatch)
    before = {p: p.read_bytes() for p in (folder / "attempts").iterdir()}
    result = module.run(root, key, parent["job"], folder.name, "section-0000")
    private = module.post.saved(folder / "private-article.json")["data"]
    assert result["state"] == "article_requires_editorial_repair"
    assert set(private["unaccepted_sections"]) == {"section-0000", "section-0001"}
    row = private["unaccepted_sections"]["section-0001"]
    assert row["accepted"] is False and row["unresolved_point_ids"]
    proof = json.loads(
        (folder / "section-0001-partial-fields-private-result.json").read_bytes()
    )
    assert proof["actual_factual_review_passed"] is True
    assert proof["accepted"] is False
    assert not proof["reader_publication_allowed"]
    original = json.loads(
        (folder / "attempts/section-0001-repair-01.json").read_bytes()
    )
    fields, mappings, spans = module.complete_fields(original["raw_response"])
    assert proof["candidate"]["paragraphs"] == fields["paragraphs"]
    assert proof["candidate"]["quiz"] == fields["quiz"]
    assert not mappings
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert (
        len(list((folder / "attempts").glob("section-0001-repair-[0-9][0-9].json")))
        == 2
    )
    assert not (folder / "reading.json").exists()
    count = len(calls)
    exited(folder)
    assert module.run(root, key, parent["job"], folder.name, "section-0000") == result
    assert len(calls) == count


def test_missing_prose_field_cannot_be_filled_from_another_response(
    request, monkeypatch
):
    root, key, parent, calls, folder = truncated_manuscript(
        request, monkeypatch, missing_body_field=True
    )
    with pytest.raises(
        ValueError, match="all_semantic_fields_complete_before_metadata"
    ):
        module.run(root, key, parent["job"], folder.name, "section-0000")
    assert not list((folder / "attempts").glob("section-0001-repair-review-*.json"))
    assert not (folder / "private-article.json").exists()


def test_partial_string_is_never_completed_or_published():
    raw = '{"heading":"Fixture","paragraphs":[{"text":"unfinished'
    with pytest.raises(
        ValueError, match="all_semantic_fields_complete_before_metadata"
    ):
        module.complete_fields(raw)
