import hashlib
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_model_bundle as module


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(tmp_path, monkeypatch):
    asr, ollama = tmp_path / "installed-asr", tmp_path / "installed-ollama"
    asr.mkdir()
    files = {
        "config.json": b"synthetic configuration",
        "model.safetensors": b"synthetic weights",
        "tokenizer.json": b"synthetic tokenizer",
    }
    spec = {}
    for name, raw in files.items():
        (asr / name).write_bytes(raw)
        spec[name] = {"size": len(raw), "sha256": sha(raw)}
    monkeypatch.setitem(module.SPECS, module.ASR_MODEL, {"files": spec})
    (asr / ".cache").mkdir()
    (asr / ".cache" / "download-token").write_text("fixture secret")
    (ollama / "blobs").mkdir(parents=True)
    blobs = [b"synthetic ollama configuration", b"synthetic ollama tensor weights"]
    entries = []
    for raw in blobs:
        (ollama / "blobs" / ("sha256-" + sha(raw))).write_bytes(raw)
        entries.append({"digest": "sha256:" + sha(raw), "size": len(raw)})
    native = ollama / "manifests/registry.ollama.ai/library/fixture/9b"
    native.parent.mkdir(parents=True)
    native.write_text(
        json.dumps({"config": entries[0], "layers": [entries[1], entries[1]]}), "utf-8"
    )
    (ollama / "id_ed25519").write_text("fixture private account key")
    monkeypatch.setattr(
        module,
        "python_environment",
        lambda: {"executables_drivers_packages_and_renderers_included": False},
    )
    return asr, ollama, "fixture:9b", sha(native.read_bytes())


def build(tmp_path, monkeypatch, **kwargs):
    args = fixture(tmp_path, monkeypatch)
    bundle = tmp_path / "bundle"
    result = module.create_bundle(bundle, *args, reserve_gib=0, **kwargs)
    return args, bundle, result


def test_complete_selected_models_restore_without_originals_or_account_files(
    tmp_path, monkeypatch
):
    args, bundle, result = build(tmp_path, monkeypatch)
    assert result["verified_assets"] == 6
    assert result["model_weights_only"] and not result["source_deletion_allowed"]
    original = {
        x["target"]: module.inside(bundle, x["object"]).read_bytes()
        for x in json.loads((bundle / "manifest.json").read_text("utf-8"))["assets"]
    }
    args[0].rename(tmp_path / "asr-unavailable")
    args[1].rename(tmp_path / "ollama-unavailable")
    restored = tmp_path / "independent"
    receipt = module.restore_bundle(bundle, restored, reserve_gib=0)
    assert receipt["restored_files"] == 6
    assert {
        p.relative_to(restored).as_posix(): p.read_bytes()
        for p in restored.rglob("*")
        if p.is_file()
    } == original
    assert all(".cache" not in name and "id_ed25519" not in name for name in original)
    assert module.verify_bundle(bundle)["manifest_sha256"] == result["manifest_sha256"]


def test_interruption_reuses_verified_objects_and_preserves_identity(
    tmp_path, monkeypatch
):
    args = fixture(tmp_path, monkeypatch)
    bundle = tmp_path / "bundle"

    def interrupt(**fields):
        raise InterruptedError("fixture stop after an immutable object")

    with pytest.raises(InterruptedError):
        module.create_bundle(bundle, *args, reserve_gib=0, progress=interrupt)
    objects = [p for p in (bundle / "objects").rglob("*") if p.is_file()]
    assert len(objects) == 1 and not (bundle / "manifest.json").exists()
    mtime = objects[0].stat().st_mtime_ns
    result = module.create_bundle(bundle, *args, reserve_gib=0)
    assert result["verified_assets"] == 6
    assert objects[0].stat().st_mtime_ns == mtime
    assert module.create_bundle(bundle, *args, reserve_gib=0)["cached"]


def test_missing_capacity_does_not_create_bundle_or_restore(tmp_path, monkeypatch):
    args, bundle, _ = build(tmp_path, monkeypatch)
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    missing = tmp_path / "missing" / "bundle"
    with pytest.raises(InterruptedError, match="capacity"):
        module.create_bundle(missing, *args, reserve_gib=0)
    assert not missing.parent.exists()
    restored = tmp_path / "missing-restore"
    with pytest.raises(InterruptedError, match="capacity"):
        module.restore_bundle(bundle, restored, reserve_gib=0)
    assert not restored.exists()
    assert module.create_bundle(bundle, *args, reserve_gib=0)["cached"]


def test_space_loss_during_a_file_copy_cleans_only_its_unaccepted_part(
    tmp_path, monkeypatch
):
    args = fixture(tmp_path, monkeypatch)
    bundle = tmp_path / "bundle"
    calls = 0

    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 2:
            monkeypatch.setattr(
                module.shutil, "disk_usage", lambda _: SimpleNamespace(free=0)
            )

    with pytest.raises(InterruptedError, match="reserved disk"):
        module.create_bundle(bundle, *args, reserve_gib=0, checkpoint=checkpoint)
    assert not (bundle / "manifest.json").exists()
    assert not list(bundle.rglob("*.part"))
    assert (args[0] / "config.json").read_bytes() == b"synthetic configuration"


