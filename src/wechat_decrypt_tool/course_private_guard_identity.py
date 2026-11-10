"""Authenticate the actual guard under a Windows virtualenv launcher."""
from __future__ import annotations

from pathlib import Path
import time

import psutil


def require(ok, code):
    if not ok:
        raise ValueError("requires_private_guard_identity_" + code)


def observe(child, command, status, read, *, process=psutil.Process, sleep=time.sleep, timeout=120):
    launcher = process(child.pid)
    launcher_identity = {"pid": child.pid, "created": launcher.create_time()}
    deadline = time.monotonic() + timeout
    while True:
        if Path(status).is_file():
            row = read(status)
            try:
                guard = process(row["pid"])
                lineage = [launcher, *launcher.children(recursive=True)]
                require(guard.create_time() == row["created"]
                    and any(p.pid == guard.pid and p.create_time() == row["created"] for p in lineage)
                    and guard.cmdline()[1:] == command[1:]
                    and row["pid"] != psutil.Process().pid, "actual_live_guard_is_the_launched_command_or_its_descendant")
                return {"launcher": launcher_identity, "guard": {k: row[k] for k in ("pid", "created")},
                    "actual_guard_ancestry_observed_while_live": True, "new_model_calls": 0}
            except psutil.NoSuchProcess:
                require(False, "guard_exited_before_actual_identity_was_observed")
        require(child.poll() is None and time.monotonic() < deadline,
            "actual_guard_identity_must_be_observed_before_exit_without_retry")
        sleep(0.1)


def joined(child, status, proof, read, *, live, sleep=time.sleep):
    require(proof["actual_guard_ancestry_observed_while_live"] and child.pid == proof["launcher"]["pid"],
        "same_observed_launcher_and_actual_guard")
    while child.poll() is None:
        sleep(5)
    returncode = child.wait()
    row = read(status)
    require(returncode == 0 and row["state"] == "worker_exited" and row["returncode"] == 0
        and all(row[k] == v for k, v in proof["guard"].items()) and not live(proof["guard"]),
        "same_actual_guard_successfully_exited_after_launcher_join")
    return proof
