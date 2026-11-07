from contextlib import nullcontext
import json
from pathlib import Path

import pytest

from wechat_decrypt_tool import course_pdf_tail_continue as tail


def fixture(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    root.mkdir()
    folder = root / "plans"
    folder.mkdir()
    controls = []
    validation = folder / "validation.json"
    validation.write_text("{}", "utf-8")
    controls.append(validation)
    plans = {}
    for number in range(7, 11):
        plans[number] = folder / f"{number}.json"
        plans[number].write_text(json.dumps({"number": number}), "utf-8")
        controls.append(plans[number])
    prefix = "pdf-queue-20261103-0008"
    launch_path = root / "pdf-selected-queue-continuation-0007-0010-launched.json"
    identity = {"pid": 10, "created": 1.0}
    launch = {**identity, "first": 7, "last": 10, "after_prefix": "pdf-queue-20261103-0006",
        "inputs_base": "retained-inputs", "results_base": "retained-results",
        "bound_control_sha256": {str(p): tail.queue.sha(p.read_bytes()) for p in controls}}
    launch_path.write_text(json.dumps(launch), "utf-8")
    status = {**launch, "state": "paused", "number": 8, "prefix": prefix,
        "completed_batches": [{"number": 7, "prefix": "pdf-queue-20261103-0007", "page_occurrences": 1, "manifest_sha256": "previous-proof"}]}
    status_path = launch_path.with_name(launch_path.name.replace("-launched", "-progress"))
    status_path.write_text(json.dumps(status), "utf-8")
    pending = [{"pid": 40, "created": 2.0}]
    (root / (prefix + "-resume-status.json")).write_text(json.dumps({**pending[0], "state": "finite_batch_verified_and_backed_up"}), "utf-8")
    old = {"renderer": "replaced-tool.exe", "renderer_sha256": "same-renderer", "plan_sha256": tail.queue.sha(plans[8].read_bytes())}
    binding_path = root / (prefix + "-binding.json")
    binding_path.write_text(json.dumps(old), "utf-8")
    for suffix in ("-binding.json", "-validation.json"):
        (root / ("pdf-queue-20261103-0007" + suffix)).write_text("{}", "utf-8")
    resolver = root / "retained-resolver.py"
    resolver.write_text("# retained resolver", "utf-8")
    receipt = root / "environment-receipt.json"
    receipt.write_text("{}", "utf-8")
    relocation = root / (prefix + "-renderer-relocation.json")
    relocation.write_text(json.dumps({"retained_resolver_source": str(resolver), "environment_receipt_path": str(receipt)}), "utf-8")
    (root / "supervisor-stop.flag").write_bytes(b"retained stop history")
    attempted = []
    monkeypatch.setattr(tail.queue, "alive", lambda value: False)
    monkeypatch.setattr(tail.queue, "queue_plans", lambda folder: list(plans.items()))
    monkeypatch.setattr(tail.queue, "ReadOnlyArchive", lambda root: object())
    monkeypatch.setattr(tail.queue, "require_unattempted", lambda store, scope: attempted.append(scope))
    monkeypatch.setattr(tail, "frozen_pdf_scope", lambda value: {"number": value["number"]})
    monkeypatch.setattr(tail, "preserved_terminal", lambda *args: {})
    monkeypatch.setattr(tail.renderer, "verified_binding", lambda *args: (binding_path, {**old, "renderer": "identical-retained.exe"}))
    return root, folder, prefix, pending, status_path, attempted


def call_bind(data):
    root, folder, prefix, pending, _, _ = data
    return tail.bind(root, folder, 7, 10, prefix, pending)


def test_only_unattempted_tail_uses_resolved_location_and_preserves_originals(tmp_path, monkeypatch):
    data = fixture(tmp_path, monkeypatch)
    root, _, prefix, _, _, attempted = data
    originals = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    _, template, controls, _ = call_bind(data)
    assert attempted == [{"number": 9}, {"number": 10}]
    assert template["renderer"] == "identical-retained.exe"
    assert json.loads((root / (prefix + "-binding.json")).read_bytes())["renderer"] == "replaced-tool.exe"
    assert root / "supervisor-stop.flag" in controls
    assert all(p.read_bytes() == raw for p, raw in originals.items())


@pytest.mark.parametrize("change,reason", [
    ("control", "old_control_changed"),
    ("prepared", "future_batch_already_attempted"),
    ("partition", "old_completed_partition_changed"),
    ("failed", "partial_not_healthy"),
    ("wrong_worker", "exact_partial_worker_required"),
    ("live_controller", "old_controller_still_live"),
])
def test_unsafe_tail_bind_is_rejected(tmp_path, monkeypatch, change, reason):
    data = fixture(tmp_path, monkeypatch)
    root, folder, prefix, _, status_path, _ = data
    if change == "control":
        (folder / "9.json").write_bytes(b"changed")
    elif change == "prepared":
        (root / "pdf-queue-20261103-0009-binding.json").write_bytes(b"retained failure")
    elif change == "partition":
        value = json.loads(status_path.read_bytes())
        value["completed_batches"] = []
        status_path.write_text(json.dumps(value), "utf-8")
    elif change == "failed":
        path = root / (prefix + "-resume-status.json")
        path.write_text(json.dumps({"pid": 40, "created": 2.0, "state": "failed"}), "utf-8")
    elif change == "wrong_worker":
        data[3][0]["pid"] = 41
    else:
        monkeypatch.setattr(tail.queue, "alive", lambda value: value["pid"] == 10)
    with pytest.raises(ValueError, match=reason):
        call_bind(data)


def test_failed_guard_stops_before_the_next_batch_and_cannot_be_relaunched(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    root.mkdir()
    stop = root / "supervisor-stop.flag"
    stop.write_bytes(b"unchanged")
    plans = {8: root / "8.json", 9: root / "9.json", 10: root / "10.json"}
    for path in plans.values():
        path.write_text("{}", "utf-8")
    original_controls = {stop: tail.queue.sha(stop.read_bytes())}
    monkeypatch.setattr(tail, "bind", lambda *args: (plans, {"renderer": "same", "renderer_sha256": "same"}, dict(original_controls),
        {"inputs_base": str(tmp_path / "inputs"), "results_base": str(tmp_path / "results")}))
    monkeypatch.setattr(tail.queue, "checkpoint", lambda *args: None)
    monkeypatch.setattr(tail.queue, "alive", lambda value: False)
    monkeypatch.setattr(tail.renderer, "resolve_renderer", lambda *args: nullcontext())
    monkeypatch.setattr(tail.continuation, "verify_terminal", lambda *args: {})
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    prepared = []
    monkeypatch.setattr(tail.queue, "prepare", lambda *args: prepared.append(args[2]))

    def fail(*args, **kwargs):
        assert kwargs["maximum_power"] == 200 and kwargs["maximum_temperature"] == 70
        raise ValueError("synthetic guarded failure")

    monkeypatch.setattr(tail.queue, "guarded", fail)
    with pytest.raises(ValueError, match="synthetic guarded failure"):
        tail.run(root, root, 7, 10, "pdf-queue-20261103-0008", [{"pid": 1, "created": 1.0}])
    assert prepared == ["pdf-queue-20261103-0009"]
    assert stop.read_bytes() == b"unchanged"
    status = json.loads((root / "pdf-retained-tail-0009-0010-progress.json").read_bytes())
    assert status["state"] == "failed" and status["completed_batches"] == []
    with pytest.raises(ValueError, match="tail_already_launched"):
        tail.run(root, root, 7, 10, "pdf-queue-20261103-0008", [{"pid": 1, "created": 1.0}])
    assert prepared == ["pdf-queue-20261103-0009"]


@pytest.mark.parametrize("change", [None, "no_permission", "retained_marker", "backup"])
def test_missing_old_stop_requires_exact_manual_receipt_and_independent_copy(tmp_path, monkeypatch, change):
    root = tmp_path / "archive"
    folder = root / "wiki/manual-resume"
    folder.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    original = root / "supervisor-stop.flag"
    raw = b"retained thermal stop"
    digest = tail.queue.sha(raw)
    marker = folder / (original.name + "." + digest)
    marker.write_bytes(raw)
    receipt = folder / "receipt.json"
    receipt.write_text(json.dumps({"manual_user_confirmation": True,
        "automatic_thermal_resume_allowed": False, "old_budgets_reset": False,
        "original_markers": [{"name": original.name, "sha256": digest, "bytes": len(raw), "original_retained": True}]}), "utf-8")
    backup = Path.home() / "Documents/CourseKnowledgeBackups" / folder.name
    backup.mkdir(parents=True)
    for p in (receipt, marker):
        (backup / p.name).write_bytes(p.read_bytes())
    if change == "no_permission":
        value = json.loads(receipt.read_bytes())
        value["manual_user_confirmation"] = False
        receipt.write_text(json.dumps(value), "utf-8")
    elif change == "retained_marker":
        marker.write_bytes(b"different")
    elif change == "backup":
        (backup / receipt.name).write_bytes(b"different")
    if change:
        with pytest.raises(ValueError):
            tail.retained_controls(root, {original: digest}, folder)
    else:
        controls, relocations = tail.retained_controls(root, {original: digest}, folder)
        assert not original.exists() and original not in controls
        assert controls[marker] == digest and len(relocations) == 1
        assert relocations[0]["original_path"] == str(original)
