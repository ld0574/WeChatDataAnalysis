from wechat_decrypt_tool.course_exclusions import write_exclusions
from wechat_decrypt_tool.course_knowledge import KnowledgeStore


def test_repair_receipt_does_not_imply_primary_static_image_acceptance(tmp_path):
    store = KnowledgeStore(tmp_path / "kb")
    store.write_json("inventory-unique.json", {"videos": []})
    store.write_json(
        "image-followup-validation.json", {"all_later_failure_sources_verified": True}
    )
    store.write_json(
        "primary-static-image-validation-progress.json",
        {"stage": "waiting_for_restore"},
    )
    result = write_exclusions(store.root)
    assert result["recorded_validations"]["primary_static_images"] == {}
    assert result["recorded_validations"]["later_image_repairs"][
        "all_later_failure_sources_verified"
    ]
    assert (
        result["progress"]["primary-static-image-validation"]["stage"]
        == "waiting_for_restore"
    )
    assert not result["complete_course_archive"]
    assert not result["source_deletion_allowed"]
    assert not result["report_is_completion_evidence"]
    text = (store.root / "KNOWN-EXCLUSIONS.md").read_text("utf-8")
    assert "主批次成功来源的全量逐项校验尚待通过" in text
    assert "后续失败补跑的独立校验已有通过回执" in text
    assert (
        result["evidence"]["primary-static-image-validation.json"]["available"] is False
    )


def test_primary_static_image_receipt_is_retained_without_claiming_final_acceptance(
    tmp_path,
):
    store = KnowledgeStore(tmp_path / "kb")
    store.write_json("inventory-unique.json", {"videos": []})
    receipt = {
        "all_primary_successful_occurrences_verified": True,
        "primary_successful_occurrences_verified": 4384,
    }
    store.write_json("primary-static-image-validation.json", receipt)
    result = write_exclusions(store.root)
    assert result["recorded_validations"]["primary_static_images"] == receipt
    assert (
        result["evidence"]["primary-static-image-validation.json"]["available"] is True
    )
    assert "主批次成功来源的全量逐项校验已有通过回执" in (
        store.root / "KNOWN-EXCLUSIONS.md"
    ).read_text("utf-8")
    assert not result["complete_course_archive"]
    assert not result["source_deletion_allowed"]
