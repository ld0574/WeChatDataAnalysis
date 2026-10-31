import hashlib
import json
from pathlib import Path

import pytest

from wechat_decrypt_tool import course_lesson_exports as module
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, atomic_json
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive


@pytest.fixture
def archive(tmp_path):
    store = KnowledgeStore(tmp_path / "archive")
    source = tmp_path / "lesson.mp4"
    source.write_bytes(b"synthetic media; no real decoder needed")
    item = {
        "path": str(source),
        "bytes": source.stat().st_size,
        "mtime_ns": source.stat().st_mtime_ns,
        "duration_seconds": 175,
        "audio_streams": [{"codec": "synthetic"}],
    }
    key = store.register(item)
    folder = store.root / "lessons" / key
    folder.mkdir(parents=True)
    atomic_json(
        folder / "frames-completed.json",
        {"step": 30, "complete_visual_coverage": False},
    )
    atomic_json(
        store.root / "inventory-unique.json",
        {"videos": [item], "unique_readable_videos": 1},
    )
    for n in range(7):
        store.save_segment(key, n, n * 25, (n + 1) * 25, f"synthetic segment {n}")
    with store.write() as db:
        db.execute(
            "UPDATE videos SET status='transcribed',model=? WHERE id=?",
            (module.ASR_MODEL, key),
        )
        for second in (0, 150):
            db.execute(
                "INSERT INTO frames(video,time,path,description,status,model) VALUES(?,?,?,?,?,?)",
                (
                    key,
                    second,
                    f"lessons/{key}/frames/{second}.jpg",
                    "synthetic frame",
                    "ready",
                    module.MODEL,
                ),
            )
        segments = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM segments WHERE video=? ORDER BY n", (key,)
            )
        ]
        frames = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM frames WHERE video=? ORDER BY time", (key,)
            )
        ]
        for window, page in enumerate((segments[:6], segments[6:])):
            ids = [page[0]["id"], -frames[window]["id"]]
            note = {
                "summary": f"synthetic summary {window}",
                "concepts": [
                    {
                        "name": "shared concept",
                        "explanation": "repeated explanation",
                        "evidence": ids,
                    },
                    {
                        "name": f"concept {window}",
                        "explanation": "other explanation",
                        "evidence": ids[:1],
                    },
                ],
                "relations": [
                    {
                        "from": "shared concept",
                        "to": f"concept {window}",
                        "relation": "supports",
                        "evidence": ids[:1],
                    }
                ],
            }
            db.execute(
                "INSERT INTO notes VALUES(?,?,?,?,?)",
                (key, window, json.dumps(note), module.MODEL, "ready"),
            )
    store.export(key)
    return store, key, item


def run(archive, *, full=False):
    store, key, _ = archive
    return module.validate_exports(ReadOnlyArchive(store.root), None if full else [key])


def change_file(archive, name, mutate):
    store, key, _ = archive
    path = store.root / "lessons" / key / name
    value = json.loads(path.read_text("utf-8"))
    mutate(value)
    atomic_json(path, value)


def test_full_roundtrip_needs_no_original_or_model_and_preserves_repeated_explanations(
    archive,
):
    store, key, item = archive
    Path(item["path"]).unlink()
    result = run(archive, full=True)
    assert result["accepted"] and result["full_audio_inventory_exports_consistent"]
    assert (
        not result["source_deletion_allowed"]
        and not result["semantic_accuracy_verified"]
    )
    assert (
        not result["audio_coverage_verified"] and not result["complete_visual_coverage"]
    )
    lesson = result["completed"][0]
    assert (lesson["segments"], lesson["frames"], lesson["note_windows"]) == (7, 2, 2)
    assert (lesson["concepts"], lesson["relations"]) == (3, 2)
    assert (
        result["inventory_sha256"]
        == hashlib.sha256(
            (store.root / "inventory-unique.json").read_bytes()
        ).hexdigest()
    )
    graph = json.loads((store.root / "lessons" / key / "graph.json").read_text("utf-8"))
    assert graph["concepts"][0]["explanations"] == ["repeated explanation"] * 2


def test_full_inventory_accounts_for_unregistered_lessons_and_exclusions(
    archive, tmp_path
):
    store, _, item = archive
    second = {**item, "path": str(tmp_path / "never-registered.mp4")}
    atomic_json(
        store.root / "inventory-unique.json",
        {"videos": [item, second, {"audio_streams": []}], "unique_readable_videos": 2},
    )
    result = run(archive, full=True)
    assert not result["accepted"] and result["requested"] == 2
    assert result["excluded_noneligible_inventory_rows"] == 1
    assert result["errors"][0]["code"] == "unregistered_lesson"
    subset = run(archive)
    assert subset["accepted"] and not subset["full_audio_inventory_exports_consistent"]
    assert subset["scope"] == "explicit_lesson_subset"


