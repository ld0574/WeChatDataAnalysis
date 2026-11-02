import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from test_course_wiki_documents import archive as source_archive
from test_course_wiki_documents import courseware as source_courseware
from test_course_wiki_documents import draft, packet
from test_course_wiki_documents import published as source_published
from wechat_decrypt_tool import course_library as library
from wechat_decrypt_tool import course_wiki as wiki
from wechat_decrypt_tool import course_wiki_snapshot as snapshot
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive

archive = source_archive
courseware = source_courseware
published = source_published


@pytest.fixture
def publication(courseware):
    store, key, *_ = courseware
    evidence = json.loads((store.root / "lessons" / key / "evidence.json").read_bytes())
    for row in evidence["frames"]:
        Image.new("RGB", (24, 18), "white").save(store.root / row["path"], "JPEG")
    library.publish(ReadOnlyArchive(store.root))
    p = packet(courseware)
    wiki.publish_draft(store, draft(p), p, "e" * 32, "fixture", "fixture-digest")
    return store.root


def test_restore_routes_without_original_archive(publication, tmp_path):
    backup = snapshot.create(publication, tmp_path / "backup", reserve_gib=0)
    manifest = json.loads((backup / snapshot.MANIFEST).read_bytes())
    assert not any(
        Path(row["path"]).suffix in {".sqlite3", ".sqlite", ".db", ".wal"}
        or "gpu.lock" in row["path"]
        for row in manifest["files"]
    )
    shutil.rmtree(publication)
    restored = snapshot.restore(backup, tmp_path / "restored", reserve_gib=0)
    result = snapshot.smoke(restored)
    assert result["article_versions"] == result["current_topics"] == 1
    assert result["article_image_routes_verified"] == 1
    assert not result["database_required"]
    client = TestClient(library.create_app(snapshot.PublishedStore(restored)))
    assert (
        client.get("/wiki/api/catalog").json()["published_snapshot"]["id"]
        == manifest["id"]
    )
    assert client.get("/wiki/api/coverage").status_code == 503
    assert (
        client.get("/wiki/api/courseware/search", params={"q": "synthetic"}).status_code
        == 503
    )
    source = client.get("/library-assets/course_library.js").text
    assert "snapshotNotice(versions)" in source and "snapshotNotice(wiki)" in source
    assert client.get("/library-assets/course_learning.js").status_code == 200
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("wiki_snapshot_isolated_reader.py")),
            str(restored),
            str(publication),
            str(Path(snapshot.__file__).parent),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    isolated = json.loads(process.stdout)
    assert isolated["isolated_reader_verified"]
    assert isolated["original_root_access_attempts"] == 0


def test_constrained_reports_survive_actual_isolated_reader_restore(
    publication, tmp_path, monkeypatch
):
    from wechat_decrypt_tool import course_pdf_ocr
    from wechat_decrypt_tool import course_wiki_review as review
    from wechat_decrypt_tool import course_wiki_completeness as omissions
    from test_course_wiki_completeness import response

    monkeypatch.setattr(
        course_pdf_ocr, "local_model_digest", lambda _: "local-fixed-weights"
    )
    monkeypatch.setattr(
        omissions, "local_model_digest", lambda _: "local-fixed-weights"
    )

    def chat(model, messages, *, schema):
        context = json.loads(messages[1]["content"])
        if "windows" in context:
            return json.dumps(response(context))
        return json.dumps(
            {
                "reviews": [
                    {
                        "target": target["target"],
                        "verdict": "partial",
                        "reason": "Synthetic condition gap.",
                        "evidence": [
                            {
                                "witness": next(
                                    key
                                    for key, w in context["witnesses"].items()
                                    if w["source"] in target["citations"]
                                )
                            }
                        ],
                    }
                    for target in context["targets"]
                ]
            }
        )

    from wechat_decrypt_tool import course_wiki_writer as writer

    monkeypatch.setattr(review, "local_chat", chat)
    monkeypatch.setattr(writer, "local_chat", chat)
    article_id = "e" * 32
    review.review(ReadOnlyArchive(publication), article_id, constrained_evidence=True)
    omissions.review(
        ReadOnlyArchive(publication), article_id, constrained_evidence=True
    )
    backup = snapshot.create(
        publication, tmp_path / "constrained-backup", reserve_gib=0
    )
    manifest = json.loads((backup / snapshot.MANIFEST).read_bytes())
    assert any(
        row["path"].endswith("/course_wiki_check_constraints.py")
        for row in manifest["files"]
    )
    restored = snapshot.restore(
        backup, tmp_path / "constrained-restored", reserve_gib=0
    )
    assert snapshot.smoke(restored)["complete_model_reports"] == 2
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("wiki_snapshot_isolated_reader.py")),
            str(restored),
            str(publication),
            str(Path(snapshot.__file__).parent),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    isolated = json.loads(process.stdout)
    assert (
        isolated["isolated_reader_verified"]
        and isolated["original_root_access_attempts"] == 0
    )


