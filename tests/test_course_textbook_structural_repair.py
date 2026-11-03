import json

import pytest

from wechat_decrypt_tool import course_textbook_composition as composition
from wechat_decrypt_tool import course_textbook_structural_repair as module
from test_course_textbook_authoring import archive as archive
from test_course_textbook_composition import disputed_archive as disputed_archive
from test_course_textbook_diagram_quarantine import stopped_invalid_diagram
from test_course_textbook_manuscript_continue import exited


def test_original_unused_repair_corrects_mapping_and_graph_without_new_write_budget(
    request, monkeypatch
):
    root, key, parent, calls, folder = stopped_invalid_diagram(
        request, monkeypatch, invalid_mapping=True
    )
    before = {p: p.read_bytes() for p in (folder / "attempts").iterdir()}
    result = module.run(root, key, parent["job"], folder.name, "section-0000")
    assert result["state"] == "article_requires_editorial_repair"
    private = module.post.saved(folder / "private-article.json")["data"]
    assert set(private["unaccepted_sections"]) == {"section-0000"}
    proof = json.loads(
        (folder / "section-0001-structural-repair-private-result.json").read_bytes()
    )
    assert proof["factual_checks_passed"] is True
    assert proof["semantic_repairs"] == 1
    assert not proof["reader_publication_allowed"]
    contract = json.loads((folder / ("section-0001" + module.SUFFIX)).read_bytes())
    assert contract["additional_model_stage_roles"] == 0
    assert contract["original_stage_attempt_budget"] == 2
    assert {row["kind"] for row in contract["structural_defects"]} == {
        "invalid_generated_diagram",
        "paragraph_mapping_out_of_bounds",
    }
    assert len(list((folder / "attempts").glob("section-0001-write-*.json"))) == 2
    assert (
        len(list((folder / "attempts").glob("section-0001-repair-[0-9][0-9].json")))
        == 1
    )
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert not (folder / "reading.json").exists()
    count = len(calls)
    exited(folder)
    assert module.run(root, key, parent["job"], folder.name, "section-0000") == result
    assert len(calls) == count


def test_negative_final_review_stays_unaccepted_while_private_manuscript_finishes(
    request, monkeypatch
):
    root, key, parent, calls, folder = stopped_invalid_diagram(
        request, monkeypatch, invalid_mapping=True
    )
    original = composition.AuthoringJob.generate_response

    def reply(self, messages, *, schema):
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if self.current == "section-0001-repair-review":
            value["quiz"]["passed"] = False
        return json.dumps(value), metrics

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", reply)
    result = module.run(root, key, parent["job"], folder.name, "section-0000")
    private = module.post.saved(folder / "private-article.json")["data"]
    assert set(private["unaccepted_sections"]) == {"section-0000", "section-0001"}
    assert all(not row["accepted"] for row in private["unaccepted_sections"].values())
    proof = json.loads(
        (folder / "section-0001-structural-repair-private-result.json").read_bytes()
    )
    assert not proof["factual_checks_passed"]
    assert not (folder / "reading.json").exists()
    count = len(calls)
    exited(folder)
    assert module.run(root, key, parent["job"], folder.name, "section-0000") == result
    assert len(calls) == count


def test_original_repair_attempt_limit_survives_partial_failure_and_restart(
    request, monkeypatch
):
    root, key, parent, calls, folder = stopped_invalid_diagram(
        request, monkeypatch, invalid_mapping=True
    )
    original = composition.AuthoringJob.generate_response
    invoked = []

    def reply(self, messages, *, schema):
        if self.current == "section-0001-repair":
            invoked.append(self.current)
            return "{}", None
        return original(self, messages, schema=schema)

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", reply)
    for _ in range(2):
        with pytest.raises(
            ValueError, match="stage_attempt_budget_exhausted_section-0001-repair"
        ):
            module.run(root, key, parent["job"], folder.name, "section-0000")
        exited(folder)
        assert len(invoked) == 2


def test_oversized_complete_request_is_not_shortened_or_sent(request, monkeypatch):
    root, key, parent, calls, folder = stopped_invalid_diagram(
        request, monkeypatch, invalid_mapping=True
    )
    monkeypatch.setattr(module.post, "CONTEXT_LIMIT", 1)
    count = len(calls)
    with pytest.raises(ValueError, match="complete_request_fits_context"):
        module.run(root, key, parent["job"], folder.name, "section-0000")
    assert len(calls) == count
    assert not list((folder / "attempts").glob("section-0001-repair-[0-9][0-9].json"))
