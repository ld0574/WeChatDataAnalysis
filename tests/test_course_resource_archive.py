import hashlib
import json

import pytest

from wechat_decrypt_tool.course_knowledge import KnowledgeStore
from wechat_decrypt_tool.course_resource_archive import archive_resources


def test_portable_resources_deduplicate_bytes_and_retain_silent_motion(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name, data in [
        ("a.txt", b"read only"),
        ("same.txt", b"read only"),
        ("sound.mp4", b"large audio video"),
        ("demo.mp4", b"silent demo"),
        ("lesson.mp4.downloading", b"pending recovery"),
    ]:
        (source / name).write_bytes(data)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps({"excluded_without_audio": [{"path": str(source / "demo.mp4")}]})
    )
    store = KnowledgeStore(tmp_path / "kb")
    for _ in range(2):
        result = archive_resources(store, source, inventory, reserve_gib=0)
    assert (
        len(result["completed"]) == 3
        and len(result["pending"]) == 1
        and not result["errors"]
    )
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    assert len(list((store.root / "resources/objects").glob("*/*"))) == 2
    for row in result["completed"]:
        assert (
            hashlib.sha256((store.root / row["object"]).read_bytes()).hexdigest()
            == row["sha256"]
        )
    (source / "a.txt").write_bytes(b"changed")
    result = archive_resources(store, source, inventory, reserve_gib=0)
    assert len(result["errors"]) == 1 and "changed" in result["errors"][0]["error"]


def test_modified_object_is_preserved_and_reported(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_bytes(b"original")
    inventory = tmp_path / "inventory.json"
    inventory.write_text("{}")
    store = KnowledgeStore(tmp_path / "kb")
    result = archive_resources(store, source, inventory, reserve_gib=0)
    obj = store.root / result["completed"][0]["object"]
    obj.write_bytes(b"tampered")
    result = archive_resources(store, source, inventory, reserve_gib=0)
    assert len(result["errors"]) == 1 and obj.read_bytes() == b"tampered"


def test_budget_refuses_copy_before_writing_objects(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_bytes(b"original")
    inventory = tmp_path / "inventory.json"
    inventory.write_text("{}")
    store = KnowledgeStore(tmp_path / "kb")
    with pytest.raises(InterruptedError, match="budget"):
        archive_resources(store, source, inventory, budget_gib=0, reserve_gib=0)
    assert not (store.root / "resources/objects").exists()
