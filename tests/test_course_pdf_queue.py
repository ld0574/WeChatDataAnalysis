import json
from contextlib import nullcontext

import pytest

from wechat_decrypt_tool import course_pdf_queue as module
from wechat_decrypt_tool.course_pdf_batch_backup import backup_batch
from test_course_pdf_selected_pages import page_history, retained_fixture


def queue_fixture(tmp_path, monkeypatch):
    store, renderer, independent, plan, run, _, calls, chat = retained_fixture(
        tmp_path, monkeypatch
    )
    parent = plan([1], "parent-plan.json")
    run(parent, "parent")
    backup = backup_batch(
        store,
        parent,
        store.root / "parent-ocr-report.json",
        renderer,
        tmp_path / "parent-backup",
        prefix="parent-copy",
        minimum_free_gib=0,
        rerender=independent,
    )
    store.write_json("parent-validation.json", backup)
    selected = plan([3, 5], "next-plan.json")
    target = dict(json.loads(selected.read_bytes())["pdf_sources_selected_pages"][0])
    del target["selected_pages"]
    store.write_json(
        "pdf-all-pages-retained-plan.json", {"pdf_sources_all_pages": [target]}
    )
    store.write_json("old-failure.json", {"state": "failed", "budget": 0})
    template = {
        "model": "local-fixture",
        "model_digest": "c" * 64,
        "renderer": str(renderer),
        "renderer_sha256": module.sha(renderer.read_bytes()),
        "preserved_reports": {
            "old-failure.json": module.sha(
                (store.root / "old-failure.json").read_bytes()
            )
        },
    }
    monkeypatch.setattr(module, "local_model_digest", lambda _: "c" * 64)
    return store, selected, template, independent, calls, chat


def prepare_fixture(tmp_path, monkeypatch):
    store, selected, template, independent, calls, chat = queue_fixture(
        tmp_path, monkeypatch
    )
    prefix = "pdf-queue-20261103-0003"
    binding = module.prepare(
        store.root,
        selected,
        prefix,
        template,
        "parent-validation.json",
        tmp_path / "inputs",
        tmp_path / "results",
        reserve_gib=0,
    )
    return store, prefix, binding, independent, calls, chat


def test_retained_only_finite_batch_preserves_prior_history_and_verifies_exact_backup(
    tmp_path, monkeypatch
):
    store, prefix, binding, independent, calls, chat = prepare_fixture(
        tmp_path, monkeypatch
    )
    prior = page_history(store, 1)
    old = (store.root / "old-failure.json").read_bytes()
    assert module.validate_inputs(store.root, prefix) == binding
    assert calls == [1]  # Preparation does not perform inference.
    monkeypatch.setattr(module, "checkpoint", lambda root, **kwargs: None)
    monkeypatch.setattr(module, "local_chat", chat)
    monkeypatch.setattr(module, "gpu_lease", lambda _: nullcontext())
    hooks = []
    monkeypatch.setattr(module.sys, "addaudithook", hooks.append)
    actual_backup = module.backup_batch

    def backed_up(*args, **kwargs):
        return actual_backup(*args, **kwargs, minimum_free_gib=0, rerender=independent)

    monkeypatch.setattr(module, "backup_batch", backed_up)
    module.worker(store.root, prefix)
    receipt = module.read(store.root / (prefix + "-validation.json"))
    assert receipt["page_occurrences"] == receipt["unique_raw_pages"] == 2
    assert set(receipt["full_native_page_counts"].values()) == {5}
    assert receipt["selected_page_scope"] and not receipt["source_deletion_allowed"]
    assert module.verify_backup(receipt) == receipt["manifest_sha256"]
    assert calls == [1, 3, 5] and page_history(store, 1) == prior
    assert (store.root / "old-failure.json").read_bytes() == old
    original = module.read(store.root / "resources-manifest.json")["source"]
    with pytest.raises(PermissionError, match="original_course_read_forbidden"):
        hooks[0]("open", (original + "/any-file",))
    with pytest.raises(ValueError, match="batch_already_prepared"):
        module.prepare(
            store.root,
            store.root / "next-plan.json",
            prefix,
            {},
            "parent-validation.json",
            tmp_path / "inputs",
            tmp_path / "results",
            reserve_gib=0,
        )


