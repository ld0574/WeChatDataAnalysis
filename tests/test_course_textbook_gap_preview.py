import copy

from fastapi.testclient import TestClient

from wechat_decrypt_tool import course_textbook_gap_preview as preview
from wechat_decrypt_tool.course_library import canonical
from test_course_textbook_gap_plan import inputs
from test_course_textbook_gap_write import candidate, packet, review


def test_only_clean_draft_and_counts_cross_http_boundary(tmp_path):
    value = inputs()
    parent = value["parent"]
    path = tmp_path / "wiki/whole-lesson-repair-jobs" / parent
    path.mkdir(parents=True)
    preview.writer.atomic_json(path / "corrected-reading.json", value["book"])
    client = TestClient(preview.create_app(tmp_path, parent))
    page = client.get("/")
    assert page.status_code == 200
    assert "尚未验收" in page.text
    assert "Existing explanation" in page.text
    assert "complete_sources" not in page.text and "gap1" not in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert client.get("/status").json()["quality_complete"] is False
    assert client.get("/manifest.json").status_code == 404
    assert client.get("/../../private-authoring-plan.json").status_code == 404


def test_partial_projection_preserves_unwritten_units_and_checks_cache(tmp_path, monkeypatch):
    value, plan = packet()
    parent = value["parent"]
    parent_folder = tmp_path / "wiki/whole-lesson-repair-jobs" / parent
    parent_folder.mkdir(parents=True)
    preview.writer.atomic_json(parent_folder / "corrected-reading.json", value["book"])
    binding = {"bound": True}
    monkeypatch.setattr(preview.writer, "load_plan", lambda *args: (value, plan, binding))
    recipe = {"packet": {"sources": value["sources"]},
              "extra": {"plan": "d" * 32, "plan_bindings": binding, "parent_bindings": value["bindings"]}}
    key = preview.sha(canonical(recipe))[:32]
    folder = tmp_path / "wiki/textbook-gap-write-jobs" / key
    folder.mkdir(parents=True)
    save = preview.writer.atomic_json
    save(folder / "manifest.json", {"id": key, "recipe": recipe})
    save(tmp_path / "wiki/textbook-gap-write-current.json", {"parent": parent, "job": key, "stage": "writing"})
    unit = plan["units"][0]
    generated = candidate(unit)
    assessment = review(generated, unit)
    proof = {"unit": unit["unit"], "candidate": generated, "review": assessment,
             "checks_passed": True, "reader_publication_allowed": False}
    save(folder / (unit["unit"] + "-private-result.json"), proof)
    for suffix, data in [("-write", generated), ("-review-0", assessment)]:
        save(folder / (unit["unit"] + suffix + ".json"), {"data": data, "data_sha256": preview.sha(canonical(data))})
    book, status = preview.projection(tmp_path, parent)
    assert status["units_written"] == 1 and status["total_units"] == 2
    assert len(book["chapters"][0]["sections"][0]["paragraphs"]) == 2
    assert book["quiz"][0] == value["book"]["quiz"][0]
    assert "source_keys" not in str(book)
    changed = copy.deepcopy(generated)
    changed["paragraphs"][0]["text"] += "Changed"
    proof["candidate"] = changed
    save(folder / (unit["unit"] + "-private-result.json"), proof)
    assert TestClient(preview.create_app(tmp_path, parent)).get("/").status_code == 503
