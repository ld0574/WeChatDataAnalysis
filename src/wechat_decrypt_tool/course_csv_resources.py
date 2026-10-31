"""Stream complete CSV rows into hash-bound, atomically published search revisions."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path

from .course_attachments import (
    archive_locator,
    digest,
    normalized_original,
    open_archive_member,
    safe_member,
)
from .course_notebook_outputs import NOTEBOOK_CREDENTIAL
from .course_text_resources import redact_credentials

EXTRACTOR = "csv-complete-rows-v1/strict-unicode/credential-mask"
CHUNK_CHARS = 3000
MAX_BYTES = 512 * 1024**2
SECRET_COLUMN = re.compile(
    r"^(?:api[_-]?key|access[_-]?token|secret|password|passwd|credential|authorization|private[_-]?key)$",
    re.I,
)


def spool_rows(source, destination, plan, checkpoint=lambda: None):
    """Keep field strings/empty fields/quoted newlines; never infer a header or types."""
    encoding = plan["encoding"]
    if encoding not in {"utf-8-sig", "utf-8", "utf-16", "utf-32", "gb18030"}:
        raise ValueError("CSV encoding is outside the audited allowlist")
    if plan["delimiter"] not in ",\t;|" or len(plan["delimiter"]) != 1:
        raise ValueError("CSV delimiter is outside the audited allowlist")
    counts = {"rows": 0, "cells": 0, "characters": 0, "chunks": 0, "masked_cells": 0}
    histogram = Counter()
    buffer = ""
    offset = first = last = output_bytes = 0
    secret_columns = set()
    old_limit = csv.field_size_limit(16 * 1024**2)
    try:
        with (
            Path(source).open(
                "r", encoding=encoding, errors="strict", newline=""
            ) as stream,
            Path(destination).open("w", encoding="utf-8", newline="\n") as spool,
        ):
            reader = csv.reader(
                stream,
                delimiter=plan["delimiter"],
                quotechar=plan.get("quotechar", '"'),
                doublequote=plan.get("doublequote", True),
                skipinitialspace=plan.get("skipinitialspace", False),
                strict=True,
            )
            for number, row in enumerate(reader, 1):
                if number % 1000 == 1:
                    checkpoint()
                counts["rows"] = number
                counts["cells"] += len(row)
                counts["characters"] += sum(map(len, row))
                histogram[len(row)] += 1
                if sum(map(len, row)) > 32 * 1024**2 or len(row) > 100000:
                    raise ValueError("CSV record exceeds bounded indexing size")
                if number == 1:
                    counts["first_row_sha256"] = hashlib.sha256(
                        json.dumps(row, ensure_ascii=False).encode("utf-8")
                    ).hexdigest()
                    secret_columns = {
                        n
                        for n, value in enumerate(row)
                        if SECRET_COLUMN.fullmatch(value.strip())
                    }
                values = []
                for column, value in enumerate(row):
                    if number > 1 and column in secret_columns and value:
                        value, masked = "[REDACTED]", 1
                    elif "=" in value:
                        value, masked, _ = redact_credentials(
                            value, sensitive_pattern=NOTEBOOK_CREDENTIAL
                        )
                    else:
                        masked = 0
                    counts["masked_cells"] += bool(masked)
                    values.append(value)
                body = (
                    json.dumps(
                        {"row": number, "values": values},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                output_bytes += len(body.encode("utf-8"))
                if output_bytes > 2 * 1024**3:
                    raise ValueError("CSV indexed output exceeds bounded size")
                position = 0
                while position < len(body):
                    if not buffer:
                        first = number
                    last = number
                    take = min(CHUNK_CHARS - len(buffer), len(body) - position)
                    buffer += body[position : position + take]
                    position += take
                    if len(buffer) == CHUNK_CHARS:
                        location = (
                            f"csv chars {offset:012d} rows {first:010d}-{last:010d}"
                        )
                        spool.write(
                            json.dumps([location, buffer], ensure_ascii=False) + "\n"
                        )
                        counts["chunks"] += 1
                        offset += len(buffer)
                        buffer = ""
            if buffer:
                location = f"csv chars {offset:012d} rows {first:010d}-{last:010d}"
                spool.write(json.dumps([location, buffer], ensure_ascii=False) + "\n")
                counts["chunks"] += 1
                offset += len(buffer)
    except UnicodeError:
        raise ValueError(
            "CSV strict Unicode decoding failed; original retained"
        ) from None
    except csv.Error:
        raise ValueError(
            "CSV strict record parsing failed; original retained"
        ) from None
    finally:
        csv.field_size_limit(old_limit)
    for field, actual in (
        ("rows_including_first", counts["rows"]),
        ("cells", counts["cells"]),
        ("characters", counts["characters"]),
        ("first_row_sha256", counts.get("first_row_sha256")),
    ):
        if field in plan and plan[field] != actual:
            raise ValueError("CSV differs from frozen full-source profile: " + field)
    if (
        "columns_histogram" in plan
        and {str(k): v for k, v in histogram.items()} != plan["columns_histogram"]
    ):
        raise ValueError("CSV column counts differ from frozen full-source profile")
    return {
        **counts,
        "indexed_characters": offset,
        "columns_histogram": dict(histogram),
        "encoding": encoding,
        "delimiter": plan["delimiter"],
        "first_row_is_not_inferred_header": True,
        "field_types_not_coerced": True,
        "row_numbers_are_one_based": True,
        "raw_source_retained": True,
        "source_code_executed": False,
    }


def publish_spool(
    store,
    path,
    key,
    raw_digest,
    spool,
    metadata,
    *,
    batch_size=128,
    on_batch=lambda completed: None,
):
    """Stage inactive chunks in bounded transactions, then publish the complete revision."""
    from .course_knowledge import tokens

    revision = hashlib.sha256(
        (raw_digest + "\0" + EXTRACTOR + "\0" + key).encode()
    ).hexdigest()
    pending = []
    total = 0

    def flush():
        if not pending:
            return
        with store.write() as db:
            for location, text in pending:
                existing = db.execute(
                    "SELECT text,raw_digest,extractor FROM documents WHERE path=? AND locator=? AND digest=?",
                    (str(path), location, revision),
                ).fetchone()
                if existing:
                    if tuple(existing) != (text, raw_digest, EXTRACTOR):
                        raise RuntimeError(
                            "Staged CSV revision differs from frozen source"
                        )
                else:
                    db.execute(
                        "INSERT INTO documents(path,locator,digest,text,search_text,active,source_key,extractor,raw_digest) VALUES(?,?,?,?,?,0,?,?,?)",
                        (
                            str(path),
                            location,
                            revision,
                            text,
                            " ".join(tokens(Path(path).name + " " + text)),
                            key,
                            EXTRACTOR,
                            raw_digest,
                        ),
                    )
        pending.clear()
        on_batch(total)
        store.checkpoint()
        time.sleep(0.02)

    with Path(spool).open(encoding="utf-8") as stream:
        for line in stream:
            location, text = json.loads(line)
            pending.append((key + " " + location, text))
            total += 1
            if len(pending) >= batch_size:
                flush()
        flush()
    if total != metadata["chunks"]:
        raise ValueError("CSV spool chunk count mismatch")
    store.checkpoint()
    with store.write() as db:
        count = db.execute(
            "SELECT count(*) FROM documents WHERE path=? AND source_key=? AND digest=?",
            (str(path), key, revision),
        ).fetchone()[0]
        if count != total:
            raise RuntimeError("Staged CSV contains missing or extra chunks")
        prefix = key + " "
        db.execute(
            "UPDATE documents SET active=0 WHERE path=? AND extractor='legacy-v1' AND locator>=? AND locator<?",
            (str(path), prefix, prefix + "\U0010ffff"),
        )
        db.execute(
            "UPDATE documents SET active=0 WHERE path=? AND source_key=? AND digest!=? AND active=1",
            (str(path), key, revision),
        )
        db.execute(
            "UPDATE documents SET active=0 WHERE path=? AND source_key IN (SELECT source_key FROM resource_revisions WHERE path=? AND json_extract(metadata,'$.parent_source_key')=? AND json_extract(metadata,'$.parent_raw_digest')!=?)",
            (str(path), str(path), key, raw_digest),
        )
        db.execute(
            "UPDATE documents SET active=1 WHERE path=? AND source_key=? AND digest=?",
            (str(path), key, revision),
        )
        encoded = json.dumps(metadata, ensure_ascii=False)
        db.execute(
            "INSERT OR IGNORE INTO document_version_metadata VALUES(?,?,?,?)",
            (str(path), key, revision, encoded),
        )
        db.execute(
            "INSERT INTO resource_revisions VALUES(?,?,?,?,?,?,?) ON CONFLICT(path,source_key) DO UPDATE SET raw_digest=excluded.raw_digest,extractor=excluded.extractor,revision=excluded.revision,metadata=excluded.metadata,completed=excluded.completed",
            (str(path), key, raw_digest, EXTRACTOR, revision, encoded, time.time()),
        )
    return total


def index_csv_resources(store, profile_path, *, reserve_gib=32):
    profile = json.loads(Path(profile_path).read_text("utf-8"))
    if profile.get("errors"):
        raise ValueError("CSV frozen profile still contains errors")
    manifest = json.loads((store.root / "resources-manifest.json").read_text("utf-8"))
    resources = {
        normalized_original(
            str(Path(manifest["source"]) / safe_member(x["relative_path"]))
        ): x
        for x in manifest["completed"]
    }
    plans = []
    seen = set()
    for item in profile["completed"]:
        for alias in item["aliases"]:
            original = resources[normalized_original(alias["path"])]
            if original["sha256"] != item["container_sha256"]:
                raise ValueError("CSV profile container differs from retained manifest")
            key = archive_locator(alias["chain"]) if alias["chain"] else ""
            for relative, candidate in resources.items():
                identity = (relative, key)
                if candidate["sha256"] == original["sha256"] and identity not in seen:
                    seen.add(identity)
                    path = str(Path(manifest["source"]) / candidate["relative_path"])
                    plans.append((item, path, alias["chain"], candidate))
    result = {
        "requested": len(plans),
        "completed": [],
        "errors": [],
        "originals_not_modified": True,
    }
    checked = set()

    def progress(stage, **extra):
        store.write_json(
            "large-csv-index-progress.json",
            {
                "stage": stage,
                "pid": __import__("os").getpid(),
                "updated": time.time(),
                "completed": len(result["completed"]),
                "errors": len(result["errors"]),
                "total": len(plans),
                **extra,
            },
        )

    with tempfile.TemporaryDirectory(prefix="csv-index-", dir=store.root) as directory:
        work = Path(directory)
        parsed = {}
        for item, path, chain, resource in plans:
            store.checkpoint()
            progress("indexing")
            key = archive_locator(chain) if chain else ""
            try:
                if (
                    shutil.disk_usage(store.root).free
                    < reserve_gib * 1024**3 + item["bytes"] * 6
                ):
                    raise InterruptedError("CSV archive reserve would be exceeded")
                source = (store.root / resource["object"]).resolve()
                if not source.is_relative_to(
                    (store.root / "resources/objects").resolve()
                ):
                    raise ValueError("CSV object outside retained resource root")
                before = source.stat()
                state = (str(source), before.st_size, before.st_mtime_ns)
                if before.st_size != resource["bytes"] or (
                    state not in checked and digest(source) != resource["sha256"]
                ):
                    raise ValueError("Retained CSV container changed")
                checked.add(state)
                sha = item["raw_digest"]
                if (
                    not re.fullmatch("[a-f0-9]{64}", sha)
                    or not 0 <= item["bytes"] <= MAX_BYTES
                ):
                    raise ValueError("Invalid CSV byte profile")
                policy = tuple(
                    item.get(k, default)
                    for k, default in (
                        ("encoding", None),
                        ("delimiter", None),
                        ("quotechar", '"'),
                        ("doublequote", True),
                        ("skipinitialspace", False),
                    )
                )
                if sha in parsed and parsed[sha][2] != policy:
                    raise ValueError(
                        "Identical CSV bytes have conflicting audited parser policies"
                    )
                if sha not in parsed:
                    raw = work / (sha + ".raw")
                    hasher = hashlib.sha256()
                    copied = 0

                    def copy(stream, expected):
                        nonlocal copied
                        if expected != item["bytes"]:
                            raise ValueError(
                                "CSV input byte count differs from profile"
                            )
                        with raw.open("wb") as target:
                            while block := stream.read(1024**2):
                                store.checkpoint()
                                copied += len(block)
                                if copied > expected:
                                    raise ValueError(
                                        "CSV source exceeds audited byte count"
                                    )
                                target.write(block)
                                hasher.update(block)

                    if chain:
                        with open_archive_member(
                            source, chain, checkpoint=store.checkpoint
                        ) as (entry, stream):
                            copy(stream, entry.file_size)
                    else:
                        with source.open("rb") as stream:
                            copy(stream, before.st_size)
                    if copied != item["bytes"] or hasher.hexdigest() != sha:
                        raise ValueError("CSV full raw hash differs from profile")
                    spool = work / (sha + ".jsonl")
                    metadata = spool_rows(raw, spool, item, store.checkpoint)
                    parsed[sha] = (spool, metadata, policy)
                    raw.unlink()
                after = source.stat()
                if (before.st_size, before.st_mtime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise RuntimeError("Retained CSV changed during parsing")
                spool, metadata, _ = parsed[sha]
                for expected, field in (
                    ("rows_including_first", "rows"),
                    ("cells", "cells"),
                    ("characters", "characters"),
                    ("first_row_sha256", "first_row_sha256"),
                ):
                    if expected in item and item[expected] != metadata.get(field):
                        raise ValueError(
                            "Identical CSV bytes have conflicting audited counters"
                        )
                context = {**metadata, "archive_members": chain}
                previous = store.document_revision(path, key, sha, EXTRACTOR)
                cached = previous is not None
                if cached and previous != json.loads(json.dumps(context)):
                    raise RuntimeError(
                        "Committed CSV metadata differs; increment extractor version"
                    )
                if not cached:
                    publish_spool(
                        store,
                        path,
                        key,
                        sha,
                        spool,
                        context,
                        on_batch=lambda n: progress(
                            "staging_hidden_chunks",
                            source_chunks_staged=n,
                            source_chunks_expected=metadata["chunks"],
                        ),
                    )
                result["completed"].append(
                    {
                        "path": path,
                        "source_key": key,
                        "raw_digest": sha,
                        "rows": metadata["rows"],
                        "chunks": metadata["chunks"],
                        "cached": cached,
                    }
                )
            except InterruptedError:
                progress("paused")
                raise
            except Exception as exc:
                # Decode/parser exceptions can quote source bytes; keep diagnostics technical only.
                result["errors"].append(
                    {"path": path, "source_key": key, "error_type": type(exc).__name__}
                )
            store.write_json("large-csv-index-report.json", result)
    progress("finished_with_errors" if result["errors"] else "batch_finished")
    store.write_json("large-csv-index-report.json", result)
    return result


def main():
    import argparse
    from .course_knowledge import KnowledgeStore, schema_lease

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    store = KnowledgeStore(args.root)
    with schema_lease(store.root / "large-csv-index.lock", timeout=0):
        result = index_csv_resources(store, args.profile)
    print(
        json.dumps(
            {
                "completed": len(result["completed"]),
                "errors": len(result["errors"]),
                "requested": result["requested"],
            }
        )
    )


if __name__ == "__main__":
    main()
