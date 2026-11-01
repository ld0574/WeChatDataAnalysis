import hashlib
import json
from pathlib import Path

import pytest

from wechat_decrypt_tool import course_concepts as module
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, atomic_json
from wechat_decrypt_tool.course_lesson_exports import ASR_MODEL, MODEL
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive


@pytest.fixture
def archive(tmp_path):
    store = KnowledgeStore(tmp_path / "archive")
    items, keys = [], []
    for number in range(2):
        source = tmp_path / f"lesson-{number}.mp4"
        source.write_bytes(b"synthetic source")
        item = {
            "path": str(source),
            "bytes": source.stat().st_size,
            "mtime_ns": source.stat().st_mtime_ns,
            "duration_seconds": 175,
            "audio_streams": [{"codec": "synthetic"}],
        }
        items.append(item)
        key = store.register(item)
        keys.append(key)
        folder = store.root / "lessons" / key
        folder.mkdir(parents=True)
        atomic_json(
            folder / "frames-completed.json",
            {"step": 30, "complete_visual_coverage": False},
        )
        for n in range(7):
            store.save_segment(key, n, n * 25, (n + 1) * 25, f"speech-{number}-{n}")
        with store.write() as db:
            db.execute(
                "UPDATE videos SET status='transcribed',model=? WHERE id=?",
                (ASR_MODEL, key),
            )
            db.execute(
                "INSERT INTO frames(video,time,path,description,status,model) VALUES(?,?,?,?,?,?)",
                (
                    key,
                    0,
                    f"lessons/{key}/frames/0.jpg",
                    f"screen-{number}",
                    "ready",
                    MODEL,
                ),
            )
            frame_id = db.execute(
                "SELECT id FROM frames WHERE video=?", (key,)
            ).fetchone()[0]
            segments = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM segments WHERE video=? ORDER BY n", (key,)
                )
            ]
            for window in (0, 1):
                ids = [segments[window * 6]] + ([-frame_id] if window == 0 else [])
                note = {
                    "summary": "synthetic",
                    "concepts": [
                        {
                            "name": "Shared",
                            "explanation": f"explanation-{number}-{window}",
                            "evidence": ids,
                        },
                        {
                            "name": f"Topic-{number}",
                            "explanation": "local",
                            "evidence": ids,
                        },
                    ],
                    "relations": [
                        {
                            "from": "Shared",
                            "to": f"Topic-{number}",
                            "relation": "supports",
                            "evidence": ids,
                        }
                    ],
                }
                db.execute(
                    "INSERT INTO notes VALUES(?,?,?,?,?)",
                    (key, window, json.dumps(note), MODEL, "ready"),
                )
        store.export(key)
    atomic_json(
        store.root / "inventory-unique.json",
        {"videos": items, "unique_readable_videos": 2},
    )
    return store, keys, items


def graph(archive):
    return module.build_graph(ReadOnlyArchive(archive[0].root))


def test_full_cross_course_graph_keeps_window_provenance_and_retained_text(archive):
    store, keys, items = archive
    for item in items:
        Path(item["path"]).unlink()
    before = hashlib.sha256(store.path.read_bytes()).hexdigest()
    result = graph(archive)
    assert hashlib.sha256(store.path.read_bytes()).hexdigest() == before
    assert result["full_inventory_graph_built"]
    assert result["requested_lessons"] == len(result["lessons"]) == 2
    assert not result["deferred"]
    assert len(result["concepts"]) == 3
    shared = next(n for n in result["concepts"] if n["name"] == "Shared")
    assert shared["lesson_count"] == 2
    assert {
        (o["video"], o["window"], o["explanation"]) for o in shared["occurrences"]
    } == {
        (key, w, f"explanation-{i}-{w}") for i, key in enumerate(keys) for w in (0, 1)
    }
    assert len(result["relations"]) == 2
    assert all(len(e["occurrences"]) == 2 for e in result["relations"])
    for i, key in enumerate(keys):
        occurrences = [o for o in shared["occurrences"] if o["video"] == key]
        first = next(o for o in occurrences if o["window"] == 0)
        cited = [result["evidence"][ref] for ref in first["evidence"]]
        assert {(e["kind"], e["text"]) for e in cited} == {
            ("speech", f"speech-{i}-0"),
            ("frame", f"screen-{i}"),
        }
        assert all(e["video"] == key and e["start"] == 0 for e in cited)
    assert not any(
        result[k]
        for k in (
            "semantic_equivalence_verified",
            "semantic_accuracy_verified",
            "complete_visual_coverage",
            "audio_coverage_verified",
            "source_deletion_allowed",
        )
    )


def test_missing_canonical_lesson_is_deferred_and_does_not_shrink_scope(
    archive, tmp_path
):
    store, _, items = archive
    atomic_json(
        store.root / "inventory-unique.json",
        {
            "videos": items
            + [
                {**items[0], "path": str(tmp_path / "unregistered.mp4")},
                {"audio_streams": []},
            ],
            "unique_readable_videos": 3,
        },
    )
    result = graph(archive)
    assert not result["full_inventory_graph_built"]
    assert result["requested_lessons"] == 3 and len(result["lessons"]) == 2
    assert result["excluded_noneligible_inventory_rows"] == 1
    assert [e["code"] for e in result["deferred"]] == ["unregistered_lesson"]


