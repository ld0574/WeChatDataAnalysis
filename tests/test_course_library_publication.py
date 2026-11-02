"""A live or incomplete producer must not erase previously published lessons."""

import json

import pytest

from test_course_wiki import archive as source_archive
from test_course_wiki import published as source_published
from wechat_decrypt_tool import course_library as library
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive

archive = source_archive
published = source_published


@pytest.mark.parametrize("change", ["missing_export", "pending_ocr", "removed_source"])
def test_refresh_rejects_lost_previous_sources_without_replacing_catalog(published, change):
    store, key = published
    catalog = store.root / "library/catalog.json"
    before = catalog.read_bytes()
    if change == "missing_export":
        (store.root / "lessons" / key / "evidence.json").unlink()
    elif change == "pending_ocr":
        with store.write() as db:
            db.execute("UPDATE frames SET status='pending' WHERE video=?", (key,))
    else:
        inventory = json.loads((store.root / "inventory-unique.json").read_bytes())
        replacement = {**inventory["videos"][0], "path": str(store.root / "replacement.mp4")}
        atomic_json(store.root / "inventory-unique.json", {
            "videos": [replacement], "unique_readable_videos": 1,
        })
    with pytest.raises(ValueError, match="library_publication_would_remove_existing_sources"):
        library.publish(ReadOnlyArchive(store.root))
    assert catalog.read_bytes() == before
    assert library.Library(store.root).lesson(key)["id"] == key


def test_export_changed_after_validation_keeps_previous_catalog(published, monkeypatch):
    store, key = published
    catalog = store.root / "library/catalog.json"
    before = catalog.read_bytes()
    validate = library.validate_exports

    def changed_after_validation(archive):
        result = validate(archive)
        path = store.root / "lessons" / key / "evidence.json"
        path.write_bytes(path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(library, "validate_exports", changed_after_validation)
    with pytest.raises(ValueError, match="library_publication_would_remove_existing_sources"):
        library.publish(ReadOnlyArchive(store.root))
    assert catalog.read_bytes() == before


def test_refresh_retains_completed_lessons_while_new_source_is_pending(published):
    store, key = published
    inventory = json.loads((store.root / "inventory-unique.json").read_bytes())
    pending = {**inventory["videos"][0], "path": str(store.root / "pending.mp4")}
    inventory["videos"].append(pending)
    inventory["unique_readable_videos"] = 2
    atomic_json(store.root / "inventory-unique.json", inventory)
    result = library.publish(ReadOnlyArchive(store.root))
    assert result["published"] == 1 and result["requested"] == 2
    assert len(result["unpublished"]) == 1
    assert library.Library(store.root).lesson(key)["id"] == key
