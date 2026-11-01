import json

import pytest

from test_course_wiki_revision import archive as source_archive
from test_course_wiki_revision import published as source_published
from test_course_wiki_revision import setup
from wechat_decrypt_tool import course_pdf_ocr
from wechat_decrypt_tool import course_wiki_completeness as omissions
from wechat_decrypt_tool import course_wiki_revision as revision
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_knowledge import GpuBusyError
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki import Wiki

archive = source_archive
published = source_published


def report(store, article_id, monkeypatch, verdict="partial"):
    repair_model = writer.local_chat
    monkeypatch.setattr(
        omissions, "local_model_digest", course_pdf_ocr.local_model_digest
    )
    calls = []

    def judge(name, messages, *, schema):
        context = json.loads(messages[1]["content"])
        first = not calls
        calls.append(context)
        rows = []
        for window, original in context["windows"].items():
            target = next(
                key
                for key, item in context["article_targets"].items()
                if original["source"] in item["citations"]
                and key.startswith("section-")
                and not key.endswith("-code")
            )
            selected = verdict if first and not rows else "covered"
            rows.append(
                {
                    "window": window,
                    "coverage": selected,
                    "reason": "Synthetic missing condition."
                    if selected != "covered"
                    else "Synthetic source explained.",
                    "article_evidence": [] if selected == "missing" else [target],
                }
            )
        return json.dumps({"reviews": rows})

    monkeypatch.setattr(writer, "local_chat", judge)
    result = omissions.review(ReadOnlyArchive(store.root), article_id)
    monkeypatch.setattr(writer, "local_chat", repair_model)
    return result["job"]


def test_reverse_partial_is_repaired_even_when_forward_review_has_no_flags(
    published, monkeypatch
):
    store, article_id, calls = setup(published, monkeypatch, flagged=())
    job = report(store, article_id, monkeypatch)
    before = Wiki(store.root).article(article_id)
    result = revision.revise(
        ReadOnlyArchive(store.root), article_id, omission_jobs=[job]
    )
    after = Wiki(store.root).article(result["id"])
    assert len(calls) == 1
    assert calls[0]["original"]["target"] == "section-0-p-0"
    assert calls[0]["source_omission_findings"][0]["coverage"] == "partial"
    source = calls[0]["source_omission_findings"][0]["original"]["source"]
    assert calls[0]["sources"][source] == before["packet"]["sources"][source]
    assert after["article"]["sections"][1:] == before["article"]["sections"][1:]
    assert canonical(Wiki(store.root).article(article_id)) == canonical(before)
    assert after["packet"]["revision"]["omission_reports"][0]["job"] == job
    assert not after["checks"]["semantic_accuracy_verified"]
    assert (
        revision.revise(ReadOnlyArchive(store.root), article_id, omission_jobs=[job])[
            "state"
        ]
        == "already_published"
    )
    assert len(calls) == 1


def test_missing_source_explanation_appends_prose_without_rewriting_other_paragraphs(
    published, monkeypatch
):
    store, article_id, calls = setup(published, monkeypatch, flagged=())
    job = report(store, article_id, monkeypatch, "missing")
    before = Wiki(store.root).article(article_id)
    result = revision.revise(
        ReadOnlyArchive(store.root), article_id, omission_jobs=[job]
    )
    after = Wiki(store.root).article(result["id"])
    assert calls[0]["original"]["target"] == "section-0-append-0"
    assert calls[0]["original"]["text"] == ""
    original = before["article"]["sections"][0]["paragraphs"]
    added = after["article"]["sections"][0]["paragraphs"]
    assert added[:-1] == original
    assert after["article"]["sections"][1:] == before["article"]["sections"][1:]


def test_unresolved_missing_material_becomes_visible_gap(published, monkeypatch):
    def omit(context, calls):
        return json.dumps(
            {
                "action": "omit",
                "text": "",
                "references": [],
                "gap": "Synthetic source cannot support an explanation.",
            }
        )

    store, article_id, _ = setup(published, monkeypatch, flagged=(), intercept=omit)
    job = report(store, article_id, monkeypatch, "missing")
    before = Wiki(store.root).article(article_id)
    result = revision.revise(
        ReadOnlyArchive(store.root), article_id, omission_jobs=[job]
    )
    after = Wiki(store.root).article(result["id"])
    assert after["article"]["sections"] == before["article"]["sections"]
    assert "Synthetic source cannot support an explanation." in after["article"]["gaps"]
    assert not after["checks"]["full_topic_coverage_verified"]