@pytest.mark.parametrize("damage", ["missing_window", "cross_window_citation"])
def test_invalid_lesson_does_not_leak_partial_concepts(archive, damage):
    store, keys, _ = archive
    with store.write() as db:
        if damage == "missing_window":
            db.execute("DELETE FROM notes WHERE video=? AND window=1", (keys[1],))
        else:
            row = db.execute(
                "SELECT data FROM notes WHERE video=? AND window=1", (keys[1],)
            ).fetchone()
            note = json.loads(row[0])
            first_id = db.execute(
                "SELECT id FROM segments WHERE video=? AND n=0", (keys[1],)
            ).fetchone()[0]
            note["concepts"][0]["evidence"] = [first_id]
            db.execute(
                "UPDATE notes SET data=? WHERE video=? AND window=1",
                (json.dumps(note), keys[1]),
            )
    store.export(keys[1])
    result = graph(archive)
    assert len(result["lessons"]) == 1 and result["lessons"][0]["video"] == keys[0]
    assert result["deferred"][0]["video"] == keys[1]
    assert not any(
        o["video"] == keys[1] for n in result["concepts"] for o in n["occurrences"]
    )


@pytest.mark.parametrize("change", ["mutate", "remove"])
def test_export_changed_after_gate_is_deferred(archive, monkeypatch, change):
    store, keys, _ = archive
    original = module.validate_exports

    def gate(reader):
        result = original(reader)
        path = store.root / "lessons" / keys[1] / "evidence.json"
        if change == "mutate":
            path.write_bytes(path.read_bytes() + b" ")
        else:
            path.unlink()
        return result

    monkeypatch.setattr(module, "validate_exports", gate)
    result = graph(archive)
    assert len(result["lessons"]) == 1 and not result["full_inventory_graph_built"]
    assert result["deferred"][0]["code"] == (
        "export_changed_after_validation"
        if change == "mutate"
        else "export_unavailable_after_validation"
    )


def test_inventory_change_never_replaces_previously_published_graph(
    archive, monkeypatch
):
    store, _, _ = archive
    module.write_graph(ReadOnlyArchive(store.root))
    old = {
        name: (store.root / name).read_bytes()
        for name in ("corpus-graph.json", "corpus-graph.html")
    }
    original = module.validate_exports

    def gate(reader):
        result = original(reader)
        path = store.root / "inventory-unique.json"
        path.write_bytes(path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(module, "validate_exports", gate)
    with pytest.raises(RuntimeError, match="inventory changed"):
        module.write_graph(ReadOnlyArchive(store.root))
    assert old == {name: (store.root / name).read_bytes() for name in old}


def test_identity_does_not_guess_semantic_equivalence():
    assert (
        len(
            {
                module.identity(s)
                for s in ["RAG", "rag", "RAG ", "ＲＡＧ", "检索增强生成"]
            }
        )
        == 5
    )
    assert module.identity(["a", "b", "c"]) != module.identity(["a", "c", "b"])


def test_duplicate_claim_does_not_duplicate_graph_occurrence(archive):
    store, keys, _ = archive
    before = graph(archive)
    with store.write() as db:
        raw = db.execute(
            "SELECT data FROM notes WHERE video=? AND window=0", (keys[0],)
        ).fetchone()[0]
        note = json.loads(raw)
        note["concepts"].append(note["concepts"][0])
        note["relations"].append(note["relations"][0])
        db.execute(
            "UPDATE notes SET data=? WHERE video=? AND window=0",
            (json.dumps(note), keys[0]),
        )
    store.export(keys[0])
    after = graph(archive)
    for kind in ("concepts", "relations", "evidence"):
        assert before[kind] == after[kind]


def test_offline_html_data_cannot_inject_markup_or_javascript(archive):
    value = graph(archive)
    attack = '</script><img src=x onerror="alert(1)">&\u2028\u2029'
    value["concepts"][0]["name"] = attack
    rendered = module.render_html(value)
    assert attack not in rendered
    data = rendered.split('<script id="graph-data" type="application/json">', 1)[
        1
    ].split("</script>", 1)[0]
    assert "<" not in data and ">" not in data and "&" not in data
    assert json.loads(data)["concepts"][0]["name"] == attack
    assert "textContent=text" in rendered and "innerHTML" not in rendered
    assert "http://" not in rendered and "https://" not in rendered


def test_written_graph_and_embedded_offline_view_are_identical(archive):
    store, _, _ = archive
    value = module.write_graph(ReadOnlyArchive(store.root))
    assert json.loads((store.root / "corpus-graph.json").read_text("utf-8")) == value
    text = (store.root / "corpus-graph.html").read_text("utf-8")
    data = text.split('<script id="graph-data" type="application/json">', 1)[1].split(
        "</script>", 1
    )[0]
    assert json.loads(data) == value
    assert not (store.root / "corpus-graph.html.tmp").exists()


def test_graph_rebuild_after_archive_move_requires_no_originals(archive, tmp_path):
    store, _, items = archive
    before = graph(archive)
    for item in items:
        Path(item["path"]).unlink()
    moved = tmp_path / "restored-archive"
    store.root.rename(moved)
    after = module.write_graph(ReadOnlyArchive(moved))
    assert {k: v for k, v in after.items() if k != "updated"} == {
        k: v for k, v in before.items() if k != "updated"
    }


def test_cli_complete_flag_fails_when_lesson_is_pending(archive, monkeypatch, capsys):
    store, keys, _ = archive
    (store.root / "lessons" / keys[1] / "graph.json").unlink()
    monkeypatch.setattr(
        "sys.argv", ["course_concepts", "--root", str(store.root), "--require-complete"]
    )
    assert module.main() == 2
    counts = json.loads(capsys.readouterr().out)
    assert counts["requested_lessons"] == 2 and counts["included_lessons"] == 1
    assert not counts["full_inventory_graph_built"]
