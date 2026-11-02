import copy
import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from test_course_wiki import archive as source_archive
from test_course_wiki import plan
from test_course_wiki import published as source_published
from test_course_wiki_writer import model
from wechat_decrypt_tool import course_wiki_review as review
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_knowledge import GpuBusyError, ModelOutputTruncatedError
from wechat_decrypt_tool.course_library import canonical, create_app
from wechat_decrypt_tool.course_lesson_exports import digest
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki import Wiki

archive = source_archive
published = source_published


def setup(published, monkeypatch, intercept=None, *, constrained=False):
    store, key = published
    model(monkeypatch, store, key)
    article_id = writer.generate(ReadOnlyArchive(store.root), plan(key))["id"]
    calls = []

    def chat(model_name, messages, *, schema):
        context = json.loads(messages[1]["content"])
        assert schema == review.selection_schema(context, constrained=constrained)
        calls.append(context)
        if intercept:
            custom = intercept(context, calls)
            if custom is not None:
                return custom
        rows = []
        for target in context["targets"]:
            ref = target["citations"][0]
            rows.append(
                {
                    "target": target["target"],
                    "verdict": "partial",
                    "reason": "Synthetic condition needs further review.",
                    "evidence": [
                        {
                            "witness": next(
                                k
                                for k, w in context["witnesses"].items()
                                if w["source"] == ref
                            )
                        }
                    ],
                }
            )
        return json.dumps({"reviews": rows})

    monkeypatch.setattr(review, "local_chat", chat)
    return store, article_id, calls


def run(store, article_id):
    return review.review(ReadOnlyArchive(store.root), article_id)


def test_crosscheck_is_separate_from_article_and_never_semantic_acceptance(
    published, monkeypatch
):
    store, article_id, calls = setup(published, monkeypatch)
    original = canonical(Wiki(store.root).article(article_id))
    receipt = run(store, article_id)
    assert receipt["summary"]["partial"] == len(receipt["reviews"])
    assert any(r["target"].startswith("quiz-") for r in receipt["reviews"])
    assert any(r["target"].startswith("section-") for r in receipt["reviews"])
    assert receipt["literal_witnesses_verified"]
    assert not any(
        receipt[k]
        for k in (
            "independent_semantic_review",
            "semantic_accuracy_verified",
            "image_relevance_verified",
            "source_deletion_allowed",
        )
    )
    assert canonical(Wiki(store.root).article(article_id)) == original
    n = len(calls)
    assert run(store, article_id)["summary"] == receipt["summary"]
    assert len(calls) == n


@pytest.mark.parametrize("mutation", ["quote", "citation", "missing", "duplicate"])
def test_invalid_witnesses_or_target_coverage_are_never_accepted(
    published, monkeypatch, mutation
):
    store, article_id, _ = setup(published, monkeypatch)
    value = Wiki(store.root).article(article_id)
    _, targets = next(review.batches(value["article"]))
    ref = targets[0]["citations"][0]
    result = {
        "reviews": [
            {
                "target": targets[0]["target"],
                "verdict": "supported",
                "reason": "Synthetic reason",
                "evidence": [
                    {"source": ref, "quote": value["packet"]["sources"][ref]["text"]}
                ],
            }
        ]
    }
    if mutation == "quote":
        result["reviews"][0]["evidence"][0]["quote"] = "invented witness"
    elif mutation == "citation":
        result["reviews"][0]["evidence"][0]["source"] = "not-cited"
    elif mutation == "missing":
        result["reviews"] = []
    else:
        result["reviews"] *= 2
    with pytest.raises(ValueError):
        review.validate(result, targets, value["packet"]["sources"])


