import copy
import json

import jsonschema
import pytest

from test_course_textbook_gap_additions import packet
from wechat_decrypt_tool import course_textbook_edition_sources as module


@pytest.mark.parametrize("kind", ["covered", "non_teaching", "missing"])
def test_source_dispositions_are_mutually_exclusive_and_source_bound(kind):
    value, targets, _, _ = packet()
    schema = module.schema_for(targets, {"p000": "Actual paragraph"}, value["sources"])
    result = {"sources": {k: {"kind": kind, "reason": "Synthetic independent reason",
        "source_quote": value["sources"][k]["text"][:400],
        **({"paragraph_ids": ["p000"]} if kind == "covered" else {})} for k in targets}}
    module.validate(result, schema, targets, value["sources"])
    changed = copy.deepcopy(result)
    changed["sources"]["gap1"]["covered"] = True
    with pytest.raises(jsonschema.ValidationError):
        module.validate(changed, schema, targets, value["sources"])
    changed = copy.deepcopy(result)
    changed["sources"]["gap1"]["source_quote"] = "Fabricated quotation"
    with pytest.raises(jsonschema.ValidationError):
        module.validate(changed, schema, targets, value["sources"])


def test_positive_coverage_requires_real_nonempty_paragraph_positions():
    value, targets, _, _ = packet()
    schema = module.schema_for(targets, {"p000": "Actual paragraph"}, value["sources"])
    result = {"sources": {k: {"kind": "covered", "reason": "Synthetic reason",
        "source_quote": value["sources"][k]["text"][:400], "paragraph_ids": []} for k in targets}}
    with pytest.raises(jsonschema.ValidationError):
        module.validate(result, schema, targets, value["sources"])
    for row in result["sources"].values():
        row["paragraph_ids"] = ["p999"]
    with pytest.raises(jsonschema.ValidationError):
        module.validate(result, schema, targets, value["sources"])


def test_edition_code_change_is_rejected_before_loading_parent_material(tmp_path):
    recipe = {"extra": {"module_sha256": "changed", "tokenizer_sha256": "changed"}}
    key = module.sha(module.canonical(recipe))[:32]
    folder = tmp_path / "wiki/textbook-edition-jobs" / key
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"id": key, "recipe": recipe}), encoding="utf-8")
    (folder / "progress.json").write_text(json.dumps({"stage": "complete"}), encoding="utf-8")
    (folder / "result.json").write_text(json.dumps({"job": key}), encoding="utf-8")
    with pytest.raises(ValueError, match="edition_code_and_tokenizer_unchanged"):
        module.load(tmp_path, key, tmp_path / "unused-tokenizer")
