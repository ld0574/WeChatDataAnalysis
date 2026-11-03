"""Compose a private whole-lesson manuscript while source disputes stay open.

Disputed extracted claims are quarantined, never used as factual navigation.
Their complete originals become explicit writing tasks with neighboring context.
This does not revise a source ledger or publish a reading payload. A later full
original-to-manuscript coverage review is required even if section reviews pass.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

import jsonschema
import psutil

from . import course_textbook_authoring as author
from .course_archive_gpu import checked_sample
from .course_knowledge import atomic_json
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_textbook_ledger import validate_result
from .course_textbook_source_revision import POLICY as REVISION_POLICY
from .course_wiki_writer import DraftJob

POLICY = "private-source-grounded-whole-lesson-composition-v1"
CONTRACT = "private-composition-execution.json"
NOTICE = """
pending_source_cards 是尚有争议的完整原文任务，不是已经成立的知识点。
相关旧提取结论已隔离，不可把卡片中的原话或审核意见当成成品正文。
neighbors 只帮助理解指代、接续和适用条件，不要求本目标承担邻居独有的额外教学。
对每个待核对任务，逐句检查原文实际教了什么，跨原来时间片段重组解释。
若目标含教学，coverage 的 teaching=true，并用正文段落完整解释且引用目标原文；
若确实只有行政、寒暄或导航，teaching=false，paragraphs=[]，reason 留充分依据供独立复核。
短片段、解释不完整或在别处重复均不是排除有效教学的理由。
原文没有的定义、实现细节、条件和结论不可补造。此阶段只生成内部完整文章，不能发布阅读版。
"""
PROMPTS = {
    author.GROUP_PROMPT: author.GROUP_PROMPT
    + NOTICE
    + "\n待核对原文按实际知识关联并入合适章节，不用待核对编号或时间片段作章节。",
    author.WRITE_PROMPT: author.WRITE_PROMPT + NOTICE,
    author.REVIEW_PROMPT: author.REVIEW_PROMPT
    + NOTICE
    + "\n逐项核对 coverage 的教学判断。排除理由必须与完整目标原文一致；教学覆盖须包含其条件、例子和限制。",
    author.REPAIR_PROMPT: author.REPAIR_PROMPT + NOTICE,
    author.READ_PROMPT: author.READ_PROMPT,
}


def require(ok, reason):
    if not ok:
        raise ValueError("requires_private_composition_" + reason)


def neighbors(inventory, target):
    """Exact adjacent originals, without rewriting, clipping or invented context."""
    sources = inventory["sources"]
    eligible = [
        ref
        for ref, source in sources.items()
        if source["kind"] in {"speech", "frame"}
        and source.get("origin", {}).get("lesson") == inventory["lesson"]
    ]
    require(target in eligible, "temporal_target_original")
    index = eligible.index(target)
    return eligible[max(0, index - 2) : index] + eligible[index + 1 : index + 3]


def load_inputs(root, inventory_key, ledger_job):
    from .course_textbook_resolution import inputs

    inventory, parent = inputs(root, ledger_job)
    require(sha(canonical(inventory)) == inventory_key, "matching_inventory")
    require(
        parent.get("policy") == REVISION_POLICY
        and parent.get("source_revision_rounds") == 1
        and parent["state"] == "ledger_requires_semantic_repair"
        and parent["issues"]
        and not parent["automatic_local_evidence_review_passed"],
        "terminal_unresolved_source_revision",
    )
    folder = Path(root) / "wiki/textbook-keyed-jobs" / ledger_job
    progress = json.loads((folder / "progress.json").read_bytes())
    require(progress["stage"] == "source_revision_complete", "completed_parent")
    try:
        owner = psutil.Process(progress["pid"])
        alive = abs(owner.create_time() - progress["created"]) < 0.001
    except psutil.NoSuchProcess:
        alive = False
    require(not alive, "parent_worker_exited")
    targets = sorted(issue["reference"] for issue in parent["issues"])
    require(
        len(targets) == len(set(targets))
        and all("source_revision_audit" in issue for issue in parent["issues"]),
        "exact_original_disputes",
    )
    scope = set(targets)
    quarantined = {
        key: point
        for key, point in parent["points"].items()
        if scope.intersection(point["citations"])
    }
    require(
        all(set(point["citations"]) <= scope for point in quarantined.values()),
        "explicit_shared_source_closure_before_composition",
    )
    points = {
        key: copy.deepcopy(point)
        for key, point in parent["points"].items()
        if key not in quarantined
    }
    cards = {}
    for number, ref in enumerate(targets):
        key = f"U{number:04d}"
        require(key not in points, "unique_original_task_identity")
        points[key] = {
            "label": f"待核对完整教学原文 {number + 1}",
            "teaching": inventory["sources"][ref]["text"],
            "category": "context",
            "citations": [ref],
        }
        cards[key] = {"reference": ref, "neighbors": neighbors(inventory, ref)}
    # This is a writing packet, deliberately not a new "completed ledger".
    working = {
        "policy": POLICY,
        "job": parent["job"],
        "lesson": inventory["lesson"],
        "inventory_sha256": inventory_key,
        "points": points,
        "pending_source_cards": cards,
        "parent_ledger_sha256": sha(canonical(parent)),
        "quarantined_point_keys": sorted(quarantined),
        "inherited_issues": copy.deepcopy(parent["issues"]),
        "confirmed_point_count": len(points) - len(cards),
        "source_revision_budget_refreshed": False,
        "reader_publication_allowed": False,
    }
    validate_result(parent, inventory, ledger_job)
    return inventory, working


def original_packet(ids, ledger, inventory):
    refs = {ref for key in ids for ref in ledger["points"][key]["citations"]}
    for key in ids:
        if key in ledger["pending_source_cards"]:
            refs.update(ledger["pending_source_cards"][key]["neighbors"])
    return {"sources": {ref: inventory["sources"][ref] for ref in sorted(refs)}}


def writing_context(ids, ledger, inventory):
    packet = original_packet(ids, ledger, inventory)
    return {
        "assigned_points": {key: ledger["points"][key] for key in ids},
        "sources": {
            ref: {"kind": row["kind"], "text": row["text"]}
            for ref, row in packet["sources"].items()
        },
        "pending_source_cards": {
            key: ledger["pending_source_cards"][key]
            for key in ids
            if key in ledger["pending_source_cards"]
        },
        "pending_cards_are_verified_facts": False,
    }


organize = author.organize


def writing_units(plan, ledger, inventory):
    return author.writing_units(plan, ledger, inventory, context_fn=writing_context)


def section_schema(ids, ledger):
    value = author.section_schema(ids)
    for key in set(ids).intersection(ledger["pending_source_cards"]):
        row = value["properties"]["coverage"]["properties"][key]
        row["properties"]["paragraphs"]["minItems"] = 0
        row["properties"]["teaching"] = {"type": "boolean"}
        row["required"].append("teaching")
    return value


def validate_section(value, ids, ledger, inventory):
    from .course_textbook_article import validate_graph

    jsonschema.validate(value, section_schema(ids, ledger))
    packet = original_packet(ids, ledger, inventory)

    def supported(row):
        citations = row["citations"]
        require(
            bool(citations)
            and len(citations) == len(set(citations))
            and set(citations) <= set(packet["sources"]),
            "scoped_original_citations",
        )

    for field in ("heading", "objective"):
        author.nonempty(value[field])
    for row in [*value["paragraphs"], value["overview"], value["takeaway"]]:
        author.nonempty(row["text"])
        supported(row)
    paragraphs = [row["text"].strip() for row in value["paragraphs"]]
    require(len(paragraphs) == len(set(paragraphs)), "unique_authored_paragraphs")
    for key, row in value["coverage"].items():
        author.nonempty(row["reason"])
        if key in ledger["pending_source_cards"] and not row["teaching"]:
            require(not row["paragraphs"], "explicit_non_teaching_disposition")
            continue
        require(
            bool(row["paragraphs"])
            and all(i < len(paragraphs) for i in row["paragraphs"]),
            "complete_teaching_coverage_paragraphs",
        )
        refs = {
            ref
            for index in row["paragraphs"]
            for ref in value["paragraphs"][index]["citations"]
        }
        require(
            bool(refs.intersection(ledger["points"][key]["citations"])),
            "primary_original_support_in_body",
        )
    for graph in value["diagrams"]:
        supported(graph)
        validate_graph({k: v for k, v in graph.items() if k != "citations"})
    supported(value["quiz"])
    for field in ("question", "answer", "explanation"):
        author.nonempty(value["quiz"][field])
    for criterion in value["quiz"]["criteria"]:
        author.nonempty(criterion)


def assemble(inventory, ledger, plan, units, sections):
    value = author.assemble(inventory, ledger, plan, units, sections)
    value.update(
        composition_policy=POLICY,
        parent_ledger_sha256=ledger["parent_ledger_sha256"],
        inherited_issues=ledger["inherited_issues"],
        quarantined_point_keys=ledger["quarantined_point_keys"],
        pending_source_cards=ledger["pending_source_cards"],
        reader_publication_allowed=False,
    )
    return value


def final_issues(inventory, ledger, private):
    require(
        set(private["private_coverage"]) == set(ledger["points"]),
        "complete_private_manuscript_allocation",
    )
    return [
        {
            "kind": "complete_original_to_manuscript_coverage_review_required",
            "references": sorted(
                v["reference"] for v in ledger["pending_source_cards"].values()
            ),
            "inherited_source_issues_retained": len(ledger["inherited_issues"]),
            "reader_publication_allowed": False,
        }
    ]


class AuthoringJob(author.AuthoringJob):
    def __init__(self, store, inventory, ledger, model_digest, wait_seconds):
        from .course_wiki_text_inference import profile

        DraftJob.__init__(
            self,
            store,
            {"sources": inventory["sources"]},
            "qwen3.5:9b",
            model_digest,
            wait_seconds,
            pipeline=POLICY,
            job_directory="textbook-composition-jobs",
            max_attempts=2,
            recipe_extra={
                "inventory_sha256": sha(canonical(inventory)),
                "working_inputs_sha256": sha(canonical(ledger)),
                "parent_ledger_sha256": ledger["parent_ledger_sha256"],
                "prompts": list(PROMPTS.values()),
                "pending_source_cards": ledger["pending_source_cards"],
                "context_limits": [
                    author.PLAN_CHARACTERS,
                    author.WRITE_CHARACTERS,
                    author.READ_CHARACTERS,
                ],
                "semantic_repairs_per_article_section": 1,
                "source_revision_budget_refreshed": False,
                "reader_publication_allowed": False,
                "text_profile": profile(),
                "module_sha256": {
                    name: sha(Path(__file__).with_name(name + ".py").read_bytes())
                    for name in (
                        "course_textbook_composition",
                        "course_textbook_authoring",
                        "course_textbook_article",
                        "course_textbook_evidence",
                        "course_textbook_ledger",
                        "course_wiki_writer",
                        "course_wiki_text_inference",
                    )
                },
            },
        )
        path = self.root / "private-composition-inputs.json"
        if path.exists():
            saved = json.loads(path.read_bytes())
            require(
                saved["data_sha256"] == sha(canonical(ledger))
                and saved["data"] == ledger,
                "unchanged_working_packet",
            )
        else:
            author.save(self.root, "private-composition-inputs", ledger)
        self.section_count, self.finished_sections = 0, 0
        self.point_count = len(ledger["points"])

    def stage(self, name, prompt, schema, context, validate, **kwargs):
        return super().stage(
            name, PROMPTS.get(prompt, prompt), schema, context, validate, **kwargs
        )

    def progress(self, state, stage, **extra):
        self.current = stage
        value = {
            **self.identity,
            "state": state,
            "stage": stage,
            "job": self.key,
            "policy": POLICY,
            "updated": time.time(),
            "completed_stages": self.completed,
            "completed_sections": self.finished_sections,
            "total_sections": self.section_count,
            "writing_cards": self.point_count,
            "reader_article_published": False,
            "reader_publication_allowed": False,
            **extra,
        }
        atomic_json(self.root / "progress.json", value)
        atomic_json(
            self.store.root / "wiki/textbook-composition-last-build.json", value
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("command", choices=["run", "guard"])
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--ledger-job", required=True)
    parser.add_argument("--wait-gpu-seconds", default=3600, type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "guard":
            from .course_gpu_guard import run as guarded

            load_inputs(args.root, args.inventory, args.ledger_job)
            guarded(
                args.root,
                [
                    sys.executable,
                    "-m",
                    __spec__.name,
                    "--root",
                    str(args.root.resolve()),
                    "run",
                    "--inventory",
                    args.inventory,
                    "--ledger-job",
                    args.ledger_job,
                    "--wait-gpu-seconds",
                    str(args.wait_gpu_seconds),
                ],
                maximum_power=200,
                maximum_temperature=70,
                guard_name="wiki-revision-thermal-guard",
                sampler=lambda: checked_sample(args.root),
            )
        else:
            print(
                json.dumps(
                    author.run(
                        args.root,
                        args.inventory,
                        args.ledger_job,
                        wait_seconds=args.wait_gpu_seconds,
                        protocol=sys.modules[__name__],
                    )
                )
            )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "state": "stopped",
                    "error_type": type(exc).__name__,
                    "error_sha256": sha(str(exc).encode()),
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
