"""Check frozen PDF scopes without treating selected pages as entire documents."""

from __future__ import annotations

import re


def frozen_pdf_scope(plan):
    """Return exact page identities; ``pages`` always means the full native count."""
    if not isinstance(plan, dict) or not isinstance(plan.get("summary"), dict):
        raise ValueError("Invalid PDF frozen source plan")
    if "pdf_sources_selected_pages" in plan and (
        plan.get("pdf_pages_needing_ocr") or plan.get("pdf_warnings")
    ):
        raise ValueError("Selected PDF plans cannot mix legacy page requests")
    expected, sources, digests = {}, {}, {}
    for field in ("pdf_sources_all_pages", "pdf_sources_selected_pages"):
        rows = plan.get(field, [])
        if not isinstance(rows, list):
            raise ValueError("Invalid PDF frozen source plan")
        for target in rows:
            if not (
                isinstance(target, dict)
                and isinstance(target.get("path"), str)
                and target["path"]
                and isinstance(target.get("source_key"), str)
                and type(target.get("pages")) is int
                and 0 < target["pages"] <= 2000
                and isinstance(target.get("parent_raw_digest"), str)
                and re.fullmatch("[a-f0-9]{64}", target["parent_raw_digest"])
            ):
                raise ValueError("Invalid PDF frozen source plan")
            if field == "pdf_sources_selected_pages":
                pages = target.get("selected_pages")
                if not (
                    isinstance(pages, list)
                    and pages
                    and all(type(p) is int and 1 <= p <= target["pages"] for p in pages)
                    and len(set(pages)) == len(pages)
                ):
                    raise ValueError("Invalid PDF selected pages")
            else:
                if "selected_pages" in target:
                    raise ValueError("All-page PDF source cannot also select pages")
                pages = range(1, target["pages"] + 1)
            source = (target["path"], target["source_key"])
            binding = (target["parent_raw_digest"], target["pages"])
            if source in sources and sources[source] != binding:
                raise ValueError("Conflicting PDF frozen source revisions or counts")
            if binding[0] in digests and digests[binding[0]] != binding[1]:
                raise ValueError("Conflicting PDF native counts for identical digest")
            sources[source], digests[binding[0]] = binding, binding[1]
            for page in pages:
                identity = (*source, page)
                if identity in expected:
                    raise ValueError("PDF frozen plan has duplicate page occurrences")
                expected[identity] = target
    count = plan["summary"].get("page_occurrences")
    if type(count) is not int or count != len(expected) or not expected:
        raise ValueError("PDF frozen plan page count differs")
    return expected


def selected_pdf_targets(reports):
    """Canonical explicit scopes cannot be silently widened by legacy warnings."""
    all_rows, selected_rows, count = [], [], 0
    for report in reports:
        if report.get("pdf_pages_needing_ocr") or report.get("pdf_warnings"):
            raise ValueError("Selected PDF plans cannot mix legacy page requests")
        count += len(frozen_pdf_scope(report))
        all_rows.extend(report.get("pdf_sources_all_pages", []))
        selected_rows.extend(report.get("pdf_sources_selected_pages", []))
    frozen_pdf_scope(
        {
            "pdf_sources_all_pages": all_rows,
            "pdf_sources_selected_pages": selected_rows,
            "summary": {"page_occurrences": count},
        }
    )
    return [
        {
            "path": row["path"],
            "source_key": row["source_key"],
            "page": None,
            "expected_digest": row["parent_raw_digest"],
            "expected_pages": row["pages"],
            **({"selected_pages": row["selected_pages"]} if selected else {}),
        }
        for selected, rows in ((False, all_rows), (True, selected_rows))
        for row in rows
    ]
