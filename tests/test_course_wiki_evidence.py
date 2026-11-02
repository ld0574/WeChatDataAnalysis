import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_course_wiki import archive as source_archive, published as source_published
from test_course_wiki_snapshot_writers import retain_frames
from test_course_wiki_synthesis import model
from wechat_decrypt_tool import course_library as library
from wechat_decrypt_tool import course_wiki as wiki
from wechat_decrypt_tool import course_wiki_coverage as coverage
from wechat_decrypt_tool import course_wiki_evidence as evidence
from wechat_decrypt_tool import course_wiki_snapshot as snapshot
from wechat_decrypt_tool import course_wiki_synthesis as synthesis
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_lesson_exports import digest
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive

archive = source_archive
published = source_published


def retain_lesson(root, lesson):
    raw = library.canonical(lesson)
    key = digest(raw)
    (root / "library/objects" / (key + ".json")).write_bytes(raw)
    path = root / "library/catalog.json"
    catalog = json.loads(path.read_bytes())
    next(r for r in catalog["lessons"] if r["id"] == lesson["id"])["artifact"] = key
    atomic_json(path, catalog)


@pytest.fixture
def long_evidence(published):
    store, video = published
    retain_frames(store, video)
    lesson = copy.deepcopy(library.Library(store.root).lesson(video))
    section = lesson["sections"][0]
    lesson["evidence"][str(section["context"][0])]["text"] = (
        "Synthetic full speech. 原样保留😀\r\n" * 150
    )
    frame = next(e for e in lesson["evidence"].values() if e["kind"] == "frame")
    frame["text"] = "Original code and OCR.\n" * 400 + "\n    return value\n"
    retain_lesson(store.root, lesson)
    prepared = evidence.create(ReadOnlyArchive(store.root), video, section["id"])
    return store, lesson, prepared


def selection(prepared, number):
    return {
        "video": prepared["video"],
        "sections": [prepared["section"]],
        "evidence_partition": prepared["partition"],
        "evidence_page": number,
    }


def test_all_characters_and_images_allocate_exactly_once_without_summaries(
    long_evidence,
):
    store, lesson, prepared = long_evidence
    manifest = evidence.read(store.root, prepared["partition"])
    assert len(manifest["pages"]) >= 4
    collected = {}
    for n in range(prepared["pages"]):
        packet = wiki.prepare(store.root, "Synthetic topic", [selection(prepared, n)])
        evidence.validate(store.root, packet)
        assert evidence.fits(packet["sources"])
        for source in packet["sources"].values():
            origin = source["text_origin"]
            collected.setdefault(origin["reference"], []).append(source)
            original = manifest["originals"][origin["reference"]]
            assert source["text"] == original["text"][origin["start"] : origin["end"]]
            assert origin["source_sha256"] == digest(library.canonical(original))
            if original["kind"] == "frame":
                assert source["image_sha256"] == original["image_sha256"]
            assert origin["semantic_boundary_verified"] is False
    assert set(collected) == set(manifest["originals"])
    for ref, pieces in collected.items():
        assert "".join(p["text"] for p in pieces) == manifest["originals"][ref]["text"]
        assert pieces[0]["text_origin"]["start"] == 0
        assert pieces[-1]["text_origin"]["end"] == len(
            manifest["originals"][ref]["text"]
        )
    assert (
        evidence.create(
            ReadOnlyArchive(store.root), prepared["video"], prepared["section"]
        )
        == prepared
    )
    with pytest.raises(ValueError, match="bounded context"):
        wiki.prepare(
            store.root,
            "Original whole window",
            [{"video": lesson["id"], "sections": [prepared["section"]]}],
        )


@pytest.mark.parametrize("text", ["", " ", "\r\n\t", "😀汉字\r\n" * 1200, "x" * 9001])
def test_ranges_preserve_empty_whitespace_unicode_and_unbroken_lines(text):
    ranges = evidence.spans(text)
    assert "".join(text[a:b] for a, b in ranges) == text
    assert ranges[0][0] == 0 and ranges[-1][1] == len(text)
    assert all(b - a <= evidence.MAX_PIECE for a, b in ranges)
    assert all(b == c for (_, b), (c, _) in zip(ranges, ranges[1:]))