def test_new_omission_target_reuses_checkpoint_after_gpu_wait(published, monkeypatch):
    interrupted = []

    def block(context, calls):
        if "source_omission_findings" in context and not interrupted:
            interrupted.append(True)
            raise GpuBusyError("Synthetic busy GPU")

    store, article_id, calls = setup(
        published, monkeypatch, flagged=("overview",), intercept=block
    )
    job = report(store, article_id, monkeypatch)
    with pytest.raises(GpuBusyError):
        revision.revise(ReadOnlyArchive(store.root), article_id, omission_jobs=[job])
    assert len(Wiki(store.root).catalog()["articles"]) == 1
    result = revision.revise(
        ReadOnlyArchive(store.root), article_id, omission_jobs=[job]
    )
    assert result["state"] == "draft_published"
    assert sum(c["original"]["target"] == "overview" for c in calls) == 1


def test_changed_historical_report_prevents_publication(published, monkeypatch):
    changed = []

    def alter(context, calls):
        if not changed:
            changed.append(True)
            path = store.root / "wiki/completeness-jobs" / job / "receipt.json"
            value = json.loads(path.read_bytes())
            value["summary"]["covered"] += 1
            path.write_bytes(canonical(value))

    store, article_id, _ = setup(published, monkeypatch, flagged=(), intercept=alter)
    job = report(store, article_id, monkeypatch)
    with pytest.raises(ValueError, match="omissions_"):
        revision.revise(ReadOnlyArchive(store.root), article_id, omission_jobs=[job])
    assert len(Wiki(store.root).catalog()["articles"]) == 1


def test_code_only_omission_never_changes_pinned_code():
    parent = {"id": "article"}
    row = {
        "original": {"source": "source"},
        "coverage": "partial",
        "article_evidence": ["section-0-code"],
    }
    all_targets = {
        "section-0-code": {
            "target": "section-0-code",
            "text": "literal()",
            "citations": ["source"],
        }
    }
    # Report validation itself is exercised by the real-report integration cases.
    # This case isolates selecting prose when a valid report points only at code.
    from unittest.mock import patch

    with patch.object(
        omissions,
        "published_report",
        return_value={
            "state": "model_omissions_complete",
            "chapters": [{"stage": "chapter-0", "reviews": [row]}],
        },
    ):
        targets, findings, _ = revision.omission_repairs(
            None, parent, ["job"], all_targets
        )
    assert list(findings) == ["section-0-append-0"]
    assert targets["section-0-code"] == all_targets["section-0-code"]


def test_source_reference_and_duplicate_job_gates():
    with pytest.raises(ValueError, match="invalid_omission_jobs"):
        revision.omission_repairs(None, {}, ["same", "same"], {})
    with pytest.raises(ValueError, match="omission_source_not_referenced"):
        revision.validate_omission_fix(
            {
                "action": "replace",
                "text": "Wrong source explanation.",
                "references": ["other"],
                "gap": "",
            },
            {"sources": {"source": {}, "other": {}}},
            [{"original": {"source": "source"}}],
        )


def test_source_scoped_additions_split_sources_but_keep_same_source_windows():
    from unittest.mock import patch

    rows = [
        {"original": {"source": source}, "coverage": "missing", "article_evidence": []}
        for source in ("a", "b", "a")
    ]
    with patch.object(
        omissions,
        "published_report",
        return_value={
            "state": "model_omissions_complete",
            "chapters": [{"stage": "chapter-0-source-0", "reviews": rows}],
        },
    ):
        targets, findings, _ = revision.omission_repairs(
            None, {"id": "article"}, ["job"], {}, source_scoped_additions=True
        )
    assert targets["section-0-append-0"]["citations"] == ["a"]
    assert targets["section-0-append-1"]["citations"] == ["b"]
    assert len(findings["section-0-append-0"]) == 2
    packet = {
        "title": "Synthetic",
        "sources": {"a": {"text": "Whole A"}, "b": {"text": "Whole B"}},
    }
    scoped, context, _, schema = revision.repair_request(
        {"article": {"sections": [{"paragraphs": []}]}},
        packet,
        "section-0-append-0",
        targets["section-0-append-0"],
        {},
        findings["section-0-append-0"],
        source_scoped_additions=True,
    )
    assert scoped["sources"] == context["sources"] == {"a": {"text": "Whole A"}}
    refs = schema["oneOf"][0]["properties"]["references"]
    assert refs == {
        "type": "array",
        "items": {"type": "string", "enum": ["a"]},
        "minItems": 1,
        "maxItems": 1,
    }


