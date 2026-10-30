"""Read-only native slide rendering with source hashes and resumable PNG evidence."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from xml.etree import ElementTree


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024**2), b""):
            h.update(block)
    return h.hexdigest()


def slide_targets(reports):
    groups = {}
    for report in reports:
        for row in report.get("visual_pages_needing_ocr", []):
            if row.get("kind") != "pptx":
                continue
            match = re.fullmatch(r"(.*) slide (\d+)", row["location"])
            if not match or int(match[2]) < 1:
                raise ValueError("Invalid slide locator")
            groups.setdefault((row["path"], match[1]), set()).add(int(match[2]))
    return [
        {"path": p, "source_key": key, "pages": sorted(pages)}
        for (p, key), pages in groups.items()
    ]


def validate_deck(path):
    """Avoid activating macros or linked data while rendering course input."""
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            if entry.filename.lower().endswith("vbaproject.bin"):
                raise ValueError("Macro payload requires separate review")
            if not entry.filename.endswith(".rels"):
                continue
            if entry.file_size > 16 * 1024**2:
                raise ValueError("Relationship XML exceeds bound")
            for relation in ElementTree.fromstring(archive.read(entry)):
                if relation.get("TargetMode") == "External" and not relation.get(
                    "Type", ""
                ).endswith("/hyperlink"):
                    raise ValueError("External linked asset requires separate review")


@contextmanager
def slide_source(source, target, temporary):
    root = Path(source).resolve()
    path = Path(target["path"]).resolve()
    key = target["source_key"]
    if not path.is_relative_to(root) or path.is_symlink():
        raise ValueError("Slide source outside course root")
    state = path.stat()
    if key:
        if not key.startswith("archive "):
            raise ValueError("Unsupported slide locator")
        name = key[len("archive ") :]
        safe = Path(name.replace("\\", "/"))
        if safe.is_absolute() or ".." in safe.parts:
            raise ValueError("Unsafe slide archive member")
        deck = Path(temporary) / "source.pptx"
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo(name)
            if info.file_size > 512 * 1024**2:
                raise ValueError("Slide archive member exceeds bound")
            with archive.open(info) as src, deck.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1024**2)
    else:
        deck = path
        if path.suffix.lower() != ".pptx" or state.st_size > 512 * 1024**2:
            raise ValueError("Unsupported slide input")
    validate_deck(deck)
    raw = digest(deck)
    yield deck, raw
    after = path.stat()
    if (state.st_size, state.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ) or digest(deck) != raw:
        raise RuntimeError("Source changed during native slide rendering")


@contextmanager
def native_session():
    import gc
    import pythoncom
    import win32com.client
    import psutil

    before = {
        p.pid
        for p in psutil.process_iter(["name"])
        if (p.info["name"] or "").lower() == "powerpnt.exe"
    }
    pythoncom.CoInitialize()
    app = None
    security = None
    alerts = None

    @contextmanager
    def open_deck(deck):
        presentation = None
        try:
            presentation = app.Presentations.Open(str(deck), -1, 0, 0)
            if not presentation.ReadOnly:
                raise RuntimeError("Native renderer did not open read-only")
            yield app, presentation
        finally:
            if presentation is not None:
                presentation.Close()
            presentation = None

    try:
        app = win32com.client.DispatchEx("PowerPoint.Application")
        security = app.AutomationSecurity
        alerts = app.DisplayAlerts
        app.AutomationSecurity = 3
        app.DisplayAlerts = 1
        yield open_deck
    finally:
        try:
            if app is not None:
                if security is not None:
                    app.AutomationSecurity = security
                if alerts is not None:
                    app.DisplayAlerts = alerts
                if not before and app.Presentations.Count == 0:
                    app.Quit()
        finally:
            app = None
            gc.collect()
            pythoncom.CoUninitialize()


@contextmanager
def native_powerpoint(deck):
    with native_session() as open_deck:
        with open_deck(deck) as pair:
            yield pair


def render_slides(store, reports, source, *, limit=0, open_deck=native_powerpoint):
    if open_deck is native_powerpoint:
        with native_session() as opener:
            return render_slides(store, reports, source, limit=limit, open_deck=opener)

    targets = slide_targets(
        [json.loads(Path(p).read_text(encoding="utf-8")) for p in reports]
    )
    completed = []
    errors = []
    count = 0
    for target in targets:
        store.checkpoint()
        try:
            with tempfile.TemporaryDirectory(
                prefix="slide-render-", dir=store.root
            ) as temporary:
                with slide_source(source, target, Path(temporary).resolve()) as (
                    deck,
                    raw,
                ):
                    with open_deck(deck) as (app, presentation):
                        version = str(app.Version)
                        renderer = "ppt-native-v1-" + version + "-max2000"
                        folder = store.root / "document-visuals" / renderer / raw
                        folder.mkdir(parents=True, exist_ok=True)
                        manifest_path = folder / "manifest.json"
                        manifest = (
                            json.loads(manifest_path.read_text(encoding="utf-8"))
                            if manifest_path.exists()
                            else {
                                "source_digest": raw,
                                "renderer": renderer,
                                "slides": {},
                            }
                        )
                        w = float(presentation.PageSetup.SlideWidth)
                        h = float(presentation.PageSetup.SlideHeight)
                        if w <= 0 or h <= 0:
                            raise ValueError("Invalid slide dimensions")
                        width = round(w * 2000 / max(w, h))
                        height = round(h * 2000 / max(w, h))
                        for page in target["pages"]:
                            if limit and count >= limit:
                                break
                            store.checkpoint()
                            if page > presentation.Slides.Count:
                                raise ValueError(
                                    "Slide number exceeds actual slide count"
                                )
                            store.write_json(
                                "slides-render-progress.json",
                                {
                                    "stage": "rendering",
                                    "updated": time.time(),
                                    "pid": __import__("os").getpid(),
                                    "processed": count,
                                    "errors": len(errors),
                                    "current": {**target, "slide": page},
                                },
                            )
                            image = folder / f"{page:05}.png"
                            saved = manifest["slides"].get(str(page))
                            if saved:
                                if (
                                    not image.is_file()
                                    or digest(image) != saved["image_digest"]
                                ):
                                    raise RuntimeError(
                                        "Committed slide image changed; preserve evidence and review"
                                    )
                            else:
                                output = (Path(temporary) / "page.png").resolve()
                                presentation.Slides.Item(page).Export(
                                    str(output), "PNG", width, height
                                )
                                from PIL import Image

                                with Image.open(output) as bitmap:
                                    if bitmap.size != (width, height):
                                        raise RuntimeError(
                                            "Native slide image has wrong dimensions"
                                        )
                                    bitmap.verify()
                                output.replace(image)
                                saved = {
                                    "image": str(image.relative_to(store.root)),
                                    "image_digest": digest(image),
                                    "width": width,
                                    "height": height,
                                }
                                manifest["slides"][str(page)] = saved
                                store.write_json(
                                    str(manifest_path.relative_to(store.root)), manifest
                                )
                            completed.append(
                                {
                                    "path": target["path"],
                                    "source_key": target["source_key"],
                                    "slide": page,
                                    "source_digest": raw,
                                    "renderer": renderer,
                                    **saved,
                                }
                            )
                            count += 1
        except InterruptedError:
            raise
        except Exception as exc:
            errors.append({**target, "error": type(exc).__name__ + ": " + str(exc)})
        store.write_json(
            "slides-render-report.json",
            {
                "completed": completed,
                "errors": errors,
                "limit": limit,
                "ocr_pending": True,
                "originals_retained": True,
            },
        )
        if limit and count >= limit:
            break
    result = {
        "completed": completed,
        "errors": errors,
        "limit": limit,
        "ocr_pending": True,
        "originals_retained": True,
    }
    store.write_json("slides-render-report.json", result)
    store.write_json(
        "slides-render-progress.json",
        {
            "stage": "batch_finished" if not errors else "finished_with_errors",
            "updated": time.time(),
            "processed": count,
            "errors": len(errors),
            "limit": limit,
        },
    )
    return result
