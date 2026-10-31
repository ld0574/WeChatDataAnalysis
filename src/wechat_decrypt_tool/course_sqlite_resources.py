"""Index ordinary SQLite tables without executing source-defined SQL objects."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from .course_tabular_resources import index_audited_resources


def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def sqlite_chunks(raw, checkpoint=lambda: None):
    if len(raw) > 64 * 1024**2 or not raw.startswith(b"SQLite format 3\x00"):
        raise ValueError("Unsupported SQLite input; raw retained")
    chunks, counts = [], {"rows": 0, "tables": 0, "binary_cells": 0}
    omitted = []
    text_bytes = 0

    def append(location, records):
        nonlocal text_bytes
        body = "\n".join(records)
        text_bytes += len(body.encode("utf-8"))
        if text_bytes > 64 * 1024**2:
            raise ValueError("SQLite indexed text bound exceeded; raw retained")
        chunks.append((location, body))

    def typed(value):
        if isinstance(value, bytes):
            counts["binary_cells"] += 1
            return {
                "type": "blob",
                "bytes": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
                "raw_retained": True,
            }
        if isinstance(value, float) and not math.isfinite(value):
            return {"type": "real", "value": str(value)}
        return {
            "type": {str: "text", int: "integer", float: "real", type(None): "null"}[
                type(value)
            ],
            "value": value,
        }

    # Parse only a disposable copy; never attach the original or execute schema SQL.
    with tempfile.TemporaryDirectory(prefix="course-sqlite-") as directory:
        path = Path(directory) / "source.db"
        path.write_bytes(raw)
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as db:
            db.enable_load_extension(False)
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8 * 1024**2)
            db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1024 * 1024)
            schema = db.execute(
                "SELECT type,name,sql FROM sqlite_schema ORDER BY type,name"
            ).fetchall()
            table_types = {
                row[1]: row[2] for row in db.execute("PRAGMA main.table_list")
            }
            for kind, name, sql in schema:
                checkpoint()
                append(
                    "schema " + kind + " " + name,
                    [
                        json.dumps(
                            {"kind": kind, "name": name, "sql": sql, "executed": False},
                            ensure_ascii=False,
                        )
                    ],
                )
                if (
                    kind != "table"
                    or name.startswith("sqlite_")
                    or table_types.get(name) != "table"
                ):
                    if kind in ("table", "view"):
                        omitted.append(
                            {
                                "name": name,
                                "kind": table_types.get(name, kind),
                                "reason": "source-defined or internal object not executed",
                            }
                        )
                    continue
                fields = db.execute(
                    "PRAGMA main.table_xinfo(" + quote_identifier(name) + ")"
                ).fetchall()
                columns = [row[1] for row in fields if row[6] == 0]
                hidden = [row[1] for row in fields if row[6] != 0]
                if hidden:
                    omitted.append(
                        {
                            "name": name,
                            "columns": hidden,
                            "reason": "generated or hidden expressions not evaluated",
                        }
                    )
                if not columns:
                    continue
                allowed = set(columns)

                def authorize(action, arg1, arg2, database, origin):
                    if action == sqlite3.SQLITE_SELECT:
                        return sqlite3.SQLITE_OK
                    if (
                        action == sqlite3.SQLITE_READ
                        and database == "main"
                        and arg1 == name
                        and arg2 in allowed
                        and origin is None
                    ):
                        return sqlite3.SQLITE_OK
                    return sqlite3.SQLITE_DENY

                db.set_authorizer(authorize)
                try:
                    # No ORDER BY expressions/functions; source revision + row ordinal
                    # identifies each result in this immutable table scan.
                    query = (
                        "SELECT "
                        + ",".join(map(quote_identifier, columns))
                        + " FROM main."
                        + quote_identifier(name)
                        + " NOT INDEXED"
                    )
                    records, length, part, table_rows = [], 0, 0, 0
                    for row in db.execute(query):
                        checkpoint()
                        counts["rows"] += 1
                        table_rows += 1
                        if counts["rows"] > 500000:
                            raise ValueError("SQLite row bound exceeded; raw retained")
                        record = json.dumps(
                            {
                                "table": name,
                                "row_ordinal": table_rows,
                                "values": {
                                    column: typed(value)
                                    for column, value in zip(columns, row)
                                },
                            },
                            ensure_ascii=False,
                            allow_nan=False,
                        )
                        if records and length + len(record) > 6000:
                            append(f"table {name} part {part}", records)
                            records, length, part = [], 0, part + 1
                        records.append(record)
                        length += len(record)
                    if records:
                        append(f"table {name} part {part}", records)
                    counts["tables"] += 1
                finally:
                    db.set_authorizer(None)
    return chunks, {
        **counts,
        "omitted_objects": omitted,
        "source_sql_executed": False,
        "row_locator": "immutable revision/table scan ordinal",
        "binary_values": "digest and size; exact bytes in retained raw source",
    }


def index_sqlite_resources(store, audit_path):
    def validate(item):
        if item.get("sidecar_members"):
            raise ValueError(
                "SQLite sidecars require separate reconciliation; raw retained"
            )

    return index_audited_resources(
        store,
        audit_path,
        parser=sqlite_chunks,
        kind="sqlite",
        extractor="sqlite-ordinary-tables-v1/sqlite-" + sqlite3.sqlite_version,
        max_bytes=64 * 1024**2,
        validate_item=validate,
        forbid_sibling_suffixes=("-wal", "-shm", "-journal"),
    )