def test_native_visual_report_survives_isolated_restore_and_requires_checkpoints(
    publication, tmp_path, monkeypatch
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from wechat_decrypt_tool import course_wiki_visual_review as visual

    @contextmanager
    def slot(*args):
        yield

    monkeypatch.setattr(visual, "slot", slot)
    monkeypatch.setattr(visual, "local_model_digest", lambda model: "synthetic-weights")

    def chat(model, messages, *, schema):
        context = json.loads(messages[1]["content"])
        assert messages[1]["images"]
        value = {
            "target": context["target"],
            "relevance": "partial",
            "caption_support": "partial",
            "reason": "Synthetic incomplete figure.",
            "observations": [
                {
                    "description": "Synthetic visible figure.",
                    "visible_text": "",
                    "box": [0, 0, 1000, 1000],
                }
            ],
            "missing_visual_information": ["Synthetic omitted condition."],
            "limitations": ["Not independent review."],
        }
        return json.dumps(value), {
            "prompt_eval_count": 1000,
            "context_length": 16384,
            "size_vram": 8 * 1024**2,
        }

    monkeypatch.setattr(visual, "vision_chat", chat)
    article = "e" * 32
    result = visual.review(
        SimpleNamespace(root=publication, checkpoint=lambda: None), article
    )
    backup = snapshot.create(publication, tmp_path / "visual-backup", reserve_gib=0)
    restored = snapshot.restore(backup, tmp_path / "visual-restored", reserve_gib=0)
    assert visual.report(restored, article, job=result["job"]) == result
    proof = snapshot.verify(restored)
    assert proof["complete_model_reports"] == 1
    assert not proof["semantic_accuracy_verified"]
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("wiki_snapshot_isolated_reader.py")),
            str(restored),
            str(publication),
            str(Path(snapshot.__file__).parent),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert json.loads(process.stdout)["original_root_access_attempts"] == 0
    relative = (
        f"wiki/visual-review-jobs/{result['job']}/{result['results'][0]['target']}.json"
    )
    manifest = json.loads((restored / snapshot.MANIFEST).read_bytes())
    manifest["files"] = [r for r in manifest["files"] if r["path"] != relative]
    atomic_json(restored / snapshot.MANIFEST, manifest)
    (restored / relative).unlink()
    with pytest.raises((ValueError, OSError)):
        snapshot.verify(restored)


