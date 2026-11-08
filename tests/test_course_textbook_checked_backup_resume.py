from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_checked_backup_resume as m


def test_runtime_states_keep_both_content_addressed_versions_and_never_replace_legacy_copy(tmp_path):
    source, destination = tmp_path / "guard-progress.json", tmp_path / "backups/guard-progress.json"
    destination.parent.mkdir()
    destination.write_bytes(b'legacy-retained-state')
    source.write_bytes(b'{"state":"running"}')
    first = m.versioned_runtime(source, destination)
    source.write_bytes(b'{"state":"worker_exited"}')
    second = m.versioned_runtime(source, destination)
    assert first != second
    assert first.read_bytes() == b'{"state":"running"}'
    assert second.read_bytes() == b'{"state":"worker_exited"}'
    assert destination.read_bytes() == b'legacy-retained-state'
    assert m.versioned_runtime(source, destination) == second


def test_runtime_copy_hashes_and_copies_the_same_single_read(tmp_path, monkeypatch):
    source = tmp_path / "guard-progress.json"
    source.write_bytes(b'first-complete-state')
    original = Path.read_bytes
    reads = []
    def read(path):
        if path == source:
            reads.append(path)
            return b'first-complete-state' if len(reads) == 1 else b'changed-state'
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    target = m.versioned_runtime(source, tmp_path / "versions/guard-progress.json")
    assert target.read_bytes() == b'first-complete-state'
    assert len(reads) == 1
    assert target.name.startswith(m.whole.sha(b'first-complete-state'))


def test_static_evidence_still_rejects_any_changed_backup_and_keeps_old_bytes(tmp_path, monkeypatch):
    job = SimpleNamespace(root=tmp_path / "job", key="a" * 32)
    job.root.mkdir()
    source = job.root / "actual-response.json"
    source.write_bytes(b'original')
    monkeypatch.setattr(m.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(m.writing, "bindings", lambda: {})
    m.backup_job(job)
    target = tmp_path / "Documents/CourseKnowledgeBackups/whole-source-articles" / job.key / source.name
    source.write_bytes(b'changed')
    with pytest.raises(ValueError, match="whole_sources_immutable_backup"):
        m.backup_job(job)
    assert target.read_bytes() == b'original'


def test_progress_and_nested_guard_versions_do_not_conflict_with_static_evidence(tmp_path, monkeypatch):
    job = SimpleNamespace(root=tmp_path / "job", key="a" * 32)
    (job.root / "guard").mkdir(parents=True)
    progress, guard = job.root / "progress.json", job.root / "guard/guard-progress.json"
    progress.write_bytes(b'phase-1')
    guard.write_bytes(b'guard-1')
    (job.root / "actual-response.json").write_bytes(b'unchanged')
    monkeypatch.setattr(m.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(m.writing, "bindings", lambda: {})
    m.backup_job(job)
    progress.write_bytes(b'phase-2')
    guard.write_bytes(b'guard-2')
    m.backup_job(job)
    versions = tmp_path / "Documents/CourseKnowledgeBackups/whole-source-articles" / job.key / "runtime-versions"
    assert len(list(versions.glob('*-progress.json'))) == 2
    assert len(list((versions / 'guard').glob('*-guard-progress.json'))) == 2


def test_backup_hook_is_local_to_scope_and_restored_after_failure():
    original = m.writing.backup_job
    with pytest.raises(RuntimeError):
        with m.installed_backup():
            assert m.writing.backup_job is m.backup_job
            raise RuntimeError("Synthetic failure")
    assert m.writing.backup_job is original


def test_authentication_reuses_original_final_review_without_new_inference(monkeypatch):
    bundle, final = SimpleNamespace(), {"final_checks_passed": True}
    monkeypatch.setattr(m.reviewed, "load", lambda *args: bundle)
    calls = []
    def review(actual, tokenizer, *, read_only):
        calls.append(read_only)
        return final
    monkeypatch.setattr(m.reviewed, "final_review", review)
    assert m.inputs("root", "key", "tokenizer") == (bundle, final)
    assert calls == [True]
