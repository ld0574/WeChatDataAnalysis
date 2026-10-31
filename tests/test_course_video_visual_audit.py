import hashlib
import json
from fractions import Fraction
from types import SimpleNamespace
from contextlib import contextmanager

import pytest
from PIL import Image

from wechat_decrypt_tool import course_video_visual_audit as module
from wechat_decrypt_tool.course_knowledge import KnowledgeStore


def synthetic_source(tmp_path):
    av = pytest.importorskip("av")
    path = tmp_path / "synthetic.mkv"
    with av.open(str(path), "w") as container:
        stream = container.add_stream("ffv1", rate=2)
        stream.width = stream.height = 256
        stream.pix_fmt = "bgr0"
        for index in range(130):
            image = Image.new("RGB", (256, 256), "black")
            if 20 <= index < 22:
                image.paste("red", (20, 20, 100, 100))
            if index >= 60:
                image.putpixel((100, 100), (255, 255, 255))
            frame = av.VideoFrame.from_image(image)
            frame.pts = index
            frame.time_base = Fraction(1, 2)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    store = KnowledgeStore(tmp_path / "kb")
    key = store.register({"path": str(path), "duration_seconds": 65})
    return store, key, path


def test_transient_scene_and_small_native_change_missed_by_sampling(tmp_path):
    store, key, path = synthetic_source(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report = module.audit_window(store, key, seconds=65)
    assert report["decoded_frames"] == 130
    assert report["sample_anchors_considered"] == 3
    assert report["sample_anchors_kept"] == 1
    assert report["near_static_skips_with_different_native_pixels"] == 2
    assert report["unique_rgb_images_absent_from_kept_anchors"] == 2
    assert report["frames_with_rgb_absent_from_kept_anchors"] == 72
    assert not report["complete_visual_coverage"]
    assert not report["source_deletion_allowed"]
    assert not report["full_source_hash_verified"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    saved = store.root / "video-visual-audits" / f"{key}-0-65.json"
    assert json.loads(saved.read_text("utf-8")) == report
    assert len(list(saved.parent.iterdir())) == 1
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM frames").fetchone()[0] == 0


def test_bounded_window_keeps_exact_pts_and_observes_end_boundary(tmp_path):
    store, key, _ = synthetic_source(tmp_path)
    report = module.audit_window(store, key, start=30, seconds=2)
    assert report["decoded_frames"] == 4
    assert report["first_time"] == [30, 1]
    assert report["last_time"] == [63, 2]
    assert report["boundary_frame_observed"]
    assert all(
        Fraction(*r["time"]) == r["pts"] * Fraction(*r["time_base"])
        for r in report["frames"]
    )


def test_source_changed_or_limit_hit_does_not_publish_success(tmp_path, monkeypatch):
    store, key, path = synthetic_source(tmp_path)
    monkeypatch.setattr(module, "MAX_FRAMES", 2)
    with pytest.raises(RuntimeError, match="limit"):
        module.audit_window(store, key, seconds=60)
    assert not (store.root / "video-visual-audits").exists()
    path.write_bytes(path.read_bytes() + b"source changed")
    with pytest.raises(RuntimeError, match="identity"):
        module.audit_window(store, key, seconds=60)
    assert not (store.root / "video-visual-audits").exists()


def test_read_only_diagnostic_runs_during_an_existing_writer_transaction(tmp_path):
    store, key, _ = synthetic_source(tmp_path)
    with store.write() as writer:
        writer.execute("UPDATE videos SET status='pending' WHERE id=?", (key,))
        reader = module.ReadOnlyArchive(store.root)
        result = module.audit_window(reader, key, seconds=1)
        assert result["decoded_frames"] == 2
        with reader.connect() as db:
            with pytest.raises(
                __import__("sqlite3").OperationalError, match="readonly"
            ):
                db.execute("DELETE FROM videos")


def test_read_only_archive_does_not_create_missing_database(tmp_path):
    with pytest.raises(ValueError, match="existing course archive"):
        module.ReadOnlyArchive(tmp_path / "absent")
    assert not (tmp_path / "absent").exists()


def test_premature_native_eof_is_explicit(tmp_path):
    store, key, _ = synthetic_source(tmp_path)
    with store.write() as db:
        db.execute("UPDATE videos SET duration=180 WHERE id=?", (key,))
    with pytest.raises(RuntimeError, match="ended before"):
        module.audit_window(store, key, seconds=90)
    assert not (store.root / "video-visual-audits").exists()


def test_diagnostic_is_portable_without_implying_video_is_archived(tmp_path):
    from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot

    store, key, path = synthetic_source(tmp_path)
    report = module.audit_window(store, key, seconds=1)
    backup = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    path.unlink()
    destination = restore_snapshot(
        backup["snapshot"], tmp_path / "restored", reserve_gib=0
    )
    saved = destination / "video-visual-audits" / f"{key}-0-1.json"
    assert json.loads(saved.read_text("utf-8")) == report
    assert not report["complete_visual_coverage"]
    assert not list(destination.rglob("*.mkv"))


@pytest.mark.parametrize("timestamps", [[None], [0, 0], [1, 0]])
def test_missing_or_nonmonotonic_timestamps_do_not_publish(
    tmp_path, monkeypatch, timestamps
):
    av = pytest.importorskip("av")
    source = tmp_path / "placeholder.mp4"
    source.write_bytes(b"synthetic source")
    store = KnowledgeStore(tmp_path / "kb")
    key = store.register({"path": str(source), "duration_seconds": 65})

    @contextmanager
    def decoder(*args, **kwargs):
        frames = [
            SimpleNamespace(
                pts=pts,
                time_base=Fraction(1),
                width=64,
                height=64,
                to_image=lambda: Image.new("RGB", (64, 64), "black"),
            )
            for pts in timestamps
        ]
        yield SimpleNamespace(
            streams=SimpleNamespace(video=[SimpleNamespace(thread_count=0)]),
            decode=lambda **kwargs: iter(frames),
        )

    monkeypatch.setattr(av, "open", decoder)
    with pytest.raises(RuntimeError, match="timestamp"):
        module.audit_window(store, key, seconds=60)
    assert not (store.root / "video-visual-audits").exists()


@pytest.mark.parametrize(
    "start,seconds", [(-30, 60), (1, 60), (0, 301), (0, 0), (0, True)]
)
def test_invalid_or_unbounded_windows_are_rejected(tmp_path, start, seconds):
    store = KnowledgeStore(tmp_path)
    with pytest.raises(ValueError, match="bounded window"):
        module.audit_window(store, "a" * 24, start=start, seconds=seconds)
