"""No skipped or duplicated source ranges in the new omission protocol."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from test_course_wiki_check_constraints import reverse_context
from test_course_wiki_completeness import (
    archive as source_archive,
    courseware as source_courseware,
    drafted as source_drafted,
    long_plan as source_long_plan,
    published as source_published,
    response,
)
from test_course_wiki_snapshot_writers import retain_frames
from wechat_decrypt_tool import course_wiki_check_constraints as constraints
from wechat_decrypt_tool import course_wiki_completeness as omissions
from wechat_decrypt_tool import course_wiki_snapshot as snapshot
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_knowledge import GpuBusyError, atomic_json
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki import Wiki

archive = source_archive
published = source_published
courseware = source_courseware
long_plan = source_long_plan
drafted = source_drafted


def as_keyed(value):
    return {
        "reviews": {
            r["window"]: {k: v for k, v in r.items() if k != "window"}
            for r in value["reviews"]
        }
    }


def five_windows():
    context = reverse_context()
    context["windows"] = {f"a@{i}": {"source": "a"} for i in range(5)}
    return context


def test_observed_five_rows_four_unique_windows_cannot_pass_keyed_grammar():
    context = five_windows()
    value = response(context)
    value["reviews"][-1]["window"] = value["reviews"][0]["window"]
    Draft202012Validator(constraints.reverse(context)).validate(value)
    with pytest.raises(ValueError, match="missing_or_duplicate_windows"):
        omissions.validate(value, context)
    schema = omissions.selection_schema(context, keyed=True)
    Draft202012Validator.check_schema(schema)
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(as_keyed(value))
    with pytest.raises(ValueError, match="keyed_result_shape_invalid"):
        omissions.decode_keyed(as_keyed(value), context)
    complete = as_keyed(response(context))
    Draft202012Validator(schema).validate(complete)
    assert omissions.decode_keyed(complete, context) == response(context)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_window",
        "missing_window",
        "array",
        "extra_field",
        "foreign_evidence",
        "empty_supported",
    ],
)
def test_keyed_shape_and_evidence_remain_strict(mutation):
    context = five_windows()
    value = as_keyed(response(context))
    if mutation == "extra_window":
        value["reviews"]["foreign@0"] = copy.deepcopy(value["reviews"]["a@0"])
    elif mutation == "missing_window":
        del value["reviews"]["a@0"]
    elif mutation == "array":
        value = response(context)
    elif mutation == "extra_field":
        value["reviews"]["a@0"]["window"] = "a@0"
    elif mutation == "foreign_evidence":
        value["reviews"]["a@0"]["article_evidence"] = ["section-1-p-0"]
    else:
        value["reviews"]["a@0"]["article_evidence"] = []
    with pytest.raises(ValueError, match="keyed_result_shape_invalid"):
        omissions.decode_keyed(value, context)


@pytest.mark.parametrize("verdict", ["missing", "not_essential", "unclear"])
def test_honest_no_evidence_judgments_are_preserved(verdict):
    context = five_windows()
    value = as_keyed(response(context))
    for row in value["reviews"].values():
        row.update(coverage=verdict, article_evidence=[])
    normalized = omissions.decode_keyed(value, context)
    assert all(r["coverage"] == verdict for r in normalized["reviews"])


def mock_model(monkeypatch, *, keyed=True, intercept=None):
    calls = []

    def chat(name, messages, *, schema):
        context = json.loads(messages[1]["content"])
        assert schema == omissions.selection_schema(
            context, constrained=not keyed, keyed=keyed
        )
        assert messages[0]["content"].startswith(
            omissions.KEYED_SYSTEM if keyed else omissions.SYSTEM
        )
        calls.append(context)
        if intercept:
            value = intercept(context, calls)
            if value is not None:
                return value
        value = response(context)
        return json.dumps(as_keyed(value) if keyed else value)

    monkeypatch.setattr(writer, "local_chat", chat)
    return calls


def check(drafted, **kw):
    store, article = drafted
    return omissions.review(ReadOnlyArchive(store.root), article, per_source=True, **kw)


def test_keyed_reports_resume_and_restore_without_original_access(
    drafted, monkeypatch, published
):
    store, article = drafted
    retain_frames(store, published[1])
    original = canonical(Wiki(store.root).article(article))
    calls = mock_model(monkeypatch)
    result = check(drafted, keyed_windows=True)
    assert result["generation_constraints"] == constraints.KEYED_REVERSE_POLICY
    assert canonical(Wiki(store.root).article(article)) == original
    assert result["all_selected_characters_partitioned"]
    assert not result["semantic_accuracy_verified"]
    assert not result["source_deletion_allowed"]
    count = len(calls)
    restored_report = omissions.published_report(store.root, article)
    assert restored_report["summary"] == result["summary"]
    assert check(drafted, keyed_windows=True)["job"] == result["job"]
    assert len(calls) == count
    folder = store.root / "wiki/completeness-jobs" / result["job"]
    checkpoints = [
        json.loads(p.read_bytes())["data"] for p in folder.glob("chapter-*.json")
    ]
    assert checkpoints and all(isinstance(c["reviews"], dict) for c in checkpoints)
    backup = snapshot.create(
        store.root, store.root.parent / "keyed-backup", reserve_gib=0
    )
    restored = snapshot.restore(
        backup, store.root.parent / "keyed-restored", reserve_gib=0
    )
    assert omissions.published_report(restored, article) == restored_report
    checked = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("wiki_snapshot_isolated_reader.py")),
            str(restored),
            str(store.root),
            str(Path(snapshot.__file__).parent),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert json.loads(checked.stdout)["original_root_access_attempts"] == 0


@pytest.mark.parametrize("keyed_first", [False, True])
def test_old_and_keyed_cannot_switch_to_refresh_exhausted_budget(
    drafted, monkeypatch, keyed_first
):
    calls = mock_model(
        monkeypatch,
        keyed=keyed_first,
        intercept=lambda *a: '{"reviews":{}}' if keyed_first else '{"reviews":[]}',
    )
    options = {"keyed_windows": True} if keyed_first else {"constrained_evidence": True}
    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        check(drafted, **options)
    store, _ = drafted
    retained = {
        p: p.read_bytes()
        for p in store.root.glob("wiki/completeness-jobs/*/attempts/*.json")
    }
    assert len(calls) == 3 and len(retained) == 3
    with pytest.raises(ValueError, match="constraint_change_requires_new_article"):
        check(
            drafted,
            **(
                {"constrained_evidence": True}
                if keyed_first
                else {"keyed_windows": True}
            ),
        )
    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        check(drafted, **options)
    assert len(calls) == 3
    assert all(p.read_bytes() == raw for p, raw in retained.items())


def test_pause_and_gpu_wait_preserve_keyed_completed_stages(drafted, monkeypatch):
    state = ["gpu"]

    def interrupt(context, calls):
        if len(calls) == 2 and state:
            if state[0] == "gpu":
                state[0] = "pause"
                raise GpuBusyError("synthetic occupied GPU")
        if state and state[0] == "pause":
            state.clear()
            raise InterruptedError("synthetic pause")

    calls = mock_model(monkeypatch, intercept=interrupt)
    with pytest.raises(GpuBusyError):
        check(drafted, keyed_windows=True)
    store, _ = drafted
    before = {
        p: p.read_bytes()
        for p in store.root.glob("wiki/completeness-jobs/*/chapter-*.json")
    }
    assert len(before) == 1
    with pytest.raises(InterruptedError):
        check(drafted, keyed_windows=True)
    result = check(drafted, keyed_windows=True)
    assert result["state"] == "model_omissions_complete"
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert calls.count(calls[0]) == 1


def test_duplicate_raw_json_fields_are_rejected_without_changing_old_parser():
    job = object.__new__(omissions.OmissionJob)
    raw = '{"reviews":{"a@0":{"coverage":"missing"},"a@0":{"coverage":"covered"}}}'
    job.keyed_windows = True
    with pytest.raises(ValueError, match="duplicate_json_keys"):
        job.parse_response(raw)
    job.keyed_windows = False
    assert job.parse_response(raw) == json.loads(raw)


def test_keyed_policy_is_only_supported_by_reverse_checks():
    assert constraints.generation_policy(constraints.KEYED_REVERSE_POLICY, reverse=True)
    with pytest.raises(ValueError, match="unknown_generation_constraints"):
        constraints.generation_policy(constraints.KEYED_REVERSE_POLICY)


def test_duplicate_fields_fail_inside_real_bounded_stage(drafted, monkeypatch):
    def duplicated(context, calls):
        value = as_keyed(response(context))
        key, row = next(iter(value["reviews"].items()))
        members = [
            json.dumps(k) + ":" + json.dumps(v) for k, v in value["reviews"].items()
        ]
        members.append(json.dumps(key) + ":" + json.dumps(row))
        return '{"reviews":{' + ",".join(members) + "}}"

    calls = mock_model(monkeypatch, intercept=duplicated)
    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        check(drafted, keyed_windows=True)
    store, _ = drafted
    paths = list(store.root.glob("wiki/completeness-jobs/*/attempts/*.json"))
    assert len(calls) == len(paths) == 3
    assert all(
        json.loads(p.read_bytes())["validation_error"]
        == "duplicate_json_keys_in_keyed_omissions"
        for p in paths
    )


def test_rehashed_keyed_checkpoint_cannot_hide_a_missing_result(drafted, monkeypatch):
    from wechat_decrypt_tool.course_lesson_exports import digest

    store, article = drafted
    mock_model(monkeypatch)
    result = check(drafted, keyed_windows=True)
    folder = store.root / "wiki/completeness-jobs" / result["job"]
    path = next(folder.glob("chapter-*.json"))
    value = json.loads(path.read_bytes())
    value["data"]["reviews"].pop(next(iter(value["data"]["reviews"])))
    value["data_sha256"] = digest(canonical(value["data"]))
    atomic_json(path, value)
    with pytest.raises(ValueError, match="keyed_result_shape_invalid"):
        omissions.published_report(store.root, article)
