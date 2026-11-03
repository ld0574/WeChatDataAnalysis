from copy import deepcopy
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_final_edit as edit
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_textbook_evidence import sha


def test_full_originals_and_all_related_body_survive_batching():
    sources = {
        f"R{i}": {"kind": "speech", "text": (str(i) + "甲，乙。\n") * 50}
        for i in range(17)
    }
    working = {
        "points": {f"P{i}": {"citations": [ref]} for i, ref in enumerate(sources)}
    }
    private = {
        "chapters": [
            {
                "sections": [
                    {
                        "id": "section-a",
                        "paragraphs": [
                            {"text": "解释一", "citations": ["R0"]},
                            {
                                "text": "解释二：通过知识点关联而非直接引用检索",
                                "citations": ["R1"],
                            },
                        ],
                    }
                ]
            }
        ],
        "private_coverage": {
            key: {"section": "section-a"} for key in working["points"]
        },
    }
    batches = edit.source_batches({"sources": sources}, working, private)
    observed = {
        key: row["target"]["text"] for batch in batches for key, row in batch.items()
    }
    assert observed == {key: source["text"] for key, source in sources.items()}
    assert sum(map(len, batches)) == len(sources)
    assert len(batches) >= 3
    first = next(batch["R0"] for batch in batches if "R0" in batch)
    assert set(first["paragraphs"]) == {"section-a/0", "section-a/1"}


@pytest.mark.parametrize(
    "teaching,covered,paragraph_ids",
    [
        (False, True, ["a/0"]),
        (False, False, ["a/0"]),
        (True, True, []),
        (True, False, ["a/0"]),
    ],
)
def test_source_review_cannot_claim_coverage_without_actual_mapping(
    teaching, covered, paragraph_ids
):
    row = {
        "sources": {
            "R0": {
                "teaching": teaching,
                "covered": covered,
                "paragraph_ids": paragraph_ids,
                "reason": "判断依据",
            }
        }
    }
    with pytest.raises(ValueError, match="requires_final_editor_"):
        edit.validate_sources(row, ["R0"], {"a/0": {}})


def test_source_review_rejects_an_invented_or_missing_source():
    row = {
        "sources": {
            "R1": {
                "teaching": True,
                "covered": True,
                "paragraph_ids": ["a/0"],
                "reason": "判断依据",
            }
        }
    }
    with pytest.raises(jsonschema.ValidationError):
        edit.validate_sources(row, ["R0"], {"a/0": {}})


def test_duplicate_body_detection_works_across_different_sections():
    sections = {
        "a": {"paragraphs": [{"text": "实际说明"}]},
        "b": {"paragraphs": [{"text": " 实际说明 "}]},
    }
    assert edit.duplicates(sections) == [[["a", 0], ["b", 0]]]


def test_reason_or_heading_changes_do_not_turn_failed_prose_into_a_new_review_candidate():
    original = {
        "heading": "甲",
        "objective": "目标",
        "paragraphs": [{"text": "说明", "citations": ["R0"]}],
        "coverage": {"P0": {"paragraphs": [0], "reason": "旧内部映射"}},
    }
    changed = deepcopy(original)
    changed["heading"] = "乙"
    changed["coverage"]["P0"]["reason"] = "新的内部映射"
    assert edit.reviewed_content(original) == edit.reviewed_content(changed)
    changed["paragraphs"][0]["text"] += "，并补充原先遗漏的条件"
    assert edit.reviewed_content(original) != edit.reviewed_content(changed)


def test_negative_audit_is_preserved_without_inventing_an_acceptance():
    audit = {
        "paragraphs": {
            "0": {"passed": True, "reason": "支持"},
            "1": {"passed": False, "reason": "条件遗漏"},
        },
        "quiz": {"passed": False, "reason": "答案错误"},
    }
    result = edit.negative(audit)
    assert result == {
        "paragraphs": {"1": audit["paragraphs"]["1"]},
        "quiz": audit["quiz"],
    }
    assert result["quiz"]["passed"] is False
    assert edit.negative({"quiz": {"passed": True, "reason": "支持"}}) is None


def test_authenticated_review_rejects_a_cache_that_disagrees_with_actual_model_call(
    tmp_path, monkeypatch
):
    value = {"passed": True, "reason": "支持"}
    atomic_json(
        tmp_path / "review.json",
        {
            "input_sha256": "binding",
            "data_sha256": sha(canonical(value)),
            "data": value,
        },
    )
    job = SimpleNamespace(
        root=tmp_path,
        model_digest="model",
        stage_input=lambda *a, **k: (edit.VERDICT, "binding"),
    )
    record = {
        "accepted_structure": True,
        "model_metrics": {},
        "raw_response": '{"passed":false,"reason":"遗漏"}',
    }
    monkeypatch.setattr(edit.post, "verify_requests", lambda *a: [(None, record)])
    monkeypatch.setattr(edit, "validate_metrics", lambda *a: None)
    with pytest.raises(ValueError, match="actual_stage_bytes"):
        edit.authenticated(
            job, "review", "prompt", edit.VERDICT, {}, lambda v: None, {"sources": {}}
        )


def test_final_verdict_budget_keeps_all_checks_and_does_not_change_their_values():
    schema = edit.author.review_schema(
        {"paragraphs": [{"text": "说明"}], "diagrams": []}, ["P0", "P1"]
    )
    short = edit.short_verdict_schema(schema)
    assert set(short["properties"]) == set(schema["properties"])
    assert set(short["properties"]["coverage"]["properties"]) == {"P0", "P1"}
    assert (
        short["properties"]["paragraphs"]["properties"]["0"]["properties"]["reason"][
            "maxLength"
        ]
        == 72
    )
    assert (
        "maxLength"
        not in schema["properties"]["paragraphs"]["properties"]["0"]["properties"][
            "reason"
        ]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"context_length": 16384},
        {"eval_count": 8193},
        {"prompt_eval_count": 25000},
        {"done_reason": "length"},
        {"size_vram": 0},
        {"model_digest": "other"},
    ],
)
def test_editorial_profile_requires_measured_gpu_residency_and_full_output_headroom(
    changes,
):
    valid = {
        "context_length": 32768,
        "eval_count": 7000,
        "prompt_eval_count": 11000,
        "done_reason": "stop",
        "size_vram": 7_000_000_000,
        "model_digest": "model",
    }
    edit.validate_metrics(valid, "model")
    with pytest.raises(ValueError, match="measured_gpu_context_and_output_headroom"):
        edit.validate_metrics({**valid, **changes}, "model")
