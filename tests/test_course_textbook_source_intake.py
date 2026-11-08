import copy
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_source_intake as m
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, atomic_json as write_json
from wechat_decrypt_tool.course_library import canonical


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, value)


def prepared(tmp_path, monkeypatch):
    root = (tmp_path / "archive").resolve()
    KnowledgeStore(root)
    atomic_json(root / "resources-manifest.json", {"completed": []})
    lessons = [{"id": f"{n:024x}", "state": "draft_available" if n < 4 else "organizing",
        "artifact": m.whole.sha(f"synthetic-publication-{n}".encode())} for n in range(340)]
    monkeypatch.setattr(m, "delivered", lambda _: {lessons[0]["id"]})
    monkeypatch.setattr(m.whole, "backup", lambda *_a: None)
    monkeypatch.setattr(m.whole, "local_model_digest", lambda *_a: pytest.fail("CPU intake requested model"))
    parents = {}
    for lesson in lessons[:4]:
        published = {"id": lesson["id"], "source_export_sha256": m.whole.sha(b"synthetic-full-export"),
            "evidence": {str(n): {"kind": "speech", "start": n, "end": n + 1,
                "text": f"Synthetic source {n}: exact whitespace\n  a repeated example \n"} for n in (0, 1)}}
        lesson["artifact"] = m.whole.sha(canonical(published))
        (root / "library/objects").mkdir(parents=True, exist_ok=True)
        (root / "library/objects" / (lesson["artifact"] + ".json")).write_bytes(canonical(published))
        value = m.evidence.inventory(published, lesson_artifact=lesson["artifact"], attachment_scope={
            "association_verified": False, "unresolved": True, "selected_notebooks": []})
        key = m.whole.sha(canonical(value))
        atomic_json(root / "wiki/textbook-evidence" / key / "manifest.json", value)
        parents[lesson["id"]] = key
    atomic_json(root / "library/catalog.json", {"lessons": lessons})
    existing, _ = m.whole.prepare(root, parents[lessons[1]["id"]])
    return root, lessons, parents, existing


def test_all_new_ready_inputs_are_lossless_existing_work_and_model_budgets_untouched(tmp_path, monkeypatch):
    root, lessons, parents, existing = prepared(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for name in ("textbook-evidence", "textbook-whole-record-evidence")
        for p in (root / "wiki" / name).glob("*/manifest.json")}
    budget = root / "wiki/synthetic-existing-model-budget.json"
    budget.write_bytes(b'{"attempts":2,"exhausted":true}')
    result = m.run(root)
    assert result["new_whole_inputs"] == 2 and result["original_records"] == 4
    assert result["available_source_lessons"] == 4 and result["remaining_unpublished_sources"] == 336
    assert result["model_calls"] == result["articles_written"] == 0
    assert result["already_delivered_lessons_preserved"] == result["existing_whole_lessons_preserved"] == 1
    assert not result["semantic_accuracy_verified"] and not result["reader_publication_allowed"]
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert budget.read_bytes() == b'{"attempts":2,"exhausted":true}'
    folder = root / "wiki/first-whole-record-source-intakes" / result["batch"]
    rows = m.whole.read(folder / "prepared-inputs.json")
    assert {r["lesson"] for r in rows} == {lessons[2]["id"], lessons[3]["id"]}
    for row in rows:
        inventory = m.whole.load(root, row["inventory"])
        parent = m.evidence.load(root, parents[row["lesson"]])
        assert inventory["originals"] == parent["originals"]
        assert len(inventory["sources"]) == len(parent["originals"])
    assert m.whole.load(root, existing)["lesson"] == lessons[1]["id"]
    with pytest.raises(ValueError, match="no_new_whole_record_inputs"):
        m.run(root)
    assert len(list((root / "wiki/first-whole-record-source-intakes").iterdir())) == 1


def test_readonly_verification_reconstructs_actual_inputs_without_prepare_or_writes(tmp_path, monkeypatch):
    root, _, _, _ = prepared(tmp_path, monkeypatch)
    result = m.run(root)
    before = {p: p.read_bytes() for p in root.rglob("*.json")}
    monkeypatch.setattr(m.whole, "prepare", lambda *_a: pytest.fail("read-only preparation"))
    monkeypatch.setattr(m, "checkpoint", lambda *_a: pytest.fail("read-only checkpoint"))
    assert m.verify(root, result["batch"]) == result
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_publication_metadata_changes_require_exact_all_originals_not_hash_override(tmp_path, monkeypatch):
    root, lessons, parents, _ = prepared(tmp_path, monkeypatch)
    old = m.evidence.load(root, parents[lessons[2]["id"]])
    published = m.Library(root).object(lessons[2]["artifact"])
    published["synthetic_note_metadata"] = "Changed metadata, identical full originals."
    current = m.whole.sha(canonical(published))
    (root / "library/objects" / (current + ".json")).write_bytes(canonical(published))
    catalog = m.whole.read(root / "library/catalog.json")
    catalog["lessons"][2]["artifact"] = current
    atomic_json(root / "library/catalog.json", catalog)
    result = m.run(root)
    folder = root / "wiki/first-whole-record-source-intakes" / result["batch"]
    proof = m.whole.read(folder / "manifest.json")["parent_current_original_equivalence"][lessons[2]["id"]]
    assert proof["complete_original_records_equal"] and not proof["publication_bytes_equal"]
    assert proof["raw_export_byte_identity_claimed"] is False
    assert m.evidence.load(root, parents[lessons[2]["id"]]) == old
    assert m.verify(root, result["batch"]) == result


