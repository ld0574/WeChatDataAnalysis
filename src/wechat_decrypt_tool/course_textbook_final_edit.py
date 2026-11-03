"""One bounded whole-article edit of a completed private manuscript.

Earlier calls and failed audits are immutable inputs. New prose receives its
own factual, point, original-source and reading checks; old coverage is never
relabelled as coverage of the edited article. Only statistics leave the CLI.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from pathlib import Path

import jsonschema

from . import course_textbook_authoring as author
from . import course_textbook_composition as composition
from . import course_textbook_deferred_repair as deferred
from . import course_textbook_post_repair_review as post
from .course_knowledge import ModelOutputTruncatedError, atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_article import reading_book, standalone_html
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore
from .course_wiki import object_schema
from .course_wiki_writer import DraftJob

POLICY = "finite-whole-article-editorial-rewrite-and-actual-body-audit-v1"
CONTRACT = "whole-article-editorial-execution.json"
MAX_REQUEST = 35000
SOURCE_REQUEST = 15000
OPTIONS = {
    "temperature": 0,
    "presence_penalty": 0,
    "repeat_penalty": 1.05,
    "repeat_last_n": 256,
    "num_ctx": 32768,
    "num_predict": 8192,
    "num_gpu": 999,
}
REASON = {"type": "string", "minLength": 1, "maxLength": 72}
VERDICT = object_schema({"passed": {"type": "boolean"}, "reason": REASON})
EDIT = (
    author.COMMON
    + """
你正在做整篇文章的最后一轮实质编辑。旧稿和历史负面审核仅供查错，不是事实依据。
必须根据本节 assigned_points 和完整 sources 重写本节，修复所有已指出的缺漏、事实和图文问题。
按问题、定义、原理、条件、示例、边界组织连贯正文；有关知识合并解释，不逐卡抄摘要。
邻节标题只是学习导航，禁止复制旧稿、邻节或套用同一篇正文。把本节独有的教学信息讲完整。
other_section_headings 仅用于理解边界，其他小节的知识不能当作当前遗漏，也不能代替当前正文。
不要凭空增加来源没有的知识。材料中的代码如果必要，应讲清作用、输入条件、示例结果和限制。
每段 citations 仅列完整支持本段的原文；coverage 必须映射实际讲清该点的段落，不靠填引用过关。
已有重复解释要重组，不写时间片段、来源笔记、“老师提到”或编辑过程说明。
pending_source_cards 中的完整原文尚有争议，直接依据原文判断其中有无教学，不能照抄旧判断。
若提供 final_negative_review，依据 sources 逐项修复这份对新稿的负面结论；不能只改内部理由而保留原错。
coverage.reason 用不超过24字的索引说明；把推导、条件和例子写在正文，不能挤进内部映射。
解释图只画有依据的关系；没有必要时 diagrams=[]。自测必须检验理解或应用并有可用掌握标准。
只输出完整 JSON，不声称已经验收。
"""
)
REVIEW = (
    author.REVIEW_PROMPT
    + """
这是对实际新正文的最终事实和覆盖检查，不是对旧稿的投票。
必须核对每段自己的 citations；检查同一段是否真能支持所映射的不同知识，不能因有引用就通过。
reason 用72字内准确说明依据或缺口，避免重复原文、长篇重述和泛泛的通过结论。
"""
)
SOURCE_REVIEW = (
    author.COMMON
    + """
