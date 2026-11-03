"""Immutable source-note deltas against a previously verified reader manifest.

No extraction/model jobs or SQLite access occur here. Existing backup chains
and original publications are never modified; incomplete copies remain visible.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import time

from .course_backup import inside
from .course_knowledge import atomic_json, schema_lease
from .course_library import Library
from .course_wiki_snapshot import _images

POLICY = "published-source-note-chain-delta-v1"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path):
    return json.loads(Path(path).read_bytes())


def checkpoint(root, destination, reserve_gib):
    if (root / "pause.flag").exists():
        raise InterruptedError("library_delta_pause_requested")
    if any(
        shutil.disk_usage(path).free < reserve_gib * 1024**3
        for path in (root, destination)
    ):
        raise InterruptedError("library_delta_disk_reserve_reached")


def file_rows(manifest):
    rows = {}
    for item in manifest["files"]:
        name = item["path"]
        relative = PurePosixPath(name)
        if (PureWindowsPath(name).drive or relative.is_absolute()
                or "\\" in name or ":" in name or ".." in relative.parts
                or name != relative.as_posix() or name == "."):
            raise ValueError("library_delta_invalid_base_path")
        if (
            name in rows
            or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
            or type(item["bytes"]) is not int
            or item["bytes"] < 0
        ):
            raise ValueError("library_delta_invalid_base_files")
        rows[name] = item
    return rows


def available(catalog):
    rows = {}
    for row in catalog["lessons"]:
        if row["state"] == "draft_available":
            if row["id"] in rows:
                raise ValueError("library_delta_duplicate_source")
            rows[row["id"]] = row
    return rows


def verify_notes(root, client, catalog, changed):
    """Check frozen HTTP objects, every new section search and every new image."""
    library, checks = Library(root), []
    ready = available(catalog)
    for key in changed:
        note = library.object(ready[key]["artifact"])
        response = client.get("/library/api/lesson/" + key)
        response.raise_for_status()
        if response.json() != note:
            raise ValueError("library_delta_http_note_changed")
        for section in note["sections"]:
            if not section.get("concepts"):
                raise ValueError("library_delta_section_without_search_concept")
            offset, seen = 0, set()
            while True:
                if offset in seen:
                    raise ValueError("library_delta_search_cycle")
                seen.add(offset)
                response = client.get(
                    "/library/api/search",
                    params={"q": section["concepts"][0]["name"], "video": key,
                            "limit": 100, "offset": offset},
                )
                response.raise_for_status()
                page = response.json()
                if any(row.get("section") == section["id"] for row in page["items"]):
                    break
                offset = page["next_offset"]
                if offset is None:
                    raise ValueError("library_delta_section_not_searchable")
        images = {row["image_sha256"] for row in _images(note)}
        for sha in images:
            response = client.get("/library/frame/" + sha)
            response.raise_for_status()
            if digest(response.content) != sha:
                raise ValueError("library_delta_http_image_changed")
        checks.append({"source": key, "sections_search_verified": len(note["sections"]),
                       "images_verified": len(images), "http_note_exact": True})
    return checks


def save(root, parent, base_manifest, base_catalog, expected_base_sha256, *,
         required_manifests=(), client=None, reserve_gib=32):
    """Save only changed immutable objects; bind a complete predecessor chain.

    A composed manifest is a dependency inventory, not a standalone archive.
    The caller supplies the previous independently verified chain, whose exact
    manifest identities remain explicit in every result. New deltas are checked
    byte-for-byte here; independent restored-reader acceptance stays separate.
    """
    root, parent = Path(root).resolve(), Path(parent).resolve()
    base_manifest, base_catalog = Path(base_manifest).resolve(), Path(base_catalog).resolve()
    if (
        not parent.is_dir()
        or parent.is_relative_to(root)
        or root.is_relative_to(parent)
        or type(reserve_gib) is not int
        or reserve_gib < 0
    ):
        raise ValueError("library_delta_invalid_destination_or_reserve")
    checkpoint(root, parent, reserve_gib)
    with schema_lease(root / "library-delta.lock", timeout=0):
        base_raw = base_manifest.read_bytes()
        if digest(base_raw) != expected_base_sha256:
            raise ValueError("library_delta_base_manifest_changed")
        baseline = load(base_manifest)
        entries = file_rows(baseline)
        old_raw = base_catalog.read_bytes()
        if digest(old_raw) != entries["library/catalog.json"]["sha256"]:
            raise ValueError("library_delta_base_catalog_changed")
        old = available(json.loads(old_raw))
        # Capture pointer bytes once. Later pointer advances cannot corrupt this
        # version: every dependency below is addressed by its frozen hash.
        catalog_raw = (root / "library/catalog.json").read_bytes()
        catalog = json.loads(catalog_raw)
        ready = available(catalog)
        if not old.keys() <= ready.keys():
            raise ValueError("library_delta_previous_source_lost")
        changed = sorted(key for key in ready if old.get(key, {}).get("artifact") != ready[key]["artifact"])
        if not changed:
            return {"state": "no_new_source_note_objects", "published_notes": len(ready),
                    "files_written": 0, "model_calls": 0}
        references = {str(base_manifest): expected_base_sha256}
        inherited = list(baseline.get("required_manifests", []))
        sibling = base_manifest.parent / "source-note-delta.json"
        if sibling.is_file():
            prior_delta = load(sibling)
            if prior_delta.get("policy") == POLICY:
                if prior_delta["combined_manifest_sha256"] != expected_base_sha256:
                    raise ValueError("library_delta_prior_delta_manifest_changed")
                inherited.append({"path": str(sibling), "sha256": digest(sibling.read_bytes())})
        if (any(key.endswith("manifest_sha256") for key in baseline)
                and not inherited and not required_manifests):
            raise ValueError("library_delta_unbound_predecessor_chain")
        for reference in [*inherited, *required_manifests]:
            path = Path(reference["path"]).resolve()
            if digest(path.read_bytes()) != reference["sha256"]:
                raise ValueError("library_delta_required_manifest_changed")
            if str(path) in references and references[str(path)] != reference["sha256"]:
                raise ValueError("library_delta_conflicting_predecessor")
            references[str(path)] = reference["sha256"]
        inventory = (root / "inventory-unique.json").read_bytes()
        if digest(inventory) != catalog["inventory_sha256"]:
            raise ValueError("library_delta_inventory_changed")
        pointers = {"library/catalog.json": catalog_raw, "inventory-unique.json": inventory}
        files = {}

        def add(name, expected=None):
            path = inside(root, name)
            raw = pointers[name] if name in pointers else path.read_bytes()
            sha = digest(raw)
            if expected is not None and sha != expected:
                raise ValueError("library_delta_dependency_changed")
            if entries.get(name, {}).get("sha256") != sha:
                files[name] = {"sha256": sha, "bytes": len(raw)}

        for name in pointers:
            add(name)
        terms = catalog["terms_artifact"]
        add("library/objects/" + terms + ".json", terms)
        library = Library(root)
        for key in changed:
            artifact = ready[key]["artifact"]
            note = library.object(artifact)
            if note["id"] != key:
                raise ValueError("library_delta_note_identity_changed")
            add("library/objects/" + artifact + ".json", artifact)
            for image in _images(note):
                sha = image["image_sha256"]
                name = "library/media/" + sha + ".jpg"
                if entries.get(name, {}).get("sha256") != sha:
                    add(name, sha)
        checks = verify_notes(root, client, catalog, changed) if client is not None else []
        needed = sum(row["bytes"] for row in files.values())
        if shutil.disk_usage(parent).free < needed + len(base_raw) + 64 * 1024**2 + reserve_gib * 1024**3:
            raise InterruptedError("library_delta_disk_reserve_reached")
        destination = parent / (str(len(ready)) + "-" + digest(catalog_raw)[:24])
        if destination.exists():
            raise ValueError("library_delta_destination_exists_inspect_retained_output")
        destination.mkdir()
        for name, row in files.items():
            checkpoint(root, parent, reserve_gib)
            target = inside(destination / "files", name)
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = pointers[name] if name in pointers else inside(root, name).read_bytes()
            if len(raw) != row["bytes"] or digest(raw) != row["sha256"]:
                raise ValueError("library_delta_dependency_changed_during_copy")
            target.write_bytes(raw)
            if digest(target.read_bytes()) != row["sha256"]:
                raise ValueError("library_delta_backup_bytes_changed")
        for name, row in files.items():
            entries[name] = {"path": name, **row}
        combined = {**baseline, "id": destination.name, "created": time.time(),
                    "files": [entries[name] for name in sorted(entries)],
                    "required_manifests": [{"path": path, "sha256": sha} for path, sha in references.items()]}
        atomic_json(destination / "combined-wiki-snapshot.json", combined)
        manifest = {"policy": POLICY, "base_composed_manifest_sha256": expected_base_sha256,
                    "required_manifests": combined["required_manifests"], "files": files,
                    "bytes": needed, "published_notes": len(ready), "changed_sources": changed,
                    "new_sources": sorted(ready.keys() - old.keys()), "http_checks": checks,
                    "catalog_sha256": digest(catalog_raw), "combined_manifest_sha256": digest((destination / "combined-wiki-snapshot.json").read_bytes()),
                    "standalone_reader": False, "independent_execution_verified": False,
                    "semantic_accuracy_verified": False, "source_deletion_allowed": False,
                    "updated": time.time()}
        atomic_json(destination / "source-note-delta.json", manifest)
        return {**{key: value for key, value in manifest.items() if key != "files"},
                "state": "source_note_delta_bytes_verified", "backup": str(destination),
                "files_written": len(files), "model_calls": 0,
                "delta_manifest_sha256": digest((destination / "source-note-delta.json").read_bytes())}