def test_scoped_additions_reuse_legacy_claims_without_resetting_failed_budget(
    published, monkeypatch
):
    def wrong_source(context, calls):
        if "-append-" in context["original"]["target"] and len(context["sources"]) > 1:
            required = context["original"]["citations"]
            other = next(ref for ref in context["sources"] if ref not in required)
            return json.dumps(
                {
                    "action": "replace",
                    "text": "Wrong-source synthetic explanation.",
                    "references": [other],
                    "gap": "",
                }
            )

    store, article_id, calls = setup(
        published, monkeypatch, flagged=("overview",), intercept=wrong_source
    )
    job = report(store, article_id, monkeypatch, "missing")
    with pytest.raises(ValueError, match="budget_exhausted"):
        revision.revise(ReadOnlyArchive(store.root), article_id, omission_jobs=[job])
    legacy = json.loads((store.root / "wiki/last-build.json").read_bytes())["id"]
    attempts = store.root / "wiki/jobs" / legacy / "attempts"
    before = {p.name: p.read_bytes() for p in attempts.glob("*.json")}
    assert len(before) == 4
    result = revision.revise(
        ReadOnlyArchive(store.root),
        article_id,
        omission_jobs=[job],
        source_scoped_additions=True,
    )
    assert result["state"] == "draft_published"
    assert result["id"] != legacy
    assert sum(c["original"]["target"] == "overview" for c in calls) == 1
    assert {p.name: p.read_bytes() for p in attempts.glob("*.json")} == before
    assert len(calls) == 5
    assert (
        revision.revise(
            ReadOnlyArchive(store.root),
            article_id,
            omission_jobs=[job],
            source_scoped_additions=True,
        )["state"]
        == "already_published"
    )
    assert len(calls) == 5


def test_scoped_mode_does_not_reset_exhausted_unchanged_claims(published, monkeypatch):
    def invalid(context, calls):
        return json.dumps(
            {
                "action": "replace",
                "text": "Synthetic",
                "references": ["invented"],
                "gap": "",
            }
        )

    store, article_id, calls = setup(
        published, monkeypatch, flagged=("overview",), intercept=invalid
    )
    for mode in (False, True):
        with pytest.raises(ValueError, match="budget_exhausted"):
            revision.revise(
                ReadOnlyArchive(store.root), article_id, source_scoped_additions=mode
            )
        assert len(calls) == 3


def test_prepare_freezes_real_requests_without_model_or_weight_calls(
    published, monkeypatch
):
    store, article_id, calls = setup(published, monkeypatch, flagged=("overview",))
    job = report(store, article_id, monkeypatch)
    before = canonical(Wiki(store.root).article(article_id))

    def forbidden(*args, **kwargs):
        raise AssertionError("prepare must not call a model or resolve live weights")

    monkeypatch.setattr(course_pdf_ocr, "local_model_digest", forbidden)
    monkeypatch.setattr(writer, "local_chat", forbidden)
    result = revision.revise(
        ReadOnlyArchive(store.root), article_id, omission_jobs=[job], prepare_only=True
    )
    assert result["state"] == "prepared_no_model_calls"
    assert result["model_calls"] == 0 and not calls
    path = store.root / "wiki/prepared-revisions" / (result["id"] + ".json")
    value = json.loads(path.read_bytes())
    assert {r["target"] for r in value["requests"]} == {"overview", "section-0-p-0"}
    assert not value["model_weights_bound"]
    assert canonical(Wiki(store.root).article(article_id)) == before
    assert (
        revision.revise(
            ReadOnlyArchive(store.root),
            article_id,
            omission_jobs=[job],
            prepare_only=True,
        )
        == result
    )
    value["requests"][0]["context"]["topic"] = "corrupted"
    path.write_bytes(canonical(value))
    with pytest.raises(ValueError, match="prepared_request_changed"):
        revision.revise(
            ReadOnlyArchive(store.root),
            article_id,
            omission_jobs=[job],
            prepare_only=True,
        )
