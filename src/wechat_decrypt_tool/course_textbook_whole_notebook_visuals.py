"""Recover literal notebook-image links and existing OCR without inference.

The complete-record inventory stays immutable. This private sidecar supplies
previously unlinked visual evidence for later courseware and editorial review;
neither an exact link nor accepted OCR storage establishes teaching coverage.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import io
import json
import posixpath
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
import zipfile

from markdown_it import MarkdownIt
from PIL import Image, ImageChops, ImageOps

from . import course_textbook_whole_sources as whole
from .course_attachments import safe_member
from .course_image_validation import accepted_body
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_video_visual_audit import ReadOnlyArchive

POLICY = "whole-record-notebook-literal-visual-links-existing-ocr-v1"
MAX_IMAGE_BYTES = 32 * 1024**2
MAX_PIXELS = 40_000_000


def require(ok, code):
    if not ok:
        raise ValueError("requires_whole_notebook_visuals_" + code)


def digest(value):
    return whole.sha(canonical(value))


def text(value):
    if isinstance(value, list):
        require(all(isinstance(part, str) for part in value), "literal_string_parts")
        value = "".join(value)
    require(isinstance(value, str), "literal_text")
    return value


class Images(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.references = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "img":
            values = dict(attrs)
            if values.get("src"):
                self.references.append(values["src"])
            if values.get("srcset"):
                # Retain this unresolved dependency rather than guess at its
                # comma-separated URL syntax (which can also contain data URLs).
                self.references.append("unsupported-srcset:" + values["srcset"])


def rendered_references(value):
    parser = MarkdownIt("commonmark", {"html": True})
    result = []

    def visit(tokens):
        for token in tokens:
            if token.type == "image":
                result.append(token.attrGet("src"))
            elif token.type in {"html_block", "html_inline"}:
                html = Images()
                html.feed(token.content)
                html.close()
                result.extend(html.references)
            if token.children:
                visit(token.children)

    visit(parser.parse(value))
    return result


def dependencies(notebook):
    """Record rendered, embedded and unsupported visual inputs; execute none."""
    require(notebook.get("nbformat") == 4 and isinstance(notebook.get("cells"), list), "notebook_v4")
    rows = []
    for number, cell in enumerate(notebook["cells"]):
        require(cell.get("cell_type") in {"markdown", "raw", "code"}, "known_cell_type")
        if cell["cell_type"] in {"markdown", "raw"}:
            for reference in rendered_references(text(cell.get("source", []))):
                rows.append({"cell": number, "component": "source", "reference": reference})
        for name, bundle in cell.get("attachments", {}).items():
            if any(mime.startswith("image/") for mime in bundle):
                rows.append({"cell": number, "component": "attachment", "reference": "attachment:" + name})
        for output, value in enumerate(cell.get("outputs", [])):
            for mime, data in value.get("data", {}).items():
                component = f"output-{output}-{mime}"
                if mime in {"text/html", "text/markdown"}:
                    for reference in rendered_references(text(data)):
                        rows.append({"cell": number, "component": component, "reference": reference})
                elif mime.startswith("image/") or mime in {"application/javascript", "application/vnd.jupyter.widget-view+json"}:
                    rows.append({"cell": number, "component": component, "reference": "unsupported-output:" + mime})
    return rows


def member_for(notebook_member, reference):
    """Resolve only a literal relative path inside the same retained archive."""
    url = urlsplit(reference)
    if url.scheme or url.netloc or url.query or url.fragment:
        return None
    path = unquote(url.path).replace("\\", "/")
    if not path or path.startswith("/") or ":" in path or "\x00" in path:
        return None
    member = posixpath.normpath(posixpath.join(posixpath.dirname(safe_member(notebook_member)), path))
    try:
        return safe_member(member)
    except Exception:
        return None


def bound_file(root, relative, namespace, files):
    path = (root / relative).resolve()
    require(path.is_relative_to((root / namespace).resolve()) and path.is_file(), "retained_file_namespace")
    files[path.relative_to(root).as_posix()] = whole.sha(path.read_bytes())
    return path


def existing_ocr(root, db, occurrence, original, files):
    """Use indexed source lookups, exact pixels and the original region tree."""
    key = "image visual " + occurrence["source_key"]
    revision = db.execute("SELECT * FROM resource_revisions WHERE path=? AND source_key=?",
        (occurrence["path"], key)).fetchone()
    require(revision is not None, "existing_ocr_revision")
    metadata = json.loads(revision["metadata"])
    binding = metadata["model"] + "@" + metadata["model_digest"]
    extractor = "teaching-image-vision-v1-overlap64-depth3/" + binding
    expected_revision = whole.sha((occurrence["sha256"] + "\0" + extractor + "\0" + key).encode())
    require(revision["raw_digest"] == occurrence["sha256"] and revision["extractor"] == extractor
        and revision["revision"] == expected_revision and metadata["parent_source_key"] == occurrence["source_key"]
        and metadata["parent_raw_digest"] == occurrence["sha256"]
        and all(metadata.get(k) is True for k in ("review_required", "static_image_only", "raw_source_retained")),
        "existing_ocr_source_identity")
    expected_image = "document-visuals/teaching-images/" + occurrence["sha256"] + "/image.png"
    require(metadata["image"].replace("\\", "/") == expected_image, "existing_normalized_locator")
    path = bound_file(root, metadata["image"], "document-visuals/teaching-images", files)
    require(whole.sha(path.read_bytes()) == metadata["image_digest"], "existing_normalized_hash")
    with Image.open(io.BytesIO(original)) as source, Image.open(path) as saved:
        require(getattr(source, "n_frames", 1) == 1 and source.width * source.height <= MAX_PIXELS,
            "bounded_static_original")
        source = ImageOps.exif_transpose(source).convert("RGBA")
        expected = Image.alpha_composite(Image.new("RGBA", source.size, "white"), source).convert("RGB")
        require(saved.mode == "RGB" and saved.size == expected.size
            and ImageChops.difference(saved, expected).getbbox() is None, "all_normalized_pixels")
        size = saved.size
    body = accepted_body(root, metadata, size)
    policy = "image-vision-v1-overlap64-depth3/" + binding
    cache = root / "document-visuals/image-ocr-cache" / whole.sha((metadata["image_digest"] + "\0" + policy).encode())
    for item in cache.glob("*.json"):
        bound_file(root, item, "document-visuals/image-ocr-cache", files)
    documents = db.execute("SELECT * FROM documents WHERE path=? AND source_key=? AND active=1",
        (occurrence["path"], key)).fetchall()
    chunks = {key + " chars " + str(n): body[n:n + 3000] for n in range(0, len(body), 3000)}
    require(len(documents) == len(chunks) and {d["locator"]: d["text"] for d in documents} == chunks
        and all(d["raw_digest"] == occurrence["sha256"] and d["digest"] == expected_revision
            and d["extractor"] == extractor for d in documents), "complete_existing_ocr_chunks")
    historical = db.execute("SELECT metadata FROM document_version_metadata WHERE path=? AND source_key=? AND revision=?",
        (occurrence["path"], key, expected_revision)).fetchone()
    require(historical is not None and json.loads(historical[0]) == metadata, "immutable_existing_ocr_metadata")
    return {"text": body, "image": metadata["image"], "image_sha256": metadata["image_digest"],
        "ocr_revision": expected_revision, "model": metadata["model"], "model_digest": metadata["model_digest"],
        "document_rows": [dict(d) for d in documents], "metadata": metadata, "review_required": True}


def reconstruct(root, inventory_key):
    root = Path(root).resolve()
    inventory = whole.load(root, inventory_key)
    files = {}
    for relative in ["resources-manifest.json", "teaching-image-audit.json", "images-ocr-report.json",
        "wiki/textbook-whole-record-evidence/" + inventory_key + "/manifest.json"]:
        bound_file(root, relative, ".", files)
    resources = whole.read(root / "resources-manifest.json")
    audit = whole.read(root / "teaching-image-audit.json")
    report = whole.read(root / "images-ocr-report.json")
    completed = {(r["path"], r["source_key"]) for r in report["completed"]}
    failed = {(r["path"], r["source_key"]) for r in report["errors"]}
    require(not completed.intersection(failed), "unambiguous_original_ocr_status")
    results, unresolved = [], []
    store = ReadOnlyArchive(root)
    with store.connect() as db:
        db.execute("BEGIN")
        for selected in inventory["attachment_scope"]["selected_notebooks"]:
            relative = "wiki/textbook-whole-record-evidence/" + inventory_key + "/" + selected["member_sha256"] + ".ipynb"
            path = bound_file(root, relative, "wiki/textbook-whole-record-evidence", files)
            raw = path.read_bytes()
            require(whole.sha(raw) == selected["member_sha256"], "actual_notebook_bytes")
            matches = [r for r in resources["completed"] if r["sha256"] == selected["archive_sha256"]]
            require(bool(matches), "retained_archive")
            archive_path = bound_file(root, matches[0]["object"], "resources/objects", files)
            require(files[archive_path.relative_to(root).as_posix()] == selected["archive_sha256"], "retained_archive_hash")
            with zipfile.ZipFile(archive_path) as archive:
                notebook_members = [m for m in archive.infolist() if m.filename.replace("\\", "/") == selected["member"]]
                require(len(notebook_members) == 1 and archive.read(notebook_members[0]) == raw, "exact_archived_notebook")
                for dependency in dependencies(json.loads(raw)):
                    direct = [ref for ref, row in inventory["sources"].items() if row["kind"] == "document"
                        and row["origin"]["member_sha256"] == selected["member_sha256"]
                        and row["origin"]["cell"] == dependency["cell"]]
                    origin = {**dependency, "notebook_sha256": selected["member_sha256"],
                        "archive_sha256": selected["archive_sha256"], "direct_text_source_ids": direct}
                    member = member_for(selected["member"], dependency["reference"])
                    if member is None:
                        unresolved.append({**origin, "state": "visual_dependency_requires_separate_recovery"})
                        continue
                    members = [m for m in archive.infolist() if m.filename.replace("\\", "/") == member]
                    if len(members) != 1 or members[0].is_dir() or members[0].flag_bits & 1:
                        unresolved.append({**origin, "state": "same_archive_member_missing_ambiguous_or_encrypted"})
                        continue
                    require(members[0].file_size <= MAX_IMAGE_BYTES, "bounded_archive_image")
                    image = archive.read(members[0])
                    image_sha = whole.sha(image)
                    candidates = [r for r in audit["image_occurrences"] if r["container_sha256"] == selected["archive_sha256"]
                        and r["source_key"].replace("\\", "/") == "archive " + member and r["sha256"] == image_sha]
                    require(len(candidates) == 1, "exact_same_archive_image_occurrence")
                    occurrence = candidates[0]
                    pointer = (occurrence["path"], occurrence["source_key"])
                    require(pointer in completed and pointer not in failed, "existing_successful_ocr_occurrence")
                    recovered = existing_ocr(root, db, occurrence, image, files)
                    results.append({"id": f"V{len(results):05d}", **origin, "member": member,
                        "raw_image_sha256": image_sha, "occurrence": occurrence, "existing_ocr": recovered,
                        "same_cell_link_verified": True, "semantic_coverage_verified": False})
        db.rollback()
    require(all(whole.sha((root / relative).read_bytes()) == expected for relative, expected in files.items()),
        "stable_retained_input_files")
    helpers = ["course_textbook_whole_sources", "course_textbook_evidence", "course_image_validation",
        "course_attachments", "course_video_visual_audit"]
    proof = {"policy": POLICY, "inventory": inventory_key, "inventory_sha256": digest(inventory),
        "module_sha256": whole.sha(Path(__file__).read_bytes()), "files": files,
        "helpers_sha256": {name: whole.sha(Path(__file__).with_name(name + ".py").read_bytes()) for name in helpers},
        "markdown_parser_version": importlib.metadata.version("markdown-it-py"),
        "model_calls": 0, "archive_database_read_only": True, "existing_ocr_semantics_accepted": False,
        "original_inventory_changed": False, "reader_publication_allowed": False, "source_deletion_allowed": False}
    return {"proof": proof, "visuals": results, "unresolved": unresolved,
        "visual_dependency_recovery_complete": not unresolved, "semantic_acceptance": False}


def run(root, inventory_key, *, verify=False):
    root = Path(root).resolve()
    folder = root / "wiki/textbook-whole-notebook-visuals" / whole.checked_sha(inventory_key)
    if not verify:
        whole.TextbookStore(root).checkpoint()
        require(not (root / "supervisor-stop.flag").exists(), "supervisor_stop_preserved")
        folder.mkdir(parents=True, exist_ok=True)
    require(folder.is_dir(), "existing_prepared_directory")
    with schema_lease(folder / "sidecar.lock", timeout=0):
        result = reconstruct(root, inventory_key)
        target = folder / "manifest.json"
        if target.exists():
            require(whole.read(target) == result, "immutable_recovered_sidecar")
        else:
            require(not verify, "existing_sidecar_for_verification")
            atomic_json(target, result)
        return {"state": "notebook_visual_links_reconstructed_without_inference" if verify else "notebook_visual_links_recovered_for_private_review",
            "inventory": inventory_key, "images": len(result["visuals"]),
            "unique_images": len({r["raw_image_sha256"] for r in result["visuals"]}),
            "unresolved_dependencies": len(result["unresolved"]),
            "visual_dependency_recovery_complete": result["visual_dependency_recovery_complete"], "model_calls": 0,
            "proof_sha256": digest(result["proof"]), "manifest_sha256": whole.sha(target.read_bytes()),
            "reader_publication_allowed": False, "source_deletion_allowed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("command", choices=["prepare", "verify"])
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.root, args.inventory, verify=args.command == "verify")))
    except Exception as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
