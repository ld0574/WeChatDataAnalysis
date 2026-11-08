import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_corpus_batch as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_fresh_authoring import fake_job
from test_course_textbook_point_mapped_resume import mapped_fixture
from test_course_textbook_whole_unit_repair import verdict


def candidates(tmp_path, monkeypatch):
    catalog = {"lessons": [{"id": f"{number:024x}", "artifact": f"{number:064x}",
        "state": "draft_available" if number < 7 else "pending"} for number in range(340)]}
    (tmp_path / "library").mkdir()
    atomic_json(tmp_path / "library/catalog.json", catalog)
    inventories = {}
    for number in range(7):
        key = f"{number + 100:064x}"
        value = {"lesson": f"{number:024x}", "parent_inventory_sha256": f"{number + 500:064x}",
            "sources": {"O0": {}}, "original_characters": 123, "pages": [["O0"]]}
        folder = tmp_path / "wiki/textbook-whole-record-evidence" / key
        folder.mkdir(parents=True)
        atomic_json(folder / "manifest.json", value)
        inventories[key] = value
    monkeypatch.setattr(m.whole, "load", lambda root, key: inventories[key])
    monkeypatch.setattr(m.intake.evidence, "load", lambda *args: {})
    monkeypatch.setattr(m.intake, "original_equivalence", lambda root, parent, artifact: {"artifact": artifact})
    monkeypatch.setattr(m, "manifest_inventories", lambda root: {f"{100:064x}"})
    monkeypatch.setattr(m, "reserved_lessons", lambda root: {f"{1:024x}"})
    monkeypatch.setattr(m.intake, "delivered", lambda root: {f"{2:024x}"})
    return catalog, inventories


def test_selects_next_courses_in_full_catalog_order_excluding_every_attempt_reservation_and_delivery(tmp_path, monkeypatch):
    candidates(tmp_path, monkeypatch)
    selected = m.selected_inputs(tmp_path, 2)
    assert [row["lesson"] for row in selected] == [f"{number:024x}" for number in (3, 4)]
    assert all(row["records"] == 1 and row["characters"] == 123 for row in selected)


@pytest.mark.parametrize("limit", [0, 4, True])
def test_batch_size_is_finite_and_cannot_be_bypassed(limit, tmp_path):
    with pytest.raises(ValueError, match="finite_one_to_three"):
        m.selected_inputs(tmp_path, limit)


def test_small_catalog_cannot_replace_the_full_course_scope(tmp_path, monkeypatch):
    catalog, _ = candidates(tmp_path, monkeypatch)
    catalog["lessons"] = catalog["lessons"][:7]
    atomic_json(tmp_path / "library/catalog.json", catalog)
    with pytest.raises(ValueError, match="entire_audio_scope"):
        m.selected_inputs(tmp_path, 2)


@pytest.mark.parametrize("change", ["version", "records", "characters", "groups", "equivalence"])
def test_first_stage_refuses_changed_sources_or_incomplete_original_input(change, tmp_path, monkeypatch):
    catalog, inventories = candidates(tmp_path, monkeypatch)
    item = m.selected_inputs(tmp_path, 1)[0]
    assert m.check_item(tmp_path, item)["lesson"] == item["lesson"]
    if change == "version":
        catalog["lessons"][3]["artifact"] = "f" * 64
        atomic_json(tmp_path / "library/catalog.json", catalog)
    elif change == "records":
        inventories[item["inventory"]]["sources"] = {}
    elif change == "characters":
        inventories[item["inventory"]]["original_characters"] += 1
    elif change == "groups":
        inventories[item["inventory"]]["pages"] = []
    else:
        monkeypatch.setattr(m.intake, "original_equivalence", lambda *args: {"different": True})
    with pytest.raises(ValueError):
        m.check_item(tmp_path, item)


def predecessor(tmp_path, *, state="running", returncode=None):
    plan = {"predecessor_job": "a" * 32,
        "predecessor_guard": {"pid": 10, "created": 20}, "predecessor_worker": {"pid": 11, "created": 21}}
    path = tmp_path / "wiki/textbook-whole-authoring-jobs" / plan["predecessor_job"] / m.mapped.DIRECTORY / "guard-progress.json"
    path.parent.mkdir(parents=True)
    atomic_json(path, {**plan["predecessor_guard"], "state": state, "returncode": returncode})
    return plan, path


def test_finished_state_alone_does_not_replace_observation_of_process_exit(tmp_path, monkeypatch):
    plan, _ = predecessor(tmp_path, state="worker_exited", returncode=0)
    monkeypatch.setattr(m.private_guard, "checkpoint", lambda root: None)
    observations = iter([True, True, False, False])
    sleeps = []
    m.wait_predecessor(tmp_path, plan, sleep=sleeps.append, live=lambda row: next(observations))
    assert sleeps == [15, 15]


