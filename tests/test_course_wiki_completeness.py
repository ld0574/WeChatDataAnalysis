import copy
import json

import pytest
from fastapi.testclient import TestClient

from test_course_wiki_synthesis import (
    archive as source_archive,
    courseware as source_courseware,
    long_plan as source_long_plan,
    model,
    published as source_published,
    run,
)
from wechat_decrypt_tool import course_wiki_completeness as omissions
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_knowledge import GpuBusyError
from wechat_decrypt_tool.course_library import canonical, create_app
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki import Wiki

archive = source_archive
courseware = source_courseware
published = source_published
long_plan = source_long_plan


@pytest.fixture
def drafted(long_plan, monkeypatch):
    store, _ = long_plan
    model(monkeypatch)
    article_id = run(long_plan)["id"]
    monkeypatch.setattr(
        omissions, "local_model_digest", lambda _: "frozen-local-weights"
    )
    return store, article_id


def response(context):
    return {
        "reviews": [
            {
                "window": key,
                "coverage": "covered",
                "reason": "Synthetic explanation corresponds to this original range.",
                "article_evidence": [
                    next(
                        ref
                        for ref, target in context["article_targets"].items()
                        if window["source"] in target["citations"]
                        and not ref.endswith("-code")
                    )
                ],
            }
            for key, window in context["windows"].items()
        ]
    }


def fake_model(monkeypatch, intercept=None, *, constrained=False):
    calls = []

    def chat(name, messages, *, schema):
        context = json.loads(messages[1]["content"])
        calls.append(context)
        assert schema == omissions.selection_schema(context, constrained=constrained)
        if intercept:
            custom = intercept(context, calls)
            if custom is not None:
                return custom
        return json.dumps(response(context))

    monkeypatch.setattr(writer, "local_chat", chat)
    return calls


def check(drafted):
    store, article = drafted
    return omissions.review(ReadOnlyArchive(store.root), article)


def test_every_original_character_is_assigned_without_summary_or_article_mutation(
    drafted, monkeypatch
):
    store, article_id = drafted
    original = canonical(Wiki(store.root).article(article_id))
    build_status = (store.root / "wiki/last-build.json").read_bytes()
    calls = fake_model(monkeypatch)
    result = check(drafted)
    assert len(calls) == 4
    for context in calls:
        for ref, source in context["sources"].items():
            pieces = [w for w in context["windows"].values() if w["source"] == ref]
            assert "".join(p["quote"] for p in pieces) == source["text"]
            assert pieces[0]["start"] == 0
            assert pieces[-1]["end"] == len(source["text"])
    assert canonical(Wiki(store.root).article(article_id)) == original
    assert (store.root / "wiki/last-build.json").read_bytes() == build_status
    assert len(list((store.root / "wiki/completeness-jobs").iterdir())) == 1
    assert len(list((store.root / "wiki/jobs").iterdir())) == 1
    assert result["all_selected_characters_partitioned"]
    assert not result["full_topic_coverage_verified"]
    assert not result["semantic_accuracy_verified"]
    assert not result["source_deletion_allowed"]
    saved = omissions.published_report(store.root, article_id)
    assert saved["summary"] == result["summary"]
    assert all("original" in row for c in saved["chapters"] for row in c["reviews"])
    before = (
        store.root / "wiki/completeness-jobs" / result["job"] / "receipt.json"
    ).read_bytes()
    assert check(drafted)["updated"] == result["updated"]
    assert len(calls) == 4
    assert (
        store.root / "wiki/completeness-jobs" / result["job"] / "receipt.json"
    ).read_bytes() == before
    assert (
        omissions.published_report(store.root, article_id)["summary"]
        == result["summary"]
    )
    client = TestClient(create_app(ReadOnlyArchive(store.root)))
    assert client.get("/wiki/api/completeness/" + article_id).status_code == 200
    assert (
        client.get("/wiki/api/completeness/" + article_id + "?job=../bad").status_code
        == 503
    )


