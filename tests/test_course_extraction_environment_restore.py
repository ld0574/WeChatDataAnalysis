import hashlib
import json

import pytest

from tools import course_extraction_environment_restore as restore


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "backup"
    root.mkdir()
    plan = {"files": [{"path": "python/python.exe"}, {"path": "payload/model.bin"}]}
    payloads = {
        "python/python.exe": b"synthetic-interpreter",
        "payload/model.bin": b"synthetic-weights",
        "environment-input-plan.json": json.dumps(plan).encode(),
        "extraction_bootstrap.py": b"synthetic-bootstrap",
        "run-environment.ps1": b"synthetic-launcher",
        "README.md": b"synthetic-readme",
    }
    rows = []
    for name, raw in payloads.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        rows.append(
            {"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        )
    value = {
        "policy": restore.POLICY,
        "files": rows,
        "input_plan_sha256": hashlib.sha256(
            payloads["environment-input-plan.json"]
        ).hexdigest(),
        "source_deletion_allowed": False,
    }
    manifest = root / "extraction-environment.json"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    return root, restore.file_sha(manifest), value


def probe_result(count):
    return {
        "state": "isolated_cpu_and_native_synthetic_probe_verified",
        "files_verified": count,
        "original_installation_access_attempts": 0,
        "network_connection_attempts": 0,
        "outside_module_count": 0,
        "original_installation_native_mapping_count": 0,
        "gpu_context_initialized": False,
        "gpu_inference_verified": False,
    }


def test_byte_identical_restore_preserves_source_and_bundled_probe_command(
    bundle, tmp_path
):
    source, expected, value = bundle
    destination = tmp_path / "restored"
    before = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    commands = []

    def probe(command):
        commands.append(command)
        return probe_result(len(value["files"]))

    receipt = restore.restore(source, destination, expected, reserve=0, runner=probe)
    assert receipt["files"] == len(value["files"])
    assert receipt["gpu_inference_verified"] is False
    assert commands == [
        [
            str(destination / "python/python.exe"),
            "-I",
            "-B",
            str(destination / "extraction_bootstrap.py"),
            "probe",
        ]
    ]
    assert all(p.read_bytes() == raw for p, raw in before.items())
    for row in value["files"]:
        assert (source / row["path"]).read_bytes() == (
            destination / row["path"]
        ).read_bytes()


def test_interruption_then_resume_reuses_verified_bytes(bundle, tmp_path):
    source, expected, value = bundle
    destination = tmp_path / "restored"

    def interrupt():
        if (destination / "python/python.exe").exists():
            raise InterruptedError("synthetic-pause")

    with pytest.raises(InterruptedError):
        restore.restore(source, destination, expected, reserve=0, checkpoint=interrupt)
    assert not (destination / "independent-restore-validation.json").exists()
    receipt = restore.restore(
        source,
        destination,
        expected,
        reserve=0,
        runner=lambda _: probe_result(len(value["files"])),
    )
    assert receipt["reused_files"] >= 1
    assert receipt["files"] == len(value["files"])


def test_changed_source_payload_fails_expected_hash_without_acceptance(
    bundle, tmp_path
):
    source, expected, _ = bundle
    p = source / "payload/model.bin"
    p.write_bytes(b"x" * p.stat().st_size)
    with pytest.raises(ValueError, match="expected_model_hash_mismatch"):
        restore.restore(source, tmp_path / "restored", expected, reserve=0)
    assert not (tmp_path / "restored/independent-restore-validation.json").exists()


@pytest.mark.parametrize("change", ["manifest_hash", "omission", "alias", "escape"])
def test_authority_failure_leaves_destination_absent(bundle, tmp_path, change):
    source, expected, value = bundle
    if change == "manifest_hash":
        expected = "0" * 64
    else:
        if change == "omission":
            value["files"].pop()
        elif change == "alias":
            row = dict(value["files"][0])
            row["path"] = row["path"].upper()
            value["files"].append(row)
        else:
            value["files"][0]["path"] = "../outside"
        p = source / "extraction-environment.json"
        p.write_text(json.dumps(value), encoding="utf-8")
        expected = restore.file_sha(p)
    with pytest.raises(ValueError):
        restore.restore(source, tmp_path / "restored", expected, reserve=0)
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize(
    "key,value",
    [
        ("network_connection_attempts", 1),
        ("gpu_context_initialized", True),
        ("original_installation_native_mapping_count", 1),
        ("original_installation_native_mapping_count", None),
        ("files_verified", 0),
    ],
)
def test_nonisolated_or_incomplete_probe_cannot_produce_receipt(
    bundle, tmp_path, key, value
):
    source, expected, manifest = bundle
    result = probe_result(len(manifest["files"]))
    result[key] = value
    with pytest.raises(ValueError, match="probe_not_isolated"):
        restore.restore(
            source, tmp_path / "restored", expected, reserve=0, runner=lambda _: result
        )
    assert not (tmp_path / "restored/independent-restore-validation.json").exists()


def test_unrelated_destination_is_never_overwritten(bundle, tmp_path):
    source, expected, _ = bundle
    destination = tmp_path / "existing"
    destination.mkdir()
    sentinel = destination / "keep"
    sentinel.write_bytes(b"keep")
    with pytest.raises(ValueError, match="existing_destination_not_owned"):
        restore.restore(source, destination, expected)
    assert sentinel.read_bytes() == b"keep"


def test_space_reserve_failure_does_not_create_destination(
    bundle, tmp_path, monkeypatch
):
    source, expected, _ = bundle

    def full(*args):
        raise InterruptedError("disk-reserve")

    monkeypatch.setattr(restore, "capacity", full)
    with pytest.raises(InterruptedError):
        restore.restore(source, tmp_path / "restored", expected)
    assert not (tmp_path / "restored").exists()


def test_pause_during_probe_stops_only_the_owned_child(bundle, tmp_path, monkeypatch):
    source, expected, _ = bundle
    children = []

    class Child:
        pid = 123
        stopped = False
        waited = False

        def __init__(self, *args, **kwargs):
            children.append(self)

        def poll(self):
            return 1 if self.stopped else None

        def terminate(self):
            self.stopped = True

        def wait(self, timeout):
            self.waited = True

    class Process:
        def __init__(self, *args):
            pass

        def create_time(self):
            return 1.0

        def nice(self, *args):
            pass

        def ionice(self, *args):
            pass

    def pause():
        if children:
            raise InterruptedError("synthetic-pause-during-probe")

    monkeypatch.setattr(restore.subprocess, "Popen", Child)
    monkeypatch.setattr(restore.psutil, "Process", Process)
    progress = tmp_path / "progress.json"
    with pytest.raises(InterruptedError):
        restore.restore(
            source,
            tmp_path / "restored",
            expected,
            reserve=0,
            progress=progress,
            checkpoint=pause,
        )
    assert len(children) == 1 and children[0].stopped and children[0].waited
    assert json.loads(progress.read_bytes())["state"] == "paused"
    assert not (tmp_path / "restored/independent-restore-validation.json").exists()
