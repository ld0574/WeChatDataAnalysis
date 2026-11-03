"""Audit inherited source gaps against a changed, fully bound article edition."""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from pathlib import Path

import jsonschema
from tokenizers import Tokenizer

from . import course_textbook_edition as edition
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore
from .course_wiki import object_schema
from .course_wiki_writer import DraftJob

POLICY = "changed-edition-inherited-source-gap-audit-v1"
PROMPT = edition.additions.final.writing.COVERAGE + """
article 是实际扩充后的新版本。audit_targets 是本次恰好需要逐项核对的原缺口，
complete_context_sources 保存目标及其相邻的完整原文；只对 audit_targets 输出判断。
原缺口、writer 排除意见和旧矛盾审核都不能作为事实。直接核对完整原文与实际整篇文章。
covered 必须给真正讲清该来源有效教学的 paragraph_ids；标题、关键词或相关概念不能充数。
non_teaching 仅适用于没有必须保留的有效教学。重复来源仍有有效教学且正文已解释时，
选择 covered 并给真正的正文位置，不能仅因重复就排除其中的条件、例子或限制。
若原文有必要操作、例子、条件或限制尚未解释，必须选 missing。不要把它们当口头填充排除。
source_quote 由程序固定为原文身份，只证明来源没有混淆，不证明分类或覆盖正确。
不重写文章，不输出本文未涉及来源的全课完成结论。只输出严格 JSON。
"""


def require(value, reason):
    if not value:
        raise ValueError("requires_edition_source_audit_" + reason)


def load(root, key, tokenizer):
    require(re.fullmatch(r"[a-f0-9]{32}", key), "edition_id")
    root = Path(root)
    folder = root / "wiki/textbook-edition-jobs" / key
    manifest, progress, result = [edition.read(folder / n) for n in ["manifest.json", "progress.json", "result.json"]]
    require(manifest["id"] == key == sha(canonical(manifest["recipe"]))[:32], "edition_manifest")
    extra = manifest["recipe"]["extra"]
    require(extra["module_sha256"] == sha(Path(edition.__file__).read_bytes())
            and extra["tokenizer_sha256"] == sha(Path(tokenizer).read_bytes()), "edition_code_and_tokenizer_unchanged")
    require(progress["stage"] == "complete" and not edition.additions.final.writing.planmod.identity_live(progress)
            and result["job"] == key and result["state"] == "edition_review_finished", "edition_finished")
    parents = extra["binding"]["parents"]
    paths = [n.replace("\\", "/") for n in parents]
    additions = {n.split("/")[2] for n in paths if n.startswith("wiki/textbook-gap-addition-jobs/")}
    precise = {n.split("/")[2] for n in paths if n.startswith("wiki/textbook-precision-edit-jobs/")}
    require(len(additions) == len(precise) == 1, "single_frozen_parent_pair")
    inputs, book, coverage, pending, _, binding = edition.inputs_for(root, next(iter(additions)), next(iter(precise)), tokenizer)
    require(binding == extra["binding"] and edition.read(folder / "reading.json") == book
            and result["article_sha256"] == sha(canonical(book)) == binding["article_sha256"]
            and edition.read(folder / "private-inherited-coverage.json") == coverage, "reconstructed_actual_edition")
    checks, _ = edition.checked_cache(folder, "remaining-checks")
    reading, _ = edition.checked_cache(folder, "edition-reading")
    require(result["missing_source_inputs"] == sum(v["kind"] == "missing" for v in coverage.values())
            and result["remaining_prior_issues"] == sum(not v["passed"] for v in checks["items"].values())
            and result["reading_issues"] == sum(not v["passed"] for v in reading.values()), "actual_edition_review_counts")
    return inputs, book, coverage, pending, checks, reading, {n: sha((folder / n).read_bytes()) for n in
        ["manifest.json", "reading.json", "private-inherited-coverage.json", "private-prior-checks.json", "result.json"]}


def schema_for(targets, body, sources):
    reason = {"type": "string", "minLength": 1, "maxLength": 480}
    fields = {}
    for ref in targets:
        common = {"source_quote": {"const": sources[ref]["text"][:400]}, "reason": reason}
        fields[ref] = {"anyOf": [
            object_schema({**common, "kind": {"const": "covered"}, "paragraph_ids": {
                "type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": list(body)}}}),
            object_schema({**common, "kind": {"enum": ["non_teaching", "missing"]}})]}
    return object_schema({"sources": object_schema(fields)})


def validate(value, schema, targets, sources):
    jsonschema.validate(value, schema)
    require(set(value["sources"]) == set(targets), "exact_audited_scope")
    for ref, row in value["sources"].items():
        edition.additions.real_quote(row["source_quote"], sources[ref])


class AuditJob(edition.additions.final.ReviewJob):
    def __init__(self, store, sources, digest, key, binding, tokenizer):
        self.tokenizer = Tokenizer.from_file(str(tokenizer))
        DraftJob.__init__(self, store, {"sources": sources}, "qwen3.5:9b", digest, 21600,
            pipeline=POLICY, job_directory="textbook-edition-source-jobs", max_attempts=2,
            recipe_extra={"edition": key, "edition_bindings": binding, "prompt": PROMPT,
                "module_sha256": sha(Path(__file__).read_bytes()), "tokenizer_sha256": sha(Path(tokenizer).read_bytes()),
                "maximum_semantic_repairs": 0, "old_failures_and_budgets_unchanged": True})

    def progress(self, state, stage, **extra):
        self.current = stage
        value = {**self.identity, "job": self.key, "state": state, "stage": stage, "updated": time.time(),
                 "semantic_acceptance": False, "source_deletion_allowed": False, **extra}
        atomic_json(self.root / "progress.json", value)
        atomic_json(self.store.root / "wiki/textbook-edition-source-current.json", value)


