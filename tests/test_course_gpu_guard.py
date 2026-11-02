import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_gpu_guard as guard


@pytest.fixture
def root(tmp_path):
    (tmp_path / "knowledge.sqlite3").touch()
    (tmp_path / "pause.flag").write_text("user_requested_pause")
    (tmp_path / "supervisor-stop.flag").write_text("keep_other_workers_stopped")
    return tmp_path


def reading(temperature=52, cap=120):
    return {"temperature_c": temperature, "power_w": 80, "power_limit_w": cap}


@pytest.mark.parametrize(
    "hot,cap,reason",
    [
        (70, 120, "temperature"),
        (52, 280, "power_cap"),
    ],
)
def test_preflight_does_not_resume_unsafe_gpu(root, hot, cap, reason):
    with pytest.raises(ValueError, match=reason):
        guard.run(root, [], resume=True, sampler=lambda: reading(hot, cap))
    assert (root / "pause.flag").exists()


@pytest.mark.parametrize("failure", ["hot", "cap", "sensor", "user"])
def test_stops_owned_worker_without_automatic_restart(root, failure):
    calls, samples = [], 0
    worker = SimpleNamespace(pid=123, poll=lambda: None)

    def sampler():
        nonlocal samples
        samples += 1
        if samples == 1:
            return reading()
        if failure == "sensor":
            raise OSError("synthetic sensor failure")
        if failure == "user":
            (root / "pause.flag").write_text("user_pause")
        return reading(71 if failure == "hot" else 52, 280 if failure == "cap" else 120)

    def stop(process, folder):
        calls.append(process.pid)
        (folder / "pause.flag").write_text("paused")

    guard.run(
        root,
        [],
        resume=True,
        sampler=sampler,
        launch=lambda *a, **k: worker,
        stopper=stop,
        sleep=lambda _: None,
    )
    value = json.loads((root / "gpu-thermal-guard.json").read_bytes())
    assert value["state"] == "paused" and calls == [123]
    assert not value["automatic_restart_allowed"]
    assert (root / "pause.flag").exists()
    assert (root / "supervisor-stop.flag").read_text() == "keep_other_workers_stopped"
    assert len(list(root.glob("pause-before-resume-*.flag"))) == 1


def test_sensor_unavailable_never_starts_or_clears_pause(root):
    def unavailable():
        raise OSError("synthetic failure")

    with pytest.raises(OSError):
        guard.run(root, [], resume=True, sampler=unavailable)
    assert (root / "pause.flag").read_text() == "user_requested_pause"


def test_resume_requires_explicit_request(root):
    with pytest.raises(ValueError, match="resume_required"):
        guard.run(root, [], sampler=reading)
    assert (root / "pause.flag").exists()


def test_failed_worker_launch_restores_pause(root):
    def fail(*args, **kwargs):
        raise OSError("synthetic launch failure")

    with pytest.raises(OSError):
        guard.run(root, [], resume=True, sampler=reading, launch=fail)
    assert (root / "pause.flag").read_text() == "gpu_guard_worker_launch_failed"


def test_worker_completion_does_not_restart(root):
    worker = SimpleNamespace(pid=123, poll=lambda: 0, returncode=0)
    guard.run(root, [], resume=True, sampler=reading, launch=lambda *a, **k: worker)
    value = json.loads((root / "gpu-thermal-guard.json").read_bytes())
    assert value["state"] == "worker_exited" and value["returncode"] == 0
    assert (root / "supervisor-stop.flag").exists()


def test_higher_user_requested_cap_keeps_temperature_ceiling(root):
    worker = SimpleNamespace(pid=123, poll=lambda: 0, returncode=0)
    guard.run(
        root,
        [],
        resume=True,
        maximum_power=200,
        sampler=lambda: reading(cap=200),
        launch=lambda *a, **k: worker,
    )
    value = json.loads((root / "gpu-thermal-guard.json").read_bytes())
    assert value["maximum_power_limit_w"] == 200
    assert value["maximum_temperature_c"] == 70


