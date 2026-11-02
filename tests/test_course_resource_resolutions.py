import json
from pathlib import Path

import pytest

from wechat_decrypt_tool import course_resource_resolutions as resolutions
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, atomic_json
from wechat_decrypt_tool.course_lesson_exports import digest
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive


@pytest.fixture
def evidence(tmp_path):
    store = KnowledgeStore(tmp_path / "archive")
    root = store.root
    (root / "wiki").mkdir()
    csv_path = str(tmp_path / "lesson.zip")
    office_path = str(tmp_path / "locked.zip")
    metadata = json.dumps({"rows": 2, "cells": 4, "characters": 20, "chunks": 1})
    row = {
        "path": csv_path,
        "member": "folder/data.csv",
        "source_key": "archive folder/data.csv",
        "raw_digest": "a" * 64,
        "revision": "b" * 64,
        "metadata_sha256": digest(metadata.encode()),
        "rows": 2,
        "cells": 4,
        "characters": 20,
        "active_chunks": 1,
    }
    with store.write() as db:
        db.execute(
            "INSERT INTO resource_revisions VALUES(?,?,?,?,?,?,?)",
            (
                csv_path,
                row["source_key"],
                row["raw_digest"],
                "csv-complete-rows-v1/strict-unicode/credential-mask",
                row["revision"],
                metadata,
                1,
            ),
        )
        db.execute(
            "INSERT INTO documents(path,locator,digest,text,search_text,active,source_key,extractor,raw_digest) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                csv_path,
                "csv chars 0",
                row["revision"],
                "private synthetic course prose",
                "search",
                1,
                row["source_key"],
                "csv-complete-rows-v1/strict-unicode/credential-mask",
                row["raw_digest"],
            ),
        )
    extraction = {
        "skipped": [{"path": csv_path, "member": row["member"], "reason": "old limit"}],
        "errors": [
            {"path": office_path, "member": "locked.pptx", "error": "old error"}
        ],
    }
    atomic_json(root / "courseware-index-report.json", extraction)
    atomic_json(root / "large-csv-profile.json", {"synthetic": True})
    atomic_json(
        root / "large-csv-index-report.json",
        {"requested": 1, "errors": [], "completed": [row]},
    )
    atomic_json(
        root / "large-csv-validation.json",
        {
            "source_occurrences": 1,
            "all_requested_csv_source_occurrences_verified": True,
            "all_literal_field_strings_row_order_and_fts_exact": True,
            "rows_including_first": 2,
            "cells": 4,
            "source_characters": 20,
            "active_chunks_verified": 1,
        },
    )
    atomic_json(
        root / "large-csv-validation-resume-receipt.json",
        {
            "all_six_sources_verified": True,
            "frozen_input_sha256": {
                name: digest((root / name).read_bytes())
                for name in (
                    "large-csv-profile.json",
                    "large-csv-index-report.json",
                )
            },
        },
    )
    atomic_json(
        root / "encrypted-office-library-probe.json",
        [
            {
                "member": "locked.pptx",
                "sha256": "c" * 64,
                "encrypted": True,
                "library_supported": True,
                "standard_EncryptionInfo": True,
                "standard_EncryptedPackage": True,
            }
        ],
    )
    atomic_json(
        root / "encrypted-office-password-check.json",
        [
            {
                "path": office_path,
                "member": "locked.pptx",
                "encrypted_member_sha256": "c" * 64,
                "requires_user_password": True,
                "matching_password": False,
                "source_unchanged": True,
            }
        ],
    )
    atomic_json(
        root / resolutions.CSV_RECEIPT,
        {
            "source_deletion_allowed": False,
            "resolved_skipped": [row],
            "unresolved_errors": [
                {
                    "path": office_path,
                    "member": "locked.pptx",
                    "member_sha256": "c" * 64,
                    "current_diagnosis": "encrypted_office_requires_unknown_password",
                }
            ],
            "input_sha256": {
                name: digest((root / name).read_bytes())
                for name in resolutions.CSV_INPUTS
            },
        },
    )
    source = tmp_path / "partial.downloading"
    source.write_bytes(b"synthetic video")
    stat = source.stat()
    key = digest(str(source.resolve()).encode())[:24]
    item = {
        "path": str(source),
        "relative_path": "partial.downloading",
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest(source.read_bytes()),
    }
    inventory = {"videos": [item]}
    resources = {"pending": [{"relative_path": item["relative_path"]}]}
    folder = root / "lessons" / key
    folder.mkdir(parents=True)
    (folder / "audio.m4a").write_bytes(b"synthetic audio")
    atomic_json(
        folder / "audio.json",
        {
            "verified": True,
            "video": key,
            "file": "audio.m4a",
            "file_bytes": 15,
            "source_digest": item["sha256"],
            "file_digest": digest(b"synthetic audio"),
            "source_size": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns,
        },
    )
    atomic_json(root / "inventory-unique.json", inventory)
    atomic_json(root / "resources-manifest.json", resources)
    for name in resolutions.AUDIO_INPUTS - {
        "inventory-unique.json",
        "resources-manifest.json",
    }:
        atomic_json(root / name, {"synthetic_retained_proof": True})
    atomic_json(
        root / resolutions.AUDIO_RECEIPT,
        {
            "source_deletion_allowed": False,
            "input_sha256": {
                name: digest((root / name).read_bytes())
                for name in resolutions.AUDIO_INPUTS
            },
            "rows": [
                {
                    "relative_path": item["relative_path"],
                    "video": key,
                    "source_sha256": item["sha256"],
                    "archived_audio_sha256": digest(b"synthetic audio"),
                    "audio_manifest_sha256": digest(
                        (folder / "audio.json").read_bytes()
                    ),
                    "current_source_and_archived_audio_stat_bindings_match": True,
                    "audio_packets_pcm_and_frame_timestamps_historically_verified": True,
                }
            ],
        },
    )
    return store, inventory, extraction, resources, key


