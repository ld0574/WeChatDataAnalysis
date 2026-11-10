import json
import os
import subprocess
import sys
from types import SimpleNamespace

import psutil
import pytest

from wechat_decrypt_tool import course_private_guard_identity as m


def read(path):
    return json.loads(path.read_bytes())


def test_actual_windows_virtualenv_process_identity_is_observed_and_joined(tmp_path):
    status = tmp_path / "status.json"
    script = (
        "import json,os,psutil,sys,time; from pathlib import Path; "
        "p=Path(sys.argv[1]); q=p.with_suffix('.tmp'); "
        "q.write_text(json.dumps({'pid':os.getpid(),'created':psutil.Process().create_time(),"
        "'state':'worker_exited','returncode':0})); q.replace(p); time.sleep(0.5)"
    )
    command = [sys.executable, "-c", script, str(status)]
    child = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=0x08000000 if os.name == "nt" else 0)
    try:
        proof = m.observe(child, command, status, read)
        assert proof["launcher"]["pid"] == child.pid
        assert proof["guard"]["pid"] != os.getpid()
        assert proof["actual_guard_ancestry_observed_while_live"]
        assert m.joined(child, status, proof, read, live=lambda x: psutil.pid_exists(x["pid"])) == proof
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=10)


@pytest.mark.parametrize("bad", ["unrelated", "created", "command"])
def test_unrelated_reused_or_different_command_cannot_claim_guard_identity(tmp_path, bad):
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"pid": 21, "created": 30}))
    guard = SimpleNamespace(pid=21, create_time=lambda: 31 if bad == "created" else 30,
        cmdline=lambda: ["python", "wrong" if bad == "command" else "guard"])
    launcher = SimpleNamespace(pid=20, create_time=lambda: 29,
        children=lambda **kw: [] if bad == "unrelated" else [guard])
    with pytest.raises(ValueError, match="actual_live_guard_is_the_launched_command_or_its_descendant"):
        m.observe(SimpleNamespace(pid=20, poll=lambda: None), ["python", "guard"], status, read,
            process=lambda pid: launcher if pid == 20 else guard)


def test_observed_guard_can_be_distinct_from_joined_launcher(tmp_path):
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"pid": 21, "created": 30, "state": "worker_exited", "returncode": 0}))
    proof = {"launcher": {"pid": 20, "created": 29}, "guard": {"pid": 21, "created": 30},
        "actual_guard_ancestry_observed_while_live": True}
    calls = []
    child = SimpleNamespace(pid=20, poll=lambda: 0, wait=lambda: calls.append("joined") or 0)
    assert m.joined(child, status, proof, read, live=lambda row: False) == proof
    assert calls == ["joined"]


@pytest.mark.parametrize("bad", ["pid", "created", "paused", "returncode", "live"])
def test_guard_replacement_pause_failure_or_liveness_cannot_pass_after_wrapper_exit(tmp_path, bad):
    row = {"pid": 21, "created": 30, "state": "worker_exited", "returncode": 0}
    if bad in {"pid", "created"}:
        row[bad] += 1
    elif bad == "paused":
        row["state"] = "paused"
    elif bad == "returncode":
        row["returncode"] = 1
    status = tmp_path / "status.json"
    status.write_text(json.dumps(row))
    proof = {"launcher": {"pid": 20, "created": 29}, "guard": {"pid": 21, "created": 30},
        "actual_guard_ancestry_observed_while_live": True}
    with pytest.raises(ValueError, match="same_actual_guard_successfully_exited_after_launcher_join"):
        m.joined(SimpleNamespace(pid=20, poll=lambda: 0, wait=lambda: 0), status, proof, read,
            live=lambda row: bad == "live")
