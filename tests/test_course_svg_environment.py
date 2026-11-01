import copy
import json

import pytest

from tools import course_svg_environment as env


@pytest.fixture
def source(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    payload = {
        "node_modules/@resvg/resvg-js/index.js": b"synthetic-js",
        "node_modules/@resvg/resvg-js/package.json": b'{"version":"2.6.2"}',
        "node_modules/@resvg/resvg-js/js-binding.js": b"synthetic-binding",
        "node_modules/@resvg/resvg-js-win32-x64-msvc/resvgjs.win32-x64-msvc.node": b"synthetic-native",
        "package-lock.json": b"synthetic-lock",
    }
    for name, raw in payload.items():
        p = runtime / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    for name in env.FONT_NAMES:
        (fonts / name).write_bytes(name.encode())
    node = tmp_path / "node.exe"
    node.write_bytes(b"synthetic-node")
    config = {
        k: "synthetic-" + k
        for k in (
            "engine",
            "node_sha256",
            "native_sha256",
            "renderer_script_sha256",
            "fonts",
        )
    }
    monkeypatch.setattr(
        env, "renderer_configuration", lambda *a: (str(node), None, [], config)
    )

    def synthetic_probe(root):
        value = env.verify(root)
        return {"files_verified": len(value["files"]), "png_sha256": "a" * 64}

    monkeypatch.setattr(env, "probe", synthetic_probe)
    return runtime, fonts, {"renderer": config}


def build(source, tmp_path):
    runtime, fonts, reference = source
    destination = tmp_path / "bundle"
    env.create(runtime, fonts, destination, reference, reserve=0)
    return destination


def test_full_copy_and_independent_restore_keep_every_file(source, tmp_path):
    bundle = build(source, tmp_path)
    value = env.verify(bundle)
    restored = tmp_path / "restored"
    result = env.restore(bundle, restored, reserve=0)
    assert result["files_verified"] == len(value["files"])
    for row in value["files"]:
        assert (bundle / row["path"]).read_bytes() == (
            restored / row["path"]
        ).read_bytes()
    assert (restored / "svg-environment.json").read_bytes() == (
        bundle / "svg-environment.json"
    ).read_bytes()
    assert value["expected_synthetic_png_sha256"] == "a" * 64
    assert value["system_font_loading"] is False


def test_pinned_original_renderer_must_match_before_copy(source, tmp_path):
    runtime, fonts, reference = source
    changed = copy.deepcopy(reference)
    changed["renderer"]["fonts"] = "changed"
    with pytest.raises(ValueError, match="original_renderer_changed"):
        env.create(runtime, fonts, tmp_path / "bundle", changed, reserve=0)
    assert not (tmp_path / "bundle").exists()


def test_modified_native_payload_cannot_verify_or_restore(source, tmp_path):
    bundle = build(source, tmp_path)
    native = (
        bundle
        / "runtime/node_modules/@resvg/resvg-js-win32-x64-msvc/resvgjs.win32-x64-msvc.node"
    )
    native.write_bytes(b"changed")
    with pytest.raises(ValueError, match="payload_changed"):
        env.restore(bundle, tmp_path / "restored", reserve=0)
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("change", ["missing", "alias", "escape"])
def test_incomplete_or_ambiguous_manifest_rejected(source, tmp_path, change):
    bundle = build(source, tmp_path)
    p = bundle / "svg-environment.json"
    value = json.loads(p.read_bytes())
    if change == "missing":
        value["files"] = [r for r in value["files"] if r["path"] != "fonts/msyh.ttc"]
    elif change == "alias":
        row = dict(value["files"][0])
        row["path"] = row["path"].upper()
        value["files"].append(row)
    else:
        value["files"][0]["path"] = "../outside"
    p.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        env.verify(bundle)


def test_existing_directory_and_originals_remain_unchanged(source, tmp_path):
    runtime, fonts, reference = source
    destination = tmp_path / "unrelated"
    destination.mkdir()
    sentinel = destination / "keep"
    sentinel.write_bytes(b"existing")
    before = {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="destination_exists"):
        env.create(runtime, fonts, destination, reference, reserve=0)
    assert sentinel.read_bytes() == b"existing"
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_capacity_failure_prevents_destination_creation(source, tmp_path, monkeypatch):
    def full(*a):
        raise InterruptedError("disk_reserve")

    monkeypatch.setattr(env, "capacity", full)
    runtime, fonts, reference = source
    with pytest.raises(InterruptedError):
        env.create(runtime, fonts, tmp_path / "bundle", reference)
    assert not (tmp_path / "bundle").exists()
