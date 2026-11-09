import json
import sqlite3
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

from wechat_decrypt_tool import course_pdf_failed_grid as m


def chat_fixture(tmp_path, monkeypatch, *, maximum=2, response=None):
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    monkeypatch.setattr(m.queue, "local_model_digest", lambda model: "a" * 64)
    contract = {"maximum_wire_calls": maximum, "model": "local-fixture", "model_digest": "a" * 64}
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        value = response or {"done_reason": "stop", "message": {"content": "synthetic-fixture"}}
        return httpx.Response(500 if "error" in value else 200, json=value, request=httpx.Request("POST", url))

    client = SimpleNamespace(post=post)
    folder = tmp_path / "job"
    folder.mkdir()
    chat = m.LimitedChat(tmp_path, folder, contract, client=client)
    return chat, calls, [{"role": "user", "content": "synthetic-fixture", "images": ["Zml4dHVyZQ=="]}]


def test_request_and_actual_response_retained_and_second_instance_cannot_reset_budget(tmp_path, monkeypatch):
    chat, calls, messages = chat_fixture(tmp_path, monkeypatch)
    for _ in range(2):
        assert chat("local-fixture", messages) == "synthetic-fixture"
    recreated = m.LimitedChat(chat.root, chat.folder, chat.contract, client=chat.client)
    with pytest.raises(ValueError, match="finite_first_grid_wire_budget"):
        recreated("local-fixture", messages)
    assert len(calls) == 2
    assert all(c["options"]["num_gpu"] == 999 for c in calls)
    for number in range(2):
        request = chat.folder / "calls" / f"{number:03d}-request.json"
        receipt = m.whole.read(request.with_name(f"{number:03d}-response.json"))
        assert receipt["request_sha256"] == m.whole.sha(request.read_bytes())
        assert receipt["response"]["message"]["content"] == "synthetic-fixture"


@pytest.mark.parametrize("response", [
    {"error": "prediction aborted, token repeat limit reached"},
    {"error": "synthetic-server-error"},
    {"done_reason": "length", "message": {"content": "synthetic-partial"}},
])
def test_failed_request_is_retained_without_any_automatic_http_retry(tmp_path, monkeypatch, response):
    chat, calls, messages = chat_fixture(tmp_path, monkeypatch, response=response)
    with pytest.raises((m.ModelRepetitionError, m.ModelOutputTruncatedError, httpx.HTTPStatusError)):
        chat("local-fixture", messages)
    assert len(calls) == 1
    assert m.whole.read(chat.folder / "calls/000-response.json")["response"] == response


def test_changed_model_and_nonvision_requests_are_refused_before_wire(tmp_path, monkeypatch):
    chat, calls, messages = chat_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        chat("different-fixture", messages)
    with pytest.raises(ValueError):
        chat("local-fixture", [{"role": "user", "content": "synthetic"}])
    monkeypatch.setattr(m.queue, "local_model_digest", lambda *args: "b" * 64)
    with pytest.raises(ValueError, match="same_model_before_wire"):
        chat("local-fixture", messages)
    assert not calls


def test_thermal_or_manual_stop_never_creates_new_request(tmp_path, monkeypatch):
    chat, calls, messages = chat_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: (_ for _ in ()).throw(InterruptedError("synthetic-stop")))
    with pytest.raises(InterruptedError):
        chat("local-fixture", messages)
    assert not calls and not (chat.folder / "calls").exists()


def test_pixel_identical_aliases_share_new_grid_and_existing_failed_cache_blocks_first_use(tmp_path):
    image = tmp_path / "page.png"
    Image.new("RGB", (1546, 2000), (18, 29, 40)).save(image)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    rows = [{"image": "page.png"}, {"image": "page.png"}]
    folders = m.grid_folders(tmp_path, rows, "synthetic", "a" * 64)
    assert folders == m.grid_folders(tmp_path, rows[:1], "synthetic", "a" * 64)
    assert 0 < len(folders) <= 12
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    m.unused(tmp_path, folders)
    used = tmp_path / folders[0] / "0-0-640-640.json.rejected.json"
    used.parent.mkdir(parents=True)
    used.write_text(json.dumps({"accepted_as_evidence": False}))
    with pytest.raises(ValueError, match="all_new_grid_model_roles_unused"):
        m.unused(tmp_path, folders)


def test_grid_copy_and_old_failures_are_unchanged_by_unused_check(tmp_path):
    path = tmp_path / "old-failure.json"
    path.write_text('{"split": true}')
    m.unused(tmp_path, ["new-cache"])
    assert path.read_text() == '{"split": true}'


def test_worker_refuses_unverified_inputs_before_any_model_or_cache_write(tmp_path, monkeypatch):
    folder = tmp_path / "job"
    folder.mkdir()
    monkeypatch.setattr(m, "load", lambda *args: (tmp_path, folder, {}, tmp_path / "renderer.exe"))
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    with pytest.raises(ValueError, match="actual_input_restore_before_worker"):
        m.run(tmp_path, "a" * 32)
    assert not (folder / "worker-started.json").exists()


@pytest.mark.parametrize("table", ["resource_revisions", "documents", "document_version_metadata"])
def test_any_target_history_prevents_replacing_an_existing_or_failed_revision(tmp_path, table):
    with sqlite3.connect(tmp_path / "knowledge.sqlite3") as db:
        db.execute("CREATE TABLE videos(id,path,size,mtime_ns,duration)")
        for name in ["resource_revisions", "documents", "document_version_metadata"]:
            db.execute("CREATE TABLE " + name + "(path,source_key)")
        db.execute("INSERT INTO " + table + " VALUES(?,?)", ("fixture.pdf", "visual  page 2"))
    plan = {"pdf_sources_selected_pages": [{"path": "fixture.pdf", "source_key": "", "pages": 3,
        "parent_raw_digest": "a" * 64, "selected_pages": [2]}], "summary": {"page_occurrences": 1}}
    with pytest.raises(ValueError, match="page_has_no_prior_committed_or_historical_revision"):
        m.absent(tmp_path, plan)
