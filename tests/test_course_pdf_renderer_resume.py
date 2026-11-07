import hashlib
import json

import pytest

from wechat_decrypt_tool import course_pdf_renderer_resume as renderer


def fixture(tmp_path):
    root, environment = tmp_path / "archive", tmp_path / "retained"
    root.mkdir()
    binary = environment / "tools/poppler/bin/pdftoppm.exe"
    library = binary.with_name("synthetic.dll")
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"original-renderer")
    library.write_bytes(b"original-library")
    def digest(raw):
        return hashlib.sha256(raw).hexdigest()
    manifest = {"files": [{"path": p.relative_to(environment).as_posix(), "bytes": p.stat().st_size,
        "sha256": digest(p.read_bytes())} for p in [binary, library]]}
    path = environment / "extraction-environment.json"
    path.write_text(json.dumps(manifest), "utf-8")
    receipt = tmp_path / "environment-receipt.json"
    receipt.write_text(json.dumps({"destination": str(environment), "manifest_sha256": digest(path.read_bytes())}), "utf-8")
    prefix = "pdf-queue-20261103-0020"
    binding = root / (prefix + "-binding.json")
    binding.write_text(json.dumps({"renderer": "a-replaced-system-tool.exe", "renderer_sha256": digest(binary.read_bytes()),
        "other_original_contract": "retained"}), "utf-8")
    return root, environment, receipt, prefix, binding, library


def test_only_renderer_location_is_resolved_and_original_checks_are_delegated(tmp_path, monkeypatch):
    root, env, receipt, prefix, binding, _ = fixture(tmp_path)
    original = binding.read_bytes()
    renderer.prepare(root, prefix, env, receipt)
    called = []

    def validate(request_root, request_prefix):
        called.append((request_root, request_prefix))
        value = renderer.queue.read(binding)
        assert value["other_original_contract"] == "retained"
        assert renderer.queue.sha(renderer.Path(value["renderer"]).read_bytes()) == value["renderer_sha256"]
        return value

    monkeypatch.setattr(renderer.queue, "validate_inputs", validate)
    before_read = renderer.queue.read
    with renderer.resolve_renderer(root, prefix):
        value = renderer.queue.validate_inputs(root, prefix)
        assert value["renderer"] == str(env / "tools/poppler/bin/pdftoppm.exe")
        assert binding.read_bytes() == original
    assert renderer.queue.validate_inputs is validate and renderer.queue.read is before_read
    assert called == [(root, prefix)] and binding.read_bytes() == original


@pytest.mark.parametrize("changed", ["library", "binary", "manifest", "binding", "receipt"])
def test_changed_dependency_or_proof_blocks_resumption(tmp_path, changed):
    root, env, receipt, prefix, binding, library = fixture(tmp_path)
    renderer.prepare(root, prefix, env, receipt)
    target = {"library": library, "binary": library.with_name("pdftoppm.exe"),
        "manifest": env / "extraction-environment.json", "binding": binding, "receipt": receipt}[changed]
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ValueError):
        renderer.verified_binding(root, prefix)


def test_another_batch_cannot_inherit_a_relocation_receipt(tmp_path):
    root, env, receipt, prefix, _, _ = fixture(tmp_path)
    renderer.prepare(root, prefix, env, receipt)
    with renderer.resolve_renderer(root, prefix), pytest.raises(ValueError, match="single_bound_batch"):
        renderer.queue.validate_inputs(root, "pdf-queue-20261103-0021")