def observe(evidence):
    store, inventory, extraction, resources, _ = evidence

    def load(name, optional=False):
        path = store.root / name
        return None if optional and not path.exists() else json.loads(path.read_bytes())

    return resolutions.observe(
        ReadOnlyArchive(store.root),
        load,
        inventory=inventory,
        extraction=extraction,
        resources=resources,
    )


def test_verified_matches_preserve_encrypted_and_semantic_gaps_without_emitting_prose(
    evidence,
):
    result = observe(evidence)
    assert (
        len(result["resolved_csv_skips"]) == len(result["accounted_pending_audio"]) == 1
    )
    assert len(result["known_encrypted_office"]) == 1
    assert not result["receipt_issues"]
    assert not result["unverified_skips"] and not result["unverified_pending"]
    assert (
        not result["semantic_accuracy_verified"]
        and not result["source_deletion_allowed"]
    )
    assert not result["source_bytes_rehashed"] and not result["fts_postings_reaudited"]
    assert "private synthetic course prose" not in json.dumps(result)


@pytest.mark.parametrize(
    "mutation", ["revision", "metadata", "active_count", "staged_revision"]
)
def test_changed_database_revision_or_staging_never_counts_as_resolved(
    evidence, mutation
):
    store = evidence[0]
    with store.write() as db:
        if mutation == "revision":
            db.execute("UPDATE resource_revisions SET raw_digest='new source'")
        elif mutation == "metadata":
            db.execute("UPDATE resource_revisions SET metadata='{}'")
        elif mutation == "active_count":
            db.execute("UPDATE documents SET active=0")
        else:
            db.execute(
                "INSERT INTO documents(path,source_key,digest,active) SELECT path,source_key,digest,0 FROM documents"
            )
    result = observe(evidence)
    assert not result["resolved_csv_skips"] and len(result["unverified_skips"]) == 1
    assert len(result["accounted_pending_audio"]) == 1


