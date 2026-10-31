"""Literal SVG XML text and source positioning, independent of renderer/model output."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

from .course_attachments import archive_members
from .course_notebook_outputs import NOTEBOOK_CREDENTIAL
from .course_notebook_rich import masked
from .course_svg_visuals import inspect_static_svg
from .course_tabular_resources import index_audited_resources
from .course_text_resources import redact_credentials

EXTRACTOR = "svg-literal-xml-text-v1/positions-not-layout/credential-mask"
TEXT_TAGS = {"text", "tspan", "textPath", "title", "desc"}
NO_TEXT = {
    "record_kind": "no_xml_text",
    "raw_source_retained": True,
    "outlined_or_bitmap_text_not_claimed_as_unicode": True,
}


def xml_records(raw, checkpoint=lambda: None):
    inspect_static_svg(raw)
    root = ElementTree.fromstring(raw.decode("utf-8-sig"))
    records = []
    total = masked_containers = 0

    def tag(node):
        return node.tag.rsplit("}", 1)[-1]

    def attributes(node):
        return masked(dict(node.attrib))

    def walk(node, path, ancestors, inside_text=False):
        nonlocal total, masked_containers
        checkpoint()
        if len(path) > 128:
            raise ValueError("SVG XML source nesting bound exceeded")
        container = tag(node) in TEXT_TAGS
        if container and not inside_text:
            original_text = "".join(node.itertext())
            safe_text, count, _ = redact_credentials(
                original_text, sensitive_pattern=NOTEBOOK_CREDENTIAL
            )
            masked_containers += bool(count)
            runs = []

            def record_run(child, child_path):
                checkpoint()
                if len(child_path) > 128:
                    raise ValueError("SVG text-run nesting bound exceeded")
                runs.append(
                    {
                        "xml_path": child_path,
                        "tag": tag(child),
                        "attributes": attributes(child),
                        "text": "[REDACTED: credential-bearing container]"
                        if count and child.text
                        else (child.text or ""),
                        "tail": "[REDACTED: credential-bearing container]"
                        if count and child is not node and child.tail
                        else (child.tail or "")
                        if child is not node
                        else "",
                    }
                )
                for number, nested in enumerate(child):
                    record_run(nested, child_path + [number])

            record_run(node, path)
            records.append(
                {
                    "record_kind": "literal_xml_text",
                    "xml_path": path,
                    "tag": tag(node),
                    "text": safe_text,
                    "runs": runs,
                    "ancestors": ancestors,
                    "credential_assignments_masked": count,
                    "source_positions_not_resolved_into_rendered_layout": True,
                    "source_visibility_not_inferred": True,
                }
            )
            total += len(original_text)
        if not inside_text and not container:
            ancestor = {
                "xml_path": path,
                "tag": tag(node),
                "attributes": attributes(node),
            }
            for number, child in enumerate(node):
                walk(child, path + [number], ancestors + [ancestor], False)

    walk(root, [], [])
    if not records:
        records = [dict(NO_TEXT)]
    encoded_size = sum(len(json.dumps(x, ensure_ascii=False)) for x in records)
    if encoded_size > 32 * 1024**2:
        raise ValueError("SVG literal XML text output bound exceeded")
    return records, {
        "text_containers": sum(x["record_kind"] == "literal_xml_text" for x in records),
        "recorded_source_text_characters": total,
        "credential_bearing_containers_masked": masked_containers,
        "xml_source_only": True,
        "source_code_executed": False,
        "positioning_and_style_not_resolved": True,
        "outlined_and_embedded_bitmap_text_require_separate_ocr": True,
    }


def svg_text_chunks(raw, checkpoint=lambda: None):
    records, metadata = xml_records(raw, checkpoint)
    chunks = []
    for number, record in enumerate(records):
        body = json.dumps(record, ensure_ascii=False, sort_keys=True)
        for offset in range(0, len(body), 3000):
            checkpoint()
            chunks.append(
                (
                    f"svg XML record {number:06d} chars {offset:010d}",
                    body[offset : offset + 3000],
                )
            )
    return chunks, metadata


def index_svg_texts(store, audit_path):
    audit = json.loads(Path(audit_path).read_text("utf-8"))
    targets = [
        x
        for x in audit["image_occurrences"]
        if x["state"] == "vector_raw_retained_render_audit_pending"
    ]
    plan = {
        "completed": [
            {
                "path": x["path"],
                "chain": archive_members(x["source_key"]),
                "raw_digest": x["sha256"],
            }
            for x in targets
        ],
        "source_occurrences_before_alias_expansion": len(targets),
        "frozen_teaching_image_audit": str(audit_path),
    }
    store.write_json("svg-source-text-audit.json", plan)
    return index_audited_resources(
        store,
        store.root / "svg-source-text-audit.json",
        parser=svg_text_chunks,
        kind="svg-source-text",
        extractor=EXTRACTOR,
    )


def main():
    import argparse
    from .course_knowledge import KnowledgeStore, schema_lease

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    store = KnowledgeStore(args.root)
    with schema_lease(store.root / "svg-source-text.lock", timeout=0):
        result = index_svg_texts(store, args.audit)
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