def test_per_source_batches_retain_entire_chapter_and_partition_every_window(drafted):
    store, article_id = drafted
    value = Wiki(store.root).article(article_id)
    first = value["packet"]["chapter_scopes"][0][0]
    value["packet"]["sources"]["synthetic-uncited-source"] = copy.deepcopy(
        value["packet"]["sources"][first]
    )
    value["packet"]["chapter_scopes"][0].append("synthetic-uncited-source")
    frozen = canonical(value)
    original = dict(omissions.stages(value))
    focused = list(omissions.stages(value, per_source=True))
    assert len(focused) > len(original)
    for chapter, context in original.items():
        batches = [c for name, c in focused if name.startswith(chapter + "-source-")]
        windows = {}
        for batch in batches:
            assert batch["sources"] == context["sources"]
            assert {w["source"] for w in batch["windows"].values()} == {
                batch["source_focus"]
            }
            assert all(
                batch["source_focus"] in t["citations"]
                for t in batch["article_targets"].values()
            )
            assert not windows.keys() & batch["windows"].keys()
            windows.update(batch["windows"])
        assert windows == context["windows"]
    assert canonical(value) == frozen
    unreferenced = next(c for _, c in focused if not c["article_targets"])
    schema = omissions.selection_schema(unreferenced)["properties"]["reviews"]["items"][
        "properties"
    ]
    assert schema["article_evidence"] == {"type": "array", "maxItems": 0}
    assert "covered" not in schema["coverage"]["enum"]
    data = {
        "reviews": [
            {
                "window": key,
                "coverage": "missing",
                "reason": "No corresponding explanation.",
                "article_evidence": [],
            }
            for key in unreferenced["windows"]
        ]
    }
    omissions.validate(data, unreferenced)


@pytest.mark.parametrize("per_source", [False, True])
def test_partitioned_original_prompt_binding_reads_without_rerunning_model(
    drafted, monkeypatch, per_source
):
    store, article_id = drafted
    original = Wiki(store.root).article(article_id)
    original["packet"]["evidence_partitions"] = {"synthetic": "frozen-partition"}

    class FrozenWiki:
        def __init__(self, root):
            self.root = root / "wiki"

        def article(self, key):
            assert key == article_id
            return copy.deepcopy(original)

    monkeypatch.setattr(omissions, "Wiki", FrozenWiki)
    calls = fake_model(monkeypatch)
    result = omissions.review(
        ReadOnlyArchive(store.root), article_id, per_source=per_source
    )
    folder = store.root / "wiki/completeness-jobs" / result["job"]
    before = {p.name: p.read_bytes() for p in folder.glob("*.json")}
    saved = omissions.published_report(store.root, article_id, job=result["job"])
    assert saved["summary"] == result["summary"]
    count = len(calls)
    omissions.review(ReadOnlyArchive(store.root), article_id, per_source=per_source)
    assert len(calls) == count
    assert {p.name: p.read_bytes() for p in folder.glob("*.json")} == before
    client = TestClient(create_app(ReadOnlyArchive(store.root)))
    response = client.get(
        "/wiki/api/completeness/" + article_id, params={"job": result["job"]}
    )
    assert response.status_code == 200 and response.json() == saved
    checkpoint = next(p for p in folder.glob("chapter-*.json"))
    value = json.loads(checkpoint.read_bytes())
    value["input_sha256"] = "tampered"
    checkpoint.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="omissions_checkpoint_changed"):
        omissions.published_report(store.root, article_id, job=result["job"])


def test_per_source_recipe_is_separate_and_old_reports_remain_verifiable(
    drafted, monkeypatch
):
    store, article_id = drafted
    calls = fake_model(monkeypatch)
    first = check(drafted)
    path = store.root / "wiki/completeness-jobs" / first["job"] / "receipt.json"
    original = path.read_bytes()
    first_report = canonical(
        omissions.published_report(store.root, article_id, job=first["job"])
    )
    second = omissions.review(ReadOnlyArchive(store.root), article_id, per_source=True)
    assert first["job"] != second["job"]
    assert "source_batching" not in first
    assert second["source_batching"] == omissions.SOURCE_BATCHING
    assert len(calls) == 8
    assert path.read_bytes() == original
    assert (
        canonical(omissions.published_report(store.root, article_id, job=first["job"]))
        == first_report
    )
    assert omissions.published_report(store.root, article_id)["job"] == second["job"]
    changed = json.loads(
        (
            store.root / "wiki/completeness-jobs" / second["job"] / "receipt.json"
        ).read_bytes()
    )
    changed.pop("source_batching")
    for target in (
        store.root / "wiki/completeness-jobs" / second["job"] / "receipt.json",
        store.root / "wiki/completeness" / (article_id + ".json"),
    ):
        target.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="omissions_source_batching_changed"):
        omissions.published_report(store.root, article_id)