def test_failed_budget_is_bounded_across_restart_and_keeps_raw_reply(
    published, monkeypatch
):
    store, article_id, calls = setup(
        published, monkeypatch, lambda ctx, calls: '{"reviews":[]}'
    )
    with pytest.raises(ValueError, match="budget_exhausted"):
        run(store, article_id)
    assert len(calls) == 3
    with pytest.raises(ValueError, match="budget_exhausted"):
        run(store, article_id)
    assert len(calls) == 3
    path = next((store.root / "wiki" / "review-jobs").glob("*/overview-attempt-1.json"))
    assert json.loads(path.read_bytes())["raw"] == '{"reviews":[]}'
    assert not (store.root / "wiki" / "reviews" / (article_id + ".json")).exists()


def test_resume_does_not_repeat_completed_batches(published, monkeypatch):
    interrupted = []

    def interrupt(context, calls):
        if context["targets"][0]["target"].startswith("section-1") and not interrupted:
            interrupted.append(True)
            raise GpuBusyError("Synthetic busy")

    store, article_id, calls = setup(published, monkeypatch, interrupt)
    with pytest.raises(GpuBusyError):
        run(store, article_id)
    before = copy.deepcopy(calls)
    receipt = run(store, article_id)
    assert receipt["reviews"]
    assert sum(c["targets"][0]["target"] == "overview" for c in calls) == 1
    assert len(calls) > len(before)


def test_busy_gpu_does_not_consume_attempt_budget(published, monkeypatch):
    store, article_id, calls = setup(published, monkeypatch)

    @contextmanager
    def busy(_):
        raise GpuBusyError("Synthetic busy")
        yield

    monkeypatch.setattr(review, "gpu_lease", busy)
    with pytest.raises(GpuBusyError):
        run(store, article_id)
    assert not calls
    assert not list((store.root / "wiki" / "review-jobs").glob("*/*attempt*.json"))


def test_truncated_response_is_retained_and_repaired(published, monkeypatch):
    def truncate(context, calls):
        if len(calls) == 1:
            raise ModelOutputTruncatedError(
                {"done_reason": "length", "message": {"content": "partial synthetic"}}
            )

    store, article_id, calls = setup(published, monkeypatch, truncate)
    run(store, article_id)
    path = next((store.root / "wiki" / "review-jobs").glob("*/overview-attempt-1.json"))
    assert json.loads(path.read_bytes())["raw"] == "partial synthetic"
    assert calls[1]["previous_error"] == "ModelOutputTruncatedError"


def test_tampered_checkpoint_is_not_reused(published, monkeypatch):
    store, article_id, calls = setup(published, monkeypatch)
    run(store, article_id)
    path = next((store.root / "wiki" / "review-jobs").glob("*/overview.json"))
    value = json.loads(path.read_bytes())
    value["data"]["reviews"][0]["verdict"] = "supported"
    path.write_text(json.dumps(value))
    n = len(calls)
    with pytest.raises(ValueError, match="checkpoint_changed"):
        run(store, article_id)
    assert len(calls) == n


def test_http_reports_crosscheck_only_and_does_not_run_model_or_database(
    published, monkeypatch
):
    store, article_id, calls = setup(published, monkeypatch)
    ro = ReadOnlyArchive(store.root)
    client = TestClient(create_app(ro))
    monkeypatch.setattr(ro, "connect", lambda: pytest.fail("must not read DB"))
    endpoint = "/wiki/api/review/" + article_id
    assert client.get(endpoint).json()["state"] == "not_started"
    assert not calls
    receipt = run(store, article_id)
    n = len(calls)
    result = client.get(endpoint)
    assert result.status_code == 200
    assert result.json()["state"] == "model_crosscheck_complete"
    assert result.json()["summary"] == receipt["summary"]
    assert not result.json()["independent_semantic_review"]
    assert len(calls) == n
    receipt["summary"]["supported"] = 999
    path = store.root / "wiki" / "reviews" / (article_id + ".json")
    path.write_text(json.dumps(receipt))
    assert client.get(endpoint).status_code == 503
    assert client.get("/wiki/api/article/" + article_id).status_code == 200
    assert client.get("/wiki/api/review/unknown").status_code == 404


