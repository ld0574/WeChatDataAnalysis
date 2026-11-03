import base64
import io
from contextlib import nullcontext

import pytest
from PIL import Image

from test_course_pdf_queue import prepare_fixture
from test_course_pdf_selected_pages import page_history
from wechat_decrypt_tool import course_pdf_queue_resume as resume
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_pdf_retry import RetryReportStore


@pytest.fixture
def paused_scope(tmp_path, monkeypatch):
    store, prefix, binding, independent, calls, chat = prepare_fixture(
        tmp_path, monkeypatch
    )

    def interrupted(model, messages):
        with Image.open(
            io.BytesIO(base64.b64decode(messages[-1]["images"][0]))
        ) as image:
            page = image.getpixel((0, 0))[0]
        if page == 5:
            raise InterruptedError("Fixture user pause after page three.")
        return chat(model, messages)

    plan = store.root / (prefix + "-plan.json")
    with pytest.raises(InterruptedError):
        resume.queue.run_pdf_ocr(
            RetryReportStore(store, prefix),
            [plan],
            tmp_path / "originals",
            binding["model"],
            interrupted,
            lambda _: nullcontext(),
            poppler=resume.Path(binding["renderer"]),
        )
    progress_path = store.root / (prefix + "-ocr-progress.json")
    progress = resume.queue.read(progress_path)
    assert progress["stage"] == "paused" and progress["processed"] == 1
    progress["pid"] = 99999999
    atomic_json(progress_path, progress)
    return store, prefix, binding, independent, calls, chat


def test_partial_page_is_validated_backed_up_and_reused_without_model_or_history_replay(
    paused_scope, tmp_path, monkeypatch
):
    store, prefix, binding, independent, calls, chat = paused_scope
    old_page = page_history(store, 3)
    old_reports = {
        path: path.read_bytes() for path in store.root.glob(prefix + "-*.json")
    }
    assert calls == [1, 3]
    prepared = resume.prepare(
        store.root,
        prefix,
        tmp_path / "continuation-inputs",
        reserve_gib=0,
        rerender=independent,
    )
    assert prepared["committed_pages"] == 1 and prepared["page_occurrences"] == 2
    assert calls == [1, 3] and page_history(store, 3) == old_page
    resume.queue.verify_backup(prepared)
    monkeypatch.setattr(resume.queue, "checkpoint", lambda root, **kwargs: None)
    monkeypatch.setattr(resume.queue, "local_chat", chat)
    monkeypatch.setattr(resume.queue, "gpu_lease", lambda store: nullcontext())
    hooks = []
    monkeypatch.setattr(resume.sys, "addaudithook", hooks.append)
    actual_backup = resume.backup_batch
    monkeypatch.setattr(
        resume,
        "backup_batch",
        lambda *args, **kwargs: actual_backup(
            *args, **kwargs, minimum_free_gib=0, rerender=independent
        ),
    )
    resume.worker(store.root, prefix)
    assert calls == [1, 3, 5] and page_history(store, 3) == old_page
    assert all(path.read_bytes() == raw for path, raw in old_reports.items())
    proof = resume.queue.read(store.root / (prefix + "-validation.json"))
    assert proof["page_occurrences"] == 2
    assert proof["original_committed_pages_preserved"] == 1
    assert (
        proof["resumed_from_validated_partial_scope"]
        and not proof["source_deletion_allowed"]
    )
    assert resume.queue.verify_backup(proof)
    with pytest.raises(PermissionError, match="original_course_read_forbidden"):
        hooks[0]("open", (str(tmp_path / "originals/anything"),))


@pytest.mark.parametrize(
    "changed",
    ["prior-cache", "original-progress", "resume-runtime", "input-backup", "history"],
)
def test_changed_partial_inputs_stop_before_new_inference(
    paused_scope, tmp_path, monkeypatch, changed
):
    store, prefix, binding, independent, calls, _ = paused_scope
    prepared = resume.prepare(
        store.root,
        prefix,
        tmp_path / "continuation-inputs",
        reserve_gib=0,
        rerender=independent,
    )
    monkeypatch.setattr(resume.queue, "checkpoint", lambda root, **kwargs: None)
    if changed == "prior-cache":
        image = next(
            (store.root / "document-visuals").glob("*-scale2000-dpi150/*/00003.png")
        )
        image.write_bytes(b"changed")
    elif changed == "original-progress":
        path = store.root / (prefix + "-ocr-progress.json")
        row = resume.queue.read(path)
        row["processed"] = 0
        atomic_json(path, row)
    elif changed == "resume-runtime":
        path = store.root / (prefix + "-resume-runtime.py")
        path.write_text("changed")
    elif changed == "input-backup":
        manifest = resume.queue.read(resume.Path(prepared["backup"]) / "manifest.json")
        path = resume.Path(prepared["backup"]) / manifest["files"][0]["path"]
        path.write_bytes(b"changed")
    else:
        with store.write() as db:
            db.execute(
                "UPDATE documents SET text='changed fixture' WHERE source_key='visual  page 3'"
            )
    with pytest.raises(ValueError):
        resume.worker(store.root, prefix)
    assert calls == [1, 3]
    assert (
        resume.queue.read(store.root / (prefix + "-resume-status.json"))["state"]
        == "failed"
    )


def test_uncommitted_image_is_rerendered_and_invalid_pixels_block_preparation(
    paused_scope, tmp_path
):
    store, prefix, _, independent, calls, _ = paused_scope
    path = next(
        (store.root / "document-visuals").glob("*-scale2000-dpi150/*/00005.png")
    )
    Image.new("RGB", (64, 32), "red").save(path)
    with pytest.raises(ValueError, match="uncommitted_page_pixels_changed"):
        resume.prepare(
            store.root,
            prefix,
            tmp_path / "continuation-inputs",
            reserve_gib=0,
            rerender=independent,
        )
    assert calls == [1, 3]
    assert not (store.root / (prefix + "-resume-binding.json")).exists()


def test_terminal_failure_cannot_be_retried_with_a_fresh_progress_file(
    paused_scope, tmp_path, monkeypatch
):
    store, prefix, _, independent, calls, _ = paused_scope
    resume.prepare(
        store.root,
        prefix,
        tmp_path / "continuation-inputs",
        reserve_gib=0,
        rerender=independent,
    )
    atomic_json(store.root / (prefix + "-resume-status.json"), {"state": "failed"})
    monkeypatch.setattr(resume.queue, "checkpoint", lambda root, **kwargs: None)
    with pytest.raises(ValueError, match="previous_terminal_failure_requires_review"):
        resume.worker(store.root, prefix)
    assert calls == [1, 3]
