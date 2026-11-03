import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_archive_gpu as archive


def test_both_disk_reserves_are_checked_and_exact_threshold_is_allowed(tmp_path):
    sampled = []
    reading = {"temperature_c": 55, "power_w": 190, "power_limit_w": 200}

    def sample():
        sampled.append(True)
        return reading

    def usage(volume):
        return SimpleNamespace(free={"system": 32, "archive": 45}[volume] * 1024**3)

    value = archive.checked_sample(
        tmp_path, sampler=sample, usage=usage, volumes=["system", "archive"]
    )
    assert value["free_gib"] == {"system": 32, "archive": 45}
    assert sampled == [True]
    with pytest.raises(RuntimeError, match="32gib_reserve_reached"):
        archive.checked_sample(
            tmp_path,
            sampler=sample,
            usage=lambda volume: SimpleNamespace(
                free=(31 if volume == "system" else 45) * 1024**3
            ),
            volumes=["system", "archive"],
        )
    assert sampled == [True]
    record = json.loads(
        (tmp_path / "archive-disk-reserve-last-check.json").read_bytes()
    )
    assert record["below_reserve"] == ["system"]
    assert not record["automatic_resume_allowed"]


def test_runtime_disk_limit_stops_worker_without_automatic_restart(
    tmp_path, monkeypatch
):
    (tmp_path / "knowledge.sqlite3").touch()
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    (model / "model.safetensors").write_bytes(b"fixture-weights")
    samples, launches, stopped = [], [], []

    def sample(root):
        samples.append(True)
        if len(samples) > 1:
            raise RuntimeError("course_archive_32gib_reserve_reached")
        return {"temperature_c": 55, "power_w": 190, "power_limit_w": 200}

    class Worker:
        pid = 123456

        def poll(self):
            return None

    def launch(*args, **kwargs):
        launches.append(args[0])
        return Worker()

    def stop(worker, root):
        stopped.append(worker.pid)
        (root / "pause.flag").write_text("fixture-disk-limit")

    actual = archive.course_gpu_guard.run
    monkeypatch.setattr(archive, "checked_sample", sample)
    monkeypatch.setattr(
        archive.course_gpu_guard,
        "run",
        lambda *args, **kwargs: actual(*args, **kwargs, launch=launch, stopper=stop),
    )
    archive.run(tmp_path, model)
    assert len(launches) == 1 and stopped == [123456]
    assert "--asr-batch-size" in launches[0] and launches[0][-1] == "4"
    record = json.loads((tmp_path / "gpu-thermal-guard.json").read_bytes())
    assert record["state"] == "paused" and not record["automatic_restart_allowed"]
    assert (
        record["maximum_power_limit_w"] == 200 and record["maximum_temperature_c"] == 70
    )


def test_pause_marker_is_preserved_and_does_not_launch_a_worker(tmp_path, monkeypatch):
    (tmp_path / "knowledge.sqlite3").touch()
    (tmp_path / "pause.flag").write_text("original-user-pause")
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    (model / "model.safetensors").write_bytes(b"fixture-weights")
    monkeypatch.setattr(
        archive,
        "checked_sample",
        lambda root: {"temperature_c": 55, "power_w": 100, "power_limit_w": 200},
    )
    with pytest.raises(ValueError, match="resume_required"):
        archive.run(tmp_path, model)
    assert (tmp_path / "pause.flag").read_text() == "original-user-pause"