@pytest.mark.parametrize("change", ["paused", "failed", "identity"])
def test_failed_or_paused_predecessor_never_launches_or_resumes_successor(change, tmp_path, monkeypatch):
    plan, path = predecessor(tmp_path, state="worker_exited", returncode=0)
    monkeypatch.setattr(m.private_guard, "checkpoint", lambda root: None)
    row = json.loads(path.read_bytes())
    if change == "paused":
        row["state"] = "paused"
    elif change == "failed":
        row["returncode"] = 1
    else:
        row["created"] += 1
    atomic_json(path, row)
    with pytest.raises((ValueError, InterruptedError)):
        m.wait_predecessor(tmp_path, plan, sleep=lambda value: pytest.fail("Unexpected wait"), live=lambda value: False)


def test_first_writer_uses_new_format_without_touching_original_recipe_and_readonly_forbids_generation(tmp_path, monkeypatch):
    _, inventory, working, packet, _, _ = fake_job(tmp_path, monkeypatch, read_only=True)
    original_contract, execution = {"synthetic_contract": True}, {"format": "synthetic"}
    monkeypatch.setattr(m, "first_execution", lambda *args: execution)
    cls = m.first_job_type(original_contract, execution, read_only=True)
    job = cls(None, inventory, working, None)
    assert job.reader.paths == {tmp_path / m.fresh.CONTRACT, tmp_path / m.FRESH_CONTRACT}
    before = copy.deepcopy(m.writing.composition.PROMPTS)
    with pytest.raises(ValueError, match="inference_forbidden"):
        job.generate_response()
    with pytest.raises(ValueError, match="gpu_acquisition_forbidden"):
        job.slot()
    assert original_contract == {"synthetic_contract": True}
    assert m.writing.composition.PROMPTS == before


def test_first_worker_refuses_started_writer_without_reusing_its_budget(tmp_path, monkeypatch):
    atomic_json(tmp_path / "progress.json", {"state": "stopped"})
    job = SimpleNamespace(root=tmp_path)
    monkeypatch.setattr(m, "first_bound", lambda *args: (tmp_path, {}, {}, job, {}, {}))
    monkeypatch.setattr(m.private_guard, "checkpoint", lambda root: None)
    monkeypatch.setattr(m.writing, "run", lambda *args: pytest.fail("Unexpected worker restart"))
    with pytest.raises(ValueError, match="first_writer_once"):
        m.first_worker(tmp_path, "a", "b", tmp_path / "tokenizer.json")


def test_first_writer_actual_format_is_derived_then_sent_to_independent_review_with_negative_result_retained(tmp_path, monkeypatch):
    _, inventory, working, packet, context, _ = fake_job(tmp_path, monkeypatch)
    execution = {"format": "synthetic"}
    monkeypatch.setattr(m, "first_execution", lambda *args: execution)
    job = m.first_job_type({"synthetic_contract": True}, execution, read_only=False)(None, inventory, working, None)
    _, _, _, raw = mapped_fixture()
    saved = {}

    def generate(self, name, prompt, schema, supplied, validate, **kwargs):
        if name.endswith("-write"):
            assert schema["oneOf"][0]["properties"]["paragraphs"]["items"]["properties"]["point_ids"]["items"]["enum"] == ["U0"]
            result = raw
        else:
            assert supplied["candidate"]["coverage"]["U0"]["paragraphs"] == [0]
            assert supplied["unverified_citation_proposals"]
            result = verdict(schema, passed=False)
        validate(result)
        saved[name] = result
        atomic_json(tmp_path / (name + ".json"), {"synthetic_raw": result})
        return result

    monkeypatch.setattr(m.fresh.BASE_JOB, "stage", generate)
    monkeypatch.setattr(job.reader, "stage", lambda name, *args, **kwargs: saved[name])
    candidate = job.stage("section-0000-write", m.writing.author.WRITE_PROMPT,
        m.writing.section_schema(["U0"], working), context, lambda row: None, evidence_packet=packet)
    schema = m.writing.review_schema(candidate, ["U0"])
    audit = job.stage("section-0000-review", m.writing.author.REVIEW_PROMPT, schema,
        {**context, "candidate": candidate}, lambda row: m.writing.author.validate_review(row, schema), evidence_packet=packet)
    assert not all(m.writing.author.verdicts(audit))
    assert candidate["paragraphs"][0]["text"] == raw["paragraphs"][0]["text"]
    assert json.loads((tmp_path / "section-0000-write.json").read_bytes())["synthetic_raw"] == raw


def test_verifier_restores_original_factory_even_when_reconstruction_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "first_bound", lambda *args: (tmp_path, {}, {}, None, {}, {}))
    def fail(*args):
        raise ValueError("requires_synthetic_missing_cache")
    monkeypatch.setattr(m.fresh, "reconstruct", fail)
    factory = m.fresh.job_type
    with pytest.raises(ValueError, match="requires_synthetic_missing_cache"):
        m.first_verify(tmp_path, "a", "b", tmp_path / "tokenizer.json")
    assert m.fresh.job_type is factory