@pytest.mark.parametrize(
    "name", ["large-csv-profile.json", "large-csv-validation.json"]
)
def test_changed_bound_inputs_invalidate_csv_resolution_only(evidence, name):
    atomic_json(evidence[0].root / name, {"changed": True})
    result = observe(evidence)
    assert not result["resolved_csv_skips"] and not result["known_encrypted_office"]
    assert len(result["accounted_pending_audio"]) == 1
    assert result["receipt_issues"][0]["code"] == "receipt_inputs_changed"


@pytest.mark.parametrize(
    "mutation", ["original_stat", "audio_size", "audio_manifest", "inventory"]
)
def test_changed_audio_or_original_metadata_requires_recheck(evidence, mutation):
    store, inventory, _, _, key = evidence
    if mutation == "original_stat":
        Path(inventory["videos"][0]["path"]).write_bytes(b"changed")
    elif mutation == "audio_size":
        (store.root / "lessons" / key / "audio.m4a").write_bytes(b"changed")
    elif mutation == "audio_manifest":
        atomic_json(store.root / "lessons" / key / "audio.json", {})
    else:
        atomic_json(store.root / "inventory-unique.json", {"videos": []})
    result = observe(evidence)
    assert (
        not result["accounted_pending_audio"] and len(result["unverified_pending"]) == 1
    )
    assert len(result["resolved_csv_skips"]) == 1


def test_receipt_cannot_read_paths_outside_archive(evidence):
    store = evidence[0]
    path = store.root / resolutions.CSV_RECEIPT
    value = json.loads(path.read_bytes())
    value["input_sha256"]["../private.json"] = "f" * 64
    atomic_json(path, value)
    result = observe(evidence)
    assert not result["resolved_csv_skips"]
    assert result["receipt_issues"][0]["code"] == "receipt_not_verified"


def test_missing_receipts_preserve_every_historical_finding(evidence):
    store = evidence[0]
    (store.root / resolutions.CSV_RECEIPT).unlink()
    (store.root / resolutions.AUDIO_RECEIPT).unlink()
    result = observe(evidence)
    assert (
        len(result["unverified_errors"])
        == len(result["unverified_skips"])
        == len(result["unverified_pending"])
        == 1
    )
    assert not result["resolved_csv_skips"] and not result["accounted_pending_audio"]


def test_hash_bound_but_failed_original_audit_cannot_certify_csv(evidence):
    store = evidence[0]
    audit = store.root / "large-csv-validation.json"
    value = json.loads(audit.read_bytes())
    value["all_literal_field_strings_row_order_and_fts_exact"] = False
    atomic_json(audit, value)
    path = store.root / resolutions.CSV_RECEIPT
    report = json.loads(path.read_bytes())
    report["input_sha256"]["large-csv-validation.json"] = digest(audit.read_bytes())
    atomic_json(path, report)
    result = observe(evidence)
    assert not result["resolved_csv_skips"]
    assert len(result["unverified_skips"]) == 1
    assert len(result["accounted_pending_audio"]) == 1


def test_malformed_csv_receipt_does_not_hide_valid_audio_observation(evidence):
    atomic_json(evidence[0].root / resolutions.CSV_RECEIPT, [])
    result = observe(evidence)
    assert not result["resolved_csv_skips"]
    assert len(result["accounted_pending_audio"]) == 1


def test_later_office_revision_invalidates_old_unreadable_diagnosis(evidence):
    store, _, extraction, _, _ = evidence
    old = extraction["errors"][0]
    with store.write() as db:
        db.execute(
            "INSERT INTO resource_revisions VALUES(?,?,?,?,?,?,?)",
            (
                old["path"],
                "archive " + old["member"],
                "new",
                "later extractor",
                "later revision",
                "{}",
                2,
            ),
        )
    result = observe(evidence)
    assert not result["known_encrypted_office"]
    assert len(result["unverified_errors"]) == 1