def test_http_detects_dead_review_process(published, monkeypatch):
    store, article_id, _ = setup(published, monkeypatch)
    from wechat_decrypt_tool.course_knowledge import atomic_json

    atomic_json(
        store.root / "wiki" / "last-review.json",
        {
            "article": article_id,
            "pid": 0,
            "created": 0,
            "state": "waiting_gpu",
            "stage": "overview",
            "completed_batches": 0,
            "total_batches": 12,
            "updated": 1,
        },
    )
    value = review.published_review(store.root, article_id)
    assert value["state"] == "interrupted_or_failed"
    assert value["completed_batches"] == 0


def test_witness_partition_retains_exact_source_characters_and_short_tail():
    text = ('原文 "引号"\nMixed punctuation，和空格。' * 10) + "尾"
    sources = {"source:1": {"text": text}}
    targets = [{"target": "p", "text": "Claim", "citations": ["source:1"]}]
    request = review.review_request(targets, sources)
    assert "".join(w["quote"] for w in request["witnesses"].values()) == text
    assert all(len(w["quote"].strip()) >= 4 for w in request["witnesses"].values())
    schema = review.selection_schema(request)
    fields = schema["properties"]["reviews"]["items"]["properties"]
    assert fields["target"]["enum"] == ["p"]
    assert (
        "enum" not in fields["reason"]
    )  # Shared TEXT aliases cannot leak constraints.
    assert set(fields["evidence"]["items"]["properties"]["witness"]["enum"]) == set(
        request["witnesses"]
    )
    assert review.SCHEMA["properties"]["reviews"]["items"]["properties"]["target"] == {
        "type": "string"
    }


def test_selected_witness_is_resolved_by_program_and_cannot_change_source():
    sources = {
        "one": {"text": "Original source one."},
        "two": {"text": "Original source two."},
    }
    targets = [{"target": "p", "text": "A claim", "citations": ["one"]}]
    request = review.review_request(targets, sources)
    row = {
        "target": "p",
        "verdict": "supported",
        "reason": "Synthetic reason",
        "evidence": [{"witness": "one@0"}],
    }
    normalized = review.resolve_witnesses({"reviews": [row]}, request["witnesses"])
    assert normalized["reviews"][0]["evidence"] == [
        {"source": "one", "quote": "Original source one."}
    ]
    review.validate(normalized, targets, sources)
    row["evidence"] = [{"witness": "two@0"}]
    normalized = review.resolve_witnesses({"reviews": [row]}, request["witnesses"])
    with pytest.raises(ValueError, match="uncited_witness"):
        review.validate(normalized, targets, sources)
    row["evidence"] = [{"witness": "invented"}]
    with pytest.raises(ValueError, match="invented_witness_id"):
        review.resolve_witnesses({"reviews": [row]}, request["witnesses"])


def test_legacy_literal_quote_reports_remain_readable(published, monkeypatch):
    store, article_id, calls = setup(published, monkeypatch)
    receipt = run(store, article_id)
    article = Wiki(store.root).article(article_id)
    legacy_schema = copy.deepcopy(review.SCHEMA)
    legacy_schema["properties"]["reviews"]["items"]["properties"]["evidence"][
        "items"
    ] = {
        "type": "object",
        "properties": {"source": {"type": "string"}, "quote": {"type": "string"}},
        "required": ["source", "quote"],
        "additionalProperties": False,
    }
    recipe = {
        k: receipt[k]
        for k in ("policy", "article_sha256", "model_digest", "system", "schema")
    }
    recipe.update(system="Frozen legacy literal quote prompt.", schema=legacy_schema)
    legacy_key = digest(canonical(recipe))[:32]
    folder = store.root / "wiki" / "review-jobs" / legacy_key
    folder.mkdir()
    (folder / "manifest.json").write_bytes(canonical(recipe))
    previous = store.root / "wiki" / "review-jobs" / receipt["job"]
    for stage, targets in review.batches(article["article"]):
        sources = {
            ref: article["packet"]["sources"][ref]
            for t in targets
            for ref in t["citations"]
        }
        value = json.loads((previous / (stage + ".json")).read_bytes())
        value["request_sha256"] = digest(
            canonical({"targets": targets, "sources": sources})
        )
        (folder / (stage + ".json")).write_bytes(canonical(value))
    receipt.update(recipe, job=legacy_key)
    (store.root / "wiki" / "reviews" / (article_id + ".json")).write_bytes(
        canonical(receipt)
    )
    n = len(calls)
    assert (
        review.published_review(store.root, article_id)["state"]
        == "model_crosscheck_complete"
    )
    assert len(calls) == n