def test_bound_reports_survive_restore_and_cannot_be_omitted_from_manifest(
    publication, tmp_path, monkeypatch
):
    import copy

    from wechat_decrypt_tool import course_pdf_ocr
    from wechat_decrypt_tool import course_wiki_review as review
    from wechat_decrypt_tool.course_library import canonical
    from wechat_decrypt_tool.course_lesson_exports import digest
    from wechat_decrypt_tool.course_wiki_revision import source_crosschecks

    parent_id = "e" * 32
    parent = wiki.Wiki(publication).article(parent_id)
    monkeypatch.setattr(course_pdf_ocr, "local_model_digest", lambda model: model)

    def judge(model, messages, *, schema):
        context = json.loads(messages[1]["content"])
        return json.dumps(
            {
                "reviews": [
                    {
                        "target": t["target"],
                        "verdict": "partial",
                        "reason": "Synthetic gap.",
                        "evidence": [
                            {
                                "witness": next(
                                    k
                                    for k, w in context["witnesses"].items()
                                    if w["source"] == t["citations"][0]
                                )
                            }
                        ],
                    }
                    for t in context["targets"]
                ]
            }
        )

    monkeypatch.setattr(review, "local_chat", judge)
    jobs = [
        review.review(ReadOnlyArchive(publication), parent_id, model)["job"]
        for model in ("fixture-first", "fixture-second")
    ]
    combined, bindings = source_crosschecks(publication, parent_id, jobs)
    p = copy.deepcopy(parent["packet"])
    p["revision"] = {
        "parent_article": parent_id,
        "parent_sha256": digest(canonical(parent)),
        "review_sha256": digest(canonical(combined)),
        "review_reports": bindings,
        "independent_semantic_review": False,
    }
    wiki.publish_draft(
        ReadOnlyArchive(publication),
        parent["article"],
        p,
        "d" * 32,
        "fixture",
        "fixture-digest",
    )
    backup = snapshot.create(publication, tmp_path / "bound-backup", reserve_gib=0)
    restored = snapshot.restore(backup, tmp_path / "bound-restored", reserve_gib=0)
    assert snapshot.smoke(restored)["complete_model_reports"] == 2
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("wiki_snapshot_isolated_reader.py")),
            str(restored),
            str(publication),
            str(Path(snapshot.__file__).parent),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert json.loads(process.stdout)["original_root_access_attempts"] == 0
    receipt = f"wiki/review-jobs/{jobs[0]}/receipt.json"
    (restored / receipt).unlink()
    manifest_path = restored / snapshot.MANIFEST
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"] = [r for r in manifest["files"] if r["path"] != receipt]
    atomic_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="revision_dependency_invalid"):
        snapshot.verify(restored)


@pytest.mark.parametrize(
    "mutation",
    [
        "hash",
        "missing_manifest",
        "scope",
        "runtime",
        "escape",
        "missing_image",
        "learning",
    ],
)
def test_rejects_corrupt_or_incomplete_snapshot(publication, tmp_path, mutation):
    backup = snapshot.create(publication, tmp_path / "backup", reserve_gib=0)
    path = backup / snapshot.MANIFEST
    value = json.loads(path.read_bytes())
    if mutation == "hash":
        (backup / "README.md").write_text("corrupt")
    elif mutation == "missing_manifest":
        value["files"] = [r for r in value["files"] if r["path"] != "wiki/catalog.json"]
    elif mutation == "scope":
        value["source_deletion_allowed"] = True
    elif mutation == "runtime":
        value["runtime_source_included"] = False
    elif mutation == "escape":
        value["files"][0]["path"] = "../escape"
    elif mutation == "learning":
        dependency = next(
            r["path"]
            for r in value["files"]
            if r["path"].endswith("resources/course_learning.js")
        )
        (backup / dependency).unlink()
        value["files"] = [r for r in value["files"] if r["path"] != dependency]
    else:
        next((backup / "wiki/media").glob("*.*")).unlink()
    atomic_json(path, value)
    with pytest.raises((ValueError, OSError)):
        snapshot.verify(backup)


def test_refuses_existing_destination_and_overlapping_paths(publication, tmp_path):
    with pytest.raises(ValueError, match="overlaps"):
        snapshot.create(publication, publication / "backup", reserve_gib=0)
    backup = snapshot.create(publication, tmp_path / "backup", reserve_gib=0)
    target = tmp_path / "restored"
    target.mkdir()
    (target / "keep.txt").write_text("keep")
    with pytest.raises(ValueError, match="not_empty"):
        snapshot.restore(backup, target, reserve_gib=0)
    with pytest.raises(ValueError, match="overlaps"):
        snapshot.restore(backup, backup / "restore", reserve_gib=0)
    assert (target / "keep.txt").read_text() == "keep"