@pytest.mark.parametrize("mutation", ["missing", "overlap", "text", "accepted"])
def test_rehashed_manifest_cannot_hide_source_loss_or_falsify_acceptance(
    long_evidence, mutation
):
    store, _, prepared = long_evidence
    value = evidence.read(store.root, prepared["partition"])
    if mutation == "missing":
        value["pages"][0].pop()
    elif mutation == "overlap":
        value["pages"][0][0]["end"] -= 1
    elif mutation == "text":
        next(iter(value["originals"].values()))["text"] += "Changed"
    else:
        value["topic_coverage_verified"] = True
    key = digest(library.canonical(value))[:32]
    folder = store.root / "wiki/evidence-partitions" / key
    folder.mkdir()
    atomic_json(folder / "manifest.json", value)
    with pytest.raises(ValueError, match="incomplete_or_changed_allocation"):
        evidence.read(store.root, key)


@pytest.mark.parametrize("number", [-1, True, 100000])
def test_invalid_page_is_not_silently_treated_as_a_whole_window(long_evidence, number):
    store, _, prepared = long_evidence
    with pytest.raises(ValueError, match="invalid_page_scope"):
        wiki.prepare(store.root, "Synthetic topic", [selection(prepared, number)])


def test_wrong_parent_and_changed_original_reject_old_partition(long_evidence):
    store, lesson, prepared = long_evidence
    selected = selection(prepared, 0)
    selected["sections"] = [lesson["sections"][1]["id"]]
    with pytest.raises(ValueError, match="invalid_page_scope"):
        wiki.prepare(store.root, "Synthetic topic", [selected])
    lesson["evidence"][str(lesson["sections"][0]["context"][0])]["text"] += "Changed"
    retain_lesson(store.root, lesson)
    with pytest.raises(ValueError, match="original_lesson_changed"):
        evidence.read(store.root, prepared["partition"])


def make_plan(prepared):
    return {
        "title": "Synthetic allocated evidence topic",
        "introduction": [selection(prepared, 0)],
        "chapters": [
            {
                "heading": f"Part {n}",
                "focus": f"Synthetic task {n}",
                "selections": [selection(prepared, n)],
            }
            for n in range(4)
        ],
        "exercises": [{"chapter": n, "focus": f"Synthetic task {n}"} for n in range(4)],
    }


@pytest.mark.parametrize("temporal", [False, True])
def test_allocated_source_writing_coverage_and_isolated_snapshot(
    long_evidence, tmp_path, monkeypatch, temporal
):
    store, lesson, prepared = long_evidence
    if temporal:
        prepared = evidence.create(
            ReadOnlyArchive(store.root),
            lesson["id"],
            prepared["section"],
            temporal=True,
        )
    model(monkeypatch)
    result = synthesis.generate(ReadOnlyArchive(store.root), make_plan(prepared))
    publication = wiki.Wiki(store.root).article(result["id"])
    writer_manifest = json.loads(
        (store.root / "wiki/jobs" / result["id"] / "manifest.json").read_bytes()
    )
    assert writer_manifest["recipe"]["original_range_notice"] == evidence.RANGE_NOTICE
    assert publication["packet"]["evidence_partitions"] == {
        prepared["partition"]: [0, 1, 2, 3]
    }
    ledger = coverage.build(ReadOnlyArchive(store.root))
    assert ledger["summary"]["courses_with_draft_citations"] == 1
    assert not ledger["source_deletion_allowed"]
    backup = snapshot.create(store.root, tmp_path / "backup", reserve_gib=0)
    restored = snapshot.restore(backup, tmp_path / "restored", reserve_gib=0)
    assert (
        evidence.read(restored, prepared["partition"])["originals"]
        == evidence.read(store.root, prepared["partition"])["originals"]
    )
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("wiki_snapshot_isolated_reader.py")),
            str(restored),
            str(store.root),
            str(Path(snapshot.__file__).parent),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert json.loads(process.stdout)["original_root_access_attempts"] == 0
    changed_lesson = copy.deepcopy(lesson)
    next(iter(changed_lesson["evidence"].values()))["text"] += "New source revision"
    retain_lesson(store.root, changed_lesson)
    assert wiki.Wiki(store.root).article(result["id"]) == publication
    # A new freeze must reject the stale plan, while retained publications and
    # their portable provenance remain readable independently of today's notes.
    with pytest.raises(ValueError, match="original_lesson_changed"):
        wiki.prepare(store.root, "Stale plan", [selection(prepared, 0)])
    revised_backup = snapshot.create(
        store.root, tmp_path / "after-refresh", reserve_gib=0
    )
    assert snapshot.smoke(revised_backup)["reader_routes_verified"]
    assert (
        revised_backup
        / "library/objects"
        / (publication["packet"]["bindings"][lesson["id"]]["artifact"] + ".json")
    ).exists()
    original = copy.deepcopy(publication["packet"])
    first = next(iter(original["sources"].values()))
    first["text"] += "Fabricated extension"
    with pytest.raises(ValueError, match="frozen_ranges_changed"):
        evidence.validate(store.root, original)
    unbound = copy.deepcopy(publication["packet"])
    unbound.pop("evidence_partitions")
    with pytest.raises(ValueError, match="frozen_ranges_changed"):
        evidence.validate(store.root, unbound)
    dependency = (
        restored / "wiki/evidence-partitions" / prepared["partition"] / "manifest.json"
    )
    dependency.unlink()
    manifest_path = restored / snapshot.MANIFEST
    value = json.loads(manifest_path.read_bytes())
    value["files"] = [
        r
        for r in value["files"]
        if r["path"] != dependency.relative_to(restored).as_posix()
    ]
    atomic_json(manifest_path, value)
    with pytest.raises(FileNotFoundError):
        snapshot.verify(restored)


