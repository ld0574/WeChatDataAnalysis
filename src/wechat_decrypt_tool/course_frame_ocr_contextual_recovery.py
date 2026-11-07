"""One full-frame-context check of a retained negative narrow OCR tile.

All prior responses, failures and budgets remain immutable. Three successful
tiles are reused; each unresolved tile gets one genuinely wider pixel input.
Another negative stops this explicit follow-up without automatic retries.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
from pathlib import Path
import re
import sys
import time

import httpx
import psutil
from PIL import Image

from . import course_frame_ocr_recovery as base

whole, knowledge = base.whole, base.knowledge
MODEL, LEGACY_FAILURE = base.MODEL, base.LEGACY_FAILURE
digest, live, checkpoint = base.digest, base.live, base.checkpoint
POLICY = "one-retained-negative-ocr-tile-with-full-original-and-expanded-context-v1"
PROMPT = base.PROMPT + """\n这次有两张图：完整原画面，以及 expanded_box 对应的更宽区域。
仅转录 expanded_box 内所有清晰内容，完整原图只帮助确认区域边界和上下文。
old_negative 是此前狭窄区域的未通过草稿，不是事实，不替它辩护；其不确定结果会继续保留。
原画面本身之外的内容不能恢复或编造。确实看不清时保留 uncertainties，不能声称恢复了这些字。
如果仍漏掉本区域清晰可见的信息，complete_visible_region 必须 false，后续不会自动重试。
"""


def require(ok, code):
    if not ok:
        raise ValueError("requires_contextual_frame_recovery_" + code)


def parent_records(root, parent):
    root, folder, plan = base.load(root, parent)
    failure = whole.read(folder / "failure.json")
    require(failure["reason_code"] == "requires_frame_recovery_unresolved_visible_region_preserved",
        "literal_unresolved_predecessor")
    status = whole.read(folder / "progress.json")
    require(status["stage"] == "stopped" and not live(status), "failed_worker_exited")
    receipts, positive, negative = {}, [], []
    for index in range(len(plan["tiles"])):
        path = folder / f"tile-{index:03d}-receipt.json"
        receipt = whole.read(path)
        receipts[path.name] = whole.sha(path.read_bytes())
        output = json.loads(receipt["response"]["message"]["content"])
        if output.get("complete_visible_region") is True:
            body, sha = base.request(root, plan, index)
            base.checked_receipt(receipt, body, sha, plan)
            positive.append(index)
        else:
            require(output.get("complete_visible_region") is False and bool(output.get("uncertainties")),
                "actual_explicit_negative")
            body, sha = base.request(root, plan, index)
            try:
                base.checked_receipt(receipt, body, sha, plan)
            except ValueError as exc:
                require(str(exc) == "requires_frame_recovery_unresolved_visible_region_preserved", "valid_negative_native_receipt")
            negative.append(index)
    require(0 < len(negative) <= 2, "finite_context_followup")
    require(all(plan["tiles"][index][2] - plan["tiles"][index][0] <= 256 for index in negative), "actual_narrow_region_needs_context")
    return root, folder, plan, receipts, positive, negative


def prepare(root, parent):
    root, old_folder, old_plan, receipts, positive, negative = parent_records(root, parent)
    checkpoint(root)
    guard = whole.read(root / "gpu-thermal-guard.json")
    require(not live(guard) and guard["state"] == "worker_exited" and guard.get("returncode") == 1
        and {k:guard[k] for k in ("pid", "created")} == whole.read(old_folder / "guard-started.json"),
        "exact_failed_context_predecessor")
    registry = root / "wiki/frame-context-recovery-registrations"
    registry.mkdir(parents=True, exist_ok=True)
    plan = {"policy": POLICY, "parent": parent, "parent_plan_sha256": digest(old_plan),
        "parent_receipts_sha256": receipts, "positive_tiles": positive, "negative_tiles": negative,
        "original_source": old_plan["original_source"], "original_frame": old_plan["original_frame"],
        "video": old_plan["video"], "frame": old_plan["frame"], "model_digest": old_plan["model_digest"],
        "width": old_plan["width"], "height": old_plan["height"], "frame_path": old_plan["frame_path"],
        "predecessor_guard": guard, "module_sha256": whole.sha(Path(__file__).read_bytes()),
        "parent_module_sha256": whole.sha(Path(base.__file__).read_bytes()), "prompt_sha256": digest(PROMPT),
        "maximum_attempts_per_region": 1, "automatic_retry_allowed": False,
        "automatic_thermal_resume_allowed": False, "reader_publication_allowed": False}
    key = digest(plan)[:32]
    folder = root / "wiki/frame-context-recoveries" / key
    with knowledge.schema_lease(registry / "prepare.lock", timeout=0):
        require(not (registry / (parent + ".json")).exists() and not folder.exists(), "one_followup_no_budget_refresh")
        folder.mkdir(parents=True)
        knowledge.atomic_json(registry / (parent + ".json"), {"parent": parent, "job": key})
        knowledge.atomic_json(folder / "plan.json", plan)
    return {"state": "full_original_context_followup_prepared", "job": key,
        "reused_positive_tiles": len(positive), "new_context_checks": len(negative), "model_calls": 0}


def load(root, key):
    root = Path(root).resolve()
    require(re.fullmatch(r"[a-f0-9]{32}", key) is not None, "job_id")
    folder = root / "wiki/frame-context-recoveries" / key
    plan = whole.read(folder / "plan.json")
    require(plan["policy"] == POLICY and digest(plan)[:32] == key
        and plan["module_sha256"] == whole.sha(Path(__file__).read_bytes())
        and plan["parent_module_sha256"] == whole.sha(Path(base.__file__).read_bytes())
        and plan["prompt_sha256"] == digest(PROMPT), "unchanged_context_input_code_and_budget")
    registration = whole.read(root / "wiki/frame-context-recovery-registrations" / (plan["parent"] + ".json"))
    require(registration["job"] == key, "original_once_only_registration")
    _, _, old, receipts, positive, negative = parent_records(root, plan["parent"])
    require(digest(old) == plan["parent_plan_sha256"] and receipts == plan["parent_receipts_sha256"]
        and positive == plan["positive_tiles"] and negative == plan["negative_tiles"], "all_parent_negatives_and_successes_unchanged")
    return root, folder, plan


def request(root, plan, index):
    _, old_folder, old = base.load(root, plan["parent"])
    original_box = old["tiles"][plan["negative_tiles"][index]]
    left, top, right, bottom = original_box
    box = [max(0, right - 640), max(0, bottom - 384), min(old["width"], left + 640), min(old["height"], top + 384)]
    buffers = []
    with Image.open(root / plan["frame_path"]) as native:
        native = native.convert("RGB")
        for image in (native, native.crop(box)):
            output = io.BytesIO()
            image.save(output, format="PNG")
            buffers.append(output.getvalue())
    negative = whole.read(old_folder / f"tile-{plan['negative_tiles'][index]:03d}-receipt.json")
    context = {"original_box": original_box, "expanded_box": box, "original_size": [old["width"], old["height"]],
        "old_negative": json.loads(negative["response"]["message"]["content"])}
    body = {"model": MODEL, "messages": [{"role": "system", "content": PROMPT}, {"role": "user",
        "content": json.dumps(context, ensure_ascii=False), "images": [base64.b64encode(b).decode("ascii") for b in buffers]}],
        "stream": False, "think": False, "keep_alive": "5m", "format": base.SCHEMA, "options": base.OPTIONS}
    return body, whole.sha(buffers[1]), box


def worker(root, key):
    root, folder, plan = load(root, key)
    checkpoint(root)
    owner = psutil.Process()
    identity = {"pid": owner.pid, "created": owner.create_time()}
    receipts = {}

    def progress(stage, **extra):
        knowledge.atomic_json(folder / "progress.json", {**identity, "stage": stage, "job": key,
            "updated": time.time(), "total_tiles": len(plan["negative_tiles"]), **extra})

    store = knowledge.KnowledgeStore(root)
    store.progress = progress
    with knowledge.schema_lease(folder / "worker.lock", timeout=0):
        require(not (folder / "started.json").exists(), "once_without_retry")
        knowledge.atomic_json(folder / "started.json", identity)
        try:
            source, image = base.rows(root, plan["video"], plan["frame"])
            require(source == plan["original_source"] and image == plan["original_frame"], "same_failed_source_rows")
            for index in range(len(plan["negative_tiles"])):
                body, sha, _ = request(root, plan, index)
                progress("waiting_gpu", completed_tiles=index)
                with knowledge.wait_gpu_lease(store), httpx.Client(trust_env=False, timeout=600) as client:
                    require(base.local_model_digest(MODEL) == plan["model_digest"], "same_installed_vision_weights")
                    progress("generating_full_context_check", completed_tiles=index)
                    response = client.post("http://127.0.0.1:11434/api/chat", json=body)
                    response.raise_for_status()
                    value = response.json()
                    loaded = client.get("http://127.0.0.1:11434/api/ps")
                    loaded.raise_for_status()
                    owners = [m for m in loaded.json().get("models", []) if m.get("digest") == plan["model_digest"]]
                    receipt = {"request_sha256": digest(body), "tile_sha256": sha, "response": value,
                        "loaded_model": owners[0] if len(owners) == 1 else {}}
                    path = folder / f"context-{index:03d}-receipt.json"
                    knowledge.atomic_json(path, receipt)
                    base.checked_receipt(receipt, body, sha, plan)
                    receipts[path.name] = whole.sha(path.read_bytes())
            progress("all_tiles_saved", completed_tiles=len(plan["negative_tiles"]), receipts_sha256=receipts)
            return {"state": "bounded_full_frame_context_saved", "job": key}
        except Exception as exc:
            failure = whole.failure_status(exc)
            knowledge.atomic_json(folder / "failure.json", failure)
            progress("stopped", failure=failure)
            raise


def verify(root, key):
    root, folder, plan = load(root, key)
    status = whole.read(folder / "progress.json")
    require(status["stage"] == "all_tiles_saved" and not live(status)
        and status["completed_tiles"] == len(plan["negative_tiles"])
        and not (folder / "failure.json").exists(), "successful_context_worker_exited")
    _, old_folder, old = base.load(root, plan["parent"])
    parts = [{"box": row["box"], "text": whole.read(root / row["file"])["text"]} for row in old["complete_legacy_regions"]]
    for index in plan["positive_tiles"]:
        body, sha = base.request(root, old, index)
        output = base.checked_receipt(whole.read(old_folder / f"tile-{index:03d}-receipt.json"), body, sha, old)
        parts.append({"box": old["tiles"][index], "output": output})
    for index in range(len(plan["negative_tiles"])):
        path = folder / f"context-{index:03d}-receipt.json"
        require(whole.sha(path.read_bytes()) == status["receipts_sha256"][path.name], "original_context_response")
        body, sha, box = request(root, plan, index)
        output = base.checked_receipt(whole.read(path), body, sha, plan)
        parts.append({"box": box, "output": output})
    rendered = []
    for part in parts:
        output = part.get("output")
        text = part.get("text") if output is None else "\n".join([output["text"], output["visible_diagram_relations"],
            *["不确定：" + item for item in output["uncertainties"]]]).strip() or "无可读文字或信息图示"
        rendered.append("区域 " + json.dumps(part["box"]) + "\n" + text)
    description = "原图完整区域识别草稿（重叠可能重复，原负面检查及不清晰细节保留，未作语义验收）\n" + "\n\n".join(rendered)
    return {"state": "full_context_frame_reconstructed_without_inference", "job": key, "description": description,
        "description_sha256": whole.sha(description.encode()), "old_negative_retained": True,
        "model_calls": 0, "semantic_acceptance": False, "reader_publication_allowed": False}


def apply(root, key):
    checked = verify(root, key)
    root, folder, plan = load(root, key)
    checkpoint(root)
    require(not (folder / "applied.json").exists(), "apply_checked_frame_once")
    store = knowledge.KnowledgeStore(root)
    with store.write() as db:
        source = dict(db.execute("SELECT * FROM videos WHERE id=?", (plan["video"],)).fetchone())
        image = dict(db.execute("SELECT * FROM frames WHERE id=? AND video=?", (plan["frame"], plan["video"])).fetchone())
        require(source == plan["original_source"] and image == plan["original_frame"], "unchanged_rows_at_application")
        knowledge.atomic_json(folder / "checked-result.json", checked)
        db.execute("UPDATE frames SET description=?,search_text=?,status='ready',model=? WHERE id=? AND video=?",
            (checked["description"], " ".join(knowledge.tokens(checked["description"])), MODEL, plan["frame"], plan["video"]))
    receipt = {"state": "checked_context_frame_applied_source_failure_preserved", "video": plan["video"],
        "frame": plan["frame"], "job": key, "description_sha256": checked["description_sha256"], "source_failure_unchanged": True}
    knowledge.atomic_json(folder / "applied.json", receipt)
    return receipt


def guard(root, key):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run

    root, folder, plan = load(root, key)
    checkpoint(root)
    require(whole.read(root / "gpu-thermal-guard.json") == plan["predecessor_guard"]
        and not live(plan["predecessor_guard"]), "exact_failed_predecessor_not_restarted")
    require(not (folder / "guard-started.json").exists(), "guard_once_without_retry")
    whole.backup(root / "gpu-thermal-guard.json", folder / "original-failed-guard.json")
    owner = psutil.Process()
    knowledge.atomic_json(folder / "guard-started.json", {"pid": owner.pid, "created": owner.create_time()})
    run(root, [sys.executable, "-m", __spec__.name, "--root", str(root), "worker", "--job", key],
        maximum_temperature=70, maximum_power=200, sampler=lambda: checked_sample(root))
    terminal = whole.read(root / "gpu-thermal-guard.json")
    require(terminal["state"] == "worker_exited" and terminal.get("returncode") == 0, "successful_context_guard")
    result = verify(root, key)
    knowledge.atomic_json(folder / "checked-result.json", result)
    return {k: value for k, value in result.items() if k != "description"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare").add_argument("--parent", required=True)
    for name in ("worker", "guard", "verify", "apply"):
        sub.add_parser(name).add_argument("--job", required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.root, args.parent) if args.command == "prepare" else (
            {"worker": worker, "guard": guard, "verify": verify, "apply": apply}[args.command](args.root, args.job))
        print(json.dumps({k: value for k, value in result.items() if k != "description"}))
    except Exception as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
