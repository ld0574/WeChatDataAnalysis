import sys
import types
from pathlib import Path

import pytest

from tools import course_extraction_environment_bootstrap as bootstrap
from tools.course_extraction_environment_gpu_probe import isolated_runtime
from tools.course_extraction_environment_reprobe import validate_probe


@pytest.mark.parametrize("attribute,defining_name,class_name,label", [
    ("ops", "torch._ops", "_Ops", "_ops.py"),
    ("classes", "torch._classes", "_Classes", "_classes.py"),
])
def test_dynamic_namespace_requires_registered_object_class_and_bundled_definition(
    tmp_path, monkeypatch, attribute, defining_name, class_name, label
):
    defining = types.ModuleType(defining_name)
    defining.__file__ = str(tmp_path / "torch" / label)
    cls = type(class_name, (types.ModuleType,), {"__file__": label})
    setattr(defining, class_name, cls)
    namespace = cls("torch." + attribute)
    torch = types.ModuleType("torch")
    setattr(torch, attribute, namespace)
    modules = {"torch": torch, defining_name: defining, "torch." + attribute: namespace}
    monkeypatch.setattr(bootstrap, "sys", types.SimpleNamespace(modules=modules))
    assert bootstrap.module_origin_path("torch." + attribute, namespace) == Path(defining.__file__)
    assert bootstrap.outside_module_names(tmp_path) == []

    # A genuine namespace whose defining source is outside is still rejected.
    defining.__file__ = str(tmp_path.parent / label)
    assert set(bootstrap.outside_module_names(tmp_path)) == {defining_name, "torch." + attribute}
    defining.__file__ = str(tmp_path / label)

    # An arbitrary module with the same synthetic file label cannot be exempted.
    impostor = types.ModuleType(namespace.__name__)
    impostor.__file__ = label
    modules[namespace.__name__] = impostor
    assert bootstrap.outside_module_names(tmp_path) == [namespace.__name__]
    # Nor can an unregistered instance of the right class.
    modules[namespace.__name__] = cls(namespace.__name__)
    assert bootstrap.outside_module_names(tmp_path) == [namespace.__name__]


def test_only_identity_bound_entrypoint_aliases_can_be_trusted(tmp_path, monkeypatch):
    script = tmp_path.parent / "companion.py"
    main = types.ModuleType("__main__")
    main.__file__ = str(script)
    extra = types.ModuleType("external")
    extra.__file__ = str(script)
    modules = {"__main__": main, "__mp_main__": main, "external": extra}
    monkeypatch.setattr(bootstrap, "sys", types.SimpleNamespace(modules=modules))
    assert bootstrap.outside_module_names(tmp_path, script) == ["external"]
    assert set(bootstrap.outside_module_names(tmp_path)) == set(modules)
    modules["__mp_main__"] = extra
    assert set(bootstrap.outside_module_names(tmp_path, script)) == {"external", "__mp_main__"}


def test_bootstrap_companion_must_match_its_own_anchor(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bootstrap", "--bundle", str(tmp_path),
                                     "--bootstrap-sha256", "0" * 64, "probe"])
    monkeypatch.setattr(bootstrap, "verify", lambda: pytest.fail("must not verify or activate"))
    with pytest.raises(ValueError, match="companion_bootstrap_anchor_changed"):
        bootstrap.main()


def test_gpu_rejects_unanchored_bootstrap_before_import(tmp_path):
    import hashlib

    manifest = tmp_path / "extraction-environment.json"
    manifest.write_bytes(b"{}")
    companion = tmp_path / "companion.py"
    companion.write_text("raise AssertionError('must not execute')")
    with pytest.raises(ValueError, match="bootstrap_anchor_changed"):
        isolated_runtime(tmp_path, hashlib.sha256(manifest.read_bytes()).hexdigest(),
                         bootstrap_path=companion, bootstrap_sha256="0" * 64)


@pytest.mark.parametrize("key,value", [
    ("files_verified", 0), ("probe_bootstrap_sha256", "different"),
    ("companion_bootstrap", False), ("gpu_context_initialized", True),
    ("original_installation_access_attempts", 1), ("network_connection_attempts", 1),
    ("outside_module_count", 1), ("original_installation_native_mapping_count", 1),
])
def test_companion_cannot_accept_missing_files_changed_code_or_any_isolation_failure(key, value):
    result = {"state": "isolated_cpu_and_native_synthetic_probe_verified", "files_verified": 12,
              "probe_bootstrap_sha256": "anchored", "companion_bootstrap": True,
              "gpu_context_initialized": False, "gpu_inference_verified": False,
              "original_installation_access_attempts": 0, "network_connection_attempts": 0,
              "outside_module_count": 0, "original_installation_native_mapping_count": 0}
    validate_probe(result, 12, "anchored")
    result[key] = value
    with pytest.raises(ValueError, match="incomplete_or_not_isolated"):
        validate_probe(result, 12, "anchored")