def test_per_source_interruption_keeps_checkpoints_and_wrong_source_stays_invalid(
    drafted, monkeypatch
):
    store, article_id = drafted
    interrupted = []

    def interrupt(context, calls):
        if len(calls) == 2 and not interrupted:
            interrupted.append(True)
            raise GpuBusyError("Synthetic handoff")

    calls = fake_model(monkeypatch, interrupt)
    with pytest.raises(GpuBusyError):
        omissions.review(ReadOnlyArchive(store.root), article_id, per_source=True)
    folder = next((store.root / "wiki/completeness-jobs").iterdir())
    path = folder / "chapter-0-source-0.json"
    checkpoint = path.read_bytes()
    result = omissions.review(ReadOnlyArchive(store.root), article_id, per_source=True)
    assert result["state"] == "model_omissions_complete"
    assert len(calls) == 5 and path.read_bytes() == checkpoint
    context = calls[0]
    data = response(context)
    context["article_targets"][data["reviews"][0]["article_evidence"][0]][
        "citations"
    ] = ["foreign"]
    with pytest.raises(ValueError, match="omissions_evidence_from_other_source"):
        omissions.validate(data, context)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "invented",
        "no_prose",
        "no_evidence",
        "false_absence",
        "wrong_source",
    ],
)
def test_coverage_cannot_skip_ranges_or_reuse_unrelated_or_code_only_evidence(
    drafted, mutation
):
    store, article_id = drafted
    context = list(omissions.stages(Wiki(store.root).article(article_id)))[1][1]
    data = response(context)
    row = data["reviews"][0]
    if mutation == "missing":
        data["reviews"].pop()
    elif mutation == "duplicate":
        data["reviews"][-1] = copy.deepcopy(row)
    elif mutation == "invented":
        row["article_evidence"] = ["invented"]
    elif mutation == "no_prose":
        row["article_evidence"] = ["section-1-code"]
    elif mutation == "no_evidence":
        row["article_evidence"] = []
    elif mutation == "false_absence":
        row["coverage"] = "missing"
    else:
        context["article_targets"][row["article_evidence"][0]]["citations"] = [
            "foreign"
        ]
    with pytest.raises(ValueError, match="omissions_"):
        omissions.validate(data, context)


def test_interrupted_check_resumes_frozen_chapters_without_publishing_partial_result(
    drafted, monkeypatch
):
    store, article_id = drafted
    interrupted = []

    def interrupt(context, calls):
        if len(calls) == 3 and not interrupted:
            interrupted.append(True)
            raise GpuBusyError("Synthetic handoff")

    calls = fake_model(monkeypatch, interrupt)
    with pytest.raises(GpuBusyError):
        check(drafted)
    assert not (store.root / "wiki/completeness" / (article_id + ".json")).exists()
    folder = next((store.root / "wiki/completeness-jobs").iterdir())
    saved = (folder / "chapter-0.json").read_bytes()
    result = check(drafted)
    assert result["state"] == "model_omissions_complete"
    assert (folder / "chapter-0.json").read_bytes() == saved
    assert len(calls) == 5


def test_three_attempt_budget_survives_restart_and_keeps_source_checks_unpublished(
    drafted, monkeypatch
):
    store, article_id = drafted
    calls = fake_model(monkeypatch, lambda context, calls: '{"reviews":[]}')
    for _ in range(2):
        with pytest.raises(ValueError, match="attempt_budget_exhausted"):
            check(drafted)
    assert len(calls) == 3
    assert not (store.root / "wiki/completeness" / (article_id + ".json")).exists()


@pytest.mark.parametrize("tamper", ["checkpoint", "summary", "acceptance"])
def test_published_report_rejects_changed_evidence_or_false_acceptance(
    drafted, monkeypatch, tamper
):
    store, article_id = drafted
    fake_model(monkeypatch)
    report = check(drafted)
    folder = store.root / "wiki/completeness-jobs" / report["job"]
    if tamper == "checkpoint":
        p = folder / "chapter-0.json"
        value = json.loads(p.read_bytes())
        value["input_sha256"] = "invalid"
        p.write_text(json.dumps(value), encoding="utf-8")
    else:
        report["summary"]["missing"] += int(tamper == "summary")
        report["source_deletion_allowed"] = tamper == "acceptance"
        for p in (
            folder / "receipt.json",
            store.root / "wiki/completeness" / (article_id + ".json"),
        ):
            p.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="omissions_"):
        omissions.published_report(store.root, article_id)


def test_changing_local_model_keeps_immutable_report_history(drafted, monkeypatch):
    store, article_id = drafted
    fake_model(monkeypatch)
    first = check(drafted)
    old_path = store.root / "wiki/completeness-jobs" / first["job"] / "receipt.json"
    original = old_path.read_bytes()
    second = omissions.review(
        ReadOnlyArchive(store.root), article_id, "another-local-model"
    )
    assert second["job"] != first["job"]
    assert old_path.read_bytes() == original
    assert omissions.published_report(store.root, article_id)["job"] == second["job"]
    history = omissions.published_report(store.root, article_id, job=first["job"])
    assert history["job"] == first["job"]
    assert history["updated"] == first["updated"]


