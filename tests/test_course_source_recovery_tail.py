from contextlib import contextmanager
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_source_recovery_tail as m
from wechat_decrypt_tool.course_knowledge import atomic_json


def input_fixture(tmp_path, monkeypatch, *, registered=False):
    media = tmp_path / "synthetic.mp4"
    media.write_bytes(b"synthetic source stat only")
    stat = media.stat()
    item = {"path": str(media), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    key = m.original.item_id(item)
    folder = tmp_path / "lessons" / key
    folder.mkdir(parents=True)
    (folder / "retained.bin").write_bytes(b"immutable synthetic audio input")
    dbpath = tmp_path / "registry.sqlite"

    @contextmanager
    def connect():
        db = sqlite3.connect(dbpath)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    with connect() as db:
        db.execute("CREATE TABLE videos(id TEXT PRIMARY KEY,status TEXT)")
        for table in ("segments", "frames", "notes"):
            db.execute("CREATE TABLE " + table + "(video TEXT)")
        if registered:
            db.execute("INSERT INTO videos VALUES (?,?)", (key, "pending"))
            db.commit()
        current = db.execute("SELECT * FROM videos WHERE id=?", (key,)).fetchone()
    row = {"video": key, "item": item, "initial_video": dict(current) if current else None,
           "input_files": m.frozen_files(folder), "audio_provenance": {"synthetic": True}}
    monkeypatch.setattr(m, "checkpoint", lambda root: None)
    monkeypatch.setattr(m, "no_live_source_worker", lambda item: None)
    monkeypatch.setattr(m, "input_provenance", lambda *args: {"synthetic": True})
    monkeypatch.setattr(m.original, "ReadOnlyArchive", lambda root: SimpleNamespace(connect=connect))
    return item, key, folder, row, connect


@pytest.mark.parametrize("registered", [False, True])
def test_known_pending_and_unregistered_audio_inputs_are_read_only(tmp_path, monkeypatch, registered):
    _, _, folder, row, _ = input_fixture(tmp_path, monkeypatch, registered=registered)
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    m.check_source(tmp_path, row)
    assert before == {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.mark.parametrize("change", ["media", "audio", "extra_output", "status", "segments", "frames", "notes", "binding"])
def test_changed_inputs_or_any_committed_stage_cannot_be_reprocessed(tmp_path, monkeypatch, change):
    item, key, folder, row, connect = input_fixture(tmp_path, monkeypatch, registered=True)
    if change == "media":
        Path(item["path"]).write_bytes(b"changed original")
    elif change == "audio":
        (folder / "retained.bin").write_bytes(b"changed retained input")
    elif change == "extra_output":
        (folder / "transcript.md").write_text("synthetic previous output")
    elif change == "binding":
        monkeypatch.setattr(m, "input_provenance", lambda *args: {"synthetic": False})
    else:
        with connect() as db:
            if change == "status":
                db.execute("UPDATE videos SET status='transcribed' WHERE id=?", (key,))
            else:
                db.execute("INSERT INTO " + change + " VALUES (?)", (key,))
            db.commit()
    with pytest.raises(ValueError):
        m.check_source(tmp_path, row)


def test_live_source_worker_and_unscoped_worker_are_rejected(tmp_path, monkeypatch):
    item = {"path": str(tmp_path / "synthetic.mp4")}
    def processes(args):
        return [SimpleNamespace(info={"cmdline": args})]
    module = ["python", "-m", "wechat_decrypt_tool.course_knowledge", "run"]
    monkeypatch.setattr(m.psutil, "process_iter", lambda fields: processes(module + ["--video", item["path"]]))
    with pytest.raises(ValueError, match="selected_source_worker_not_running"):
        m.no_live_source_worker(item)
    monkeypatch.setattr(m.psutil, "process_iter", lambda fields: processes(module))
    with pytest.raises(ValueError, match="no_broad_live_source_worker"):
        m.no_live_source_worker(item)
    monkeypatch.setattr(m.psutil, "process_iter", lambda fields: processes(module + ["--video", str(tmp_path / "other.mp4")]))
    m.no_live_source_worker(item)


def test_gap_provenance_preserves_each_clip_clock_and_required_incomplete_status(tmp_path, monkeypatch):
    item = {"path": "synthetic.mp4", "bytes": 1, "mtime_ns": 1}
    value = {"source": item["path"], "source_size": 1, "source_mtime_ns": 1,
             "clips": [{"file": "clip1.wav", "samples": 800000, "start_rational": [0, 1]},
                       {"file": "clip2.wav", "samples": 160000, "start_rational": [80, 1]}]}
    monkeypatch.setattr(m.gaps, "gap_evidence", lambda *args, **kwargs: value)
    result = m.input_provenance(tmp_path, {"video": "synthetic", "item": item, "kind": "gaps"})
    assert result["windows"] == 3 and result["clips"] == 2
    assert result["required_status"] == "transcribed_with_gaps" and not result["original_audio_verified"]
    assert result["plan_sha256"] == m.digest(m.gap_asr.window_plan(value))
    value["source_size"] = 2
    with pytest.raises(ValueError, match="partial_audio_source_binding"):
        m.input_provenance(tmp_path, {"video": "synthetic", "item": item, "kind": "gaps"})


def test_alternate_input_cannot_create_a_new_binding(tmp_path, monkeypatch):
    key = "a" * 24
    row = {"video": key, "item": {}, "kind": "alternate", "initial_video": {}}
    with pytest.raises(ValueError, match="existing_alternate_asr_binding"):
        m.input_provenance(tmp_path, row)
    folder = tmp_path / "lessons" / key
    folder.mkdir(parents=True)
    atomic_json(folder / "asr-audio-source.json", {})

    def binder(store, *args, **kwargs):
        store.write_json("forbidden.json", {})
    monkeypatch.setattr(m.alternate, "transcription_audio", binder)
    with pytest.raises(ValueError, match="without_any_write"):
        m.input_provenance(tmp_path, row)
    assert not (tmp_path / "forbidden.json").exists()


def export_fixture(tmp_path, monkeypatch):
    folder = tmp_path / "existing-controller"
    folder.mkdir()
    owner = {"pid": 123456, "created": 1.0}
    atomic_json(folder / "started.json", owner)
    rows = [{"video": "synthetic-0", "item": {}}]
    atomic_json(folder / "progress.json", {**owner,
        "state": "frozen_unattempted_inventory_tail_finished_existing_exceptions_remain",
        "completed_sources": [rows[0]["video"]]})
    atomic_json(folder / "synthetic-0.json", {"video": "synthetic-0", "export": {"verified": True}})
    private = folder / "source-0000-guard"
    private.mkdir()
    atomic_json(private / "guard-progress.json", {**owner, "state": "worker_exited", "returncode": 0})
    monkeypatch.setattr(m, "checkpoint", lambda root: None)
    monkeypatch.setattr(m.original, "identity_live", lambda row: False)
    monkeypatch.setattr(m.original, "ReadOnlyArchive", lambda root: None)
    monkeypatch.setattr(m.original, "validate_lesson", lambda *args: {"verified": True})
    monkeypatch.setattr(m.publication, "Library", lambda root: SimpleNamespace(catalog=lambda: {}))
    monkeypatch.setattr(m.publication, "available", lambda catalog: {"synthetic-0"})
    return folder, {"predecessor_controller": owner}, {"sources": rows}


def test_predecessor_must_actually_exit_and_all_original_results_remain_valid(tmp_path, monkeypatch):
    folder, plan, previous = export_fixture(tmp_path, monkeypatch)
    alive, sleeps = [True], []
    def sleep(seconds):
        sleeps.append(seconds)
        alive[0] = False
    m.wait(tmp_path, plan, folder, previous, live=lambda identity: alive[0], sleep=sleep)
    assert sleeps == [15]


@pytest.mark.parametrize("change", ["identity", "failed", "paused", "partial", "bad_export", "bad_guard", "guard_identity", "not_published"])
def test_failed_paused_recycled_partial_or_invalid_predecessor_cannot_release_recovery(tmp_path, monkeypatch, change):
    folder, plan, previous = export_fixture(tmp_path, monkeypatch)
    status = m.whole.read(folder / "progress.json")
    state = m.whole.read(folder / "source-0000-guard/guard-progress.json")
    if change == "identity":
        status["created"] = 2.0
    elif change in {"failed", "paused"}:
        status["state"] = "stopped" if change == "failed" else "paused"
    elif change == "partial":
        status["completed_sources"] = []
    elif change == "bad_export":
        atomic_json(folder / "synthetic-0.json", {"video": "synthetic-0", "export": {"verified": False}})
    elif change == "bad_guard":
        state["returncode"] = 1
    elif change == "guard_identity":
        state["created"] = 2.0
    else:
        monkeypatch.setattr(m.publication, "available", lambda catalog: set())
    atomic_json(folder / "progress.json", status)
    atomic_json(folder / "source-0000-guard/guard-progress.json", state)
    with pytest.raises(ValueError):
        m.wait(tmp_path, plan, folder, previous, live=lambda identity: False)


def run_fixture(tmp_path, monkeypatch, *, outcome="ok"):
    folder = tmp_path / "wiki/recovered-source-tails" / ("a" * 32)
    folder.mkdir(parents=True)
    atomic_json(folder / "independent-metadata-restore.json", {
        "state": "preserved_audio_execution_metadata_independently_restored", "queue": "a" * 32,
        "model_calls": 0, "original_root_reads": 0})
    rows = []
    for i in range(2):
        key = f"synthetic-{i}"
        lesson = tmp_path / "lessons" / key
        lesson.mkdir(parents=True)
        (lesson / "retained.bin").write_bytes(b"synthetic original input")
        rows.append({"video": key, "item": {"video": key}, "kind": "gaps" if i else "verified_original",
                     "input_files": m.frozen_files(lesson)})
    plan = {"sources": rows}
    monkeypatch.setattr(m, "load", lambda *args: (tmp_path, folder, plan, tmp_path / "previous", {}))
    monkeypatch.setattr(m, "wait", lambda *args: None)
    monkeypatch.setattr(m, "checkpoint", lambda root: None)
    monkeypatch.setattr(m, "check_source", lambda *args: None)
    monkeypatch.setattr(m.original, "worker_command", lambda root, plan, row: [row["video"]])
    monkeypatch.setattr(m.original, "ReadOnlyArchive", lambda root: None)
    monkeypatch.setattr(m.original, "validate_lesson", lambda store, key, item: {"verified": key})
    monkeypatch.setattr(m.publication, "Library", lambda root: SimpleNamespace(catalog=lambda: {}))
    monkeypatch.setattr(m.publication, "available", lambda catalog: {row["video"] for row in rows})
    monkeypatch.setattr(m.whole, "backup", lambda *args: None)

    @contextmanager
    def client(**kwargs):
        yield object()
    monkeypatch.setattr(m.original.httpx, "Client", client)

    def publish(*args):
        if outcome == "publication":
            raise ValueError("synthetic publication failure")
        return {"state": "verified"}
    monkeypatch.setattr(m.prior, "publish_verified", publish)
    calls = []

    def worker(root, private, command):
        calls.append(command)
        if outcome == "pause":
            (root / "pause.flag").write_text("synthetic new thermal pause")
            return {"state": "paused_without_automatic_resume"}
        for name in ("evidence.json", "graph.json"):
            atomic_json(root / "lessons" / command[0] / name, {})
        if outcome == "input_changed":
            (root / "lessons" / command[0] / "retained.bin").write_bytes(b"changed input")
        owner = m.whole.read(folder / "started.json")
        atomic_json(private / "guard-progress.json", {**owner, "state": "worker_exited", "returncode": 0})
        atomic_json(root / "job-progress.json", {"stage": "batch_finished", "processed": 1,
                                                  "failures": 1 if outcome == "source_failure" else 0})
        atomic_json(root / "run-plan.json", {})
        return {"state": "private_worker_exited", "returncode": 1 if outcome == "worker_failure" else 0}
    monkeypatch.setattr(m.guard, "run", worker)
    return folder, calls


def test_recovery_runs_each_preserved_source_once_and_keeps_inputs_before_final_readonly_verify(tmp_path, monkeypatch):
    folder, calls = run_fixture(tmp_path, monkeypatch)
    result = m.run(tmp_path, "a" * 32, "http://127.0.0.1:3003")
    assert result["sources"] == 2 and calls == [["synthetic-0"], ["synthetic-1"]]
    assert not result["reader_article_published"] and not result["source_deletion_allowed"]
    # A final JSON state is insufficient while the actual controller is alive.
    with pytest.raises(ValueError, match="actual_complete_recovery_controller_exit"):
        m.verify(tmp_path, "a" * 32)
    monkeypatch.setattr(m.original, "identity_live", lambda identity: False)
    assert m.verify(tmp_path, "a" * 32)["model_calls"] == 0
    with pytest.raises(ValueError, match="controller_once"):
        m.run(tmp_path, "a" * 32, "http://127.0.0.1:3003")
    assert len(calls) == 2
    assert m.whole.read(folder / "progress.json")["completed_sources"] == ["synthetic-0", "synthetic-1"]


@pytest.mark.parametrize("outcome", ["pause", "worker_failure", "source_failure", "publication"])
def test_pause_worker_export_or_publication_failure_stops_before_next_source_without_retry(tmp_path, monkeypatch, outcome):
    folder, calls = run_fixture(tmp_path, monkeypatch, outcome=outcome)
    with pytest.raises((InterruptedError, ValueError)):
        m.run(tmp_path, "a" * 32, "http://127.0.0.1:3003")
    assert calls == [["synthetic-0"]]
    assert list(folder.glob("failure-*.json"))
    assert m.whole.read(folder / "progress.json")["completed_sources"] == []
    if outcome == "pause":
        assert (tmp_path / "pause.flag").read_text() == "synthetic new thermal pause"
    with pytest.raises(ValueError, match="controller_once"):
        m.run(tmp_path, "a" * 32, "http://127.0.0.1:3003")
    assert len(calls) == 1


def test_final_results_cannot_accept_modified_original_input_bytes(tmp_path, monkeypatch):
    folder, calls = run_fixture(tmp_path, monkeypatch, outcome="input_changed")
    with pytest.raises(ValueError, match="original_audio_inputs_and_failures_preserved"):
        m.run(tmp_path, "a" * 32, "http://127.0.0.1:3003")
    assert len(calls) == 1
    assert m.whole.read(folder / "progress.json")["state"] == "stopped"
    with pytest.raises(ValueError):
        m.verify(tmp_path, "a" * 32)


def test_dispatch_rejects_unverified_or_mismatched_metadata_without_starting(tmp_path, monkeypatch):
    folder, calls = run_fixture(tmp_path, monkeypatch)
    atomic_json(folder / "independent-metadata-restore.json", {
        "state": "preserved_audio_execution_metadata_independently_restored", "queue": "b" * 32,
        "model_calls": 0, "original_root_reads": 0})
    with pytest.raises(ValueError, match="actual_independent_execution_metadata_restore"):
        m.run(tmp_path, "a" * 32, "http://127.0.0.1:3003")
    assert calls == [] and not (folder / "started.json").exists()


def selection_fixture(tmp_path, monkeypatch):
    registry = tmp_path / "registry.sqlite"
    db = sqlite3.connect(registry)
    db.execute("CREATE TABLE videos(id TEXT PRIMARY KEY,status TEXT,error TEXT)")
    items, a, b, partial, excluded = [], [], [], [], []
    for n in range(7):
        path = tmp_path / f"synthetic-{n}.mp4"
        path.write_bytes(b"synthetic media")
        stat = path.stat()
        item = {"path": str(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        items.append(item)
        key = m.original.item_id(item)
        folder = tmp_path / "lessons" / key
        folder.mkdir(parents=True)
        if n in (1, 2):
            (folder / "damaged.m4a").write_bytes(b"synthetic retained packets")
            (folder / "clip.wav").write_bytes(b"synthetic samples")
            atomic_json(folder / "audio-gap-recovery.json", {"original_packets": {"file": "damaged.m4a"},
                                                            "clips": [{"file": "clip.wav"}]})
            partial.append({"video": key, "manifest_sha256": m.whole.sha((folder / "audio-gap-recovery.json").read_bytes())})
        else:
            (folder / "audio.m4a").write_bytes(b"synthetic retained audio")
        proof = {"video": key}
        if n >= 3:
            proof["files"] = {name: value["sha256"] for name, value in m.frozen_files(folder).items()}
            b.append(proof)
            db.execute("INSERT INTO videos VALUES (?,?,?)", (key, "pending", ""))
            code = "registered_source_not_retried"
        else:
            if n == 0:
                proof["files"] = m.frozen_files(folder)
            a.append(proof)
            code = "existing_output_requires_separate_recovery"
        excluded.append({"video": key, "code": code})
    db.commit()
    db.close()

    @contextmanager
    def connect():
        connection = sqlite3.connect(registry)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()
    monkeypatch.setattr(m.original, "ReadOnlyArchive", lambda root: SimpleNamespace(connect=connect))
    monkeypatch.setattr(m, "input_provenance", lambda root, row: {"kind": row["kind"]})
    (tmp_path / "wiki").mkdir()
    atomic_json(tmp_path / "inventory-unique.json", {"videos": items})
    atomic_json(tmp_path / m.PROOFS[0], {"state": "three_excluded_audio_inputs_preflighted_without_asr", "rows": a})
    atomic_json(tmp_path / m.PROOFS[1], {"state": "four_registered_pending_alternate_audio_inputs_reverified", "rows": b})
    atomic_json(tmp_path / m.GAP_PROOF, {"rows": partial})
    return {"excluded": excluded, "sources": []}, a, b


def test_complete_selection_keeps_all_seven_exceptions_and_their_distinct_input_kinds(tmp_path, monkeypatch):
    previous, a, b = selection_fixture(tmp_path, monkeypatch)
    rows = m.select(tmp_path, previous)
    assert [row["video"] for row in rows] == [row["video"] for row in a + b]
    assert [row["kind"] for row in rows] == ["verified_original", "gaps", "gaps"] + ["alternate"] * 4
    assert all(row["initial_video"]["status"] == "pending" for row in rows[3:])
    assert all(row["initial_video"] is None for row in rows[:3])


@pytest.mark.parametrize("change", ["duplicate", "missing", "reserved", "changed_audio", "extra_gap_output"])
def test_changed_missing_duplicate_or_already_selected_exception_is_rejected_before_registration(tmp_path, monkeypatch, change):
    previous, a, b = selection_fixture(tmp_path, monkeypatch)
    if change == "duplicate":
        b[0]["video"] = a[0]["video"]
    elif change == "missing":
        b.pop()
    elif change == "reserved":
        previous["sources"] = [{"video": a[0]["video"]}]
    elif change == "changed_audio":
        (tmp_path / "lessons" / a[0]["video"] / "audio.m4a").write_bytes(b"changed audio")
    else:
        (tmp_path / "lessons" / a[1]["video"] / "previous-asr.json").write_text("{}")
    atomic_json(tmp_path / m.PROOFS[1], {"state": "four_registered_pending_alternate_audio_inputs_reverified", "rows": b})
    with pytest.raises(ValueError):
        m.select(tmp_path, previous)
    assert not (tmp_path / "wiki/recovered-source-tails").exists()