@pytest.mark.parametrize("change", ["text", "offset", "pixel_digest"])
def test_real_original_changes_cannot_use_publication_metadata_equivalence(tmp_path, monkeypatch, change):
    root, lessons, _, _ = prepared(tmp_path, monkeypatch)
    published = m.Library(root).object(lessons[2]["artifact"])
    source = published["evidence"]["0"]
    if change == "text":
        source["text"] += "A different synthetic teaching fact"
    elif change == "offset":
        source["end"] += 1
    else:
        source["image_sha256"] = "d" * 64
    current = m.whole.sha(canonical(published))
    (root / "library/objects" / (current + ".json")).write_bytes(canonical(published))
    catalog = m.whole.read(root / "library/catalog.json")
    catalog["lessons"][2]["artifact"] = current
    atomic_json(root / "library/catalog.json", catalog)
    with pytest.raises(ValueError, match="current_all_original_records"):
        m.run(root)
    assert not (root / "wiki/first-whole-record-source-intakes").exists()


@pytest.mark.parametrize("change", ["original", "catalog", "row", "result", "preserved"])
def test_modified_evidence_or_false_success_claims_refuse_verification(tmp_path, monkeypatch, change):
    root, _, parents, _ = prepared(tmp_path, monkeypatch)
    result = m.run(root)
    folder = root / "wiki/first-whole-record-source-intakes" / result["batch"]
    rows = m.whole.read(folder / "prepared-inputs.json")
    if change == "catalog":
        path = folder / "frozen-source-catalog.json"
        path.write_bytes(path.read_bytes() + b"\n")
    elif change in {"original", "preserved"}:
        parent = rows[0]["parent_inventory"] if change == "original" else next(iter(parents.values()))
        path = root / "wiki/textbook-evidence" / parent / "manifest.json"
        data = m.whole.read(path)
        data["originals"][0]["text"] += "Synthetic altered text"
        atomic_json(path, data)
    elif change == "row":
        rows[0]["original_records"] = 0
        atomic_json(folder / "prepared-inputs.json", rows)
    else:
        modified = copy.deepcopy(result)
        modified["semantic_accuracy_verified"] = True
        atomic_json(folder / "result.json", modified)
    with pytest.raises(ValueError):
        m.verify(root, result["batch"])


@pytest.mark.parametrize("change", ["scope", "source_version", "missing_parent", "ambiguous_parent"])
def test_incomplete_scope_or_unresolved_input_identity_is_not_silently_skipped(tmp_path, monkeypatch, change):
    root, lessons, parents, _ = prepared(tmp_path, monkeypatch)
    if change == "ambiguous_parent":
        value = m.evidence.load(root, parents[lessons[2]["id"]])
        value["attachment_scope"]["synthetic_alternate_version"] = True
        key = m.whole.sha(canonical(value))
        atomic_json(root / "wiki/textbook-evidence" / key / "manifest.json", value)
    else:
        catalog = m.whole.read(root / "library/catalog.json")
        if change == "scope":
            catalog["lessons"] = catalog["lessons"][:-1]
        elif change == "source_version":
            catalog["lessons"][2]["artifact"] = "f" * 64
        else:
            catalog["lessons"][4]["state"] = "draft_available"
        atomic_json(root / "library/catalog.json", catalog)
    with pytest.raises(ValueError):
        m.run(root)
    assert not (root / "wiki/first-whole-record-source-intakes").exists()


@pytest.mark.parametrize("blocked", ["disk", "supervisor_stop"])
def test_control_markers_and_disk_reserve_stop_new_intake_without_deletion(tmp_path, monkeypatch, blocked):
    root, _, _, _ = prepared(tmp_path, monkeypatch)
    stop = root / "supervisor-stop.flag"
    if blocked == "supervisor_stop":
        stop.write_bytes(b"Synthetic retained stop")
    else:
        monkeypatch.setattr(m.psutil, "disk_usage", lambda _: SimpleNamespace(free=31 * 2**30))
    with pytest.raises(ValueError):
        m.run(root)
    if blocked == "supervisor_stop":
        assert stop.read_bytes() == b"Synthetic retained stop"
    assert not (root / "wiki/first-whole-record-source-intakes").exists()