def test_single_target_strategy_has_separate_budget_and_retains_both_reports(
    published, monkeypatch
):
    store, article_id, calls = setup(published, monkeypatch)
    first = run(store, article_id)
    path = store.root / "wiki" / "review-jobs" / first["job"] / "receipt.json"
    frozen = path.read_bytes()
    n = len(calls)
    second = review.review(ReadOnlyArchive(store.root), article_id, single_targets=True)
    assert first["job"] != second["job"]
    assert second["batching"] == "single-target-v1"
    assert all(len(ctx["targets"]) == 1 for ctx in calls[n:])
    assert path.read_bytes() == frozen
    assert first["summary"] == second["summary"]
    assert (
        review.published_review(store.root, article_id, job=first["job"])["summary"]
        == first["summary"]
    )
    assert (
        review.published_review(store.root, article_id, job=second["job"])["summary"]
        == second["summary"]
    )
    original = json.loads(
        (store.root / "wiki" / "reviews" / (article_id + ".json")).read_bytes()
    )
    resumed = review.review(
        ReadOnlyArchive(store.root), article_id, single_targets=True
    )
    assert resumed["updated"] == original["updated"]


def test_immutable_review_history_rejects_changed_payload(published, monkeypatch):
    store, article_id, _ = setup(published, monkeypatch)
    receipt = run(store, article_id)
    changed = copy.deepcopy(receipt)
    changed["summary"]["supported"] += 1
    with pytest.raises(ValueError, match="immutable_receipt"):
        review.retain_receipt(Wiki(store.root), changed)
    with pytest.raises(ValueError, match="invalid_history_job"):
        review.published_review(store.root, article_id, job="../secret")


def test_constrained_review_is_frozen_readable_and_resumes_without_new_calls(
    published, monkeypatch
):
    store, article_id, calls = setup(published, monkeypatch, constrained=True)
    result = review.review(
        ReadOnlyArchive(store.root), article_id, constrained_evidence=True
    )
    assert result["generation_constraints"] == "verdict-dependent-evidence-grammar-v1"
    assert (
        review.published_review(store.root, article_id)["summary"] == result["summary"]
    )
    count = len(calls)
    assert (
        review.review(
            ReadOnlyArchive(store.root), article_id, constrained_evidence=True
        )["job"]
        == result["job"]
    )
    assert len(calls) == count
    with pytest.raises(ValueError, match="constraint_change_requires_new_article"):
        review.review(ReadOnlyArchive(store.root), article_id)
    assert len(calls) == count


@pytest.mark.parametrize("constrained", [False, True])
def test_generation_constraint_change_cannot_reset_failed_review_budget(
    published, monkeypatch, constrained
):
    store, article_id, calls = setup(
        published,
        monkeypatch,
        lambda ctx, calls: '{"reviews":[]}',
        constrained=constrained,
    )
    with pytest.raises(ValueError, match="budget_exhausted"):
        review.review(
            ReadOnlyArchive(store.root), article_id, constrained_evidence=constrained
        )
    saved = {
        p: p.read_bytes() for p in (store.root / "wiki/review-jobs").rglob("*.json")
    }
    with pytest.raises(ValueError, match="constraint_change_requires_new_article"):
        review.review(
            ReadOnlyArchive(store.root),
            article_id,
            constrained_evidence=not constrained,
        )
    assert len(calls) == 3 and all(p.read_bytes() == raw for p, raw in saved.items())
