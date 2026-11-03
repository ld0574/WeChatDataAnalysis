import copy
import json
from contextlib import contextmanager

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_authoring as author
from wechat_decrypt_tool import course_textbook_composition as composition
from wechat_decrypt_tool import course_textbook_plan_cache as module
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_authoring import archive as archive
from test_course_textbook_authoring import group_value
from test_course_textbook_composition import disputed_archive as disputed_archive
from test_course_textbook_composition import inference


def test_only_equal_ranks_are_tied_by_original_input_order():
    cards = {"z": {}, "a": {}, "b": {}, "c": {}}
    value = group_value(cards, 1)
    value["assignments"] = {
        "a": {"group": 0, "order": 3},
        "b": {"group": 0, "order": 3},
        "c": {"group": 0, "order": 8},
        "z": {"group": 0, "order": 3},
    }
    old = copy.deepcopy(value)
    actual = module.normalize(value, cards, final=True)
    assert value == old
    assert author.grouped(actual, cards, final=True)[0]["children"] == [
        "z",
        "a",
        "b",
        "c",
    ]
    assert actual["groups"] == old["groups"] and actual["title"] == old["title"]
    assert {k: v["group"] for k, v in actual["assignments"].items()} == {
        k: v["group"] for k, v in old["assignments"].items()
    }


@pytest.mark.parametrize(
    "change",
    [
        "empty-group",
        "missing-input",
        "foreign-group",
        "duplicate-heading",
        "no-collision",
    ],
)
def test_non_order_errors_are_not_recovered(change):
    cards = {"a": {}, "b": {}, "c": {}, "d": {}}
    value = group_value(cards, 2)
    for row in value["assignments"].values():
        row["order"] = 0
    if change == "empty-group":
        for row in value["assignments"].values():
            row["group"] = 0
    elif change == "missing-input":
        del value["assignments"]["a"]
    elif change == "foreign-group":
        value["assignments"]["a"]["group"] = 99
    elif change == "duplicate-heading":
        value["groups"][1]["heading"] = value["groups"][0]["heading"]
    else:
        value = group_value(cards, 2)
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        module.normalize(value, cards, final=False)


def stopped_fixture(request, monkeypatch):
    root, key, inventory, parent, _, _ = request.getfixturevalue("disputed_archive")
    _, working = composition.load_inputs(root, key, parent["job"])
    calls = inference(monkeypatch, working)
    monkeypatch.setattr(module, "local_model_digest", lambda _: "e" * 64)
    original = composition.AuthoringJob.generate_response

    @contextmanager
    def slot(self, stage):
        self.progress("generating", stage)
        yield

    monkeypatch.setattr(composition.AuthoringJob, "slot", slot)

    def collision(self, messages, *, schema):
        raw, _ = original(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] == composition.PROMPTS[author.GROUP_PROMPT]:
            for row in value["assignments"].values():
                row["order"] = 0
        return json.dumps(value), {
            "model_digest": "e" * 64,
            "done_reason": "stop",
            "prompt_eval_count": 100,
            "eval_count": 100,
            "context_length": 16384,
            "size_vram": 1024,
        }

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", collision)
    with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
        author.run(root, key, parent["job"], protocol=composition)
    folder = next((root / "wiki/textbook-composition-jobs").iterdir())
    p = folder / "progress.json"
    progress = json.loads(p.read_bytes())
    progress.update(pid=9999999, created=1)
    atomic_json(p, progress)
    return root, key, parent, folder, calls, original


def test_actual_failed_stage_recovers_without_calls_then_resumes_same_job(
    request, monkeypatch
):
    root, key, parent, folder, calls, original = stopped_fixture(request, monkeypatch)
    attempts = {p: p.read_bytes() for p in (folder / "attempts").iterdir()}
    count = len(calls)
    result = module.recover(root, key, parent["job"], folder.name, "concepts-00-0000")
    assert result["model_calls"] == 0 and result["changed_order_numbers"] > 0
    assert len(calls) == count and all(
        p.read_bytes() == raw for p, raw in attempts.items()
    )
    monkeypatch.setattr(composition.AuthoringJob, "generate_response", original)
    result = author.run(root, key, parent["job"], protocol=composition)
    assert result["job"] == folder.name
    assert (folder / "private-article.json").is_file()
    assert not (folder / "reading.json").exists()
    assert (
        sum(prompt == composition.PROMPTS[author.GROUP_PROMPT] for prompt, _ in calls)
        == 2
    )
    assert all(p.read_bytes() == raw for p, raw in attempts.items())


@pytest.mark.parametrize("change", ["different-error", "truncated", "changed-request"])
def test_inexact_or_incomplete_failed_inputs_are_rejected(request, monkeypatch, change):
    root, key, parent, folder, calls, _ = stopped_fixture(request, monkeypatch)
    p = folder / "attempts/concepts-00-0000-01.json"
    value = json.loads(p.read_bytes())
    if change == "different-error":
        value["validation_error"] = "missing_textbook_exact_concept_assignments"
    elif change == "truncated":
        value["model_metrics"]["done_reason"] = "length"
    else:
        value["request_sha256"] = "0" * 64
    atomic_json(p, value)
    count = len(calls)
    with pytest.raises(ValueError):
        module.recover(root, key, parent["job"], folder.name, "concepts-00-0000")
    assert len(calls) == count
    assert not (folder / "concepts-00-0000.json").exists()
