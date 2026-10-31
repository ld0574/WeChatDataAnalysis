import hashlib
import json

import pytest
from PIL import Image

from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot
from wechat_decrypt_tool.course_knowledge import KnowledgeStore
from wechat_decrypt_tool.course_visual_cache_assets import POLICY, visual_cache_assets


def cache_fixture(tmp_path):
    store = KnowledgeStore(tmp_path / "kb")
    image = store.root / "document-visuals/tiled-images" / ("b" * 64) / "0-0-200-80.png"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (200, 80), "white").save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    policy = POLICY + "/local-fixture@" + "a" * 64
    folder = (
        store.root
        / "document-visuals/tile-ocr-cache"
        / hashlib.sha256((digest + "\0" + policy).encode()).hexdigest()
    )
    folder.mkdir(parents=True)
    boxes = [[0, 0, 125, 80], [75, 0, 200, 80]]
    for box, payload in [
        ([0, 0, 200, 80], {"split": True}),
        *[(box, {"text": "accepted_fixture_region"}) for box in boxes],
    ]:
        (folder / ("-".join(map(str, box)) + ".json")).write_text(
            json.dumps({"policy": policy, "image_digest": digest, **payload}), "utf-8"
        )
    (folder / "0-0-200-80.json.rejected.json").write_text(
        '{"text":"rejected_fixture_output"}', "utf-8"
    )
    metadata = {
        "image": image.relative_to(store.root).as_posix(),
        "image_digest": digest,
        "inference_policy": policy,
        "inference_regions": boxes,
    }
    store.save_document_versions(
        [
            {
                "path": "source-fixture",
                "source_key": "tile-fixture",
                "raw_digest": "c" * 64,
                "extractor": "fixture",
                "chunks": [("fixture chars 0", "accepted_fixture_region")],
                "metadata": metadata,
            }
        ]
    )
    return store, folder, metadata


def test_portable_backup_keeps_accepted_leaves_and_split_decisions(tmp_path):
    store, folder, metadata = cache_fixture(tmp_path)
    expected = visual_cache_assets(store.root, metadata)
    assert len(expected) == 3 and not any("rejected" in x for x in expected)
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = restore_snapshot(
        snapshot["snapshot"], tmp_path / "restored", reserve_gib=0
    )
    assert visual_cache_assets(restored, metadata) == expected
    assert not list((restored / folder.relative_to(store.root)).glob("*.rejected.json"))


def test_missing_accepted_branch_or_mismatched_leaf_inventory_rejected(tmp_path):
    store, folder, metadata = cache_fixture(tmp_path)
    metadata["inference_regions"] = list(reversed(metadata["inference_regions"]))
    with pytest.raises(RuntimeError, match="leaves differ"):
        visual_cache_assets(store.root, metadata)
    metadata["inference_regions"].reverse()
    (folder / "75-0-200-80.json").unlink()
    with pytest.raises(FileNotFoundError):
        visual_cache_assets(store.root, metadata)


def test_split_flag_never_counts_as_an_accepted_leaf(tmp_path):
    store, folder, metadata = cache_fixture(tmp_path)
    leaf = folder / "0-0-125-80.json"
    value = json.loads(leaf.read_text("utf-8"))
    value["split"] = True
    leaf.write_text(json.dumps(value), "utf-8")
    with pytest.raises(RuntimeError, match="tree is incomplete"):
        visual_cache_assets(store.root, metadata)
