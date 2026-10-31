"""Keep known damaged sources out of ordinary ASR until recovery is published."""

import json
import re
import time


class AudioPreparationPending(RuntimeError):
    pass


def require_prepared_audio(store, key):
    path = store.root / "audio-preparation-plan.json"
    if not path.exists():
        return
    plan = json.loads(path.read_text("utf-8"))
    if plan.get("version") != 1:
        raise RuntimeError("Unknown audio preparation plan version")
    entries = [row for row in plan["sources"] if row["video"] == key]
    if not entries:
        return
    if len(entries) != 1 or not re.fullmatch(r"[a-f0-9]{24}", key):
        raise RuntimeError("Invalid or duplicate audio preparation source")
    entry = entries[0]
    if entry["manifest"] not in ("audio-recovery.json", "audio-gap-recovery.json"):
        raise RuntimeError("Invalid audio preparation manifest")
    if (store.root / "lessons" / key / entry["manifest"]).is_file():
        return
    # The caller releases/has not yet acquired the GPU while waiting. A dead
    # preparer must become a visible failure rather than an endless silent wait.
    import psutil

    try:
        worker = psutil.Process(entry["worker"]["pid"])
        live = worker.create_time() == entry["worker"]["created"] and any(
            arg.endswith(entry["worker"]["script"]) for arg in worker.cmdline()
        )
    except psutil.Error:
        live = False
    if not live:
        raise RuntimeError(
            "Audio recovery preparer exited before publishing this source"
        )
    raise AudioPreparationPending("Waiting for verified audio recovery publication")


def await_prepared_audio(store, key):
    while True:
        store.checkpoint()
        try:
            require_prepared_audio(store, key)
            return
        except AudioPreparationPending:
            store.progress("waiting_audio_preparation", video=key, gpu_lease_held=False)
            time.sleep(2)