def test_timeout_stops_only_owned_process(root, monkeypatch):
    owned = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        monkeypatch.setattr(guard.subprocess, "run", lambda *a, **k: None)

        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("synthetic worker", 15)

        with monkeypatch.context() as patch:
            patch.setattr(owned, "wait", timeout)
            guard.stop_worker(owned, root)
        assert owned.wait(timeout=5) is not None
        assert unrelated.poll() is None
        assert (root / "pause.flag").exists()
    finally:
        for process in (owned, unrelated):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize("first_stop_fails", [False, True])
def test_pause_unloads_both_course_models_even_if_first_stop_fails(
    root, monkeypatch, first_stop_fails
):
    stopped = []

    def stop(command, **kwargs):
        stopped.append(command)
        if first_stop_fails and len(stopped) == 1:
            raise subprocess.TimeoutExpired(command, 15)

    def absent(pid):
        raise guard.psutil.NoSuchProcess(pid)

    monkeypatch.setattr(guard.subprocess, "run", stop)
    monkeypatch.setattr(guard.psutil, "Process", absent)
    worker = SimpleNamespace(pid=123, wait=lambda **kwargs: 0)
    guard.stop_worker(worker, root)
    assert stopped == [
        ["ollama", "stop", "qwen3.5:9b"],
        ["ollama", "stop", "course-defiant:q4"],
    ]
    assert (root / "pause.flag").exists()
    assert (root / "supervisor-stop.flag").exists()


def test_revision_monitor_has_independent_lock_and_status(root):
    worker = SimpleNamespace(pid=123, poll=lambda: 0, returncode=0)

    def launch_main(*args, **kwargs):
        # The main monitor still holds its lock when the revision monitor starts.
        guard.run(
            root,
            [],
            sampler=reading,
            launch=lambda *a, **k: worker,
            guard_name="wiki-revision-thermal-guard",
        )
        return worker

    guard.run(root, [], resume=True, sampler=reading, launch=launch_main)
    assert (root / "gpu-thermal-guard.json").exists()
    assert (root / "wiki-revision-thermal-guard.json").exists()
    assert (root / "thermal-wiki-revision.stdout.log").exists()


def test_monitor_role_cannot_escape_archive(root):
    with pytest.raises(ValueError, match="unknown_role"):
        guard.run(root, [], guard_name="../outside")
    assert (root / "pause.flag").exists()


def test_extraction_verifier_monitor_can_coexist_without_overwriting_other_roles(root):
    worker = SimpleNamespace(pid=123, poll=lambda: 0, returncode=0)

    def extraction(*args, **kwargs):
        guard.run(root, [], sampler=reading, launch=lambda *a, **k: worker,
                  guard_name="extraction-thermal-guard")
        return worker

    def revision(*args, **kwargs):
        guard.run(root, [], sampler=reading, launch=extraction,
                  guard_name="wiki-revision-thermal-guard")
        return worker

    guard.run(root, [], resume=True, sampler=reading, launch=revision)
    for role in ("gpu-thermal-guard", "wiki-revision-thermal-guard", "extraction-thermal-guard"):
        value = json.loads((root / (role + ".json")).read_bytes())
        assert value["maximum_temperature_c"] == 70
        assert not value["automatic_restart_allowed"]
    assert (root / "thermal-extraction.stdout.log").exists()


def test_revision_cli_preserves_explicit_review_history(root, monkeypatch):
    calls = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "course_gpu_guard",
            "--root",
            str(root),
            "--revision-article",
            "article-id",
            "--review-job",
            "first-report",
            "--review-job",
            "second-report",
            "--maximum-power",
            "200",
        ],
    )
    monkeypatch.setattr(guard, "run", lambda *a, **k: calls.append((a, k)))
    guard.main()
    args, kwargs = calls[0]
    command = args[1]
    assert command.count("--review-job") == 2
    assert "first-report" in command and "second-report" in command
    assert kwargs["guard_name"] == "wiki-revision-thermal-guard"
    assert kwargs["maximum_power"] == 200