def run(root, key, tokenizer):
    store = TextbookStore(root)
    store.checkpoint()
    with schema_lease(store.root / "wiki/textbook-edition-source.lock", timeout=0):
        inputs, book, inherited, _, _, _, binding = load(root, key, tokenizer)
        targets = {k: v for k, v in inherited.items() if v["kind"] == "missing"}
        require(bool(targets), "actual_inherited_gaps")
        sources = edition.unmapped.source_scope(inputs["sources"], targets, {"additions": []})
        body = edition.additions.final.writing.planmod.body_index(book)[0]
        schema = schema_for(targets, body, sources)
        context = {"article": edition.additions.final.writing.planmod.labelled_book(book),
                   "audit_targets": list(targets), "complete_context_sources": sources,
                   "prior_negative_judgments_retained": targets}
        job = AuditJob(store, sources, inputs["model_digest"], key, binding, tokenizer)
        edition.continuation.bind_once(store.root / "wiki/textbook-edition-jobs" / key / "source-audit-binding.json",
            {"job": job.key, "manifest_sha256": sha((job.root / "manifest.json").read_bytes())})
        backup = Path.home() / "Documents/CourseKnowledgeBackups/first-lesson-source-audits" / job.key
        backup.mkdir(parents=True, exist_ok=True)
        for source in [job.root / "manifest.json", Path(__file__)]:
            target = backup / source.name
            require(not target.exists() or target.read_bytes() == source.read_bytes(), "backup_unchanged")
            if not target.exists():
                target.write_bytes(source.read_bytes())
            require(target.read_bytes() == source.read_bytes(), "backup_byte_match")
        try:
            value = job.stage("changed-edition-source-gaps", PROMPT, schema, context,
                              lambda v: validate(v, schema, targets, sources))
            combined = copy.deepcopy(inherited)
            combined.update({k: {a: b for a, b in row.items() if a != "source_quote"}
                             for k, row in value["sources"].items()})
            atomic_json(job.root / "private-source-coverage.json", {"edition": key,
                "article_sha256": sha(canonical(book)), "original_coverage": inherited,
                "target_review": value, "combined_coverage": combined})
            result = {"state": "changed_edition_source_gaps_checked", "job": job.key, "edition": key,
                "article_sha256": sha(canonical(book)), "audited_targets": len(targets), "complete_context_sources": len(sources),
                "inherited_unchanged_sources": len(inherited) - len(targets), "total_source_inputs": len(combined),
                "covered_targets": sum(r["kind"] == "covered" for r in value["sources"].values()),
                "non_teaching_targets": sum(r["kind"] == "non_teaching" for r in value["sources"].values()),
                "missing_sources": sum(r["kind"] == "missing" for r in combined.values()),
                "old_failures_and_budgets_retained": True, "semantic_acceptance": False,
                "reader_publication_allowed": False, "source_deletion_allowed": False, "goal_complete": False}
            atomic_json(job.root / "result.json", result)
            job.progress(result["state"], "complete")
            return result
        except Exception as exc:
            msg = str(exc)
            job.progress("paused" if isinstance(exc, InterruptedError) else "stopped", job.current,
                error_type=type(exc).__name__, reason_code=msg if re.fullmatch(r"[a-zA-Z0-9_-]{1,180}", msg) else None,
                error_sha256=sha(msg.encode()))
            raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--edition", required=True)
    p.add_argument("--tokenizer", type=Path, required=True)
    p.add_argument("command", choices=["worker", "guard"])
    args = p.parse_args()
    if args.command == "worker":
        print(json.dumps(run(args.root, args.edition, args.tokenizer)))
    else:
        from .course_archive_gpu import checked_sample
        from .course_gpu_guard import run as guarded
        with schema_lease(args.root / "wiki/textbook-edition-source-dispatch.lock", timeout=0):
            previous = args.root / "wiki-revision-thermal-guard.json"
            if previous.exists():
                data = previous.read_bytes()
                require(not edition.additions.final.writing.planmod.identity_live(json.loads(data)), "guard_slot_free")
                history = args.root / "wiki/textbook-edition-source-guard-history" / (sha(data) + ".json")
                history.parent.mkdir(parents=True, exist_ok=True)
                require(not history.exists() or history.read_bytes() == data, "guard_history_unchanged")
                if not history.exists():
                    history.write_bytes(data)
            guarded(args.root, [sys.executable, "-m", "wechat_decrypt_tool.course_textbook_edition_sources",
                "--root", str(args.root), "--edition", args.edition, "--tokenizer", str(args.tokenizer), "worker"],
                maximum_temperature=70, maximum_power=200, guard_name="wiki-revision-thermal-guard",
                sampler=lambda: checked_sample(args.root))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        msg = str(exc)
        print(json.dumps({"state": "stopped", "error_type": type(exc).__name__,
            "reason_code": msg if re.fullmatch(r"[a-zA-Z0-9_-]{1,180}", msg) else None, "error_sha256": sha(msg.encode())}))
        raise SystemExit(1) from None