def test_old_snapshot_without_learning_module_remains_verifiable(publication, tmp_path):
    import hashlib

    backup = snapshot.create(publication, tmp_path / "legacy", reserve_gib=0)
    path = backup / snapshot.MANIFEST
    value = json.loads(path.read_bytes())
    dependency = next(
        r["path"]
        for r in value["files"]
        if r["path"].endswith("resources/course_learning.js")
    )
    (backup / dependency).unlink()
    value["files"] = [r for r in value["files"] if r["path"] != dependency]
    html_row = next(
        r for r in value["files"] if r["path"].endswith("resources/course_library.html")
    )
    html_path = backup / html_row["path"]
    text = html_path.read_text("utf-8")
    html_path.write_text(
        "\n".join(
            line
            for line in text.splitlines()
            if "/library-assets/course_learning.js" not in line
        ),
        encoding="utf-8",
    )
    html_row["bytes"] = html_path.stat().st_size
    html_row["sha256"] = hashlib.sha256(html_path.read_bytes()).hexdigest()
    atomic_json(path, value)
    snapshot.verify(backup)


def test_refuses_insufficient_space(publication, tmp_path):
    with pytest.raises(InterruptedError, match="reserve"):
        snapshot.create(publication, tmp_path / "backup", reserve_gib=1e12)


@pytest.mark.parametrize(
    "relative",
    [
        "wiki/catalog.json",
        "library/catalog.json",
        "inventory-unique.json",
    ],
)
def test_copies_captured_pointer_bytes_when_live_pointer_advances(
    publication, tmp_path, monkeypatch, relative
):
    original = snapshot.shutil.copyfile
    pointer = publication / relative
    captured = pointer.read_bytes()
    changed = False

    def change_pointer(source, target):
        nonlocal changed
        result = original(source, target)
        if not changed:
            changed = True
            pointer.write_bytes(captured + b"\n")
        return result

    monkeypatch.setattr(snapshot.shutil, "copyfile", change_pointer)
    backup = snapshot.create(publication, tmp_path / "backup", reserve_gib=0)
    assert changed and pointer.read_bytes() != captured
    assert (backup / relative).read_bytes() == captured
    assert snapshot.smoke(backup)["article_versions"] == 1


@pytest.mark.parametrize("phase", ["collection", "copy"])
def test_new_article_publication_preserves_frozen_closure(
    publication, tmp_path, monkeypatch, phase
):
    original = snapshot.shutil.copyfile
    old_catalog = (publication / "wiki/catalog.json").read_bytes()
    old_article = wiki.Wiki(publication).article("e" * 32)
    published = False

    def publish_once():
        nonlocal published
        if not published:
            published = True
            wiki.publish_draft(
                ReadOnlyArchive(publication),
                old_article["article"],
                old_article["packet"],
                "f" * 32,
                "fixture",
                "fixture-digest",
            )

    def publish_during_copy(source, target):
        result = original(source, target)
        publish_once()
        return result

    if phase == "copy":
        monkeypatch.setattr(snapshot.shutil, "copyfile", publish_during_copy)
    else:
        original_hash = snapshot.file_hash

        def publish_during_collection(path):
            publish_once()
            return original_hash(path)

        monkeypatch.setattr(snapshot, "file_hash", publish_during_collection)
    backup = snapshot.create(publication, tmp_path / "backup", reserve_gib=0)
    assert published
    assert len(wiki.Wiki(publication).catalog()["articles"]) == 2
    assert (backup / "wiki/catalog.json").read_bytes() == old_catalog
    assert snapshot.smoke(backup)["article_versions"] == 1
    assert not snapshot.verify(backup)["semantic_accuracy_verified"]


def test_changed_immutable_article_still_rejects_snapshot(
    publication, tmp_path, monkeypatch
):
    original = snapshot.shutil.copyfile
    row = wiki.Wiki(publication).catalog()["articles"][0]
    article_path = publication / "wiki/objects" / (row["object"] + ".json")
    changed = False

    def corrupt_before_copy(source, target):
        nonlocal changed
        if not changed:
            changed = True
            article_path.write_bytes(article_path.read_bytes() + b"\n")
        return original(source, target)

    monkeypatch.setattr(snapshot.shutil, "copyfile", corrupt_before_copy)
    with pytest.raises(ValueError, match="snapshot_copied_file_changed"):
        snapshot.create(publication, tmp_path / "backup", reserve_gib=0)
    assert not list((tmp_path / "backup").rglob(snapshot.MANIFEST))
