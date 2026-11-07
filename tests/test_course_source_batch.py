import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_source_batch as module


def source(path):
    path.write_bytes(b"Synthetic media bytes; never decoded")
    stat = path.stat()
    return {"path": str(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        "audio_streams": 1, "duration_seconds": 10, "error": ""}


class Store:
    def __init__(self, registered=()):
        self.registered = set(registered)

    @contextmanager
    def connect(self):
        def execute(sql, parameters=()):
            if sql == "SELECT id FROM videos":
                return [(key,) for key in self.registered]
            assert sql == "SELECT 1 FROM videos WHERE id=?"
            return SimpleNamespace(fetchone=lambda: (1,) if parameters[0] in self.registered else None)
        yield SimpleNamespace(execute=execute)


def test_selection_skips_registered_and_existing_outputs_without_touching_them(tmp_path, monkeypatch):
    items = [source(tmp_path / f"{n}.mp4") for n in range(5)]
    registered = module.item_id(items[0])
    folder = tmp_path / "lessons" / module.item_id(items[1])
    folder.mkdir(parents=True)
    monkeypatch.setattr(module, "ReadOnlyArchive", lambda root: Store([registered]))
    result = module.select_unattempted(tmp_path, {"videos": items}, 3)
    assert [row["video"] for row in result] == [module.item_id(item) for item in items[2:]]
    assert folder.is_dir() and all(item["bytes"] == module.Path(item["path"]).stat().st_size for item in items)


def test_selection_refuses_changed_source_and_unbounded_batch(tmp_path, monkeypatch):
    item = source(tmp_path / "new.mp4")
    monkeypatch.setattr(module, "ReadOnlyArchive", lambda root: Store())
    with pytest.raises(ValueError, match="finite_one_to_three"):
        module.select_unattempted(tmp_path, {"videos": [item]}, 0)
    module.Path(item["path"]).write_bytes(b"Changed original")
    with pytest.raises(ValueError, match="frozen_new_source_stat"):
        module.select_unattempted(tmp_path, {"videos": [item]}, 1)


def test_verified_audio_only_folder_is_reused_and_manifest_change_is_rejected(tmp_path, monkeypatch):
    item = source(tmp_path / "audio-source.mp4")
    key = module.item_id(item)
    folder = tmp_path / "lessons" / key
    folder.mkdir(parents=True)
    audio = folder / "audio.m4a"
    audio.write_bytes(b"Synthetic verified archived audio")
    value = {"verified": True, "video": key, "source": item["path"], "source_size": item["bytes"],
        "source_mtime_ns": item["mtime_ns"], "file": "audio.m4a", "file_bytes": audio.stat().st_size,
        "file_digest": module.whole.sha(audio.read_bytes())}
    module.atomic_json(folder / "audio.json", value)
    originals = {p: p.read_bytes() for p in folder.iterdir()}
    monkeypatch.setattr(module, "ReadOnlyArchive", lambda root: Store())
    rows = module.select_unattempted(tmp_path, {"videos": [item]}, 1)
    assert rows[0]["video"] == key and rows[0]["retained_audio"]["file_digest"] == value["file_digest"]
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    value["verified"] = False
    module.atomic_json(folder / "audio.json", value)
    with pytest.raises(ValueError, match="unchanged_verified_audio_input"):
        module.retained_inputs(tmp_path, key, item)


def execution(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    folder = root / "wiki/source-batches" / ("a" * 32)
    folder.mkdir(parents=True)
    items = [source(tmp_path / f"source-{n}.mp4") for n in range(2)]
    plan = {"predecessor_guard": {"pid": 41, "created": 1.5},
        "predecessor_worker": {"pid": 42, "created": 1.6}, "predecessor_video": "b" * 24,
        "sources": [{"video": module.item_id(item), "item": item} for item in items],
        "knowledge_model": "qwen3.5:9b", "asr_model": str(tmp_path / "model")}
    module.atomic_json(root / "gpu-thermal-guard.json", {**plan["predecessor_guard"], "state": "worker_exited", "returncode": 0})
    module.atomic_json(root / "job-progress.json", {"pid": 42, "stage": "batch_finished", "processed": 1, "failures": 0})
    monkeypatch.setattr(module, "load_plan", lambda *args: (folder, plan))
    monkeypatch.setattr(module, "checkpoint", lambda root: None)
    monkeypatch.setattr(module, "identity_live", lambda row: False)
    monkeypatch.setattr(module, "ReadOnlyArchive", lambda root: Store())
    monkeypatch.setattr(module.whole, "backup", lambda *args: None)
    monkeypatch.setattr(module, "validate_lesson", lambda *args: {"structured_export_checked": True})
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: dummy_client())
    calls, published = [], set()

    def guarded(root, command, **kwargs):
        calls.append((command, kwargs))
        item = next(item for item in items if item["path"] == command[command.index("--video") + 1])
        key = module.item_id(item)
        target = root / "lessons" / key
        target.mkdir(parents=True)
        for name in ("evidence.json", "graph.json"):
            (target / name).write_text("{}", "utf-8")
        module.atomic_json(root / "gpu-thermal-guard.json", {"pid": 50, "created": 2.5, "state": "worker_exited", "returncode": 0})
        module.atomic_json(root / "job-progress.json", {"pid": 51, "stage": "batch_finished", "processed": 1, "failures": 0})
        published.add(key)

    monkeypatch.setattr(module, "guarded", guarded)
    monkeypatch.setattr(module.publication, "publish_once", lambda *args: {"state": "new_complete_source_notes_published_and_http_checked"})
    monkeypatch.setattr(module.publication, "Library", lambda root: SimpleNamespace(catalog=lambda: {
        "lessons": [{"id": key, "state": "draft_available"} for key in published]}))
    return root, folder, plan, calls


@contextmanager
def dummy_client():
    yield object()


def test_successful_finite_batch_keeps_gpu_contract_and_checks_each_source(tmp_path, monkeypatch):
    root, folder, plan, calls = execution(tmp_path, monkeypatch)
    module.run(root, folder.name, "http://127.0.0.1:3003")
    result = json.loads((folder / "progress.json").read_bytes())
    assert result["state"] == "finite_source_batch_finished"
    assert result["completed_sources"] == [row["video"] for row in plan["sources"]]
    assert len(calls) == 2
    for command, limits in calls:
        assert command[command.index("--asr-batch-size") + 1] == "4"
        assert command[command.index("--limit") + 1] == "1"
        assert limits["guard_name"] == "gpu-thermal-guard"
        assert limits["maximum_temperature"] == 70 and limits["maximum_power"] == 200
        assert "resume" not in limits
    with pytest.raises(ValueError, match="one_launch_no_automatic_retry"):
        module.run(root, folder.name, "http://127.0.0.1:3003")
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["guard", "source", "pause", "attempted"])
def test_negative_or_changed_state_stops_before_later_sources(tmp_path, monkeypatch, failure):
    root, folder, plan, calls = execution(tmp_path, monkeypatch)
    if failure == "guard":
        module.atomic_json(root / "gpu-thermal-guard.json", {**plan["predecessor_guard"], "state": "worker_exited", "returncode": 1})
    elif failure == "source":
        module.Path(plan["sources"][0]["item"]["path"]).write_bytes(b"Changed source")
    elif failure == "attempted":
        monkeypatch.setattr(module, "ReadOnlyArchive", lambda root: Store([plan["sources"][0]["video"]]))
    else:
        def pause(root):
            raise InterruptedError("requires_source_batch_supervisor_stop_preserved")
        monkeypatch.setattr(module, "checkpoint", pause)
    with pytest.raises((ValueError, InterruptedError)):
        module.run(root, folder.name, "http://127.0.0.1:3003")
    assert not calls
    assert len(list((folder / "failures").glob("*.json"))) == 1
    assert not json.loads((folder / "progress.json").read_bytes())["automatic_retry_allowed"]


def test_thermal_pause_stops_after_current_source_without_next_launch(tmp_path, monkeypatch):
    root, folder, _, calls = execution(tmp_path, monkeypatch)

    def paused_guard(root, command, **kwargs):
        calls.append(command)
        module.atomic_json(root / "gpu-thermal-guard.json", {"state": "paused", "reason": "temperature_limit"})

    monkeypatch.setattr(module, "guarded", paused_guard)
    with pytest.raises(ValueError, match="current_source_successful_terminal"):
        module.run(root, folder.name, "http://127.0.0.1:3003")
    assert len(calls) == 1


def test_wait_observes_exact_process_identity_without_launching_gpu(tmp_path, monkeypatch):
    root, folder, _, calls = execution(tmp_path, monkeypatch)
    observed = []
    monkeypatch.setattr(module, "identity_live", lambda row: observed.append(copy.deepcopy(row)) or True)

    def stop_wait(seconds):
        assert seconds == 15
        raise InterruptedError("requires_source_batch_supervisor_stop_preserved")

    with pytest.raises(InterruptedError):
        module.run(root, folder.name, "http://127.0.0.1:3003", sleep=stop_wait)
    assert observed[0] == {"pid": 41, "created": 1.5} and not calls
