"""Refresh portable course limitations from local evidence, without model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import time
from fractions import Fraction
from pathlib import Path


def write_exclusions(root):
    from .course_knowledge import atomic_json

    root = Path(root).resolve()
    evidence = {}

    def read(name):
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Limitation evidence must stay inside the archive")
        if not path.is_file():
            evidence[name] = {"available": False}
            return {}
        raw = path.read_bytes()
        value = json.loads(raw)
        evidence[name] = {
            "available": True,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }
        return value

    inventory = read("inventory-unique.json")
    if not isinstance(inventory.get("videos"), list):
        raise ValueError("Frozen course inventory is required")
    with sqlite3.connect(
        (root / "knowledge.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=10
    ) as db:
        db.execute("BEGIN")
        states = dict(db.execute("SELECT status,count(*) FROM videos GROUP BY status"))
        registered = {
            key: status for key, status in db.execute("SELECT id,status FROM videos")
        }
        segments, hours = db.execute(
            "SELECT count(*),coalesce(sum(end-start),0)/3600 FROM segments"
        ).fetchone()
        notes = db.execute(
            "SELECT count(*) FROM notes WHERE status='ready'"
        ).fetchone()[0]
        db.rollback()
    progress = {
        name: read(name + "-progress.json")
        for name in (
            "job",
            "pdf-ocr",
            "images-ocr",
            "image-followup-driver",
            "primary-static-image-validation",
            "pdf-retry-driver",
            "animation-driver",
            "small-image-driver",
            "svg-driver",
            "interim-backup-validation",
            "full-audio-timeline-driver",
        )
    }
    locked = read("resource-error-classification.json")
    alternate = read("alternate-audio-recovery-validation.json")
    gap_validation = read("audio-gap-recovery-validation.json")
    damaged = []
    for item in read("source-audio-error-audit.json").get("completed", []):
        key = item["video"]
        if not re.fullmatch("[a-f0-9]{24}", key):
            raise ValueError("Invalid damaged-course identity")
        entry = {
            "video": key,
            "source": item["source"],
            "decode_error_packets": item["decode_error_count"],
            "current_database_state": registered.get(key, "not_yet_processed"),
            "recovery_kind": "unverified",
            "actual_full_asr_acceptance": False,
        }
        if key in alternate.get("sources", {}):
            entry["recovery_kind"] = "alternate_recording"
            entry["alternate_recovery_receipt"] = alternate["sources"][key]
        else:
            manifest = read("lessons/" + key + "/audio-gap-recovery.json")
            if manifest:
                entry.update(
                    recovery_kind="strict_decodable_intervals",
                    usable_clips=len(manifest["clips"]),
                    gaps=manifest["gaps"],
                    source_digest=manifest["source_digest"],
                    original_packets_retained=manifest["original_packets_retained"],
                    gap_preparation_receipt=next(
                        (
                            x
                            for x in gap_validation.get("sources", [])
                            if x["video"] == key
                        ),
                        None,
                    ),
                )
        damaged.append(entry)
    animation_validation = read("animation-native-validation.json")
    images = read("teaching-image-audit.json")
    partial = []
    for item in read("animation-native-report.json").get("completed", []):
        if item["complete_frame_decode"]:
            continue
        digest = item["source_sha256"]
        if not re.fullmatch("[a-f0-9]{64}", digest):
            raise ValueError("Invalid partial-animation identity")
        manifest = read("document-animations/" + digest + ".json")
        partial.append(
            {
                "source_sha256": digest,
                "declared_frames": manifest["declared_frames"],
                "retained_strict_frames": len(manifest["frames"]),
                "retained_duration_ms": manifest["retained_duration_ms"],
                "errors": manifest["errors"],
                "complete_frame_decode": False,
                "source_occurrences": [
                    {"path": x["path"], "source_key": x["source_key"]}
                    for x in images.get("image_occurrences", [])
                    if x["sha256"] == digest
                ],
            }
        )
    csv = read("large-csv-validation.json")
    pdf = read("pdf-all-pages-retained-plan.json")
    rich = read("notebook-rich-validation.json")
    svg = read("svg-native-validation.json")
    backup = read("interim-backup-validation.json")
    current_structure = read("current-integrity-validation.json")
    local_query = read("course-server-live-validation.json")
    primary_images = read("primary-static-image-validation.json")
    image_followup = read("image-followup-validation.json")
    read("document-count-deployment-validation.json")
    read("pdf-retry-deployment.json")
    summary = {
        "format": 1,
        "updated": time.time(),
        "source_deletion_allowed": False,
        "complete_course_archive": False,
        "report_is_completion_evidence": False,
        "scope": "Metadata and recorded validation results; original media and model quality are not revalidated by this report",
        "frozen_audio_sources": len(inventory["videos"]),
        "frozen_audio_hours": sum(
            x.get("duration_seconds", 0) or 0 for x in inventory["videos"]
        )
        / 3600,
        "database_snapshot": {
            "video_states": states,
            "segments": segments,
            "nominal_saved_segment_hours": hours,
            "ready_notes": notes,
        },
        "progress": progress,
        "locked_office": {
            k: locked[k] for k in ("path", "member", "encrypted_office") if k in locked
        },
        "damaged_audio_sources": damaged,
        "partial_animations": partial,
        "animation_native_validation": animation_validation,
        "recorded_validations": {
            "large_csv": csv,
            "pdf_scope": pdf.get("summary", {}),
            "notebook_rich": rich,
            "svg_rendering": {
                k: svg[k]
                for k in (
                    "requested_sources",
                    "source_occurrences",
                    "all_requested_source_renders_verified",
                )
                if k in svg
            },
            "interim_backup": backup,
            "structural_snapshot": current_structure,
            "local_query": local_query,
            "primary_static_images": primary_images,
            "later_image_repairs": image_followup,
        },
        "evidence": evidence,
    }

    def locator(value):
        return str(value).replace("\n", " ").replace("\r", " ").replace("`", "\\`")

    def timestamp(rational):
        value = Fraction(*rational)
        if value < 0:
            raise ValueError("Negative gap clock")
        milliseconds = round(value * 1000)
        seconds, ms = divmod(milliseconds, 1000)
        return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}.{ms:03}"

    lines = [
        "# 当前归档缺口与待验收范围",
        "",
        "更新时间："
        + time.strftime("%Y-%m-%d %H:%M:%S %z")
        + "。这是一份阶段清单，不是完成或视频删除许可。所有原视频继续保留。",
        "",
        "## 全量处理与验收状态",
        "",
        f"冻结清单包含 {summary['frozen_audio_sources']} 份有声课程、约 {summary['frozen_audio_hours']:.2f} 小时。当前保存 {segments} 个语音分段，名义累计时长约 {hours:.2f} 小时、{notes} 个就绪笔记窗口；分段累计不等于逐源完整覆盖或语义验收。",
        "",
        "数据库课程状态：" + json.dumps(states, ensure_ascii=False) + "。",
        "",
        "| 任务 | 当前记录阶段 | 已处理 | 当前错误 |",
        "|---|---|---:|---:|",
    ]
    for name, item in progress.items():
        lines.append(
            f"| {name} | {locator(item.get('stage', '未记录'))} | {item.get('processed', '—')} | {item.get('errors', '—')} |"
        )
    lines += [
        "",
        "阶段文件反映写入时刻；不能仅凭文件存在推断进程存活或全范围完成。",
        "",
        "## 加密课件",
        "",
    ]
    if locked.get("encrypted_office"):
        lines += [
            "源文件：`"
            + locator(locked["path"])
            + "`。成员：`"
            + locator(locked["member"])
            + "`。当前缺少可用打开密码，原件保留且不计为已解析资料；不阻塞其他课程。",
            "",
        ]
    else:
        lines += ["未取得加密附件分类回执，仍需核对。", ""]
    lines += [
        f"## {len(damaged)} 份损坏原音轨及恢复路径",
        "",
        "原音轨的损坏事实保留。备用录制音轨与按可读区间恢复的音频分别记录；恢复准备与播放引用回执不能代替实际全片 GPU 转写验收。",
        "",
    ]
    for item in damaged:
        lines += [
            "### " + item["video"],
            "",
            "- 原件：`" + locator(item["source"]) + "`",
            "- 错误音频包："
            + str(item["decode_error_packets"])
            + "；当前数据库状态："
            + item["current_database_state"]
            + "。",
        ]
        if item["recovery_kind"] == "alternate_recording":
            lines += [
                "- 已有备用录制版本恢复回执；不能标为原视频字节副本或原始音轨已修复。"
            ]
        elif item["recovery_kind"] == "strict_decodable_intervals":
            lines += [
                f"- 已保留 {item['usable_clips']} 个独立工作音频片段；缺口不填零、不跨区间拼接。"
            ]
            lines += [
                "- 缺失区间（原视频时钟，显示到毫秒；精确有理数在 JSON 清单中）："
            ]
            lines += [
                "  - "
                + timestamp(gap["start_rational"])
                + " ～ "
                + timestamp(gap["end_rational"])
                for gap in item["gaps"]
            ]
        else:
            lines += ["- 未取得可用恢复清单，必须继续排查。"]
        lines += [""]
    lines += ["## 截断动画", ""]
    for item in partial:
        lines += [
            f"`{item['source_sha256']}`：声明 {item['declared_frames']} 帧，仅严格解码并保留前 {item['retained_strict_frames']} 帧、{item['retained_duration_ms']} 毫秒，影响 {len(item['source_occurrences'])} 处来源。损坏后段未补造，动画 OCR 和后验校验仍需看独立结果。",
            "",
        ]
        lines += [
            "- `"
            + locator(x["path"])
            + "`"
            + (" → `" + locator(x["source_key"]) + "`" if x["source_key"] else "")
            for x in item["source_occurrences"]
        ]
        lines += [""]
    lines += [
        "## 已有回执与仍存在的内容限制",
        "",
        "- 大型 CSV："
        + (
            f"记录的全范围独立校验通过，覆盖 {csv['source_occurrences']} 处来源、{csv['rows_including_first']} 行和 {csv['cells']} 个单元格；不推断遗留编码作者意图，逐块检索抽查不等于全词项倒排表穷举校验。"
            if csv.get("all_requested_csv_source_occurrences_verified")
            else "全范围验收尚未确认。"
        ),
        f"- PDF：冻结计划共 {pdf.get('summary', {}).get('page_occurrences', '未知')} 个页面来源。保留原件，页面渲染最长边为 2000 像素；分块保留的是渲染像素，不能证明原生极小字形、布局或模型解读全部正确。单页失败由独立补跑报告与合并验收报告保留。",
        "- 静态图片：主批次成功来源的全量逐项校验"
        + (
            "已有通过回执"
            if primary_images.get("all_primary_successful_occurrences_verified")
            else "尚待通过"
        )
        + "；后续失败补跑的独立校验"
        + (
            "已有通过回执"
            if image_followup.get("all_later_failure_sources_verified")
            else "尚待通过"
        )
        + "。两者范围分别保留，不以失败项补跑通过代替全部主批次成功来源的验收。模型文字与图示解读仍需复核。",
        "- Notebook：富输出的静态结构、表格与原件引用有独立回执；缺少匹配保存状态的控件不能复现交互。原 CSS、外部资源、脚本与交互运行效果未作为已完成内容。",
        "- SVG：本机固定引擎与替代字体的可重复渲染不等于原字体字形与原始视觉布局验收。原件及可直接提取的 XML 文字另行保留。",
        "- 视频：画面每 30 秒抽样并抑制近似静态图；短暂代码、鼠标操作和动画可能遗漏。原生逐帧窗口诊断只证明所查窗口，不代表全片视觉语义覆盖。",
        "- 摘要、概念和关系是本地模型草稿。引用存在和属于同课程，只证明引用结构，不能单独证明听写、摘要或答案语义正确。",
        "",
        "## 本地查询与备份",
        "",
        "本地入口：<http://127.0.0.1:3002/>。"
        + (
            "最近一次代表性接口与媒体引用检查通过。"
            if local_query.get("all_checks_passed")
            else "尚无通过的实时接口检查回执。"
        )
        + "该回执不保证服务此刻仍在运行，也不代表全部课程已入库。",
        "",
        "阶段快照与独立恢复、全部 FTS 词项/正文核对分别记录；阶段备份不能代替最终全课程归档验收。最新等待/恢复阶段见上表和 `interim-backup-validation-progress.json`。",
        "",
        "机器可读清单：`KNOWN-EXCLUSIONS.json`。其中保存本次读取的回执 SHA-256、精确缺口时钟与来源位置，迁移时与原件、模型权重包和课程快照一起保留。",
        "",
    ]
    text = "\n".join(lines)
    previous = root / "KNOWN-EXCLUSIONS.md"
    historical = root / "KNOWN-EXCLUSIONS.before-dynamic.md"
    if previous.exists() and not historical.exists():
        shutil.copyfile(previous, historical)
    atomic_json(root / "KNOWN-EXCLUSIONS.json", summary)
    part = root / "KNOWN-EXCLUSIONS.md.tmp"
    part.write_text(text, encoding="utf-8")
    part.replace(previous)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    report = write_exclusions(args.root)
    print(
        json.dumps(
            {
                "audio_sources": report["frozen_audio_sources"],
                "damaged_audio_sources": len(report["damaged_audio_sources"]),
                "partial_animation_sources": len(report["partial_animations"]),
                "source_deletion_allowed": False,
            }
        )
    )


if __name__ == "__main__":
    main()