@pytest.mark.parametrize(
    ("name", "mutate", "code"),
    [
        ("graph.json", lambda x: x["concepts"].pop(), "graph_mismatch"),
        (
            "graph.json",
            lambda x: x["concepts"].append(x["concepts"][0]),
            "graph_mismatch",
        ),
        ("graph.json", lambda x: x["relations"].clear(), "graph_mismatch"),
        (
            "evidence.json",
            lambda x: x["segments"][0].update(text="corrupt"),
            "export_segments_mismatch",
        ),
        (
            "evidence.json",
            lambda x: x["frames"][0].update(description="corrupt"),
            "export_frames_mismatch",
        ),
        (
            "evidence.json",
            lambda x: x["notes"][0].update(summary="corrupt"),
            "export_notes_mismatch",
        ),
        (
            "evidence.json",
            lambda x: x["video"].update(path="other lesson"),
            "export_video_mismatch",
        ),
        (
            "evidence.json",
            lambda x: x.update(deletion_ready=True),
            "unsafe_export_deletion_claim",
        ),
        (
            "frames-completed.json",
            lambda x: x.update(step=60),
            "capture_checkpoint_incomplete",
        ),
    ],
)
def test_corrupt_exports_fail_with_content_free_error_codes(
    archive, name, mutate, code
):
    change_file(archive, name, mutate)
    report = run(archive)
    assert not report["accepted"] and report["errors"][0]["code"] == code
    assert "corrupt" not in json.dumps(report)


def test_stale_export_error_and_update_are_ignored_but_live_error_is_not(archive):
    change_file(
        archive,
        "evidence.json",
        lambda x: x["video"].update(error="old repaired error", updated=-1),
    )
    assert run(archive)["accepted"]
    store, key, _ = archive
    with store.write() as db:
        db.execute("UPDATE videos SET error='private error' WHERE id=?", (key,))
    report = run(archive)
    assert report["errors"][0]["code"] == "recorded_lesson_error"
    assert "private error" not in json.dumps(report)


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DELETE FROM notes WHERE window=1", "missing_note_windows"),
        ("UPDATE frames SET status='pending'", "frame_ocr_incomplete"),
        ("UPDATE frames SET model='other model'", "frame_ocr_incomplete"),
        ("UPDATE notes SET model='other model'", "notes_incomplete"),
        ("UPDATE videos SET status='transcribing'", "asr_incomplete"),
        ("DELETE FROM segments WHERE n=3", "missing_segment_windows"),
    ],
)
def test_incomplete_committed_work_never_passes_even_after_reexport(archive, sql, code):
    store, key, _ = archive
    with store.write() as db:
        db.execute(sql)
    store.export(key)
    assert run(archive)["errors"][0]["code"] == code


@pytest.mark.parametrize(
    "kind", ["other_window", "other_lesson", "boolean", "missing_endpoint"]
)
def test_bad_citations_rejected_even_when_graph_and_evidence_match_db(archive, kind):
    store, key, _ = archive
    with store.write() as db:
        note = json.loads(
            db.execute("SELECT data FROM notes WHERE window=0").fetchone()[0]
        )
        if kind == "missing_endpoint":
            note["relations"][0]["to"] = "absent"
        else:
            citation = (
                db.execute("SELECT id FROM segments WHERE n=6").fetchone()[0]
                if kind == "other_window"
                else 9999
            )
            if kind == "boolean":
                citation = True  # Python equates True and the legitimate row ID 1.
            note["concepts"][0]["evidence"] = [citation]
        db.execute("UPDATE notes SET data=? WHERE window=0", (json.dumps(note),))
    store.export(key)
    code = (
        "missing_relation_endpoint"
        if kind == "missing_endpoint"
        else "citation_outside_note_window"
    )
    assert run(archive)["errors"][0]["code"] == code