def test_legacy_draft_without_chapter_plan_is_readable_and_can_be_checked(
    drafted, monkeypatch
):
    from wechat_decrypt_tool.course_wiki import publish_draft

    store, article_id = drafted
    original = Wiki(store.root).article(article_id)
    packet = copy.deepcopy(original["packet"])
    for key in (
        "chapter_scopes",
        "chapter_plan",
        "quiz_scopes",
        "exercise_plan",
        "overview_scope",
    ):
        packet.pop(key)
    legacy = publish_draft(
        ReadOnlyArchive(store.root),
        original["article"],
        packet,
        "f" * 32,
        "synthetic",
        "frozen-local-weights",
    )
    assert (
        omissions.published_report(store.root, legacy["id"])["state"] == "not_started"
    )
    fake_model(monkeypatch)
    result = omissions.review(ReadOnlyArchive(store.root), legacy["id"])
    assert len(result["chapters"]) == 4
    assert result["state"] == "model_omissions_complete"


def test_frozen_sources_outside_chapter_assignments_are_reported_as_unchecked(
    drafted, monkeypatch
):
    from wechat_decrypt_tool.course_wiki import publish_draft

    store, article_id = drafted
    original = Wiki(store.root).article(article_id)
    packet = copy.deepcopy(original["packet"])
    unchecked = packet["chapter_scopes"][3][0]
    first = packet["chapter_scopes"][0][0]
    for key in (
        "chapter_scopes",
        "chapter_plan",
        "quiz_scopes",
        "exercise_plan",
        "overview_scope",
    ):
        packet.pop(key)
    article = copy.deepcopy(original["article"])
    for paragraph in article["sections"][3]["paragraphs"]:
        paragraph["citations"] = [first]
    article["sections"][3].update(code="", code_citations=[])
    legacy = publish_draft(
        ReadOnlyArchive(store.root),
        article,
        packet,
        "f" * 32,
        "synthetic",
        "frozen-local-weights",
    )
    fake_model(monkeypatch)
    result = omissions.review(ReadOnlyArchive(store.root), legacy["id"])
    assert result["unassigned_source_ids"] == [unchecked]
    assert not result["all_selected_characters_partitioned"]
    assert omissions.published_report(store.root, legacy["id"])[
        "unassigned_source_ids"
    ] == [unchecked]


def test_constrained_omission_report_keeps_schema_and_resumes_without_calls(
    drafted, monkeypatch
):
    store, article_id = drafted
    calls = fake_model(monkeypatch, constrained=True)
    result = omissions.review(
        ReadOnlyArchive(store.root),
        article_id,
        per_source=True,
        constrained_evidence=True,
    )
    assert result["generation_constraints"] == "verdict-dependent-evidence-grammar-v1"
    assert (
        omissions.published_report(store.root, article_id)["summary"]
        == result["summary"]
    )
    count = len(calls)
    assert (
        omissions.review(
            ReadOnlyArchive(store.root),
            article_id,
            per_source=True,
            constrained_evidence=True,
        )["job"]
        == result["job"]
    )
    with pytest.raises(ValueError, match="constraint_change_requires_new_article"):
        omissions.review(ReadOnlyArchive(store.root), article_id, per_source=True)
    assert len(calls) == count
    for folder in [
        store.root / "wiki/completeness",
        store.root / "wiki/completeness-jobs" / result["job"],
    ]:
        path = folder / (
            (article_id + ".json") if folder.name == "completeness" else "receipt.json"
        )
        changed = json.loads(path.read_bytes())
        changed.pop("generation_constraints")
        path.write_bytes(canonical(changed))
    with pytest.raises(ValueError, match="generation_constraints_changed"):
        omissions.published_report(store.root, article_id)


@pytest.mark.parametrize("constrained", [False, True])
def test_generation_constraint_change_cannot_reset_omission_budget(
    drafted, monkeypatch, constrained
):
    store, article_id = drafted
    calls = fake_model(
        monkeypatch, lambda context, calls: '{"reviews":[]}', constrained=constrained
    )
    with pytest.raises(ValueError, match="budget"):
        omissions.review(
            ReadOnlyArchive(store.root), article_id, constrained_evidence=constrained
        )
    saved = {
        p: p.read_bytes()
        for p in (store.root / "wiki/completeness-jobs").rglob("*.json")
    }
    with pytest.raises(ValueError, match="constraint_change_requires_new_article"):
        omissions.review(
            ReadOnlyArchive(store.root),
            article_id,
            constrained_evidence=not constrained,
        )
    assert len(calls) == 3 and all(p.read_bytes() == raw for p, raw in saved.items())