def test_tampered_installed_or_bundled_weights_are_not_accepted(tmp_path, monkeypatch):
    args, bundle, _ = build(tmp_path, monkeypatch)
    target = args[0] / "model.safetensors"
    target.write_bytes(b"X" * target.stat().st_size)
    with pytest.raises(RuntimeError, match="frozen digest"):
        module.create_bundle(tmp_path / "bad-source", *args, reserve_gib=0)
    assert not (tmp_path / "bad-source/manifest.json").exists()
    manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
    target = module.inside(bundle, manifest["assets"][0]["object"])
    target.write_bytes(b"X" * target.stat().st_size)
    with pytest.raises(RuntimeError, match="hash or size"):
        module.restore_bundle(bundle, tmp_path / "bad-restore", reserve_gib=0)
    assert not (tmp_path / "bad-restore").exists()


@pytest.mark.parametrize(
    "kind", ["omit", "redirect", "duplicate", "wrong_native_revision"]
)
def test_bundle_scope_and_paths_must_match_all_required_models(
    tmp_path, monkeypatch, kind
):
    _, bundle, _ = build(tmp_path, monkeypatch)
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_text("utf-8"))
    if kind == "omit":
        manifest["assets"].pop()
    elif kind == "redirect":
        manifest["assets"][0]["target"] = "asr/../../outside"
    elif kind == "duplicate":
        manifest["assets"].append(manifest["assets"][0])
    else:
        manifest["ollama_manifest_sha256"] = "a" * 64
    path.write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(ValueError):
        module.restore_bundle(bundle, tmp_path / "invalid-restore", reserve_gib=0)
    assert not (tmp_path / "invalid-restore").exists()


def test_source_model_revision_and_nonempty_restore_are_guarded(tmp_path, monkeypatch):
    args, bundle, _ = build(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="selected model"):
        module.model_plan(*args[:3], "a" * 64)
    with pytest.raises(ValueError, match="explicit local"):
        module.model_plan(*args[:2], "../remote:tag", args[3])
    with pytest.raises(ValueError, match="separate"):
        module.create_bundle(args[0] / "backup", *args, reserve_gib=0)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep").write_text("keep")
    with pytest.raises(ValueError, match="empty"):
        module.restore_bundle(bundle, existing, reserve_gib=0)
    assert (existing / "keep").read_text() == "keep"


def test_wrapped_platform_manifest_exports_exact_selected_revision(
    tmp_path, monkeypatch
):
    args = fixture(tmp_path, monkeypatch)
    asr, ollama, model, selected_sha = args
    native_tag = ollama / "manifests/registry.ollama.ai/library/fixture/9b"
    native_bytes = native_tag.read_bytes()
    (ollama / "blobs" / ("sha256-" + selected_sha)).write_bytes(native_bytes)
    native_tag.write_text(
        json.dumps(
            {
                "layers": [
                    {
                        "digest": "sha256:" + selected_sha,
                        "size": len(native_bytes),
                        "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                    }
                ]
            }
        ),
        "utf-8",
    )
    bundle = tmp_path / "bundle"
    result = module.create_bundle(
        bundle, asr, ollama, model, selected_sha, reserve_gib=0
    )
    restored = tmp_path / "restored"
    module.restore_bundle(bundle, restored, reserve_gib=0)
    assert (
        restored / "ollama/manifests/registry.ollama.ai/library/fixture/9b"
    ).read_bytes() == native_bytes
    assert result["verified_assets"] == 6


def test_symlinked_ollama_blob_cannot_escape_source_root(tmp_path, monkeypatch):
    args = fixture(tmp_path, monkeypatch)
    target = next((args[1] / "blobs").iterdir())
    outside = tmp_path / "outside-weights"
    target.rename(outside)
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks unavailable on this Windows host")
    with pytest.raises(ValueError, match="declared root"):
        module.model_plan(*args)


def test_environment_records_real_package_metadata_without_loading_gpu_models():
    result = module.python_environment()
    assert result["base_packages"]
    assert any(name.lower() == "pytest" for name, _ in result["base_packages"])
    assert isinstance(result["gpu_overlay_packages"], list)
    assert not result["executables_drivers_packages_and_renderers_included"]
    assert result["asr_device"] == "cuda" and result["asr_precision"] == "float32"


def test_course_snapshot_keeps_companion_bundle_reference_without_recopied_weights(
    tmp_path, monkeypatch
):
    from wechat_decrypt_tool.course_backup import (
        create_snapshot,
        restore_snapshot,
        verify_snapshot,
    )
    from wechat_decrypt_tool.course_knowledge import KnowledgeStore

    args = fixture(tmp_path, monkeypatch)
    backup = tmp_path / "portable-backup"
    bundle = backup / "model-bundles/fixture"
    models = module.create_bundle(bundle, *args, reserve_gib=0)
    store = KnowledgeStore(tmp_path / "course")
    reference = {
        "backup_root_relative_bundle": "model-bundles/fixture",
        "model_manifest_sha256": models["manifest_sha256"],
        "model_weights_are_separate_from_course_snapshot_objects": True,
    }
    (store.root / "model-bundle-reference.json").write_text(
        json.dumps(reference), "utf-8"
    )
    snapshot = create_snapshot(store, backup, reserve_gib=0)
    assert verify_snapshot(snapshot["snapshot"])["asset_paths"] == 1
    restored = restore_snapshot(
        snapshot["snapshot"], tmp_path / "restored-course", reserve_gib=0
    )
    actual = json.loads((restored / "model-bundle-reference.json").read_text("utf-8"))
    assert actual == reference
    assert (
        module.verify_bundle(backup / actual["backup_root_relative_bundle"])[
            "manifest_sha256"
        ]
        == actual["model_manifest_sha256"]
    )