@pytest.mark.parametrize("kind", ["files", "rows"])
def test_concurrent_mutation_cannot_pass(archive, monkeypatch, kind):
    store, key, _ = archive
    original = module.read_rows
    calls = []

    def race(reader, video):
        calls.append(1)
        if len(calls) == 2:
            if kind == "rows":
                with store.write() as db:
                    db.execute("UPDATE notes SET model='changed' WHERE video=?", (key,))
            else:
                change_file(archive, "graph.json", lambda x: x["concepts"].clear())
        return original(reader, video)

    # File mutation occurs after initial read_rows, before export freezing.
    # Trigger it just after the graph was read instead for the file case.
    if kind == "files":
        read_bytes = Path.read_bytes

        def raced_read(path):
            data = read_bytes(path)
            if path.name == "graph.json" and not calls:
                calls.append(1)
                path.write_bytes(b"{}")
            return data

        monkeypatch.setattr(Path, "read_bytes", raced_read)
    else:
        monkeypatch.setattr(module, "read_rows", race)
    expected = (
        "export_changed_during_validation"
        if kind == "files"
        else "rows_changed_during_validation"
    )
    assert run(archive)["errors"][0]["code"] == expected


def test_gap_note_windows_restart_at_each_clip_and_are_bound_to_manifest(archive):
    store, key, _ = archive
    folder = store.root / "lessons" / key
    gap = {
        "clips": [
            {
                "start_rational": [0, 1],
                "samples": 400000,
                "file": "gap-audio/00000.wav",
            },
            {
                "start_rational": [150, 1],
                "samples": 400000,
                "file": "gap-audio/00001.wav",
            },
        ]
    }
    atomic_json(folder / "audio-gap-recovery.json", gap)
    binding = {
        "video": key,
        "windows": module.window_plan(gap),
        "manifest_sha256": hashlib.sha256(
            (folder / "audio-gap-recovery.json").read_bytes()
        ).hexdigest(),
    }
    atomic_json(folder / "asr-gap-source.json", binding)
    with store.write() as db:
        db.execute(
            "UPDATE videos SET status='transcribed_with_gaps' WHERE id=?", (key,)
        )
        db.execute("DELETE FROM segments WHERE n BETWEEN 1 AND 5")
        db.execute("UPDATE segments SET n=1 WHERE n=6")
        for window, n in enumerate((0, 1)):
            segment = db.execute("SELECT id FROM segments WHERE n=?", (n,)).fetchone()[
                0
            ]
            note = json.loads(
                db.execute(
                    "SELECT data FROM notes WHERE window=?", (window,)
                ).fetchone()[0]
            )
            for claim in note["concepts"] + note["relations"]:
                claim["evidence"] = [segment]
            db.execute(
                "UPDATE notes SET data=? WHERE window=?", (json.dumps(note), window)
            )
        segments = [dict(r) for r in db.execute("SELECT * FROM segments ORDER BY n")]
        notes = [
            json.loads(r[0])
            for r in db.execute("SELECT data FROM notes ORDER BY window")
        ]
    # Use synthetic structured evidence, without pretending these are decoded clips.
    change_file(
        archive,
        "evidence.json",
        lambda x: x.update(
            segments=segments,
            notes=notes,
            audio_gap_recovery=gap,
            asr_gap_source=binding,
            video={**x["video"], "status": "transcribed_with_gaps"},
        ),
    )
    nodes = {}
    for note in notes:
        for node in note["concepts"]:
            old = nodes.setdefault(
                node["name"], {"name": node["name"], "explanations": [], "evidence": []}
            )
            old["explanations"].append(node["explanation"])
            old["evidence"] = sorted(set(old["evidence"] + node["evidence"]))
    atomic_json(
        folder / "graph.json",
        {
            "video": key,
            "concepts": list(nodes.values()),
            "relations": [edge for note in notes for edge in note["relations"]],
        },
    )
    report = run(archive)
    assert report["accepted"] and report["completed"][0]["note_windows"] == 2
    change_file(
        archive, "asr-gap-source.json", lambda x: x.update(manifest_sha256="wrong")
    )
    assert run(archive)["errors"][0]["code"] == "gap_binding_mismatch"


def test_scope_rejects_empty_duplicate_and_unsafe_ids(archive):
    store, key, _ = archive
    reader = ReadOnlyArchive(store.root)
    for keys in ([], [key, key], ["../private"]):
        with pytest.raises(module.ExportMismatch):
            module.validate_exports(reader, keys)


def test_inventory_identity_change_is_rejected_without_touching_source(archive):
    store, _, item = archive
    atomic_json(
        store.root / "inventory-unique.json",
        {"videos": [{**item, "bytes": 999}], "unique_readable_videos": 1},
    )
    assert run(archive, full=True)["errors"][0]["code"] == "inventory_identity_mismatch"
