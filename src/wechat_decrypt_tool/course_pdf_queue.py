"""Sequential finite PDF batches; stop on failure, pause, or changed inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import psutil
from pypdf import PdfReader

from .course_attachments import resolve_reference, retained_reference
from .course_gpu_guard import run as guarded
from .course_knowledge import (
    KnowledgeStore,
    atomic_json,
    gpu_lease,
    local_chat,
    schema_lease,
)
from .course_pdf_batch_backup import backup_batch
from .course_pdf_ocr import local_model_digest, run_pdf_ocr
from .course_pdf_plan import frozen_pdf_scope
from .course_pdf_retry import RetryReportStore
from .course_video_visual_audit import ReadOnlyArchive

RUNTIME = (
    "course_pdf_ocr.py",
    "course_pdf_retry.py",
    "course_pdf_validation.py",
    "course_tiled_ocr.py",
    "course_attachments.py",
    "course_knowledge.py",
    "course_gpu_guard.py",
    "course_pdf_plan.py",
    "course_pdf_batch_backup.py",
    "course_pdf_queue.py",
    "course_video_visual_audit.py",
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def require(ok, reason):
    if not ok:
        raise ValueError("pdf_queue_" + reason)


def checkpoint(root, *, reserve_gib=32):
    if (root / "pause.flag").exists() or (
        root / "pdf-selected-queue-stop.flag"
    ).exists():
        raise InterruptedError("pdf_queue_pause_requested")
    volumes = {root.anchor, Path.home().anchor}
    if min(shutil.disk_usage(v).free for v in volumes) < reserve_gib * 1024**3:
        raise InterruptedError("pdf_queue_disk_reserve_reached")


def alive(identity):
    try:
        p = psutil.Process(identity["pid"])
        return p.is_running() and abs(p.create_time() - identity["created"]) < 0.001
    except psutil.NoSuchProcess:
        return False


def inside(root, name):
    path = (root / name).resolve()
    require(
        path.is_relative_to(root.resolve()) and path.is_file(),
        "input_outside_archive_or_missing",
    )
    return path


def queue_plans(folder):
    """Hash-check the whole proposed queue and prove batches are disjoint."""
    folder = Path(folder).resolve()
    entries = read(folder / "validation.json")["batches"]
    scopes, numbers, result = set(), set(), []
    for entry in entries:
        number = entry["number"]
        require(
            type(number) is int and number > 0 and number not in numbers,
            "invalid_batch_number",
        )
        numbers.add(number)
        path = inside(folder, entry["plan"])
        require(sha(path.read_bytes()) == entry["plan_sha256"], "plan_changed")
        scope = frozen_pdf_scope(read(path))
        raw_pages = {(v["parent_raw_digest"], k[2]) for k, v in scope.items()}
        require(
            len(raw_pages)
            == len(scope)
            == entry["unique_raw_pages"]
            == entry["page_occurrences"],
            "scope_count_differs",
        )
        require(not scopes.intersection(raw_pages), "overlapping_raw_pages")
        scopes.update(raw_pages)
        result.append((number, path))
    return sorted(result)


def verify_backup(receipt):
    destination = Path(receipt["backup"]).resolve()
    raw = (destination / "manifest.json").read_bytes()
    require(sha(raw) == receipt["manifest_sha256"], "predecessor_manifest_changed")
    manifest = json.loads(raw)
    require(
        manifest["page_occurrences"] == receipt["page_occurrences"],
        "predecessor_scope_differs",
    )
    require(
        len(manifest["files"]) == receipt["files"], "predecessor_file_count_differs"
    )
    for item in manifest["files"]:
        path = inside(destination, item["path"])
        require(
            path.stat().st_size == item["bytes"]
            and sha(path.read_bytes()) == item["sha256"],
            "predecessor_backup_changed",
        )
    return sha(raw)


def require_unattempted(store, scope):
    all_sources = read(store.root / "pdf-all-pages-retained-plan.json")[
        "pdf_sources_all_pages"
    ]
    renderers = [
        p
        for p in (store.root / "document-visuals").glob("*")
        if p.is_dir() and "-scale2000-dpi150" in p.name
    ]
    with store.connect() as db:
        for (_, _, page), target in scope.items():
            aliases = [
                s
                for s in all_sources
                if s["parent_raw_digest"] == target["parent_raw_digest"]
            ]
            require(aliases, "full_scope_alias_missing")
            for alias in aliases:
                identity = (
                    alias["path"],
                    "visual " + alias["source_key"] + " page " + str(page),
                )
                for table in (
                    "resource_revisions",
                    "documents",
                    "document_version_metadata",
                ):
                    require(
                        db.execute(
                            "SELECT 1 FROM "
                            + table
                            + " WHERE path=? AND source_key=? LIMIT 1",
                            identity,
                        ).fetchone()
                        is None,
                        "page_has_prior_history",
                    )
            require(
                all(
                    not list((p / target["parent_raw_digest"]).glob(f"{page:05}*"))
                    for p in renderers
                ),
                "page_has_prior_render",
            )


def prepare(
    root,
    plan_path,
    prefix,
    template,
    predecessor,
    inputs_base,
    results_base,
    *,
    reserve_gib=32,
):
    """Bind retained native sources and runtime before launching any GPU work."""
    root = Path(root).resolve()
    checkpoint(root, reserve_gib=reserve_gib)
    require(re.fullmatch(r"pdf-queue-[0-9]{8}-[0-9]{4}", prefix), "invalid_prefix")
    require(not list(root.glob(prefix + "*")), "batch_already_prepared")
    predecessor_path = inside(root, predecessor)
    verify_backup(read(predecessor_path))
    plan_path = inside(root, plan_path)
    plan_raw = plan_path.read_bytes()
    scope = frozen_pdf_scope(json.loads(plan_raw))
    require(0 < len(scope) <= 64, "finite_batch_too_large")
    store = ReadOnlyArchive(root)
    require_unattempted(store, scope)
    require(
        local_model_digest(template["model"]) == template["model_digest"],
        "model_changed",
    )
    renderer = Path(template["renderer"])
    require(
        sha(renderer.read_bytes()) == template["renderer_sha256"], "renderer_changed"
    )
    sources, counts = {}, {}
    for (path, key, _), target in scope.items():
        digest = target["parent_raw_digest"]
        if digest in sources:
            require(counts[digest] == target["pages"], "native_count_conflict")
            continue
        source = resolve_reference(store, retained_reference(store, path, key, digest))[
            "path"
        ].resolve()
        require(
            source.is_relative_to(root / "resources")
            and sha(source.read_bytes()) == digest,
            "native_source_changed",
        )
        with source.open("rb") as stream:
            count = len(PdfReader(stream).pages)
        require(count == target["pages"], "native_page_count_differs")
        sources[digest], counts[digest] = str(source), count
    preserved = dict(template["preserved_reports"])
    preserved[str(predecessor_path.relative_to(root))] = sha(
        predecessor_path.read_bytes()
    )
    for name, digest in preserved.items():
        require(
            sha(inside(root, name).read_bytes()) == digest, "preserved_report_changed"
        )
    inputs = Path(inputs_base).resolve() / prefix
    results = Path(results_base).resolve() / prefix
    for path in (inputs, results):
        require(
            not path.exists()
            and not path.is_relative_to(root)
            and not root.is_relative_to(path),
            "backup_destination_unavailable",
        )
    needed = sum(Path(p).stat().st_size for p in sources.values()) + 8 * 1024**2
    for path in (root, inputs.parent, results.parent):
        ancestor = path
        while not ancestor.exists():
            ancestor = ancestor.parent
        require(
            shutil.disk_usage(ancestor).free >= needed + reserve_gib * 1024**3,
            "backup_reserve_reached",
        )
    runtime = root / (prefix + "-runtime")
    runtime.mkdir()
    hashes = {}
    for name in RUNTIME:
        raw = Path(__file__).with_name(name).read_bytes()
        hashes[name] = sha(raw)
        (runtime / name).write_bytes(raw)
    (root / (prefix + "-plan.json")).write_bytes(plan_raw)
    extra = [(runtime / name).relative_to(root).as_posix() for name in RUNTIME]
    extra += [prefix + "-binding.json", prefix + "-prepared.json"]
    binding = {
        k: template[k] for k in ("model", "model_digest", "renderer", "renderer_sha256")
    }
    binding.update(
        plan_sha256=sha(plan_raw),
        page_occurrences=len(scope),
        sources=sources,
        full_native_page_counts=counts,
        preserved_reports=preserved,
        runtime_source_sha256=hashes,
        result_destination=str(results),
        result_extra_files=extra,
        minimum_free_gib=reserve_gib,
        maximum_power_w=200,
        maximum_temperature_c=70,
        automatic_thermal_resume=False,
    )
    atomic_json(root / (prefix + "-binding.json"), binding)
    files = {}
    inputs.mkdir(parents=True)
    candidates = {
        "archive/" + name: root / name for name in [prefix + "-plan.json", *extra[:-1]]
    }
    candidates.update(
        {
            "original-pdf/" + digest + ".pdf": Path(path)
            for digest, path in sources.items()
        }
    )
    for name, path in candidates.items():
        raw = path.read_bytes()
        target = inputs / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(raw)
        require(sha(target.read_bytes()) == sha(raw), "input_backup_changed")
        files[name] = {"bytes": len(raw), "sha256": sha(raw)}
    atomic_json(inputs / "manifest.json", {"files": files, "inputs_only": True})
    prepared = {
        "backup": str(inputs),
        "manifest_sha256": sha((inputs / "manifest.json").read_bytes()),
        "files": len(files),
    }
    atomic_json(root / (prefix + "-prepared.json"), prepared)
    return binding


def validate_inputs(root, prefix):
    binding = read(root / (prefix + "-binding.json"))
    prepared = read(root / (prefix + "-prepared.json"))
    backup = Path(prepared["backup"])
    raw = (backup / "manifest.json").read_bytes()
    require(sha(raw) == prepared["manifest_sha256"], "input_manifest_changed")
    for name, item in json.loads(raw)["files"].items():
        require(
            sha(inside(backup, name).read_bytes()) == item["sha256"],
            "input_backup_changed",
        )
        if name.startswith("archive/"):
            require(
                sha(inside(root, name.removeprefix("archive/")).read_bytes())
                == item["sha256"],
                "bound_input_changed",
            )
    for name, digest in binding["runtime_source_sha256"].items():
        require(
            sha(Path(__file__).with_name(name).read_bytes()) == digest,
            "runtime_changed",
        )
    for name, digest in binding["preserved_reports"].items():
        require(
            sha(inside(root, name).read_bytes()) == digest, "preserved_report_changed"
        )
    plan = inside(root, prefix + "-plan.json")
    require(sha(plan.read_bytes()) == binding["plan_sha256"], "plan_changed")
    require(
        len(frozen_pdf_scope(read(plan))) == binding["page_occurrences"],
        "scope_count_differs",
    )
    for digest, name in binding["sources"].items():
        source = inside(root, name)
        require(
            source.is_relative_to(root / "resources")
            and sha(source.read_bytes()) == digest,
            "native_source_changed",
        )
    require(
        sha(Path(binding["renderer"]).read_bytes()) == binding["renderer_sha256"],
        "renderer_changed",
    )
    require(
        local_model_digest(binding["model"]) == binding["model_digest"], "model_changed"
    )
    return binding


def worker(root, prefix):
    binding = validate_inputs(root, prefix)
    checkpoint(root)
    original = Path(read(root / "resources-manifest.json")["source"]).resolve()

    def audit(event, args):
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            if Path(os.fsdecode(args[0])).resolve().is_relative_to(original):
                raise PermissionError("pdf_queue_original_course_read_forbidden")

    sys.addaudithook(audit)
    store = KnowledgeStore(root)
    store.checkpoint = lambda: checkpoint(root)
    report_store = RetryReportStore(store, prefix=prefix)
    plan = root / (prefix + "-plan.json")
    with schema_lease(root / "pdf-ocr.lock", timeout=0):
        require_unattempted(store, frozen_pdf_scope(read(plan)))
        report = run_pdf_ocr(
            report_store,
            [plan],
            original,
            binding["model"],
            local_chat,
            gpu_lease,
            poppler=Path(binding["renderer"]),
            wait=True,
        )
    require(
        report["processed"] == binding["page_occurrences"] and not report["errors"],
        "batch_incomplete",
    )
    result = backup_batch(
        store,
        plan,
        root / (prefix + "-ocr-report.json"),
        Path(binding["renderer"]),
        binding["result_destination"],
        prefix=prefix,
        extra_files=[root / n for n in binding["result_extra_files"]],
    )
    validate_inputs(root, prefix)
    verify_backup(result)
    require(
        result["page_occurrences"] == binding["page_occurrences"]
        and result["full_native_page_counts"] == binding["full_native_page_counts"],
        "result_scope_differs",
    )
    atomic_json(
        root / (prefix + "-validation.json"),
        {
            **result,
            "state": "finite_batch_verified_and_backed_up",
            "source_deletion_allowed": False,
        },
    )


def drain(
    root,
    queue,
    template_path,
    after_prefix,
    driver,
    prior_worker,
    first,
    last,
    inputs,
    results,
    *,
    sleep=time.sleep,
):
    """Wait for exact predecessors, then execute each disjoint plan once."""
    root = Path(root).resolve()
    queue = inside(root, Path(queue) / "validation.json").parent
    plans = [(n, p) for n, p in queue_plans(queue) if first <= n <= last]
    require(
        [n for n, _ in plans] == list(range(first, last + 1)), "batch_range_missing"
    )
    template_file = inside(root, template_path)
    template = read(template_file)
    bound_controls = {
        path: sha(path.read_bytes())
        for path in [
            queue / "validation.json",
            template_file,
            *[p for _, p in plans],
            *[Path(__file__).with_name(name) for name in RUNTIME],
        ]
    }

    def controls_unchanged():
        for path, digest in bound_controls.items():
            require(sha(path.read_bytes()) == digest, "controller_input_changed")

    identity = {"pid": os.getpid(), "created": psutil.Process().create_time()}
    state = {
        "first": first,
        "last": last,
        "after_prefix": after_prefix,
        "completed_batches": [],
    }

    def progress(phase, **extra):
        state.update(extra)
        atomic_json(
            root / "pdf-selected-queue-progress.json",
            {
                **identity,
                "state": phase,
                "updated": time.time(),
                "source_deletion_allowed": False,
                "automatic_retry_allowed": False,
                "automatic_thermal_resume": False,
                **state,
            },
        )

    with schema_lease(root / "pdf-selected-queue.lock", timeout=0):
        try:
            with (root / "pdf-selected-queue-launched.json").open(
                "x", encoding="utf8"
            ) as stream:
                json.dump(
                    {
                        **identity,
                        **state,
                        "bound_control_sha256": {
                            str(p): digest for p, digest in bound_controls.items()
                        },
                    },
                    stream,
                )
            while alive(driver) or alive(prior_worker):
                checkpoint(root)
                controls_unchanged()
                progress(
                    "waiting_for_exact_predecessor_processes",
                    driver=driver,
                    worker=prior_worker,
                )
                sleep(20)
            predecessor = after_prefix + "-validation.json"
            verify_backup(read(inside(root, predecessor)))
            for number, plan in plans:
                checkpoint(root)
                controls_unchanged()
                prefix = "pdf-queue-20261103-" + f"{number:04}"
                progress("binding_next_disjoint_batch", number=number, prefix=prefix)
                prepare(root, plan, prefix, template, predecessor, inputs, results)
                progress(
                    "running_one_guarded_finite_batch", number=number, prefix=prefix
                )
                guarded(
                    root,
                    [
                        sys.executable,
                        "-m",
                        "wechat_decrypt_tool.course_pdf_queue",
                        "--root",
                        str(root),
                        "worker",
                        "--prefix",
                        prefix,
                    ],
                    maximum_temperature=70,
                    maximum_power=200,
                    guard_name="extraction-thermal-guard",
                )
                checkpoint(root)
                predecessor = prefix + "-validation.json"
                proof = read(inside(root, predecessor))
                verify_backup(proof)
                controls_unchanged()
                state["completed_batches"].append(
                    {
                        "number": number,
                        "prefix": prefix,
                        "page_occurrences": proof["page_occurrences"],
                        "manifest_sha256": proof["manifest_sha256"],
                    }
                )
                progress(
                    "finite_batch_terminal_backup_verified",
                    number=number,
                    prefix=prefix,
                    page_occurrences=proof["page_occurrences"],
                )
            progress("requested_finite_queue_finished", first=first, last=last)
        except BaseException as exc:
            progress(
                "paused" if isinstance(exc, InterruptedError) else "failed",
                error_type=type(exc).__name__,
                error_sha256=sha(str(exc).encode()),
            )
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    work = sub.add_parser("worker")
    work.add_argument("--prefix", required=True)
    run = sub.add_parser("drain")
    for name in ("queue", "template", "after-prefix", "inputs", "results"):
        run.add_argument("--" + name, required=True)
    for role in ("driver", "worker"):
        run.add_argument("--" + role + "-pid", type=int, required=True)
        run.add_argument("--" + role + "-created", type=float, required=True)
    run.add_argument("--first", type=int, required=True)
    run.add_argument("--last", type=int, required=True)
    args = parser.parse_args()
    if args.action == "worker":
        worker(args.root.resolve(), args.prefix)
    else:
        drain(
            args.root,
            args.queue,
            args.template,
            args.after_prefix,
            {"pid": args.driver_pid, "created": args.driver_created},
            {"pid": args.worker_pid, "created": args.worker_created},
            args.first,
            args.last,
            args.inputs,
            args.results,
        )


if __name__ == "__main__":
    main()
