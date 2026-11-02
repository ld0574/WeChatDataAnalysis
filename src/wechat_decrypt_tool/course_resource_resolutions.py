"""Reconcile historical findings with bound receipts and current metadata only.

This is a supplementary observation, never a whole-resource or semantic gate.
Large source bytes and FTS postings are deliberately not reread here.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

from .course_attachments import normalized_original
from .course_lesson_exports import digest

CSV_RECEIPT = "wiki/index-findings-reconciliation.json"
AUDIO_RECEIPT = "wiki/resource-pending-reconciliation.json"
HASH = re.compile(r"[a-f0-9]{64}\Z")
CSV_INPUTS = {
    "courseware-index-report.json",
    "large-csv-profile.json",
    "large-csv-index-report.json",
    "large-csv-validation.json",
    "large-csv-validation-resume-receipt.json",
    "encrypted-office-library-probe.json",
    "encrypted-office-password-check.json",
}
AUDIO_INPUTS = {
    "resources-manifest.json",
    "inventory-unique.json",
    "partial-download-full-validation.json",
    "full-audio-timeline-validation-plan.json",
    "full-audio-timeline-validation-report.json",
}


def _bound_report(store, load, relative, required):
    try:
        report = load(relative, optional=True)
        if report is None:
            return None, "receipt_missing"
        if report["source_deletion_allowed"] is not False:
            raise ValueError()
        inputs = report["input_sha256"]
        if not isinstance(inputs, dict) or not required <= inputs.keys():
            raise ValueError()
        for name, expected in inputs.items():
            path = PurePosixPath(name.replace("\\", "/"))
            if (
                path.is_absolute()
                or ".." in path.parts
                or PureWindowsPath(name).drive
                or not HASH.fullmatch(expected)
                or not (store.root / name).resolve().is_relative_to(store.root.resolve())
            ):
                raise ValueError()
            # Register each input in the caller's immutable snapshot before use.
            load(name)
            if digest((store.root / name).read_bytes()) != expected:
                return None, "receipt_inputs_changed"
        return report, None
    except (KeyError, TypeError, ValueError, OSError):
        return None, "receipt_not_verified"


def _csv_proof(load, report):
    indexed = load("large-csv-index-report.json")
    audited = load("large-csv-validation.json")
    resume = load("large-csv-validation-resume-receipt.json")
    rows = report["resolved_skipped"]
    if (
        indexed["errors"]
        or indexed["requested"] != len(rows)
        or len(indexed["completed"]) != len(rows)
        or audited["source_occurrences"] != len(rows)
        or audited["all_requested_csv_source_occurrences_verified"] is not True
        or audited["all_literal_field_strings_row_order_and_fts_exact"] is not True
        or resume["all_six_sources_verified"] is not True
    ):
        return False
    for name in ("large-csv-profile.json", "large-csv-index-report.json"):
        if resume["frozen_input_sha256"][name] != report["input_sha256"][name]:
            return False

    def identity(row):
        return normalized_original(row["path"]), row["source_key"], row["raw_digest"]

    if {identity(r) for r in indexed["completed"]} != {identity(r) for r in rows}:
        return False
    if len({identity(r) for r in rows}) != len(rows):
        return False
    return all(
        audited[total] == sum(r[field] for r in rows)
        for total, field in (
            ("rows_including_first", "rows"),
            ("cells", "cells"),
            ("source_characters", "characters"),
            ("active_chunks_verified", "active_chunks"),
        )
    )


def _known_office(load, old, row):
    if (
        row is None
        or row.get("current_diagnosis") != "encrypted_office_requires_unknown_password"
    ):
        return False
    probes = load("encrypted-office-library-probe.json")
    checks = load("encrypted-office-password-check.json")
    return any(
        probe.get("member") == old.get("member")
        and probe.get("sha256") == row["member_sha256"]
        and all(
            probe.get(k) is True
            for k in (
                "encrypted",
                "library_supported",
                "standard_EncryptionInfo",
                "standard_EncryptedPackage",
            )
        )
        for probe in probes
    ) and any(
        _identity(check) == _identity(old)
        and check.get("encrypted_member_sha256") == row["member_sha256"]
        and check.get("requires_user_password") is True
        and check.get("matching_password") is False
        and check.get("source_unchanged") is True
        for check in checks
    )


def _identity(row):
    return normalized_original(row["path"]), row.get("member", "")


def _csv_check(db, row):
    current = db.execute(
        "SELECT * FROM resource_revisions WHERE path=? AND source_key=?",
        (row["path"], row["source_key"]),
    ).fetchone()
    if current is None or (
        current["raw_digest"],
        current["revision"],
        current["extractor"],
    ) != (
        row["raw_digest"],
        row["revision"],
        "csv-complete-rows-v1/strict-unicode/credential-mask",
    ):
        return False
    if digest(current["metadata"].encode()) != row["metadata_sha256"]:
        return False
    metadata = json.loads(current["metadata"])
    if any(metadata[k] != row[k] for k in ("rows", "cells", "characters")):
        return False
    if metadata["chunks"] != row["active_chunks"]:
        return False
    # The covering index avoids rereading gigabytes of document text.
    count = db.execute(
        "SELECT count(*) FROM documents INDEXED BY document_source WHERE path=? AND source_key=? AND active=1",
        (row["path"], row["source_key"]),
    ).fetchone()[0]
    if count != row["active_chunks"]:
        return False
    staged = db.execute(
        "SELECT 1 FROM documents INDEXED BY document_source WHERE path=? AND source_key=? AND active=0 AND digest=? LIMIT 1",
        (row["path"], row["source_key"], row["revision"]),
    ).fetchone()
    return staged is None


def _audio_check(store, load, row, item):
    if row["video"] != digest(str(Path(item["path"]).resolve()).encode())[:24]:
        return False
    if not row["current_source_and_archived_audio_stat_bindings_match"]:
        return False
    if not row["audio_packets_pcm_and_frame_timestamps_historically_verified"]:
        return False
    if row["source_sha256"] != item["sha256"]:
        return False
    source = Path(item["path"]).stat()
    if (source.st_size, source.st_mtime_ns) != (item["bytes"], item["mtime_ns"]):
        return False
    relative = f"lessons/{row['video']}/audio.json"
    audio = load(relative)
    if digest((store.root / relative).read_bytes()) != row["audio_manifest_sha256"]:
        return False
    path = store.root / "lessons" / row["video"] / "audio.m4a"
    return (
        audio["verified"] is True
        and audio["video"] == row["video"]
        and audio["file"] == "audio.m4a"
        and audio["source_digest"] == row["source_sha256"]
        and audio["file_digest"] == row["archived_audio_sha256"]
        and (audio["source_size"], audio["source_mtime_ns"])
        == (source.st_size, source.st_mtime_ns)
        and path.stat().st_size == audio["file_bytes"]
    )


def observe(store, load, *, inventory, extraction, resources):
    """Preserve every old finding; annotate only individually verified matches."""
    extraction = extraction or {}
    resources = resources or {}
    result = {
        "observed": time.time(),
        "resolved_csv_skips": [],
        "accounted_pending_audio": [],
        "known_encrypted_office": [],
        "unverified_skips": [],
        "unverified_errors": [],
        "unverified_pending": [],
        "receipt_issues": [],
        "source_bytes_rehashed": False,
        "fts_postings_reaudited": False,
        "semantic_accuracy_verified": False,
        "complete_resource_coverage_verified": False,
        "source_deletion_allowed": False,
    }
    csv, problem = _bound_report(store, load, CSV_RECEIPT, CSV_INPUTS)
    if problem:
        result["receipt_issues"].append({"receipt": CSV_RECEIPT, "code": problem})
    audio, problem = _bound_report(store, load, AUDIO_RECEIPT, AUDIO_INPUTS)
    if problem:
        result["receipt_issues"].append({"receipt": AUDIO_RECEIPT, "code": problem})
    csv_rows, office_rows, audio_rows = {}, {}, {}
    try:
        if csv:
            if not _csv_proof(load, csv):
                raise ValueError()
            csv_rows = {_identity(r): r for r in csv["resolved_skipped"]}
            office_rows = {_identity(r): r for r in csv["unresolved_errors"]}
            if len(csv_rows) != len(csv["resolved_skipped"]):
                raise ValueError()
    except (KeyError, TypeError, ValueError):
        csv_rows, office_rows = {}, {}
        result["receipt_issues"].append({"code": "invalid_resolution_rows"})
    try:
        if audio:
            audio_rows = {r["relative_path"]: r for r in audio["rows"]}
            if len(audio_rows) != len(audio["rows"]):
                raise ValueError()
    except (KeyError, TypeError, ValueError):
        audio_rows = {}
        result["receipt_issues"].append({"code": "invalid_audio_resolution_rows"})
    with store.connect() as db:
        db.execute("BEGIN")
        for old in extraction.get("skipped", []):
            row = csv_rows.get(_identity(old))
            valid = False
            try:
                valid = row is not None and _csv_check(db, row)
            except (KeyError, TypeError, ValueError):
                pass
            target = "resolved_csv_skips" if valid else "unverified_skips"
            value = {"path": old["path"], "member": old.get("member", "")}
            if valid:
                value.update(
                    active_chunks=row["active_chunks"],
                    rows=row["rows"],
                    revision=row["revision"],
                    state="complete_csv_index_metadata_verified",
                )
            result[target].append(value)
        for old in extraction.get("errors", []):
            row = office_rows.get(_identity(old))
            # This explains an existing unresolved diagnosis, not new decryption.
            try:
                known = _known_office(load, old, row)
            except (KeyError, TypeError, ValueError):
                known = False
            if known:
                current = db.execute(
                    "SELECT 1 FROM resource_revisions WHERE path=? AND source_key=?",
                    (old["path"], "archive " + old.get("member", "")),
                ).fetchone()
                known = current is None
            result["known_encrypted_office" if known else "unverified_errors"].append(
                {"path": old["path"], "member": old.get("member", "")}
            )
    by_relative = {r["relative_path"]: r for r in inventory["videos"]}
    for old in resources.get("pending", []):
        relative = old["relative_path"]
        row, item = audio_rows.get(relative), by_relative.get(relative)
        valid = False
        try:
            valid = (
                row is not None
                and item is not None
                and _audio_check(store, load, row, item)
            )
        except (OSError, KeyError, ValueError, TypeError):
            pass
        value = {"relative_path": relative}
        if valid:
            value.update(
                video=row["video"], state="already_in_canonical_audio_inventory"
            )
        result["accounted_pending_audio" if valid else "unverified_pending"].append(
            value
        )
    return result
