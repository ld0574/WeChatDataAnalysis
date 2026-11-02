import hashlib
import json

import pytest

from wechat_decrypt_tool import course_wiki_runtime as runtime


def test_runtime_includes_schema_validation_and_its_transitive_requirements():
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    distributions = runtime.dependency_closure()
    selected = {
        canonicalize_name(dist.metadata["Name"]): dist
        for dist in distributions.values()
    }
    # Retained limitations lineage is revalidated when a snapshot is read.
    assert "jsonschema" in selected
    for dist in selected.values():
        for text in dist.requires or []:
            requirement = Requirement(text)
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue
            dependency = selected[canonicalize_name(requirement.name)]
            assert requirement.specifier.contains(dependency.version, prereleases=True)


def bundle(tmp_path):
    root = tmp_path / "bundle"
    rows = []
    for name in (
        "python/python.exe",
        "python/Lib/os.py",
        "reader_bootstrap.py",
        "run-wiki.ps1",
        "README.md",
        "site-packages/synthetic/__init__.py",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = b"Synthetic runtime fixture; never executed."
        path.write_bytes(raw)
        rows.append(
            {"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        )
    manifest = {
        "policy": runtime.POLICY,
        "platform": "windows-x64",
        "python_version": "3.11.18",
        "files": rows,
        "dependencies": {"synthetic": "1"},
        "source_deletion_allowed": False,
    }
    (root / runtime.MANIFEST).write_text(json.dumps(manifest), "utf-8")
    return root, manifest


def test_runtime_file_check_does_not_claim_independent_execution(tmp_path):
    root, manifest = bundle(tmp_path)
    report = runtime.verify(root)
    assert report["files"] == len(manifest["files"])
    assert not report["independent_execution_verified"]
    assert not report["gpu_and_rendering_runtime_included"]
    assert not report["source_deletion_allowed"]


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "C:/outside",
        "/outside",
        "python/../outside",
        "python//python.exe",
        "python/python.exe.",
        "python/python.exe ",
        "python\\python.exe",
    ],
)
def test_manifest_paths_cannot_escape_or_alias_windows_files(tmp_path, path):
    with pytest.raises(ValueError, match="invalid_relative_path"):
        runtime.relative_file(tmp_path, path)


def test_manifest_duplicate_case_alias_is_rejected(tmp_path):
    root, manifest = bundle(tmp_path)
    duplicate = dict(manifest["files"][0], path="PYTHON/PYTHON.EXE")
    manifest["files"].append(duplicate)
    (root / runtime.MANIFEST).write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(ValueError, match="duplicate_manifest_path"):
        runtime.verify(root)


def test_missing_interpreter_gate_and_changed_package_are_rejected(tmp_path):
    root, manifest = bundle(tmp_path)
    (root / "site-packages/synthetic/__init__.py").write_bytes(b"Changed package")
    with pytest.raises(ValueError, match="file_changed"):
        runtime.verify(root)
    manifest["files"] = manifest["files"][1:]
    # Remove the corrupted dependency so this isolates the completeness gate.
    manifest["files"] = [
        r for r in manifest["files"] if not r["path"].startswith("site-packages/")
    ]
    (root / runtime.MANIFEST).write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(ValueError, match="required_files_missing"):
        runtime.verify(root)


def test_restore_copies_independent_files_and_preserves_existing_targets(tmp_path):
    root, manifest = bundle(tmp_path)
    destination = tmp_path / "restored"
    report = runtime.restore(root, destination, reserve_gib=0)
    assert report["files"] == len(manifest["files"])
    assert not (root / "python/python.exe").samefile(destination / "python/python.exe")
    (destination / "python/python.exe").write_bytes(b"Independent change")
    assert (root / "python/python.exe").read_bytes() != b"Independent change"
    with pytest.raises(ValueError, match="new_and_separate"):
        runtime.restore(root, destination, reserve_gib=0)


def test_restore_negative_reserve_stops_before_writing(tmp_path):
    root, _ = bundle(tmp_path)
    destination = tmp_path / "restored"
    with pytest.raises(ValueError, match="invalid_reserve"):
        runtime.restore(root, destination, reserve_gib=-1)
    assert not destination.exists()
