"""One private finite worker; retain controls and never resume after cooling."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

import psutil

from . import course_gpu_guard as thermal
from . import course_textbook_whole_sources as whole
from .course_archive_gpu import checked_sample
from .course_knowledge import atomic_json, schema_lease


def checkpoint(root):
    whole.TextbookStore(root).checkpoint()
    if (root / "supervisor-stop.flag").exists():
        raise InterruptedError("requires_private_guard_supervisor_stop_preserved")


def run(root, folder, command, *, sample=None, launch=subprocess.Popen, stopper=thermal.stop_worker, sleep=time.sleep):
    root, folder = Path(root).resolve(), Path(folder).resolve()
    if not folder.is_relative_to(root / "wiki") or not folder.is_dir():
        raise ValueError("requires_private_guard_existing_private_job")
    owner, reading, process = psutil.Process(), None, None
    sampler = sample or (lambda: checked_sample(root))

    def progress(state, **extra):
        atomic_json(folder / "guard-progress.json", {"pid": owner.pid, "created": owner.create_time(),
            "state": state, "updated": time.time(), "reading": reading, "maximum_temperature_c": 70,
            "maximum_power_w": 200, "automatic_thermal_resume_allowed": False, **extra})

    def stop():
        retained = {n: (root / n).read_bytes() for n in ("pause.flag", "supervisor-stop.flag") if (root / n).exists()}
        for name, raw in retained.items():
            whole.backup(root / name, folder / "retained-control-markers" / (whole.sha(raw) + "-" + name))
        stopper(process, root)
        for name, raw in retained.items():
            (root / name).write_bytes(raw)

    with schema_lease(folder / "guard.lock", timeout=0):
        if (folder / "guard-started.json").exists():
            raise ValueError("requires_private_guard_once_no_auto_retry")
        atomic_json(folder / "guard-started.json", {"pid": owner.pid, "created": owner.create_time()})
        try:
            checkpoint(root)
            reading = sampler()
            reason = thermal.violation(reading, 70, 200)
            if reason:
                progress("paused", reason_code=reason, worker_launched=False)
                return {"state": "paused_without_automatic_resume"}
            with (folder / "worker.stdout.log").open("ab") as out, (folder / "worker.stderr.log").open("ab") as err:
                process = launch(command, stdout=out, stderr=err,
                    env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
                    creationflags=0x08000000 if os.name == "nt" else 0)
                while process.poll() is None:
                    reading = sampler()
                    reason = "existing_control_marker" if any((root / n).exists() for n in ("pause.flag", "supervisor-stop.flag")) else thermal.violation(reading, 70, 200)
                    if reason:
                        stop()
                        progress("paused", reason_code=reason)
                        return {"state": "paused_without_automatic_resume"}
                    progress("running", worker_pid=process.pid)
                    sleep(5)
                progress("worker_exited", returncode=process.returncode)
                if process.returncode != 0:
                    raise ValueError("requires_private_guard_successful_worker_exit")
                return {"state": "private_worker_exited", "returncode": 0}
        except BaseException as exc:
            atomic_json(folder / "guard-failure.json", whole.failure_status(exc))
            if process is not None and process.poll() is None:
                stop()
                progress("paused", reason_code="guard_exception")
            elif process is None:
                progress("paused", reason_code="guard_preflight_exception", worker_launched=False)
            raise
