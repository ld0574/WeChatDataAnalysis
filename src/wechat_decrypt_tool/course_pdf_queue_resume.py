"""Resume a paused finite PDF batch from validated committed pages and caches."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import psutil
from PIL import Image

from . import course_pdf_queue as queue
from .course_knowledge import KnowledgeStore, atomic_json, schema_lease
from .course_library import canonical
from .course_pdf_batch_backup import backup_batch
from .course_pdf_plan import frozen_pdf_scope
from .course_pdf_retry import RetryReportStore
from .course_pdf_validation import TILED_POLICY, native_render, verify_pdf_archive

POLICY = "validated-paused-finite-pdf-batch-continuation-v1"


def require(ok, reason):
    if not ok:
        raise ValueError("pdf_queue_resume_" + reason)


def paths(root, prefix):
    require(
        bool(re.fullmatch(r"pdf-queue-[0-9]{8}-[0-9]{4}", prefix)), "invalid_prefix"
    )
    root = Path(root).resolve()
    return (
        root,
        root / (prefix + "-resume-binding.json"),
        root / (prefix + "-resume-prepared.json"),
    )


def snapshot(store, scope, binding):
    renderer = (
        queue.sha(Path(binding["renderer"]).read_bytes())[:16] + "-scale2000-dpi150"
    )
    suffix = renderer + "/" + binding["model"] + "@" + binding["model_digest"]
    accepted = {"pdf-vision-v2/" + suffix, "pdf-vision-v3-tiled-v1/" + suffix}
    records = []
    with store.connect() as db:
        for identity, target in scope.items():
            path, parent, page = identity
            key = "visual " + parent + " page " + str(page)
            tables = {
                table: [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM "
                        + table
                        + " WHERE path=? AND source_key=? ORDER BY rowid",
                        (path, key),
                    )
                ]
                for table in (
                    "resource_revisions",
                    "documents",
                    "document_version_metadata",
                )
            }
            if not any(tables.values()):
                continue
            revisions = tables["resource_revisions"]
            require(
                len(revisions) == 1
                and tables["documents"]
                and tables["document_version_metadata"],
                "incomplete_existing_page_history",
            )
            row = revisions[0]
            require(
                row["raw_digest"] == target["parent_raw_digest"]
                and row["extractor"] in accepted,
                "existing_page_provenance_changed",
            )
            require(
                all(d["digest"] == row["revision"] for d in tables["documents"])
                and any(d["active"] for d in tables["documents"]),
                "existing_page_revision_changed",
            )
            records.append({"identity": list(identity), "tables": tables})
    return records


def require_preserved_rows(store, scope, binding, baseline):
    current = {tuple(row["identity"]): row for row in snapshot(store, scope, binding)}
    require(
        all(current.get(tuple(row["identity"])) == row for row in baseline),
        "committed_page_history_changed",
    )


def retained_assets(root, scope, binding, *, committed, rerender=None):
    renderer = (
        queue.sha(Path(binding["renderer"]).read_bytes())[:16] + "-scale2000-dpi150"
    )
    assets = set()
    native = rerender or native_render
    with tempfile.TemporaryDirectory(
        prefix="pdf-resume-pixels-", dir=root
    ) as temporary:
        for identity, target in scope.items():
            page, digest = identity[2], target["parent_raw_digest"]
            folder = root / "document-visuals" / renderer / digest
            image = folder / f"{page:05}.png"
            assets.update(path for path in folder.glob(f"{page:05}*") if path.is_file())
            if not image.is_file():
                continue
            if identity not in committed:
                rendered = Path(temporary) / "page.png"
                native(
                    Path(binding["sources"][digest]),
                    page,
                    rendered,
                    Path(binding["renderer"]),
                )
                with Image.open(image) as saved, Image.open(rendered) as replay:
                    require(
                        saved.size == replay.size
                        and saved.convert("RGBA").tobytes()
                        == replay.convert("RGBA").tobytes(),
                        "uncommitted_page_pixels_changed",
                    )
            image_digest = queue.sha(image.read_bytes())
            crops = root / "document-visuals/tiled-images" / image_digest
            for crop in crops.rglob("*"):
                if not crop.is_file():
                    continue
                assets.add(crop)
                policy = (
                    TILED_POLICY
                    + "/"
                    + binding["model"]
                    + "@"
                    + binding["model_digest"]
                )
                identity_key = queue.sha(
                    (queue.sha(crop.read_bytes()) + "\0" + policy).encode()
                )
                cache = root / "document-visuals/tile-ocr-cache" / identity_key
                assets.update(path for path in cache.rglob("*") if path.is_file())
    return assets


def prepare(root, prefix, destination, *, reserve_gib=32, rerender=None):
    root, contract, receipt_path = paths(root, prefix)
    queue.checkpoint(root, reserve_gib=reserve_gib)
    require(not contract.exists() and not receipt_path.exists(), "already_prepared")
    binding = queue.validate_inputs(root, prefix)
    progress_path = root / (prefix + "-ocr-progress.json")
    progress = queue.read(progress_path)
    require(
        progress["stage"] == "paused" and progress["errors"] == 0,
        "requires_paused_error_free_batch",
    )
    try:
        worker = psutil.Process(progress["pid"])
        require(
            worker.create_time() > progress["updated"] + 1,
            "original_worker_still_alive",
        )
    except psutil.NoSuchProcess:
        pass
    require(
        not (root / (prefix + "-validation.json")).exists()
        and not Path(binding["result_destination"]).exists(),
        "batch_already_has_terminal_output",
    )
    destination = Path(destination).resolve()
    require(
        not destination.exists()
        and not destination.is_relative_to(root)
        and not root.is_relative_to(destination),
        "backup_destination_unavailable",
    )
    plan = root / (prefix + "-plan.json")
    scope = frozen_pdf_scope(queue.read(plan))
    store = KnowledgeStore(root)
    store.checkpoint = lambda: queue.checkpoint(root, reserve_gib=reserve_gib)
    with (
        schema_lease(root / "pdf-ocr.lock", timeout=0),
        schema_lease(root / "pdf-independent-validation.lock", timeout=0),
    ):
        baseline = snapshot(store, scope, binding)
        require(
            type(progress["processed"]) is int
            and len(baseline) == progress["processed"] < len(scope),
            "paused_counter_differs_from_committed_scope",
        )
        committed = {tuple(row["identity"]) for row in baseline}
        verification_name = prefix + "-resume-preflight-validation.json"
        if baseline:
            verification = verify_pdf_archive(
                store,
                plan,
                Path(binding["renderer"]),
                mode="committed",
                result_name=verification_name,
                rerender=rerender,
            )
            require(
                verification["verified_completed_page_occurrences"] == len(baseline),
                "partial_independent_validation_count_differs",
            )
        else:
            atomic_json(
                root / verification_name,
                {
                    "mode": "committed",
                    "verified_completed_page_occurrences": 0,
                    "all_requested_pdf_page_occurrences_verified": False,
                    "source_deletion_allowed": False,
                },
            )
        require_preserved_rows(store, scope, binding, baseline)
        row_path = root / (prefix + "-resume-original-rows.json")
        atomic_json(row_path, {"records": baseline, "committed_pages": len(baseline)})
        original_files = [
            path
            for path in root.glob(prefix + "*")
            if path.is_file() and "-resume-" not in path.name
        ]
        protected = {
            str(path.relative_to(root)): queue.sha(path.read_bytes())
            for path in original_files
        }
        runtime = root / (prefix + "-resume-runtime.py")
        runtime.write_bytes(Path(__file__).read_bytes())
        files = {
            "archive/" + str(path.relative_to(root)).replace("\\", "/"): path
            for path in [*original_files, row_path, root / verification_name, runtime]
        }
        for name in queue.RUNTIME:
            path = root / (prefix + "-runtime") / name
            files["archive/" + path.relative_to(root).as_posix()] = path
        assets = retained_assets(
            root, scope, binding, committed=committed, rerender=rerender
        )
        for path in [*assets, *map(Path, binding["sources"].values())]:
            require(
                path.resolve().is_relative_to(root) and not path.is_symlink(),
                "retained_asset_outside_archive",
            )
            files["archive/" + path.relative_to(root).as_posix()] = path
        controls = {
            "policy": POLICY,
            "prefix": prefix,
            "original_binding_sha256": queue.sha(
                (root / (prefix + "-binding.json")).read_bytes()
            ),
            "protected_original_files": protected,
            "original_committed_pages": len(baseline),
            "page_occurrences": len(scope),
            "module_sha256": queue.sha(Path(__file__).read_bytes()),
            "input_backup": str(destination),
            "minimum_free_gib": reserve_gib,
            "automatic_retry_allowed": False,
            "automatic_thermal_resume_allowed": False,
        }
        needed = (
            sum(path.stat().st_size for path in files.values())
            + len(canonical(controls))
            + 8 * 1024**2
        )
        ancestor = destination.parent
        while not ancestor.exists():
            ancestor = ancestor.parent
        require(
            shutil.disk_usage(ancestor).free >= needed + reserve_gib * 1024**3,
            "input_backup_reserve_reached",
        )
        atomic_json(contract, controls)
        files["archive/" + contract.relative_to(root).as_posix()] = contract
        destination.mkdir(parents=True)
        entries = []
        for name, source in sorted(files.items()):
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = source.read_bytes()
            target.write_bytes(raw)
            require(
                queue.sha(target.read_bytes()) == queue.sha(raw), "input_backup_changed"
            )
            entries.append({"path": name, "bytes": len(raw), "sha256": queue.sha(raw)})
        atomic_json(
            destination / "manifest.json",
            {
                "policy": POLICY,
                "page_occurrences": len(scope),
                "committed_pages": len(baseline),
                "files": entries,
                "partial_snapshot_only": True,
                "source_deletion_allowed": False,
            },
        )
        receipt = {
            "backup": str(destination),
            "manifest_sha256": queue.sha((destination / "manifest.json").read_bytes()),
            "files": len(entries),
            "page_occurrences": len(scope),
            "committed_pages": len(baseline),
        }
        queue.verify_backup(receipt)
        atomic_json(receipt_path, receipt)
        return {
            "prefix": prefix,
            "state": "paused_scope_validated_and_backed_up",
            **receipt,
        }


def inputs(root, prefix):
    root, contract, receipt = paths(root, prefix)
    queue.checkpoint(root)
    binding = queue.validate_inputs(root, prefix)
    control = queue.read(contract)
    require(
        control["policy"] == POLICY
        and control["prefix"] == prefix
        and control["module_sha256"] == queue.sha(Path(__file__).read_bytes()),
        "continuation_runtime_changed",
    )
    require(
        control["original_binding_sha256"]
        == queue.sha((root / (prefix + "-binding.json")).read_bytes()),
        "original_binding_changed",
    )
    for name, digest in control["protected_original_files"].items():
        require(
            queue.sha(queue.inside(root, name).read_bytes()) == digest,
            "original_report_changed",
        )
    proof = queue.read(receipt)
    queue.verify_backup(proof)
    require(
        str(Path(proof["backup"]).resolve()) == control["input_backup"],
        "input_backup_binding_changed",
    )
    manifest = queue.read(Path(proof["backup"]) / "manifest.json")
    for entry in manifest["files"]:
        if entry["path"].startswith("archive/"):
            path = queue.inside(root, entry["path"][len("archive/") :])
            require(
                queue.sha(path.read_bytes()) == entry["sha256"],
                "preserved_partial_asset_changed",
            )
    baseline = queue.read(root / (prefix + "-resume-original-rows.json"))["records"]
    require(
        len(baseline)
        == control["original_committed_pages"]
        == proof["committed_pages"],
        "partial_snapshot_count_changed",
    )
    return binding, control, baseline


def worker(root, prefix):
    root = Path(root).resolve()
    status_path = root / (prefix + "-resume-status.json")
    if status_path.exists():
        previous = queue.read(status_path)
        require(
            previous["state"] != "failed", "previous_terminal_failure_requires_review"
        )
        require(not queue.alive(previous), "continuation_worker_still_alive")
    identity = {"pid": os.getpid(), "created": psutil.Process().create_time()}

    def status(state, **extra):
        atomic_json(
            status_path,
            {
                **identity,
                "state": state,
                "updated": time.time(),
                "prefix": prefix,
                "automatic_retry_allowed": False,
                "source_deletion_allowed": False,
                **extra,
            },
        )

    status("checking_bound_partial_snapshot")
    try:
        binding, control, baseline = inputs(root, prefix)
        store = KnowledgeStore(root)
        store.checkpoint = lambda: queue.checkpoint(root)
        original = Path(
            queue.read(root / "resources-manifest.json")["source"]
        ).resolve()

        def audit(event, args):
            if (
                event == "open"
                and isinstance(args[0], (str, bytes, os.PathLike))
                and Path(os.fsdecode(args[0])).resolve().is_relative_to(original)
            ):
                raise PermissionError("pdf_queue_resume_original_course_read_forbidden")

        sys.addaudithook(audit)
        plan = root / (prefix + "-plan.json")
        scope = frozen_pdf_scope(queue.read(plan))
        with schema_lease(root / "pdf-ocr.lock", timeout=0):
            require_preserved_rows(store, scope, binding, baseline)
            status(
                "continuing_validated_partial_batch",
                original_committed_pages=len(baseline),
                total_pages=len(scope),
            )
            report_prefix = prefix + "-resume"
            report = queue.run_pdf_ocr(
                RetryReportStore(store, report_prefix),
                [plan],
                original,
                binding["model"],
                queue.local_chat,
                queue.gpu_lease,
                poppler=Path(binding["renderer"]),
                wait=True,
            )
            require(
                report["processed"] == len(scope)
                and not report["errors"]
                and not report["deferred_gpu"],
                "continued_batch_incomplete",
            )
            require_preserved_rows(store, scope, binding, baseline)
            inputs(root, prefix)
            extra = [root / name for name in binding["result_extra_files"]]
            extra += [
                root / (prefix + suffix)
                for suffix in (
                    "-ocr-progress.json",
                    "-resume-binding.json",
                    "-resume-prepared.json",
                    "-resume-runtime.py",
                    "-resume-original-rows.json",
                    "-resume-preflight-validation.json",
                )
            ]
            proof = backup_batch(
                store,
                plan,
                root / (report_prefix + "-ocr-report.json"),
                Path(binding["renderer"]),
                binding["result_destination"],
                prefix=prefix,
                extra_files=extra,
            )
            queue.verify_backup(proof)
            inputs(root, prefix)
            require(
                proof["page_occurrences"] == binding["page_occurrences"]
                and proof["full_native_page_counts"]
                == binding["full_native_page_counts"],
                "terminal_scope_differs",
            )
            atomic_json(
                root / (prefix + "-validation.json"),
                {
                    **proof,
                    "state": "finite_batch_verified_and_backed_up",
                    "original_committed_pages_preserved": len(baseline),
                    "resumed_from_validated_partial_scope": True,
                    "source_deletion_allowed": False,
                },
            )
            status(
                "finite_batch_verified_and_backed_up",
                page_occurrences=len(scope),
                original_committed_pages_preserved=len(baseline),
            )
    except BaseException as exc:
        status(
            "paused"
            if isinstance(exc, InterruptedError) or (root / "pause.flag").exists()
            else "failed",
            error_type=type(exc).__name__,
            error_sha256=queue.sha(str(exc).encode()),
        )
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("command", choices=["prepare", "worker", "guard"])
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            require(args.backup is not None, "input_backup_required")
            print(json.dumps(prepare(args.root, args.prefix, args.backup)))
        elif args.command == "worker":
            worker(args.root, args.prefix)
        else:
            inputs(args.root, args.prefix)
            queue.guarded(
                args.root,
                [
                    sys.executable,
                    "-m",
                    __spec__.name,
                    "--root",
                    str(args.root.resolve()),
                    "worker",
                    "--prefix",
                    args.prefix,
                ],
                maximum_power=200,
                maximum_temperature=70,
                guard_name="extraction-thermal-guard",
            )
    except Exception as exc:
        print(json.dumps({"state": "stopped", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