逐一复核 targets 中每份完整原文，核对 actual_body 中的实际新正文，不以知识账本代替正文。
对原文中所有有效教学（包括条件、数值、例子、区别和限制）判断是否完整讲明。
重复口语、寒暄、广告、导航和无意义识别可判为非教学，但不能因正文遗漏而排除教学。
若 teaching=true 且 covered=true，必须提供真正支持全部有效教学的实际 paragraph_ids。
若有教学遗漏，teaching=true、covered=false、paragraph_ids=[]，reason 精确说明缺口。
若 teaching=false，covered=false、paragraph_ids=[]，reason 说明非教学依据。
正文已经用另一例子解释相同原理，不必复述每次相同口语；独有条件与实际教学步骤不可遗漏。
其他段落未给出不能靠猜测通过。每个目标恰好一次；reason 不超过72字。
"""
)


def require(value, code):
    if not value:
        raise ValueError("requires_final_editor_" + code)


def profile():
    return {
        "policy": "editorial-local-gpu-context32768-output8192-v1",
        "options": dict(OPTIONS),
    }


def validate_metrics(metrics, model_digest):
    require(
        metrics.get("model_digest") == model_digest
        and metrics.get("done_reason") == "stop"
        and type(metrics.get("prompt_eval_count")) is int
        and 0
        < metrics["prompt_eval_count"]
        < OPTIONS["num_ctx"] - OPTIONS["num_predict"]
        and type(metrics.get("eval_count")) is int
        and 0 < metrics["eval_count"] <= OPTIONS["num_predict"]
        and metrics.get("context_length") == OPTIONS["num_ctx"]
        and type(metrics.get("size_vram")) is int
        and metrics["size_vram"] > 0,
        "measured_gpu_context_and_output_headroom",
    )


def chat(model, messages, *, schema):
    import httpx
    from .course_pdf_ocr import local_model_digest

    weights = local_model_digest(model)
    with httpx.Client(trust_env=False, timeout=900) as client:
        response = client.post(
            "http://127.0.0.1:11434/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "think": False,
                "keep_alive": "5m",
                "format": schema,
                "options": dict(OPTIONS),
            },
        )
        response.raise_for_status()
        value = response.json()
        loaded = client.get("http://127.0.0.1:11434/api/ps")
        loaded.raise_for_status()
        owners = [
            row
            for row in loaded.json().get("models", [])
            if row.get("digest") == weights
        ]
    require(
        local_model_digest(model) == weights and len(owners) == 1,
        "unchanged_local_model_gpu_owner",
    )
    if value.get("done_reason") != "stop":
        raise ModelOutputTruncatedError(value)
    metrics = {
        key: value.get(key)
        for key in ["done_reason", "prompt_eval_count", "eval_count", "eval_duration"]
    }
    metrics.update(
        model_digest=weights,
        context_length=owners[0].get("context_length"),
        size_vram=owners[0].get("size_vram"),
    )
    validate_metrics(metrics, weights)
    return value["message"]["content"], metrics


def short_verdict_schema(schema):
    value = copy.deepcopy(schema)
    if isinstance(value, dict):
        properties = value.get("properties", {})
        if "passed" in properties and "reason" in properties:
            properties["reason"] = copy.deepcopy(REASON)
        else:
            for key, item in value.items():
                if isinstance(item, dict):
                    value[key] = short_verdict_schema(item)
    return value


def negative(value):
    """Retain every actual negative reason without copying positive chatter."""
    if not isinstance(value, dict):
        return None
    if "passed" in value:
        return value if value["passed"] is False else None
    result = {key: item for key, child in value.items() if (item := negative(child))}
    return result or None


def duplicates(sections):
    seen, result = {}, []
    for section, candidate in sections.items():
        for number, paragraph in enumerate(candidate["paragraphs"]):
            key = sha(paragraph["text"].strip().encode())
            if key in seen:
                result.append([seen[key], [section, number]])
            else:
                seen[key] = [section, number]
    return result


def edit_schema(ids, working):
    value = composition.section_schema(ids, working)
    for row in value["properties"]["coverage"]["properties"].values():
        row["properties"]["reason"] = {
            "type": "string",
            "minLength": 1,
            "maxLength": 24,
        }
    return value


def machine_section(candidate, ids, working, inventory, preceding):
    composition.validate_section(candidate, ids, working, inventory)
    prior = {
        row["text"].strip()
        for value in preceding.values()
        for row in value["paragraphs"]
    }
    require(
        not any(row["text"].strip() in prior for row in candidate["paragraphs"]),
        "duplicate_prior_body_paragraph",
    )
    teaching = any(
        key not in working["pending_source_cards"]
        or candidate["coverage"][key]["teaching"]
        for key in ids
    )
    if teaching:
        require(
            sum(len(row["text"]) for row in candidate["paragraphs"]) >= 350,
            "developed_body_explanation",
        )
    require(
        candidate["heading"].strip()
        not in {s["heading"].strip() for s in preceding.values()},
        "unique_section_heading",
    )


def reviewed_content(value):
    result = copy.deepcopy(value)
    result.pop("heading", None)
    result.pop("objective", None)
    for row in result.get("coverage", {}).values():
        row.pop("reason", None)
    return result


def section_issues(folder, section):
    result = {}
    for suffix in ["-review.json", "-repair-review.json"]:
        path = folder / (section + suffix)
        if path.exists():
            item = negative(post.saved(path)["data"])
            if item:
                result[path.stem] = item
    return result


class EditorJob(DraftJob):
    def __init__(self, root, composition_job, inventory, working, parent, digest):
        DraftJob.__init__(
            self,
            TextbookStore(root),
            {"sources": inventory["sources"]},
            "qwen3.5:9b",
            digest,
            6 * 3600,
            pipeline=POLICY,
            job_directory="textbook-final-edit-jobs",
            max_attempts=2,
            recipe_extra={
                "composition_job": composition_job,
                "parent_article_sha256": sha(canonical(parent)),
                "working_sha256": sha(canonical(working)),
                "module_sha256": sha(Path(__file__).read_bytes()),
                "text_profile": profile(),
                "prompts": [EDIT, REVIEW, SOURCE_REVIEW],
                "maximum_editorial_writes_per_section": 2,
                "original_budgets_refreshed": False,
                "maximum_request_characters": MAX_REQUEST,
                "source_request_characters": SOURCE_REQUEST,
            },
        )
        self.composition_job = composition_job

    def progress(self, state, stage, **extra):
        self.current = stage
        atomic_json(
            self.root / "progress.json",
            {
                **self.identity,
                "job": self.key,
                "composition_job": self.composition_job,
                "state": state,
                "stage": stage,
                "updated": time.time(),
                "completed_stages": self.completed,
                "source_deletion_allowed": False,
                **extra,
            },
        )

    def generate_response(self, messages, *, schema):
        require(
            len(canonical({"messages": messages, "schema": schema}).decode("utf-8"))
            <= MAX_REQUEST,
            "complete_context_fits",
        )
        return chat(self.model, messages, schema=schema)


def authenticated(job, name, prompt, schema, context, validate, packet):
    """Match a cached stage to its one real accepted call and original request."""
    actual_schema, binding = job.stage_input(
        prompt, schema, context, evidence_packet=packet
    )
    value = post.saved(job.root / (name + ".json"))
    require(value["input_sha256"] == binding, "stage_binding")
    records = post.verify_requests(job, name, prompt, schema, context, packet)
    accepted = [record for _, record in records if record.get("accepted_structure")]
    require(
        len(accepted) == 1 and accepted[0] is records[-1][1],
        "terminal_real_accepted_call",
    )
    validate_metrics(accepted[0]["model_metrics"], job.model_digest)
    require(
        json.loads(accepted[0]["raw_response"]) == value["data"], "actual_stage_bytes"
    )
    jsonschema.validate(value["data"], actual_schema)
    validate(value["data"])
    return value["data"]


def actual_body(private):
    return {
        section["id"] + "/" + str(number): paragraph
        for chapter in private["chapters"]
        for section in chapter["sections"]
        for number, paragraph in enumerate(section["paragraphs"])
    }


def source_schema(refs, paragraphs):
    mapped = {
        "type": "array",
        "items": {"type": "string", "enum": list(paragraphs)},
        "uniqueItems": True,
    }
    if not paragraphs:
        mapped = {"type": "array", "items": {"type": "string"}, "maxItems": 0}
    return object_schema(
        {
            "sources": object_schema(
                {
                    ref: object_schema(
                        {
                            "teaching": {"type": "boolean"},
                            "covered": {"type": "boolean"},
                            "paragraph_ids": mapped,
                            "reason": REASON,
                        }
                    )
                    for ref in refs
                }
            )
        }
    )


def validate_sources(value, refs, paragraphs):
    jsonschema.validate(value, source_schema(refs, paragraphs))
    for row in value["sources"].values():
        require(bool(row["reason"].strip()), "source_review_reason")
        require(
            row["teaching"] or not row["covered"], "non_teaching_not_claimed_covered"
        )
        require(
            bool(row["paragraph_ids"]) == (row["teaching"] and row["covered"]),
            "actual_source_body_mapping",
        )


def source_batches(inventory, working, private):
    """Every full source once, with all actual paragraphs of related point slots."""
    body = actual_body(private)
    sections = {s["id"]: s for c in private["chapters"] for s in c["sections"]}
    records = {}
    for ref, source in inventory["sources"].items():
        related = {
            private["private_coverage"][key]["section"]
            for key, point in working["points"].items()
            if ref in point["citations"]
        }
        direct = {
            key for key, paragraph in body.items() if ref in paragraph["citations"]
        }
        ids = direct | {
            section + "/" + str(n)
            for section in related
            for n in range(len(sections[section]["paragraphs"]))
        }
        records[ref] = {
            "target": {"kind": source["kind"], "text": source["text"]},
            "paragraphs": {key: body[key] for key in sorted(ids)},
        }
    batches, current = [], {}
    for ref, row in records.items():
        proposed = {**current, ref: row}
        context = source_context(proposed)
        size = len(canonical(context).decode("utf-8")) + len(SOURCE_REVIEW)
        if current and (size > SOURCE_REQUEST or len(proposed) > 8):
            batches.append(current)
            current = {ref: row}
        else:
            current = proposed
        require(
            len(canonical(source_context(current)).decode("utf-8")) + len(SOURCE_REVIEW)
            <= SOURCE_REQUEST,
            "whole_single_source_and_related_body_fit",
        )
    if current:
        batches.append(current)
    require(
        [key for batch in batches for key in batch] == list(inventory["sources"]),
        "every_original_source_once",
    )
    return batches


def source_context(batch):
    return {
        "targets": {key: row["target"] for key, row in batch.items()},
        "actual_body": {
            key: paragraph
            for row in batch.values()
            for key, paragraph in row["paragraphs"].items()
        },
    }


def artifact_html(book, issues):
    rendered = standalone_html(book)
    if issues:
        rendered = rendered.replace(
            "<body>",
            '<body><aside role="status" style="padding:18px;text-align:center;background:#fff2d7;color:#6a4814">未完成最终核验的编辑稿，仍有待处理问题；不能据此删除原始资料。</aside>',
            1,
        )
    return rendered


def editor_inputs(root, composition_job):
    folder, inventory, working, plan, units, _, _ = post.inputs(root, composition_job)
    parent = deferred.verified_output(root, composition_job)
    sections = {
        s["id"]: {k: v for k, v in s.items() if k != "id"}
        for c in parent["chapters"]
        for s in c["sections"]
    }
    return folder, inventory, working, plan, units, parent, sections


def writing_context(
    unit,
    inventory,
    working,
    plan,
    originals,
    sections,
    old_duplicates,
    inherited_reading,
):
    section = unit["id"]
    return {
        **composition.writing_context(unit["point_ids"], working, inventory),
        "chapter": unit["chapter"],
        "concept": unit["concept"],
        "article_outline": [plan["nodes"][key]["heading"] for key in plan["chapters"]],
        "prior_draft_sha256": sha(canonical(originals[section])),
        "exact_duplicate_locations": [
            x for x in old_duplicates if any(loc[0] == section for loc in x)
        ],
        "previous_heading": next(reversed(sections.values()))["heading"]
        if sections
        else None,
        "other_section_headings": sorted(
            {value["heading"] for key, value in originals.items() if key != section}
        ),
        "historical_reading_issues": inherited_reading,
    }


def run(root, composition_job):
    from .course_pdf_ocr import local_model_digest

    root = Path(root).resolve()
    folder, inventory, working, plan, units, parent, originals = editor_inputs(
        root, composition_job
    )
    store = TextbookStore(root)
    store.checkpoint()
    with schema_lease(folder / "whole-article-editor.lock", timeout=0):
        job = EditorJob(
            root,
            composition_job,
            inventory,
            working,
            parent,
            local_model_digest("qwen3.5:9b"),
        )
        binding = {
            "policy": POLICY,
            "job": job.key,
            "parent_article_sha256": sha(canonical(parent)),
            "module_sha256": sha(Path(__file__).read_bytes()),
            "original_budgets_refreshed": False,
        }
        path = folder / CONTRACT
        if path.exists():
            require(
                json.loads(path.read_bytes()) == binding,
                "same_finite_editorial_contract",
            )
        else:
            atomic_json(path, binding)
        author.save(job.root, "parent-article", parent)
        issue_records = post.saved(folder / "quality-issues.json")["data"]
        reading_issues = [
            x for x in issue_records if x["kind"] == "whole_reading_review"
        ]
        old_reading_blocks = author.reading_blocks(units, originals)
        old_duplicates = duplicates(originals)
        author.save(
            job.root,
            "inherited-editorial-issues",
            {
                "duplicates": old_duplicates,
                "whole_reading_reviews": reading_issues,
                "unaccepted_sections": parent["unaccepted_sections"],
            },
        )
        sections, section_reviews = {}, {}
        try:
            profile_schema = object_schema({"ready": {"const": True}})
            profile_context = {
                "task": "Return ready=true; this is a local GPU context capability check."
            }
            profile_prompt = "Return the required JSON object."

            def validate_profile(value):
                jsonschema.validate(value, profile_schema)

            job.stage(
                "gpu-profile-capability",
                profile_prompt,
                profile_schema,
                profile_context,
                validate_profile,
            )
            authenticated(
                job,
                "gpu-profile-capability",
                profile_prompt,
                profile_schema,
                profile_context,
                validate_profile,
                job.packet,
            )
            for number, unit in enumerate(units):
                ids, section = unit["point_ids"], unit["id"]
                packet = composition.original_packet(ids, working, inventory)
                inherited_reading = [
                    negative(x["review"])
                    for x in reading_issues
                    if section in old_reading_blocks[x["block"]]
                ]
                context = writing_context(
                    unit,
                    inventory,
                    working,
                    plan,
                    originals,
                    sections,
                    old_duplicates,
                    inherited_reading,
                )
                context["historical_negative_reviews"] = section_issues(folder, section)
                context["diagram_preferred"] = (
                    number == 0 or units[number - 1]["chapter_id"] != unit["chapter_id"]
                )
                schema = edit_schema(ids, working)

                def validate(v, ids=ids):
                    machine_section(v, ids, working, inventory, sections)
                    if (
                        section in parent["unaccepted_sections"]
                        and context["historical_negative_reviews"]
                    ):
                        require(
                            reviewed_content(v) != reviewed_content(originals[section]),
                            "failed_candidate_materially_changed",
                        )

                name = section + "-whole-edit"
                job.stage(name, EDIT, schema, context, validate, evidence_packet=packet)
                candidate = authenticated(
                    job, name, EDIT, schema, context, validate, packet
                )
                review_context = {
                    **composition.writing_context(ids, working, inventory),
                    "candidate": candidate,
                }
                review_schema = short_verdict_schema(
                    author.review_schema(candidate, ids)
                )
                audit_name = section + "-actual-body-review"

                def reviewer(v, schema=review_schema):
                    author.validate_review(v, schema)

                job.stage(
                    audit_name,
                    REVIEW,
                    review_schema,
                    review_context,
                    reviewer,
                    evidence_packet=packet,
                )
                audit = authenticated(
                    job,
                    audit_name,
                    REVIEW,
                    review_schema,
                    review_context,
                    reviewer,
                    packet,
                )
                if not all(author.verdicts(audit)):
                    repair_context = {
                        **context,
                        "candidate": candidate,
                        "final_negative_review": negative(audit),
                    }
                    repair_name = section + "-whole-edit-repair"
                    initial_hash = sha(canonical(reviewed_content(candidate)))

                    def repair_validate(value):
                        validate(value)
                        require(
                            sha(canonical(reviewed_content(value))) != initial_hash,
                            "repair_actually_changes_candidate",
                        )

                    job.stage(
                        repair_name,
                        EDIT,
                        schema,
                        repair_context,
                        repair_validate,
                        evidence_packet=packet,
                    )
                    candidate = authenticated(
                        job,
                        repair_name,
                        EDIT,
                        schema,
                        repair_context,
                        repair_validate,
                        packet,
                    )
                    review_context = {
                        **composition.writing_context(ids, working, inventory),
                        "candidate": candidate,
                    }
                    review_schema = short_verdict_schema(
                        author.review_schema(candidate, ids)
                    )

                    def reviewer(v, schema=review_schema):
                        author.validate_review(v, schema)

                    audit_name = section + "-repaired-actual-body-review"
                    job.stage(
                        audit_name,
                        REVIEW,
                        review_schema,
                        review_context,
                        reviewer,
                        evidence_packet=packet,
                    )
                    audit = authenticated(
                        job,
                        audit_name,
                        REVIEW,
                        review_schema,
                        review_context,
                        reviewer,
                        packet,
                    )
                sections[section], section_reviews[section] = candidate, audit
                job.progress(
                    "editing_whole_article",
                    section,
                    sections_written=len(sections),
                    total_sections=len(units),
                    sections_passing=sum(
                        all(author.verdicts(a)) for a in section_reviews.values()
                    ),
                )
            private = composition.assemble(inventory, working, plan, units, sections)
            require(not duplicates(sections), "no_exact_duplicate_body")
            author.save(job.root, "private-edited-article", private)
            issues = [
                {"kind": "section_final_review", "section": key}
                for key, audit in section_reviews.items()
                if not all(author.verdicts(audit))
            ]
            if not any(s["diagrams"] for s in sections.values()):
                issues.append({"kind": "no_supported_explanatory_diagrams"})
            for number, block in enumerate(author.reading_blocks(units, sections)):
                name = f"actual-reading-{number:04d}"
                schema = short_verdict_schema(author.READ_REVIEW)
                context = {"complete_reading_sections": block}

                def validate(v, schema=schema):
                    author.validate_review(v, schema)

                job.stage(name, author.READ_PROMPT, schema, context, validate)
                audit = authenticated(
                    job, name, author.READ_PROMPT, schema, context, validate, job.packet
                )
                if not all(author.verdicts(audit)):
                    issues.append({"kind": "whole_reading_review", "block": number})
            batches = source_batches(inventory, working, private)
            author.save(
                job.root,
                "actual-body-source-plan",
                {"article_sha256": sha(canonical(private)), "batches": batches},
            )
            reviewed_sources = {}
            for number, batch in enumerate(batches):
                context = source_context(batch)
                refs, paragraphs = list(batch), context["actual_body"]
                schema = source_schema(refs, paragraphs)
                packet = {"sources": {ref: inventory["sources"][ref] for ref in refs}}

                def validate(v, refs=refs, paragraphs=paragraphs):
                    validate_sources(v, refs, paragraphs)

                name = f"actual-originals-{number:04d}"
                job.stage(
                    name,
                    SOURCE_REVIEW,
                    schema,
                    context,
                    validate,
                    evidence_packet=packet,
                )
                audit = authenticated(
                    job, name, SOURCE_REVIEW, schema, context, validate, packet
                )
                reviewed_sources.update(audit["sources"])
                job.progress(
                    "checking_all_originals_against_actual_body",
                    name,
                    sources_reviewed=len(reviewed_sources),
                    total_sources=len(inventory["sources"]),
                    total_source_batches=len(batches),
                )
            require(
                set(reviewed_sources) == set(inventory["sources"]),
                "complete_actual_body_source_review",
            )
            missing = [
                key
                for key, row in reviewed_sources.items()
                if row["teaching"] and not row["covered"]
            ]
            if missing:
                issues.append(
                    {"kind": "original_teaching_missing_from_body", "sources": missing}
                )
            known = {
                ref
                for key, point in working["points"].items()
                if key not in working["pending_source_cards"]
                for ref in point["citations"]
            }
            conflicts = [ref for ref in known if not reviewed_sources[ref]["teaching"]]
            if conflicts:
                issues.append(
                    {"kind": "teaching_classification_conflict", "sources": conflicts}
                )
            if inventory["attachment_scope"].get("unresolved", True):
                issues.append({"kind": "attachment_association_not_yet_verified"})
            author.save(
                job.root,
                "actual-original-coverage",
                {
                    "article_sha256": sha(canonical(private)),
                    "sources": reviewed_sources,
                    "missing_sources": missing,
                    "classification_conflicts": conflicts,
                    "old_manuscript_coverage_reused": False,
                },
            )
            author.save(job.root, "quality-issues", issues)
            book = reading_book(private)
            author.save(job.root, "reading-projection", book)
            (job.root / "edited-reading.html").write_text(
                artifact_html(book, issues), encoding="utf-8"
            )
            result = {
                "state": "edited_article_requires_final_repair"
                if issues
                else "edited_article_checks_passed",
                "job": job.key,
                "composition_job": composition_job,
                "course_id": inventory["lesson"],
                "article_sha256": sha(canonical(private)),
                "parent_article_sha256": sha(canonical(parent)),
                "sections": len(sections),
                "sources_reviewed": len(reviewed_sources),
                "section_reviews_passed": sum(
                    all(author.verdicts(a)) for a in section_reviews.values()
                ),
                "missing_sources": len(missing),
                "classification_conflicts": len(conflicts),
                "quality_issues": len(issues),
                "reader_publication_allowed": not issues,
                "reader_article_published": False,
                "source_deletion_allowed": False,
            }
            author.save(job.root, "result", result)
            job.progress(
                result["state"],
                "complete",
                **{
                    k: v
                    for k, v in result.items()
                    if k not in ["state", "job", "composition_job"]
                },
            )
            return result
        except BaseException as exc:
            message = str(exc)
            job.progress(
                "paused" if isinstance(exc, InterruptedError) else "stopped",
                job.current,
                error_type=type(exc).__name__,
                error_sha256=sha(message.encode()),
                reason_code=message
                if re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", message)
                else None,
            )
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--job", required=True)
    parser.add_argument("command", choices=["run", "guard"])
    args = parser.parse_args(argv)
    try:
        if args.command == "guard":
            from .course_archive_gpu import checked_sample
            from .course_gpu_guard import run as guarded

            guarded(
                args.root,
                [
                    sys.executable,
                    "-c",
                    "from wechat_decrypt_tool.course_textbook_final_edit import main; main()",
                    "--root",
                    str(args.root.resolve()),
                    "--job",
                    args.job,
                    "run",
                ],
                maximum_power=200,
                maximum_temperature=70,
                guard_name="wiki-revision-thermal-guard",
                sampler=lambda: checked_sample(args.root),
            )
        else:
            print(json.dumps(run(args.root, args.job)))
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
