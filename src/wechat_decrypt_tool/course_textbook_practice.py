"""Version-bound choice exercises and grading for the local article reader."""
from __future__ import annotations

import html
import json
from pathlib import Path

import jsonschema

from .course_knowledge import atomic_json
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_wiki import TEXT, object_schema

LETTERS = list("ABCD")
CHOICE = object_schema({"id": {"type": "string", "pattern": "^Q[1-5]$"},
    "type": {"enum": ["single", "multiple"]}, "question": TEXT,
    "options": object_schema({key: TEXT for key in LETTERS}),
    "correct": {"type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True, "items": {"enum": LETTERS}},
    "explanation": TEXT,
    "support": {"type": "array", "minItems": 1, "items": object_schema({"section": TEXT, "quote": TEXT})}})
SCHEMA = object_schema({"questions": {"type": "array", "minItems": 1, "maxItems": 5, "items": CHOICE}})


def source_sections(book):
    return {section["id"]: {"kind": "article", "text": "\n\n".join(section["paragraphs"])}
        for chapter in book["chapters"] for section in chapter["sections"]}


def validate(value, book):
    jsonschema.validate(value, SCHEMA)
    sources = source_sections(book)
    ids = [row["id"] for row in value["questions"]]
    if ids != [f"Q{i + 1}" for i in range(len(ids))]:
        raise ValueError("requires_choice_quiz_ordered_unique_ids")
    for row in value["questions"]:
        if (row["type"] == "single") != (len(row["correct"]) == 1):
            raise ValueError("requires_choice_quiz_consistent_answer_type")
        if len({text.strip() for text in row["options"].values()}) != 4:
            raise ValueError("requires_choice_quiz_distinct_options")
        for item in row["support"]:
            if item["section"] not in sources or item["quote"] not in sources[item["section"]]["text"]:
                raise ValueError("requires_choice_quiz_literal_article_support")
    return value


def store(root, revision, value, proof):
    pack = {"format": "course-choice-practice-v1", "article_sha256": revision, "questions": value["questions"], "proof": proof}
    digest = sha(canonical(pack))
    folder = Path(root) / "wiki/textbook-reading/practice"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (digest + ".json")
    if target.exists() and target.read_bytes() != canonical(pack):
        raise ValueError("requires_choice_quiz_immutable_pack")
    if not target.exists():
        target.write_bytes(canonical(pack))
    atomic_json(folder / (revision + ".current.json"), {"pack_sha256": digest})
    return digest


def load(root, revision, book):
    folder = Path(root) / "wiki/textbook-reading/practice"
    pointer = folder / (revision + ".current.json")
    if not pointer.exists():
        return None
    key = json.loads(pointer.read_bytes())["pack_sha256"]
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("requires_choice_quiz_valid_pack_key")
    raw = (folder / (key + ".json")).read_bytes()
    pack = json.loads(raw)
    if sha(raw) != key or pack["article_sha256"] != revision or pack["format"] != "course-choice-practice-v1":
        raise ValueError("requires_choice_quiz_version_bound_pack")
    validate({"questions": pack["questions"]}, book)
    if pack["proof"].get("independent_review_passed") is not True:
        raise ValueError("requires_choice_quiz_independent_review")
    proof = pack["proof"]
    job = proof.get("job", "")
    if len(job) != 32 or any(c not in "0123456789abcdef" for c in job):
        raise ValueError("requires_choice_quiz_review_job")
    folder = Path(root) / "wiki/textbook-choice-quiz-jobs" / job
    checked = json.loads((folder / "accepted-practice-proof.json").read_bytes())
    if (proof != {**checked, "proof_sha256": sha(canonical(checked))}
            or checked["article_sha256"] != revision
            or checked["questions_sha256"] != sha(canonical(pack["questions"]))
            or set(checked["models"]) != {"qwen3.5:9b", "course-defiant:q4"}
            or len(set(checked["models"].values())) != 2):
        raise ValueError("requires_choice_quiz_actual_review_proof")
    expected = [{"choices-write", "choices-review"}, {"choices-repair", "choices-repair-review"},
        {"retained-candidate", "choices-review"}]
    if set(checked["stages"]) not in expected:
        raise ValueError("requires_choice_quiz_declared_review_stages")
    for name, digest in checked["stages"].items():
        if sha((folder / (name + ".json")).read_bytes()) != digest:
            raise ValueError("requires_choice_quiz_unchanged_review_stage")
    return {**pack, "pack_sha256": key}


def public(pack):
    return {"pack_sha256": pack["pack_sha256"], "article_sha256": pack["article_sha256"],
        "questions": [{key: row[key] for key in ("id", "type", "question", "options")} for row in pack["questions"]]}


def grade(pack, request):
    if not isinstance(request, dict) or set(request) != {"pack_sha256", "answers"} or request["pack_sha256"] != pack["pack_sha256"]:
        raise ValueError("requires_choice_quiz_matching_submission")
    answers = request["answers"]
    if not isinstance(answers, dict) or set(answers) != {row["id"] for row in pack["questions"]}:
        raise ValueError("requires_choice_quiz_all_questions_answered")
    results = []
    for row in pack["questions"]:
        selected = answers[row["id"]]
        if (not isinstance(selected, list) or not 1 <= len(selected) <= 4
                or any(type(key) is not str or key not in LETTERS for key in selected)
                or len(set(selected)) != len(selected) or row["type"] == "single" and len(selected) != 1):
            raise ValueError("requires_choice_quiz_valid_selection")
        results.append({"id": row["id"], "correct": set(selected) == set(row["correct"]),
            "correct_options": row["correct"], "explanation": row["explanation"]})
    return {"score": sum(row["correct"] for row in results), "total": len(results), "results": results}


def practice_html(pack, course, *, offline=False):
    esc = html.escape
    parts = ['<section id="reading-self-test"><h2>课后自测</h2>']
    if not pack:
        return parts[0] + '<p>选择题正在整理。</p></section>'
    parts.append(f'<p>共 {len(pack["questions"])} 题。完成全部选择后提交，查看得分与答案解析。</p>')
    parts.append(f'<form id="choice-practice" data-pack="{pack["pack_sha256"]}" data-course="{course}" data-offline="{str(offline).lower()}">')
    for index, row in enumerate(pack["questions"], 1):
        kind = "单选" if row["type"] == "single" else "多选"
        parts.append(f'<fieldset data-question="{row["id"]}" data-type="{row["type"]}"><legend>{index}. {esc(row["question"])} <span class="question-kind">{kind}</span></legend>')
        for letter, text in row["options"].items():
            input_type = "radio" if row["type"] == "single" else "checkbox"
            parts.append(f'<label class="choice-option"><input type="{input_type}" name="{row["id"]}" value="{letter}"><span><b>{letter}.</b> {esc(text)}</span></label>')
        parts.append(f'<div class="choice-feedback" id="feedback-{row["id"]}" hidden></div></fieldset>')
    parts.append('<p id="choice-status" role="status" aria-live="polite">尚未提交。</p><button type="submit">提交并查看解析</button><button type="button" id="choice-reset">重新作答</button></form></section>')
    if offline:
        data = json.dumps({"questions": pack["questions"]}, ensure_ascii=False).replace("<", "\\u003c")
        parts.append('<script type="application/json" id="offline-practice-key">' + data + '</script>')
    return "".join(parts)


STYLE = """
#choice-practice{display:block;margin:1.5rem 0}#choice-practice fieldset{min-width:0;margin:1.5rem 0;padding:1.2rem;border:1px solid #d9dfd8;border-radius:8px}
#choice-practice legend{padding:0 .4rem;font-weight:600;line-height:1.7}.question-kind{font-size:.8rem;color:#526455;white-space:nowrap}
.choice-option{display:flex;gap:.7rem;align-items:flex-start;padding:.7rem;margin:.3rem 0;background:#fffefa;border:1px solid #e2e6de;border-radius:4px;cursor:pointer}
.choice-option input{flex:none;width:1.15rem;height:1.15rem;margin:.35rem 0 0;accent-color:#285743}.choice-option:has(input:checked){background:#edf3ec;border-color:#456251}
#choice-practice button{font:inherit;padding:.65rem 1rem;margin:.4rem .7rem .4rem 0;border:1px solid #456251;border-radius:4px;background:#285743;color:white;cursor:pointer}
#choice-practice button:disabled{opacity:.6;cursor:wait}#choice-reset{background:transparent!important;color:#285743!important}
.choice-feedback{margin-top:1rem;padding:1rem;background:#edf3ec;overflow-wrap:anywhere}.choice-feedback[data-correct=false]{background:#fff0d4}.choice-feedback[hidden]{display:none}
"""
