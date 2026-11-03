import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from wechat_decrypt_tool import course_pdf_queue_continue as module
from test_course_pdf_queue import page_history, queue_fixture

queue = module.queue


def fixture(tmp_path, monkeypatch):
    store, plan, template, independent, calls, chat = queue_fixture(
        tmp_path, monkeypatch
    )
    root = store.root
    folder = root / "queue"
    folder.mkdir()
    entries = []
    for number, page in [(3, 2), (4, 3), (5, 5)]:
        data = queue.read(plan)
        data["pdf_sources_selected_pages"][0]["selected_pages"] = [page]
        data["summary"]["page_occurrences"] = 1
        path = folder / f"{number}.json"
        path.write_text(json.dumps(data), encoding="utf8")
        entries.append(
            {
                "number": number,
                "plan": path.name,
                "plan_sha256": queue.sha(path.read_bytes()),
                "unique_raw_pages": 1,
                "page_occurrences": 1,
            }
        )
    store.write_json("queue/validation.json", {"batches": entries})
    store.write_json("template.json", template)
    monkeypatch.setattr(queue, "checkpoint", lambda root, **kw: None)
    monkeypatch.setattr(queue, "gpu_lease", lambda _: nullcontext())
    monkeypatch.setattr(queue, "local_chat", chat)
    monkeypatch.setattr(queue.sys, "addaudithook", lambda _: None)
    actual_prepare, actual_backup = queue.prepare, queue.backup_batch
    monkeypatch.setattr(queue, "prepare", lambda *a: actual_prepare(*a, reserve_gib=0))
    monkeypatch.setattr(
        queue,
        "backup_batch",
        lambda *a, **kw: actual_backup(
            *a, **kw, minimum_free_gib=0, rerender=independent
        ),
    )
    prefix = "pdf-queue-20261103-0003"
    queue.prepare(
        root,
        folder / "3.json",
        prefix,
        template,
        "parent-validation.json",
        tmp_path / "inputs",
        tmp_path / "results",
    )
    queue.worker(root, prefix)
    for suffix in [
        "-resume-binding.json",
        "-resume-prepared.json",
        "-resume-original-rows.json",
        "-resume-preflight-validation.json",
    ]:
        store.write_json(prefix + suffix, {"fixture": True})
    (root / (prefix + "-resume-runtime.py")).write_bytes(b"# fixture\n")
    pending = [{"pid": 9999998, "created": 1}]
    store.write_json(
        prefix + "-resume-status.json",
        {**pending[0], "state": "finite_batch_verified_and_backed_up"},
    )
    controls = {
        str(p): queue.sha(p.read_bytes())
        for p in [
            folder / "validation.json",
            root / "template.json",
            *(folder / f"{n}.json" for n in range(3, 6)),
            *(Path(queue.__file__).with_name(n) for n in queue.RUNTIME),
        ]
    }
    store.write_json(
        "pdf-selected-queue-launched.json",
        {
            "pid": 9999999,
            "created": 1,
            "first": 3,
            "last": 5,
            "bound_control_sha256": controls,
        },
    )
    store.write_json(
        "pdf-selected-queue-progress.json",
        {
            "pid": 9999999,
            "created": 1,
            "state": "paused",
            "number": 3,
            "prefix": prefix,
            "completed_batches": [],
        },
    )
    (root / "supervisor-stop.flag").write_bytes(b"user-pause-preserved")
    launched = []

    def guarded(root, command, **limits):
        assert limits["maximum_temperature"] == 70
        assert limits["maximum_power"] == 200
        assert limits["guard_name"] == "extraction-thermal-guard"
        assert callable(limits["sampler"])
        launched.append(command[-1])
        queue.worker(root, command[-1])

    monkeypatch.setattr(queue, "guarded", guarded)
    args = (
        root,
        folder,
        root / "template.json",
        prefix,
        4,
        5,
        tmp_path / "inputs",
        tmp_path / "results",
        pending,
    )
    return store, calls, launched, args


def test_verified_partial_predecessor_continues_only_future_pages_once(
    tmp_path, monkeypatch
):
    store, calls, launched, args = fixture(tmp_path, monkeypatch)
    root = store.root
    old = {
        p: p.read_bytes()
        for p in root.iterdir()
        if p.is_file()
        and (
            p.name.startswith("pdf-selected-queue-")
            or p.name == "supervisor-stop.flag"
            or p.name.startswith("pdf-queue-20261103-0003")
        )
    }
    history = [page_history(store, p) for p in (1, 2)]
    module.run(*args)
    assert launched == ["pdf-queue-20261103-0004", "pdf-queue-20261103-0005"]
    assert calls == [1, 2, 3, 5]
    assert [page_history(store, p) for p in (1, 2)] == history
    assert all(p.read_bytes() == raw for p, raw in old.items())
    progress = queue.read(
        root / "pdf-selected-queue-continuation-0004-0005-progress.json"
    )
    assert progress["state"] == "requested_finite_queue_finished"
    assert [p["number"] for p in progress["completed_batches"]] == [4, 5]
    assert not progress["automatic_retry_allowed"]
    with pytest.raises(ValueError, match="already_launched"):
        module.run(*args)
    assert calls == [1, 2, 3, 5]


