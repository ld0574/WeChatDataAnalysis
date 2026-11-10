from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_graph_tail_roles as m
from wechat_decrypt_tool.course_knowledge import atomic_json


def dispatcher(tmp_path, monkeypatch, *, readonly=False, failure=None):
    calls, original_roles, derived = [], [], []

    class Parent:
        def __init__(self):
            self.reader = SimpleNamespace(paths=set())
            self.finished_sections, self.section_count = 0, 0

        def stage(self, name, *args, **kwargs):
            calls.append(name)
            if failure:
                raise ValueError(failure)
            return "accepted_original_stage"

        def progress(self, *args, **kwargs):
            return self.finished_sections, self.section_count

    base = SimpleNamespace(folder=tmp_path / "tail", root=tmp_path, count=1,
        units=[{"id": "section-0000"}, {"id": "section-0001"}, {"id": "section-0002"}])
    monkeypatch.setattr(m, "parent_type", lambda *args, **kwargs: Parent)

    def bundle(*args):
        return SimpleNamespace(folder=base.folder / "units/section-0002", paths=set(), prefix="section-0002")

    def roles(base, bundle, *, read_only):
        original_roles.append(read_only)
        return {"files": {}, "candidate": "actual_original_model_repair", "final_review": "actual_original_final_review"}

    def derive(bundle, result, name, *args, **kwargs):
        derived.append((name, kwargs["read_only"]))
        return result["candidate"] if name.endswith("write") else result["final_review"], tmp_path / name

    monkeypatch.setattr(m, "unit_bundle", bundle)
    monkeypatch.setattr(m, "original_roles", roles)
    monkeypatch.setattr(m.prior.mapped, "derived_stage", derive)
    registry = {}
    return m.tail_type(base, registry, read_only=readonly)(), base, calls, original_roles, derived, registry


def invoke(job, name):
    return job.stage(name, "original_prompt", {}, {}, lambda value: None, evidence_packet={})


def test_successful_original_write_is_not_replaced_or_reaudited(tmp_path, monkeypatch):
    job, _, calls, roles, derived, registry = dispatcher(tmp_path, monkeypatch)
    assert invoke(job, "section-0002-write") == "accepted_original_stage"
    assert calls == ["section-0002-write"] and not roles and not derived and not registry


def test_exhausted_future_graph_write_spends_only_original_unused_roles_once(tmp_path, monkeypatch):
    job, _, calls, roles, derived, registry = dispatcher(tmp_path, monkeypatch,
        failure="wiki_stage_attempt_budget_exhausted_section-0002-write")
    assert invoke(job, "section-0002-write") == "actual_original_model_repair"
    assert invoke(job, "section-0002-review") == "actual_original_final_review"
    assert calls == ["section-0002-write"] and roles == [False]
    assert derived == [("section-0002-write", False), ("section-0002-review", False)]
    assert list(registry) == ["section-0002"]


@pytest.mark.parametrize("readonly,failure", [(True, "wiki_stage_attempt_budget_exhausted_section-0002-write"),
    (False, "requires_gpu_temperature_pause"), (False, "requires_missing_source"),
    (False, "wiki_stage_attempt_budget_exhausted_section-0001-write")])
def test_unrelated_failures_and_missing_readonly_derivations_never_dispatch_roles(tmp_path, monkeypatch, readonly, failure):
    job, _, calls, roles, _, registry = dispatcher(tmp_path, monkeypatch, readonly=readonly, failure=failure)
    with pytest.raises(ValueError, match="only_original_graph_exhaustion_dispatch_no_other_retry"):
        invoke(job, "section-0002-write")
    assert calls == ["section-0002-write"] and not roles and not registry


def test_readonly_existing_unit_authenticates_roles_without_entering_failed_write(tmp_path, monkeypatch):
    job, base, calls, roles, derived, _ = dispatcher(tmp_path, monkeypatch, readonly=True,
        failure="Unexpected original inference")
    path = base.folder / "units/section-0002/execution.json"
    path.parent.mkdir(parents=True)
    atomic_json(path, {"synthetic": True})
    before = path.read_bytes()
    assert invoke(job, "section-0002-write") == "actual_original_model_repair"
    assert not calls and roles == [True] and derived == [("section-0002-write", True)]
    assert path.read_bytes() == before


def test_repair_failure_is_terminal_and_does_not_repeat_exhausted_write(tmp_path, monkeypatch):
    job, _, calls, _, _, registry = dispatcher(tmp_path, monkeypatch,
        failure="wiki_stage_attempt_budget_exhausted_section-0002-write")

    def failed(*args, **kwargs):
        raise ValueError("wiki_stage_attempt_budget_exhausted_section-0002-repair")

    monkeypatch.setattr(m, "original_roles", failed)
    with pytest.raises(ValueError, match="exhausted_section-0002-repair"):
        invoke(job, "section-0002-write")
    assert calls == ["section-0002-write"] and not registry