@pytest.mark.parametrize(
    "tamper", ["old-report", "input-backup", "bound-runtime", "native"]
)
def test_changed_bound_inputs_stop_before_inference(tmp_path, monkeypatch, tamper):
    store, prefix, binding, _, calls, _ = prepare_fixture(tmp_path, monkeypatch)
    if tamper == "old-report":
        path = store.root / "old-failure.json"
    elif tamper == "input-backup":
        prepared = module.read(store.root / (prefix + "-prepared.json"))
        path = module.Path(prepared["backup"]) / "archive" / (prefix + "-plan.json")
    elif tamper == "bound-runtime":
        path = store.root / (prefix + "-runtime/course_pdf_validation.py")
    else:
        path = module.Path(next(iter(binding["sources"].values())))
    path.write_bytes(b"changed fixture")
    with pytest.raises(ValueError, match="pdf_queue_"):
        module.validate_inputs(store.root, prefix)
    assert calls == [1]


@pytest.mark.parametrize(
    "history_table", ["documents", "resource_revisions", "document_version_metadata"]
)
def test_prior_history_in_any_table_cannot_be_silently_retried(
    tmp_path, monkeypatch, history_table
):
    store, selected, template, _, calls, _ = queue_fixture(tmp_path, monkeypatch)
    with store.write() as db:
        row = db.execute(
            "SELECT * FROM " + history_table + " WHERE source_key='visual  page 1'"
        ).fetchone()
        values = dict(row)
        values["source_key"] = "visual  page 3"
        if history_table == "documents":
            values["locator"] = "visual  page 3 chars 0"
        values.pop("id", None)
        db.execute(
            "INSERT INTO "
            + history_table
            + "("
            + ",".join(values)
            + ") VALUES("
            + ",".join("?" for _ in values)
            + ")",
            tuple(values.values()),
        )
    with pytest.raises(ValueError, match="page_has_prior_history"):
        module.prepare(
            store.root,
            selected,
            "pdf-queue-20261103-0003",
            template,
            "parent-validation.json",
            tmp_path / "inputs",
            tmp_path / "results",
            reserve_gib=0,
        )
    assert calls == [1]


def test_native_page_count_is_checked_and_corrupt_predecessor_stops_preparation(
    tmp_path, monkeypatch
):
    store, selected, template, _, calls, _ = queue_fixture(tmp_path, monkeypatch)
    data = module.read(selected)
    data["pdf_sources_selected_pages"][0]["pages"] = 6
    store.write_json(selected.name, data)
    with pytest.raises(ValueError, match="native_page_count_differs"):
        module.prepare(
            store.root,
            selected,
            "pdf-queue-20261103-0003",
            template,
            "parent-validation.json",
            tmp_path / "inputs",
            tmp_path / "results",
            reserve_gib=0,
        )
    receipt = module.read(store.root / "parent-validation.json")
    destination = module.Path(receipt["backup"])
    manifest = module.read(destination / "manifest.json")
    (destination / manifest["files"][0]["path"]).write_bytes(b"corrupt fixture")
    with pytest.raises(ValueError, match="predecessor_backup_changed"):
        module.prepare(
            store.root,
            selected,
            "pdf-queue-20261103-0003",
            template,
            "parent-validation.json",
            tmp_path / "inputs",
            tmp_path / "results",
            reserve_gib=0,
        )
    assert calls == [1]


def make_queue(root, plan, *, overlap=False):
    folder = root / "queue"
    folder.mkdir()
    entries = []
    for number, page in [(3, 3), (4, 3 if overlap else 5)]:
        value = module.read(plan)
        value["pdf_sources_selected_pages"][0]["selected_pages"] = [page]
        value["summary"]["page_occurrences"] = 1
        target = folder / f"{number}.json"
        target.write_text(json.dumps(value), encoding="utf8")
        entries.append(
            {
                "number": number,
                "plan": target.name,
                "plan_sha256": module.sha(target.read_bytes()),
                "unique_raw_pages": 1,
                "page_occurrences": 1,
            }
        )
    (folder / "validation.json").write_text(
        json.dumps({"batches": entries}), encoding="utf8"
    )
    return folder


def test_queue_rejects_overlapping_raw_pages(tmp_path, monkeypatch):
    store, plan, _, _, _, _ = queue_fixture(tmp_path, monkeypatch)
    folder = make_queue(store.root, plan, overlap=True)
    with pytest.raises(ValueError, match="overlapping_raw_pages"):
        module.queue_plans(folder)


