"""Index recorded notebook outputs from retained bytes; never execute notebook code."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from .course_attachments import archive_members, resolve_reference, retained_reference
from .course_text_resources import redact_credentials

EXTRACTOR = "notebook-recorded-outputs-v2/strict-json-credential-mask"
SOURCE_EXTRACTOR = "retained-notebook-source-v1/strict-json-credential-mask"
NOTEBOOK_CREDENTIAL = re.compile(
    r"(?:^|_)(?:(?:api|private|secret|access|refresh|auth|bearer|session)_?(?:key|token)|password|passwd|secret|credentials?|authorization|pwd)$",
    re.I,
)
MAX_BYTES = 16 * 1024**2
MAX_CHARACTERS = 32 * 1024**2


def source_chunks(raw, checkpoint=lambda: None):
    if len(raw) > MAX_BYTES:
        raise ValueError("Notebook input byte bound exceeded; raw retained")
    notebook = json.loads(raw.decode("utf-8-sig"))
    chunks = []
    for n, cell in enumerate(notebook["cells"], 1):
        checkpoint()
        value = cell.get("source", [])
        if isinstance(value, list) and all(isinstance(line, str) for line in value):
            value = "".join(value)
        if not isinstance(value, str):
            raise ValueError("Malformed notebook source cell; raw retained")
        value, _, _ = redact_credentials(value, sensitive_pattern=NOTEBOOK_CREDENTIAL)
        chunks.extend(
            ("cell " + str(n) + " chars " + str(offset), value[offset : offset + 3000])
            for offset in range(0, len(value), 3000)
        )
    return chunks, {
        "pages": len(notebook["cells"]),
        "code_executed": False,
        "raw_source_retained": True,
        "source_cells_recovered_from_retained_notebook": True,
    }


def output_chunks(raw, checkpoint=lambda: None):
    if len(raw) > MAX_BYTES:
        raise ValueError("Notebook input byte bound exceeded; raw retained")
    notebook = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
        raise ValueError("Unsupported notebook structure; raw retained")
    chunks, pending, parts = [], [], []
    characters = outputs = representations = redactions = 0

    def text(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list) and all(isinstance(line, str) for line in value):
            return "".join(value)
        raise ValueError("Malformed notebook text representation; raw retained")

    def masked_json(value):
        nonlocal redactions
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if NOTEBOOK_CREDENTIAL.search(key):
                    result[key] = "[REDACTED]"
                    redactions += 1
                else:
                    result[key] = masked_json(item)
            return result
        if isinstance(value, list):
            return [masked_json(item) for item in value]
        return value

    def emit(locator, value):
        nonlocal characters, redactions
        value, masked, _ = redact_credentials(
            value, sensitive_pattern=NOTEBOOK_CREDENTIAL
        )
        redactions += masked
        characters += len(value)
        if characters > MAX_CHARACTERS:
            raise ValueError("Notebook output text bound exceeded; raw retained")
        parts.append({"locator": locator, "characters": len(value)})
        for offset in range(0, len(value), 3000):
            checkpoint()
            chunks.append(
                (locator + " chars " + str(offset), value[offset : offset + 3000])
            )

    for cell_number, cell in enumerate(notebook["cells"], 1):
        checkpoint()
        if not isinstance(cell, dict) or not isinstance(cell.get("outputs", []), list):
            raise ValueError("Malformed notebook cell outputs; raw retained")
        prefix = "cell " + str(cell_number)
        for name, attachment in cell.get("attachments", {}).items():
            if not isinstance(attachment, dict):
                raise ValueError("Malformed notebook attachment; raw retained")
            for mime in attachment:
                pending.append(
                    {
                        "locator": prefix + " attachment " + name,
                        "mime": mime,
                        "reason": "embedded attachment retained, visual interpretation pending",
                    }
                )
        for number, output in enumerate(cell.get("outputs", []), 1):
            if not isinstance(output, dict):
                raise ValueError("Malformed notebook output; raw retained")
            outputs += 1
            locator = prefix + " output " + str(number)
            kind = output.get("output_type")
            if kind == "stream":
                emit(
                    locator + " stream " + str(output.get("name", "")),
                    text(output.get("text", "")),
                )
            elif kind == "error":
                for field in ("ename", "evalue"):
                    emit(locator + " " + field, text(output.get(field, "")))
                traceback = output.get("traceback", [])
                if not isinstance(traceback, list):
                    raise ValueError("Malformed notebook traceback; raw retained")
                for line, value in enumerate(traceback, 1):
                    emit(locator + " traceback " + str(line), text(value))
            elif kind in ("execute_result", "display_data", "update_display_data"):
                data = output.get("data")
                if not isinstance(data, dict):
                    raise ValueError("Malformed notebook MIME bundle; raw retained")
                for mime, value in sorted(data.items()):
                    location = locator + " mime " + mime
                    if mime.startswith("text/") or mime == "application/javascript":
                        emit(location, text(value))
                        representations += 1
                        if mime in {"text/html", "application/javascript"}:
                            pending.append(
                                {
                                    "locator": location,
                                    "mime": mime,
                                    "reason": "inert markup/code indexed, rendered visual/runtime interpretation pending",
                                }
                            )
                    elif mime == "application/json" or mime.endswith("+json"):
                        emit(
                            location,
                            json.dumps(
                                masked_json(value),
                                ensure_ascii=False,
                                allow_nan=False,
                                sort_keys=True,
                            ),
                        )
                        representations += 1
                        if mime.startswith("application/vnd."):
                            pending.append(
                                {
                                    "locator": location,
                                    "mime": mime,
                                    "reason": "vendor JSON indexed, rendered visual/runtime interpretation pending",
                                }
                            )
                    else:
                        pending.append(
                            {
                                "locator": location,
                                "mime": mime,
                                "reason": "nontext MIME retained, visual/runtime interpretation pending",
                            }
                        )
            else:
                pending.append(
                    {
                        "locator": locator,
                        "output_type": kind,
                        "reason": "unsupported recorded output type, raw retained",
                    }
                )
    return chunks, {
        "cells": len(notebook["cells"]),
        "recorded_outputs": outputs,
        "textual_mime_representations": representations,
        "characters_indexed": characters,
        "parts": parts,
        "credential_assignments_or_json_fields_redacted": redactions,
        "pending_visual_or_unsupported_representations": pending,
        "html_and_javascript_indexed_as_inert_text": True,
        "code_executed": False,
        "raw_source_retained": True,
    }


def index_notebook_outputs(store, audit_path):
    audit = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    targets = audit["completed"]
    completed, errors, parsed, seen = [], [], {}, set()

    def progress(stage):
        store.write_json(
            "notebook-outputs-progress.json",
            {
                "stage": stage,
                "pid": __import__("os").getpid(),
                "updated": time.time(),
                "completed": len(completed),
                "errors": len(errors),
                "total": len(targets),
            },
        )

    for target in targets:
        store.checkpoint()
        pointer = (target["path"], target["source_key"])
        if pointer in seen:
            raise ValueError("Duplicate notebook audit source identity")
        seen.add(pointer)
        progress("indexing_retained_notebook_outputs")
        try:
            members = archive_members(target["source_key"])
            if (
                Path(members[-1] if members else target["path"]).suffix.lower()
                != ".ipynb"
            ):
                raise ValueError("Audited source is not a notebook")
            with store.connect() as db:
                parent = db.execute(
                    "SELECT raw_digest FROM resource_revisions WHERE path=? AND source_key=?",
                    pointer,
                ).fetchone()
            if parent is not None and parent[0] != target["raw_digest"]:
                raise RuntimeError("Audited notebook parent revision changed")
            result = resolve_reference(
                store, retained_reference(store, *pointer, target["raw_digest"])
            )
            path = result["path"]
            if path.stat().st_size > MAX_BYTES:
                raise ValueError("Retained notebook byte bound exceeded")
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            if sha != target["raw_digest"]:
                raise RuntimeError("Notebook differs from audited source revision")
            if parent is None:
                cells, source_metadata = source_chunks(raw, store.checkpoint)
                store.save_document_versions(
                    [
                        {
                            "path": target["path"],
                            "source_key": target["source_key"],
                            "raw_digest": sha,
                            "extractor": SOURCE_EXTRACTOR,
                            "chunks": [
                                (target["source_key"] + " " + location, text)
                                for location, text in cells
                            ],
                            "metadata": {
                                **source_metadata,
                                "historical_checkpoint": ".ipynb_checkpoints"
                                in (target["path"] + target["source_key"]),
                            },
                        }
                    ]
                )
            key = "notebook outputs " + target["source_key"]
            metadata = store.document_revision(target["path"], key, sha, EXTRACTOR)
            cached = metadata is not None
            if metadata is None:
                if sha not in parsed:
                    parsed[sha] = output_chunks(raw, store.checkpoint)
                chunks, metadata = parsed[sha]
                store.save_document_versions(
                    [
                        {
                            "path": target["path"],
                            "source_key": key,
                            "raw_digest": sha,
                            "extractor": EXTRACTOR,
                            "chunks": [
                                (key + " " + location, text)
                                for location, text in chunks
                            ],
                            "metadata": {
                                **metadata,
                                "parent_source_key": target["source_key"],
                                "parent_raw_digest": sha,
                            },
                        }
                    ]
                )
            completed.append(
                {
                    **target,
                    "cached": cached,
                    "recorded_outputs": metadata["recorded_outputs"],
                    "pending_representations": len(
                        metadata["pending_visual_or_unsupported_representations"]
                    ),
                }
            )
        except InterruptedError:
            progress("paused")
            raise
        except Exception as exc:
            errors.append({**target, "error": type(exc).__name__ + ": " + str(exc)})
        store.write_json(
            "notebook-outputs-report.json",
            {"completed": completed, "errors": errors, "requested": len(targets)},
        )
    result = {
        "completed": completed,
        "errors": errors,
        "requested": len(targets),
        "originals_retained": True,
        "code_executed": False,
    }
    store.write_json("notebook-outputs-report.json", result)
    progress("finished_with_errors" if errors else "batch_finished")
    return result
