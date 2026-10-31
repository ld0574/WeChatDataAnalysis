"""Read-only structural checks without random document-table index probes."""

from __future__ import annotations

import sys


def audit_document_locations(
    db, *, checkpoint=lambda: None, progress=lambda **kwargs: None,
    batch_size=4096, max_memory_bytes=768 * 1024**2,
):
    """Scan the caller's consistent transaction using exact SQLite BINARY keys.

    No text, paths or locators are returned or reported. NULLs are retained in
    the keys, matching GROUP BY rather than SQL UNIQUE's NULL semantics. The
    bounded Python ledger avoids an index scan's random overflow-page reads.
    Exceeding its budget raises; a partial scan is never marked accepted.
    """
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    if type(max_memory_bytes) is not int or max_memory_bytes < 1:
        raise ValueError("max_memory_bytes must be a positive integer")
    seen, shared = {}, {}
    payload_bytes = 0
    counts = dict(documents=0, active_documents=0,
                  duplicate_active_document_locations=0,
                  invalid_document_active_flags=0)

    def memory_used():
        return payload_bytes + sys.getsizeof(seen) + sys.getsizeof(shared)

    cursor = db.execute(
        "SELECT path,source_key,locator,active FROM documents NOT INDEXED"
    )
    try:
        while True:
            checkpoint()
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            for row in rows:
                path, source_key, locator, active = row
                counts['documents'] += 1
                if active not in (0, 1):
                    counts['invalid_document_active_flags'] += 1
                if active != 1:
                    continue
                counts['active_documents'] += 1
                # Paths and source keys are repeated across many chunks. Keep
                # their exact types/values while sharing their allocations.
                prefix = []
                for value in (path, source_key):
                    if value not in shared:
                        shared[value] = value
                        payload_bytes += sys.getsizeof(value)
                    prefix.append(shared[value])
                key = (prefix[0], prefix[1], locator)
                if key in seen:
                    if not seen[key]:
                        seen[key] = True
                        counts['duplicate_active_document_locations'] += 1
                else:
                    seen[key] = False
                    payload_bytes += sys.getsizeof(key) + sys.getsizeof(locator)
            retained_bytes = memory_used()
            if retained_bytes > max_memory_bytes:
                raise MemoryError("Document structural ledger exceeded its memory budget")
            progress(**counts, retained_key_bytes=retained_bytes)
    finally:
        cursor.close()
    return counts