def test_temporal_partition_keeps_speech_and_frames_in_time_order_without_dropping_characters():
    video = "a" * 24
    sources = {
        str(n): {
            "id": n,
            "kind": "speech" if n > 0 else "frame",
            "start": start,
            "end": start + 5,
            "text": str(n) * 500,
        }
        for n, start in [(1, 0), (2, 20), (3, 40), (-1, 10), (-2, 30), (-3, 50)]
    }
    lesson = {
        "id": video,
        "title": "Synthetic lesson",
        "evidence": sources,
        "sections": [{"id": "section-0", "start": 0, "end": 60, "context": [1, 2]}],
    }
    legacy = evidence.recipe(lesson, video, "section-0")
    temporal = evidence.recipe(lesson, video, "section-0", temporal=True)
    assert video + ":3" not in legacy["originals"]
    assert video + ":3" in temporal["originals"]
    assert list(temporal["originals"]) == [
        video + ":" + str(n) for n in [1, -1, 2, -2, 3, -3]
    ]
    actual = {}
    for page in temporal["pages"]:
        for key, source in evidence.materialize(temporal["originals"], page).items():
            origin = source["text_origin"]
            actual.setdefault(origin["reference"], []).append(source["text"])
    assert {k: "".join(v) for k, v in actual.items()} == {
        k: v["text"] for k, v in temporal["originals"].items()
    }
    assert temporal["temporal_proximity_is_not_semantic_alignment"] is True
    assert temporal["semantic_boundary_verified"] is False
    assert temporal["source_deletion_allowed"] is False


def test_new_temporal_freeze_leaves_existing_partition_and_default_identity_unchanged(
    long_evidence,
):
    store, lesson, legacy = long_evidence
    path = (
        store.root / "wiki/evidence-partitions" / legacy["partition"] / "manifest.json"
    )
    before = path.read_bytes()
    temporal = evidence.create(
        ReadOnlyArchive(store.root), lesson["id"], legacy["section"], temporal=True
    )
    assert temporal["partition"] != legacy["partition"]
    assert path.read_bytes() == before
    assert (
        evidence.create(ReadOnlyArchive(store.root), lesson["id"], legacy["section"])
        == legacy
    )
    assert evidence.read(store.root, legacy["partition"])["policy"] == evidence.POLICY
    assert (
        evidence.read(store.root, temporal["partition"])["policy"]
        == evidence.TEMPORAL_POLICY
    )
