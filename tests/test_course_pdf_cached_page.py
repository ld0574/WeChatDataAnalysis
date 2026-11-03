import pytest

from wechat_decrypt_tool import course_pdf_cached_page as module
from test_course_pdf_selected_pages import page_history, retained_fixture


def accepted_uncommitted_fixture(tmp_path, monkeypatch):
    store, renderer, independent, plan, run, _, calls, _ = retained_fixture(
        tmp_path, monkeypatch
    )
    selected = plan([3], "selected.json")
    excluded = plan([1, 5], "excluded.json")

    def writer_busy(_):
        raise RuntimeError("Another course archive operation still holds writer.lock")

    with monkeypatch.context() as patch:
        patch.setattr(store, "save_document_versions", writer_busy)
        result = run(selected, "old-failure")
    assert result["processed"] == 0 and len(result["errors"]) == 1
    assert all(not rows for rows in page_history(store, 3).values())
    return (
        store,
        renderer,
        independent,
        module.json.loads(selected.read_bytes()),
        module.json.loads(excluded.read_bytes()),
        calls,
    )


def run_recovery(
    store,
    renderer,
    independent,
    plan,
    excluded,
    callback=lambda _: None,
    prefix="pdf-cached-test",
):
    return module.recover(
        store,
        plan,
        renderer,
        model="local-fixture",
        model_digest="c" * 64,
        excluded_plans=[excluded],
        before_commit=callback,
        prefix=prefix,
        rerender=independent,
    )


def test_writer_failure_cache_can_be_committed_without_repeating_inference(
    tmp_path, monkeypatch
):
    store, renderer, independent, plan, excluded, calls = accepted_uncommitted_fixture(
        tmp_path, monkeypatch
    )
    old = {p.name: p.read_bytes() for p in store.root.glob("old-failure*.json")}
    proofs = []
    proof = run_recovery(store, renderer, independent, plan, excluded, proofs.append)
    assert calls == [3] and proof["model_calls"] == proof["gpu_lease_calls"] == 0
    assert proofs == [proof] and proof["native_pages"] == 5 and proof["page"] == 3
    assert len(page_history(store, 3)["documents"]) == 1
    assert {p.name: p.read_bytes() for p in store.root.glob("old-failure*.json")} == old
    from wechat_decrypt_tool.course_pdf_validation import verify_pdf_archive

    result = verify_pdf_archive(
        store,
        store.root / "selected.json",
        renderer,
        rerender=independent,
        report_path=store.root / "pdf-cached-test-ocr-report.json",
        result_name="cached-result-validation.json",
    )
    assert result["all_requested_pdf_page_occurrences_verified"]
    with pytest.raises(ValueError, match="report_already_exists"):
        run_recovery(store, renderer, independent, plan, excluded)


def test_active_queue_overlap_and_missing_queue_proof_are_rejected(
    tmp_path, monkeypatch
):
    store, renderer, independent, plan, _, _ = accepted_uncommitted_fixture(
        tmp_path, monkeypatch
    )
    with pytest.raises(ValueError, match="overlaps_active_queue"):
        run_recovery(store, renderer, independent, plan, plan)
    with pytest.raises(ValueError, match="active_queue_scopes_required"):
        module.recover(
            store,
            plan,
            renderer,
            model="local-fixture",
            model_digest="c" * 64,
            excluded_plans=[],
            before_commit=lambda _: None,
            prefix="pdf-test",
        )
    assert all(not rows for rows in page_history(store, 3).values())


@pytest.mark.parametrize(
    "problem", ["backup-failed", "asset-changed", "duplicate-commit"]
)
def test_precommit_failure_or_concurrent_commit_never_overwrites_history(
    tmp_path, monkeypatch, problem
):
    store, renderer, independent, plan, excluded, _ = accepted_uncommitted_fixture(
        tmp_path, monkeypatch
    )
    before = page_history(store, 3)

    def callback(proof):
        if problem == "backup-failed":
            raise OSError("fixture backup unavailable")
        if problem == "asset-changed":
            image = next(name for name in proof["assets"] if name.endswith(".png"))
            (store.root / image).write_bytes(b"changed fixture image")

    # Duplicate test avoids recursively taking the same per-page lease: emulate
    # an independent writer committing between proof and our writer transaction.
    if problem == "duplicate-commit":
        original_save = store.save_document_versions

        def save_twice(versions):
            original_save(versions)
            frozen = page_history(store, 3)
            with pytest.raises(RuntimeError, match="no longer absent"):
                original_save(versions)
            assert page_history(store, 3) == frozen
            raise RuntimeError("Document alias target is no longer absent")

        monkeypatch.setattr(store, "save_document_versions", save_twice)

        def callback(_):
            pass

    expected = (
        OSError
        if problem == "backup-failed"
        else RuntimeError
        if problem == "duplicate-commit"
        else ValueError
    )
    with pytest.raises(expected):
        run_recovery(store, renderer, independent, plan, excluded, callback)
    if problem != "duplicate-commit":
        assert page_history(store, 3) == before
    assert not (store.root / "pdf-cached-test-ocr-report.json").exists()


@pytest.mark.parametrize("problem", ["wrong-count", "wrong-pixels", "wrong-model"])
def test_native_source_pixels_and_model_cache_binding_must_all_match(
    tmp_path, monkeypatch, problem
):
    store, renderer, independent, plan, excluded, _ = accepted_uncommitted_fixture(
        tmp_path, monkeypatch
    )
    if problem == "wrong-count":
        plan["pdf_sources_selected_pages"][0]["pages"] = 6
    elif problem == "wrong-pixels":
        actual = independent

        def independent(*args):
            diagnostics = actual(*args)
            module.Image.new("RGB", (64, 32), (1, 2, 3)).save(args[2])
            return diagnostics
    else:
        image = next(store.root.glob("document-visuals/*/*/00003.png"))
        cache_path = next(image.parent.glob("00003-*.json"))
        data = module.json.loads(cache_path.read_bytes())
        data["model_digest"] = "d" * 64
        cache_path.write_text(module.json.dumps(data), encoding="utf8")
    with pytest.raises((ValueError, RuntimeError)):
        run_recovery(store, renderer, independent, plan, excluded)
    assert all(not rows for rows in page_history(store, 3).values())