def test_exact_live_predecessor_is_waited_and_failed_batch_is_not_retried(
    tmp_path, monkeypatch
):
    store, plan, template, _, _, _ = queue_fixture(tmp_path, monkeypatch)
    folder = make_queue(store.root, plan)
    store.write_json("template.json", template)
    live_calls = iter([True, False, False])
    monkeypatch.setattr(module, "alive", lambda _: next(live_calls))
    monkeypatch.setattr(module, "checkpoint", lambda root: None)
    prepared, commands, sleeps = [], [], []
    monkeypatch.setattr(module, "prepare", lambda *args: prepared.append(args[2]))
    # A guard that returns without a successful worker receipt must stop the queue.
    monkeypatch.setattr(
        module, "guarded", lambda root, command, **kw: commands.append(command)
    )
    with pytest.raises(ValueError, match="input_outside_archive_or_missing"):
        module.drain(
            store.root,
            folder,
            "template.json",
            "parent",
            {"pid": 1, "created": 1},
            {"pid": 2, "created": 2},
            3,
            4,
            tmp_path / "inputs",
            tmp_path / "results",
            sleep=sleeps.append,
        )
    assert sleeps == [20] and prepared == ["pdf-queue-20261103-0003"]
    assert (
        len(commands) == 1 and commands[0][2] == "wechat_decrypt_tool.course_pdf_queue"
    )
    assert (
        module.read(store.root / "pdf-selected-queue-progress.json")["state"]
        == "failed"
    )


def test_pause_or_dedicated_stop_is_never_automatically_cleared(tmp_path):
    for name in ["pause.flag", "pdf-selected-queue-stop.flag"]:
        flag = tmp_path / name
        flag.write_text("stop", encoding="utf8")
        with pytest.raises(InterruptedError, match="pause_requested"):
            module.checkpoint(tmp_path, reserve_gib=0)
        assert flag.read_text("utf8") == "stop"
        flag.unlink()


def test_control_input_change_during_verified_wait_stops_without_launch(
    tmp_path, monkeypatch
):
    store, plan, template, _, calls, _ = queue_fixture(tmp_path, monkeypatch)
    folder = make_queue(store.root, plan)
    store.write_json("template.json", template)
    monkeypatch.setattr(module, "alive", lambda _: True)
    monkeypatch.setattr(module, "checkpoint", lambda root: None)

    def change_template(_):
        store.write_json("template.json", {"changed": True})

    with pytest.raises(ValueError, match="controller_input_changed"):
        module.drain(
            store.root,
            folder,
            "template.json",
            "parent",
            {"pid": 1, "created": 1},
            {"pid": 2, "created": 2},
            3,
            4,
            tmp_path / "inputs",
            tmp_path / "results",
            sleep=change_template,
        )
    assert calls == [1]
    assert not list(store.root.glob("pdf-queue-*-binding.json"))


def test_two_disjoint_batches_finish_serially_with_verified_backups(
    tmp_path, monkeypatch
):
    store, plan, template, independent, calls, chat = queue_fixture(
        tmp_path, monkeypatch
    )
    folder = make_queue(store.root, plan)
    store.write_json("template.json", template)
    monkeypatch.setattr(module, "alive", lambda _: False)
    monkeypatch.setattr(module, "checkpoint", lambda root, **kwargs: None)
    monkeypatch.setattr(module, "local_chat", chat)
    monkeypatch.setattr(module, "gpu_lease", lambda _: nullcontext())
    monkeypatch.setattr(module.sys, "addaudithook", lambda _: None)
    actual_prepare, actual_backup = module.prepare, module.backup_batch
    monkeypatch.setattr(
        module, "prepare", lambda *args: actual_prepare(*args, reserve_gib=0)
    )
    monkeypatch.setattr(
        module,
        "backup_batch",
        lambda *args, **kwargs: actual_backup(
            *args, **kwargs, minimum_free_gib=0, rerender=independent
        ),
    )
    launched = []

    def run_guard(root, command, **limits):
        assert limits == {
            "maximum_temperature": 70,
            "maximum_power": 200,
            "guard_name": "extraction-thermal-guard",
        }
        launched.append(command[-1])
        module.worker(root, command[-1])

    monkeypatch.setattr(module, "guarded", run_guard)
    module.drain(
        store.root,
        folder,
        "template.json",
        "parent",
        {"pid": 1, "created": 1},
        {"pid": 2, "created": 2},
        3,
        4,
        tmp_path / "inputs",
        tmp_path / "results",
    )
    progress = module.read(store.root / "pdf-selected-queue-progress.json")
    assert progress["state"] == "requested_finite_queue_finished"
    assert [x["number"] for x in progress["completed_batches"]] == [3, 4]
    assert launched == ["pdf-queue-20261103-0003", "pdf-queue-20261103-0004"]
    assert calls == [1, 3, 5]
    for prefix in launched:
        receipt = module.read(store.root / (prefix + "-validation.json"))
        assert receipt["page_occurrences"] == 1
        assert module.verify_backup(receipt) == receipt["manifest_sha256"]