def test_unbound_graph_failure_is_rejected_before_original_roles(tmp_path, monkeypatch):
    job, _, calls, roles, _, _ = dispatcher(tmp_path, monkeypatch,
        failure="wiki_stage_attempt_budget_exhausted_section-0002-write")

    def unrelated(*args):
        raise ValueError("requires_graph_write_resume_reconstructed_same_actual_graph_failure")

    monkeypatch.setattr(m, "unit_bundle", unrelated)
    with pytest.raises(ValueError, match="same_actual_graph_failure"):
        invoke(job, "section-0002-write")
    assert calls == ["section-0002-write"] and not roles


def test_earlier_original_units_and_whole_reading_keep_parent_dispatch(tmp_path, monkeypatch):
    job, _, calls, roles, derived, _ = dispatcher(tmp_path, monkeypatch)
    for name in ("concepts-00-0000", "section-0000-write", "section-0000-repair-review", "reading-0000"):
        assert invoke(job, name) == "accepted_original_stage"
    assert len(calls) == 4 and not roles and not derived


def test_previous_factory_is_restored_after_original_role_failure(tmp_path, monkeypatch):
    original_factory = m.prior.original.job_type
    bundle = SimpleNamespace(folder=tmp_path / "unit", paths=set())
    base = SimpleNamespace()
    monkeypatch.setattr(m, "unit_execution", lambda *args: {"synthetic": True})
    monkeypatch.setattr(m.prior, "require_unused", lambda value: None)
    monkeypatch.setattr(m, "parent_type", lambda *args, **kwargs: "prepared_parent_class")

    def fail(*args, **kwargs):
        assert m.prior.original.job_type(None, read_only=False) == "prepared_parent_class"
        raise RuntimeError("Synthetic original role failure")

    monkeypatch.setattr(m.prior, "original_roles", fail)
    with pytest.raises(RuntimeError):
        m.original_roles(base, bundle, read_only=False)
    assert m.prior.original.job_type is original_factory
    assert m.whole.read(bundle.folder / "execution.json") == {"synthetic": True}
    with pytest.raises(ValueError, match="single_original_unit_registration_without_retry"):
        m.original_roles(base, bundle, read_only=False)


def test_used_original_roles_are_rejected_before_registration(tmp_path, monkeypatch):
    bundle = SimpleNamespace(folder=tmp_path / "unit", paths=set())

    def used(*args):
        raise ValueError("requires_all_remaining_original_roles_unused")

    monkeypatch.setattr(m.prior, "require_unused", used)
    with pytest.raises(ValueError, match="remaining_original_roles_unused"):
        m.original_roles(None, bundle, read_only=False)
    assert not bundle.folder.exists()


def test_inherited_negative_and_graph_issues_are_preserved_without_acceptance(monkeypatch):
    def original(*args):
        return [{"kind": "original_negative_review"}, {"kind": "previous_graph_issue"}]
    monkeypatch.setattr(m.writing, "final_issues", original)
    base = SimpleNamespace(inventory={"synthetic": True})
    unit = SimpleNamespace(candidate={"synthetic": True}, defects=[{"unknown_endpoints": 1}])
    with pytest.raises(RuntimeError):
        with m.runtime_issues(base, {"section-0012": (unit, {"final_checks_passed": False})}):
            actual = m.writing.final_issues(base.inventory, {}, [])
            assert len(actual) == 3 and actual[0]["kind"] == "original_negative_review"
            assert actual[1]["kind"] == "previous_graph_issue" and actual[2]["adapter_graph_changes"] == 0
            raise RuntimeError("Synthetic interrupt")
    assert m.writing.final_issues is original


def test_worker_requires_real_input_restore_before_started_or_any_original_role(tmp_path, monkeypatch):
    bundle = SimpleNamespace(root=tmp_path, folder=tmp_path / "tail", job=SimpleNamespace(root=tmp_path / "writer"),
        old_status={"state": "stopped"})
    bundle.job.root.mkdir()
    bundle.folder.mkdir()
    atomic_json(bundle.job.root / "progress.json", bundle.old_status)
    atomic_json(bundle.folder / "independent-input-restore.json", {"state": "wrong"})
    monkeypatch.setattr(m, "load", lambda *args: bundle)
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    with pytest.raises(ValueError, match="independent_inputs_restored_before_original_roles"):
        m.worker(tmp_path, "a" * 32)
    assert not (bundle.folder / "started.json").exists()
