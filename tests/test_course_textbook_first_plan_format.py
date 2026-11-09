import copy
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_first_plan_format as m
from wechat_decrypt_tool.course_knowledge import atomic_json


def fixture_job(tmp_path, monkeypatch, *, read_only=False):
    cards = {
        key: {"label": key, "teaching": "Synthetic explanation", "category": "concept"}
        for key in "abcdefghijkl"
    }
    raw = {
        "group_count": 2,
        "title": "Synthetic title",
        "groups": [
            {"heading": "Prerequisites", "purpose": "Understand conditions"},
            {"heading": "Applications", "purpose": "Apply the principles"},
        ],
        "assignments": {
            key: {"group": i // 6, "order": i % 6} for i, key in enumerate(cards)
        },
    }
    context = {"items": cards, "mode": "concepts"}
    saved, calls = {}, []

    class Base:
        def __init__(self):
            self.root, self.max_attempts = tmp_path, 2
            self.reader = SimpleNamespace(
                paths=set(),
                stage=lambda name, *args, **kwargs: copy.deepcopy(saved[name]),
            )

        def stage(self, *args, **kwargs):
            calls.append("original_nonplanning_role")
            return "original-nonplanning-result"

    job = m.job_type(Base, {"synthetic": "original-first-use"}, read_only=read_only)()

    def generate(self, name, prompt, schema, actual_context, validate, **kwargs):
        calls.append(name)
        assert actual_context["items"] == cards and actual_context["mode"] == "concepts"
        jsonschema.validate(raw, schema)
        outside = copy.deepcopy(raw)
        outside["assignments"]["a"]["group"] = raw["group_count"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(outside, schema)
        validate(raw)
        saved[name] = copy.deepcopy(raw)
        atomic_json(tmp_path / (name + ".json"), {"synthetic_actual_response": raw})

    monkeypatch.setattr(m.fresh.BASE_JOB, "stage", generate)
    return job, cards, raw, context, calls, saved


def test_actual_first_decoding_schema_is_count_bound_and_means_the_same_groups(
    tmp_path, monkeypatch
):
    job, cards, raw, context, calls, _ = fixture_job(tmp_path, monkeypatch)
    before = copy.deepcopy(raw)
    value = job.stage(
        "concepts-00-0000",
        m.writing.author.GROUP_PROMPT,
        m.writing.author.grouping_schema(cards, final=False),
        context,
        lambda v: m.writing.author.grouped(v, cards, final=False),
    )
    assert calls == ["concepts-00-0000"] and raw == before
    assert value == {key: row for key, row in raw.items() if key != "group_count"}
    assert len(job.reader.paths) == 2
    assert (
        m.writing.whole.read(
            tmp_path / "concepts-00-0000-first-counted-plan-request.json"
        )["attempt_budget"]
        == 2
    )


@pytest.mark.parametrize(
    "existing", ["attempts/concepts-00-0000-01.json", "concepts-00-0000.json"]
)
def test_cannot_opt_in_after_original_planning_role_was_attempted(
    existing, tmp_path, monkeypatch
):
    job, cards, _, context, calls, _ = fixture_job(tmp_path, monkeypatch)
    path = tmp_path / existing
    path.parent.mkdir(exist_ok=True)
    atomic_json(path, {"synthetic_spent": True})
    with pytest.raises(ValueError, match="before_first_original_attempt"):
        job.stage(
            "concepts-00-0000",
            m.writing.author.GROUP_PROMPT,
            m.writing.author.grouping_schema(cards, final=False),
            context,
            lambda v: None,
        )
    assert calls == []


def test_readonly_replay_reuses_exact_actual_response_without_generation(
    tmp_path, monkeypatch
):
    job, cards, _, context, _, saved = fixture_job(tmp_path, monkeypatch)
    args = (
        "concepts-00-0000",
        m.writing.author.GROUP_PROMPT,
        m.writing.author.grouping_schema(cards, final=False),
        context,
        lambda v: None,
    )
    value = job.stage(*args)
    readonly, _, _, _, calls, restored = fixture_job(
        tmp_path, monkeypatch, read_only=True
    )
    restored.update(saved)
    assert readonly.stage(*args) == value and calls == []


def test_readonly_replay_requires_the_existing_first_use_format_contract(
    tmp_path, monkeypatch
):
    job, cards, _, context, calls, _ = fixture_job(
        tmp_path, monkeypatch, read_only=True
    )
    with pytest.raises(ValueError, match="before_first_original_attempt"):
        job.stage(
            "concepts-00-0000",
            m.writing.author.GROUP_PROMPT,
            m.writing.author.grouping_schema(cards, final=False),
            context,
            lambda v: None,
        )
    assert calls == []


def test_nonplanning_roles_keep_the_original_writer_and_review_path(
    tmp_path, monkeypatch
):
    job, _, _, _, calls, _ = fixture_job(tmp_path, monkeypatch)
    assert (
        job.stage("section-0000-write", "original", {}, {}, lambda v: None)
        == "original-nonplanning-result"
    )
    assert calls == ["original_nonplanning_role"]


def test_actual_format_binding_contains_the_frozen_counted_implementation():
    assert len(m.binding()["module_sha256"]) == 64
    assert len(m.binding()["counted_module_sha256"]) == 64
    assert Path(m.counted.__file__).is_file()
