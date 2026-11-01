import hashlib
import json
from types import SimpleNamespace

import pytest

from tools import course_extraction_environment_bootstrap as bootstrap
from tools import course_extraction_environment_copy as copier


def spec(source, path="payload/model.bin"):
    stat = source.stat()
    return {"path": path, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def test_copy_hashes_source_and_target_and_never_changes_original(tmp_path):
    source = tmp_path / "original.bin"
    raw = b"complete synthetic model bytes\0" * 100
    source.write_bytes(raw)
    row = spec(source)
    row["expected_sha256"] = hashlib.sha256(raw).hexdigest()
    target = tmp_path / "bundle/payload/model.bin"
    before = source.stat().st_mtime_ns
    copied = copier.copy_one(source, target, row, capacity_check=lambda *a: None)
    assert target.read_bytes() == source.read_bytes() == raw
    assert source.stat().st_mtime_ns == before
    assert copied == {
        "path": row["path"],
        "bytes": len(raw),
        "sha256": row["expected_sha256"],
    }
    assert not target.with_name(target.name + ".environment-copy-part").exists()


def test_changed_source_is_not_copied_under_old_frozen_version(tmp_path):
    source = tmp_path / "original.bin"
    source.write_bytes(b"original")
    row = spec(source)
    source.write_bytes(b"changed size")
    target = tmp_path / "bundle/model.bin"
    with pytest.raises(ValueError, match="frozen_source_changed"):
        copier.copy_one(source, target, row, capacity_check=lambda *a: None)
    assert not target.parent.exists()


def test_source_change_during_copy_never_promotes_partial_file(tmp_path):
    source = tmp_path / "original.bin"
    source.write_bytes(b"original")
    row = spec(source)
    target = tmp_path / "bundle/model.bin"
    changed = False

    def checkpoint():
        nonlocal changed
        if not changed:
            source.write_bytes(b"changed size during copy")
            changed = True

    with pytest.raises(ValueError, match="source_changed_during_copy"):
        copier.copy_one(
            source, target, row, checkpoint=checkpoint, capacity_check=lambda *a: None
        )
    assert not target.exists()


def test_expected_model_identity_and_disk_reserve_cannot_be_ignored(tmp_path):
    source = tmp_path / "original.bin"
    source.write_bytes(b"wrong model bytes")
    row = spec(source)
    row["expected_sha256"] = "0" * 64
    target = tmp_path / "bundle/model.bin"
    with pytest.raises(ValueError, match="expected_model_hash_mismatch"):
        copier.copy_one(source, target, row, capacity_check=lambda *a: None)
    assert not target.exists()

    def no_space(*args):
        raise InterruptedError("synthetic_no_space")

    with pytest.raises(InterruptedError, match="synthetic_no_space"):
        copier.copy_one(source, target, row, capacity_check=no_space)
    assert not target.exists()
    with pytest.raises(ValueError, match="overlaps_source"):
        copier.copy_one(source, source, row, capacity_check=lambda *a: None)


def test_component_mapping_does_not_read_an_unlisted_external_source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    inside = root / "model.bin"
    inside.write_bytes(b"model")
    other = tmp_path / "outside.bin"
    other.write_bytes(b"outside")
    components = {"models": {"source_root": str(root), "target_prefix": "models"}}
    row = {"component": "models", "source": str(inside), "path": "models/model.bin"}
    assert copier.source_file(row, components) == inside
    with pytest.raises(ValueError, match="outside_component"):
        copier.source_file({**row, "source": str(other)}, components)
    with pytest.raises(ValueError, match="mapping_changed"):
        copier.source_file({**row, "path": "models/wrong.bin"}, components)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    root.mkdir()
    monkeypatch.setattr(bootstrap, "BUNDLE", root)
    planned = [
        "python/python.exe",
        "python/Lib/os.py",
        "ollama/ollama.exe",
        "src/wechat_decrypt_tool/course_knowledge.py",
        "tools/ffmpeg/ffmpeg.exe",
        "tools/poppler/bin/pdftoppm.exe",
        "payload/synthetic-model.bin",
    ]
    plan = {"files": [{"path": p} for p in planned]}
    plan_raw = json.dumps(plan).encode()
    files = []
    for p in [
        *planned,
        "extraction_bootstrap.py",
        "run-environment.ps1",
        "README.md",
        "environment-input-plan.json",
    ]:
        target = root / p
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = (
            plan_raw
            if p == "environment-input-plan.json"
            else b"synthetic fixture, not executable"
        )
        target.write_bytes(raw)
        files.append(
            {"path": p, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        )
    manifest = {
        "policy": bootstrap.POLICY,
        "files": files,
        "source_deletion_allowed": False,
        "input_plan_sha256": hashlib.sha256(plan_raw).hexdigest(),
    }
    (root / "extraction-environment.json").write_text(json.dumps(manifest), "utf-8")
    return root, manifest


def test_independent_verifier_checks_complete_plan_and_all_payload_bytes(bundle):
    root, manifest = bundle
    assert bootstrap.verify()[0] == manifest
    (root / "payload/synthetic-model.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="payload_changed"):
        bootstrap.verify()


def test_rehashed_file_manifest_cannot_hide_an_omitted_planned_model(bundle):
    root, manifest = bundle
    manifest["files"] = [
        r for r in manifest["files"] if r["path"] != "payload/synthetic-model.bin"
    ]
    (root / "extraction-environment.json").write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(ValueError, match="incomplete_planned_payload"):
        bootstrap.verify()


def test_renamed_case_alias_and_modified_input_plan_are_rejected(bundle):
    root, manifest = bundle
    alias = dict(manifest["files"][0])
    alias["path"] = alias["path"].upper()
    manifest["files"].append(alias)
    (root / "extraction-environment.json").write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(ValueError, match="duplicate_manifest_path"):
        bootstrap.verify()
    (root / "environment-input-plan.json").write_text("{}", "utf-8")
    with pytest.raises(ValueError, match="input_plan_changed"):
        bootstrap.verify()


@pytest.mark.parametrize(
    "relative",
    [
        "../outside",
        "C:/outside",
        "payload/../outside",
        "payload\\outside",
        "payload//outside",
    ],
)
def test_payload_path_never_escapes_the_bundle(bundle, relative):
    with pytest.raises(ValueError, match="invalid_relative_path"):
        bootstrap.inside(relative)


def test_native_dll_mapping_from_original_installation_is_detected(tmp_path):
    original = tmp_path / "original-runtime"
    restored = tmp_path / "restored-runtime"
    paths = [original / "torch/lib/torch_cpu.dll", restored / "torch/lib/torch_cpu.dll"]
    assert bootstrap.original_native_mappings(paths, [original]) == [
        str(paths[0].resolve())
    ]
    assert bootstrap.original_native_mappings(paths[1:], [original]) == []


def test_native_mapping_sibling_prefix_is_not_mistaken_for_original(tmp_path):
    original = tmp_path / "python"
    sibling = tmp_path / "python-restored/python311.dll"
    assert bootstrap.original_native_mappings([sibling], [original]) == []


def test_bootstrap_never_falls_back_to_the_original_python(bundle):
    with pytest.raises(ValueError, match="requires_bundled_interpreter"):
        bootstrap.activate()


def test_interrupted_copy_resumes_verified_files_and_keeps_foreign_directory(
    tmp_path, monkeypatch
):
    wiki = tmp_path / "archive/wiki"
    wiki.mkdir(parents=True)
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "backup"
    records = []
    for i in range(102):
        file = source / f"{i}.bin"
        file.write_bytes(f"synthetic {i}".encode())
        records.append(
            {
                **spec(file, f"payload/{i}.bin"),
                "source": str(file),
                "component": "fixture",
            }
        )
    plan = {
        "destination": str(destination),
        "files": records,
        "bytes": sum(r["bytes"] for r in records),
        "required_allocated_bytes_estimate": 4096,
        "python_version": "synthetic",
        "components": [
            {"name": "fixture", "source_root": str(source), "target_prefix": "payload"}
        ],
    }
    raw = json.dumps(plan).encode()
    plan_path = wiki / "plan.json"
    plan_path.write_bytes(raw)
    inventory = wiki / "inventory.json"
    inventory.write_text(
        json.dumps({"plan_sha256": hashlib.sha256(raw).hexdigest()}), "utf-8"
    )
    for name, value in {
        "ROOT": wiki.parent,
        "WIKI": wiki,
        "PLAN": plan_path,
        "INVENTORY": inventory,
        "PROGRESS": wiki / "progress.json",
    }.items():
        monkeypatch.setattr(copier, name, value)
    monkeypatch.setattr(
        copier.psutil,
        "Process",
        lambda *a: SimpleNamespace(
            nice=lambda *a: None, ionice=lambda *a: None, create_time=lambda: 1.0
        ),
    )
    monkeypatch.setattr(copier, "capacity", lambda *a, **kw: None)
    stop = [True]

    def checkpoint():
        if stop[0] and len(list((destination / "payload").glob("*.bin"))) == 100:
            raise InterruptedError("synthetic_pause_after_100")

    monkeypatch.setattr(
        copier, "ReadOnlyArchive", lambda root: SimpleNamespace(checkpoint=checkpoint)
    )
    copied = []
    original = copier.copy_one

    def record_copy(*a, **kw):
        copied.append(a[2]["path"])
        return original(*a, **{**kw, "capacity_check": lambda *a: None})

    monkeypatch.setattr(copier, "copy_one", record_copy)
    with pytest.raises(SystemExit):
        copier.main()
    checkpoint_file = destination / "copy-checkpoint.json"
    assert len(json.loads(checkpoint_file.read_bytes())["files"]) == 100
    assert len(copied) == 100
    before = {p.name: p.read_bytes() for p in (destination / "payload").glob("*.bin")}
    stop[0] = False

    def do_not_launch_verifier(*a, **kw):
        raise OSError("synthetic_verifier_launch_not_executed")

    monkeypatch.setattr(copier.subprocess, "Popen", do_not_launch_verifier)
    with pytest.raises(SystemExit):
        copier.main()
    assert len(copied) == 102
    assert all(
        (destination / "payload" / name).read_bytes() == raw
        for name, raw in before.items()
    )
    assert len(json.loads(checkpoint_file.read_bytes())["files"]) == 102
    assert not (
        wiki / "extraction-environment-20261102-backup-validation.json"
    ).exists()

    # An unrelated existing destination must never be adopted or overwritten.
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    user_file = foreign / "keep.txt"
    user_file.write_bytes(b"user data")
    plan["destination"] = str(foreign)
    raw = json.dumps(plan).encode()
    plan_path.write_bytes(raw)
    inventory.write_text(
        json.dumps({"plan_sha256": hashlib.sha256(raw).hexdigest()}), "utf-8"
    )
    with pytest.raises(SystemExit):
        copier.main()
    assert list(foreign.iterdir()) == [user_file]
    assert user_file.read_bytes() == b"user data"