@pytest.mark.parametrize(
    "change", ["original-control", "completed-counter", "future-attempt"]
)
def test_changed_or_attempted_scope_never_launches(tmp_path, monkeypatch, change):
    store, calls, launched, args = fixture(tmp_path, monkeypatch)
    if change == "original-control":
        store.write_json("template.json", {"changed": True})
    elif change == "completed-counter":
        data = queue.read(store.root / "pdf-selected-queue-progress.json")
        data["completed_batches"] = [{"number": 3}]
        store.write_json("pdf-selected-queue-progress.json", data)
    else:
        store.write_json("pdf-queue-20261103-0004-ocr-progress.json", {"processed": 0})
    with pytest.raises(ValueError, match="pdf_queue_continue_"):
        module.run(*args)
    assert not launched and calls == [1, 2]


@pytest.mark.parametrize("change", ["missing-receipt", "backup-corrupt", "wrong-scope"])
def test_predecessor_needs_independent_backup_not_page_counter(
    tmp_path, monkeypatch, change
):
    store, calls, launched, args = fixture(tmp_path, monkeypatch)
    path = store.root / "pdf-queue-20261103-0003-validation.json"
    proof = queue.read(path)
    if change == "missing-receipt":
        path.unlink()
    elif change == "backup-corrupt":
        manifest = queue.read(Path(proof["backup"]) / "manifest.json")
        (Path(proof["backup"]) / manifest["files"][0]["path"]).write_bytes(b"corrupt")
    else:
        proof["plan_sha256"] = "f" * 64
        store.write_json(path.name, proof)
    with pytest.raises(ValueError):
        module.run(*args)
    assert not launched and calls == [1, 2]


def test_failed_future_batch_is_recorded_and_never_retried(tmp_path, monkeypatch):
    store, calls, launched, args = fixture(tmp_path, monkeypatch)

    def failed(root, command, **kw):
        launched.append(command[-1])

    monkeypatch.setattr(queue, "guarded", failed)
    with pytest.raises(ValueError, match="input_outside_archive_or_missing"):
        module.run(*args)
    progress = queue.read(
        store.root / "pdf-selected-queue-continuation-0004-0005-progress.json"
    )
    assert progress["state"] == "failed" and progress["error_sha256"]
    with pytest.raises(ValueError, match="already_launched"):
        module.run(*args)
    assert launched == ["pdf-queue-20261103-0004"] and calls == [1, 2]


def test_control_change_while_waiting_stops_before_prepare(tmp_path, monkeypatch):
    store, calls, launched, args = fixture(tmp_path, monkeypatch)
    bound, phase = module.bind, []

    def bind(*a):
        result = bound(*a)
        phase.append("waiting")
        return result

    monkeypatch.setattr(module, "bind", bind)
    monkeypatch.setattr(queue, "alive", lambda identity: bool(phase))

    def mutate(_):
        (store.root / "supervisor-stop.flag").write_bytes(b"new-stop")

    with pytest.raises(ValueError, match="bound_control_changed"):
        module.run(*args, sleep=mutate)
    assert not launched and calls == [1, 2]
    assert (store.root / "supervisor-stop.flag").read_bytes() == b"new-stop"


def test_unrelated_process_cannot_act_as_exact_predecessor(tmp_path, monkeypatch):
    store, calls, launched, args = fixture(tmp_path, monkeypatch)
    args = (
        *args[:-1],
        [{"pid": module.os.getpid(), "created": module.psutil.Process().create_time()}],
    )
    with pytest.raises(ValueError, match="predecessor_process_role_differs"):
        module.run(*args)
    assert not launched and calls == [1, 2]


def test_pause_preserves_flags_without_running_queue(tmp_path, monkeypatch):
    store, calls, launched, args = fixture(tmp_path, monkeypatch)
    monkeypatch.undo()
    (store.root / "pause.flag").write_text("user", encoding="utf8")
    with pytest.raises(InterruptedError, match="pause_requested"):
        module.run(*args)
    assert (store.root / "pause.flag").read_text() == "user"
    assert (store.root / "supervisor-stop.flag").read_bytes() == b"user-pause-preserved"
    assert not launched and calls == [1, 2]


def test_completed_predecessors_are_verified_and_not_scheduled_again(
    tmp_path, monkeypatch
):
    store, calls, launched, args = fixture(tmp_path, monkeypatch)
    root, folder = args[:2]
    prefix = "pdf-queue-20261103-0004"
    queue.prepare(
        root,
        folder / "4.json",
        prefix,
        queue.read(root / "template.json"),
        "pdf-queue-20261103-0003-validation.json",
        args[6],
        args[7],
    )
    queue.worker(root, prefix)
    for suffix in [
        "-resume-binding.json",
        "-resume-prepared.json",
        "-resume-original-rows.json",
        "-resume-preflight-validation.json",
    ]:
        store.write_json(prefix + suffix, {"fixture": True})
    (root / (prefix + "-resume-runtime.py")).write_bytes(b"# fixture\n")
    store.write_json(
        prefix + "-resume-status.json",
        {**args[-1][0], "state": "finite_batch_verified_and_backed_up"},
    )
    proof = queue.read(root / "pdf-queue-20261103-0003-validation.json")
    status = queue.read(root / "pdf-selected-queue-progress.json")
    status.update(
        number=4,
        prefix=prefix,
        completed_batches=[
            {
                "number": 3,
                "prefix": "pdf-queue-20261103-0003",
                "page_occurrences": 1,
                "manifest_sha256": proof["manifest_sha256"],
            }
        ],
    )
    store.write_json("pdf-selected-queue-progress.json", status)
    preserved = (root / "pdf-selected-queue-progress.json").read_bytes()
    module.run(*args[:3], prefix, 5, 5, *args[6:])
    assert launched == ["pdf-queue-20261103-0005"] and calls == [1, 2, 3, 5]
    assert (root / "pdf-selected-queue-progress.json").read_bytes() == preserved
