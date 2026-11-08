import copy
from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest
import jsonschema
from tokenizers import Tokenizer, models

from wechat_decrypt_tool import course_pdf_ocr
from wechat_decrypt_tool import course_textbook_final_section_edit as m
from wechat_decrypt_tool.course_knowledge import KnowledgeStore


def candidate():
    def claim(text):
        return {"text": text, "citations": ["O00000"]}
    return {"heading": "Synthetic section", "objective": "Understand the synthetic fact",
        "overview": claim("Synthetic overview"), "takeaway": claim("Synthetic takeaway"),
        "paragraphs": [claim("Synthetic explanation " + str(i)) for i in range(3)], "diagrams": [],
        "coverage": {"P0": {"paragraphs": [0, 1, 2], "reason": "Synthetic complete explanation"}},
        "quiz": {"question": "Synthetic question?", "answer": "Synthetic answer", "explanation": "Synthetic explanation",
            "criteria": ["Synthetic mastery criterion"], "citations": ["O00000"]}}


def prepared(tmp_path, monkeypatch):
    root = (tmp_path / "archive").resolve()
    KnowledgeStore(root)
    tokenizer = root / "wiki/tokenizer.json"
    tokenizer.parent.mkdir(exist_ok=True)
    Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]")).save(str(tokenizer))
    original = root / "parent-original.json"
    original.write_bytes(b'{"synthetic_original_budget":1,"negative":true}')
    proof = {"files": {original.name: m.whole.sha(original.read_bytes())}}
    proof_path = root / "parent-proof.json"
    m.atomic_json(proof_path, proof)
    ledger = {"points": {"P0": {"label": "Synthetic point", "teaching": "Synthetic fact",
        "category": "concept", "citations": ["O00000"]}}, "pending_source_cards": {}}
    source = {"kind": "speech", "text": "Synthetic complete original\n" + "unchanged literal " * 1000}
    context = {"sources": {"O00000": source}, "context_sources": {}, "assigned_points": ledger["points"],
        "pending_source_cards": {}, "pending_cards_are_verified_facts": False, "attachment_scope": {"unresolved": True},
        "candidate": candidate(), "original_negative_review": {"synthetic_private_reason": "Retained old negative"}}
    data = {"parent_proof": proof, "parent_proof_path": proof_path.name, "parent_book_sha256": "b" * 64,
        "parent_issue_count": 9, "context": context, "ledger": ledger, "inventory": {"sources": {"O00000": source}},
        "schema": m.grammar.bind(m.writing.section_schema(["P0"], ledger), {"sources": {"O00000": source}}),
        "ids": ["P0"], "parent_unit_candidate_sha256": m.digest(candidate())}
    m.writing.validate_section(candidate(), data["ids"], ledger, data["inventory"])
    monkeypatch.setattr(m, "source_inputs", lambda *_a, **_k: copy.deepcopy(data))
    monkeypatch.setattr(m.private_guard, "checkpoint", lambda _: None)
    monkeypatch.setattr(m.private_guard, "checked_sample", lambda _: reading())
    monkeypatch.setattr(m.writing, "local_model_digest", lambda name: "synthetic-digest-" + name)
    monkeypatch.setattr(course_pdf_ocr, "local_model_digest", lambda name: "synthetic-digest-" + name)
    monkeypatch.setattr(m.writing, "identity_live", lambda _: False)
    args = (root, "a" * 32, "section-0013", tokenizer)
    result = m.prepare(*args)
    return (*m.load(root, result["job"]), result["job"], args, original)


def reading(temperature=55):
    return {"temperature_c": temperature, "power_limit_w": 180, "power_w": 100}


def backend(monkeypatch, *, negative=False, bad_write=False, cpu=False):
    import httpx

    calls = []
    state = {}

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, json):
            request = json
            state.update(request=request)
            calls.append(copy.deepcopy(request))
            if request["model"] == "qwen3.5:9b":
                value = candidate()
                if bad_write:
                    value["paragraphs"][0]["citations"] = ["not_visible"]
            else:
                value = {"paragraphs": {str(i): {"passed": True, "reason": "Synthetic evidence"} for i in range(3)},
                    "coverage": {"P0": {"passed": not negative, "reason": "Synthetic evidence"}}, "diagrams": {},
                    **{key: {"passed": True, "reason": "Synthetic evidence"} for key in ("overview", "takeaway", "quiz", "readability")}}
            response = {"done": True, "done_reason": "stop", "eval_count": 50, "prompt_eval_count": 2,
                "eval_duration": 1_000_000_000, "message": {"content": __import__("json").dumps(value)}}
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: response)

        def get(self, url):
            request = state["request"]
            return SimpleNamespace(json=lambda: {"models": [{"digest": "synthetic-digest-" + request["model"],
                "context_length": request["options"]["num_ctx"], "size_vram": 0 if cpu else 12345}]})

    monkeypatch.setattr(httpx, "Client", Client)
    return calls


def test_complete_new_editorial_version_with_separate_model_review_keeps_original_private(tmp_path, monkeypatch):
    root, folder, _, data, key, _, original = prepared(tmp_path, monkeypatch)
    before = original.read_bytes()
    calls = backend(monkeypatch)
    result = m.worker(root, key)
    assert result["section_editorial_review_passed"] and not result["reader_publication_allowed"]
    assert result["original_parent_issues_retained"] == 9 and original.read_bytes() == before
    assert [row["model"] for row in calls] == ["qwen3.5:9b", "course-defiant:q4"]
    first = json.loads(calls[0]["messages"][1]["content"])
    second = json.loads(calls[1]["messages"][1]["content"])
    assert first["sources"] == second["sources"] == data["context"]["sources"]
    assert "original_negative_review" not in second
    assert not (root / "wiki/textbook-reading/catalog.json").exists()
    assert (folder / "result.json").is_file()


def test_negative_is_preserved_and_does_not_get_an_automatic_semantic_revision(tmp_path, monkeypatch):
    root, folder, _, _, key, args, _ = prepared(tmp_path, monkeypatch)
    calls = backend(monkeypatch, negative=True)
    result = m.worker(root, key)
    assert not result["section_editorial_review_passed"] and result["negative_checks"] == 1
    before = (folder / "result.json").read_bytes()
    with pytest.raises(ValueError, match="worker_once_no_retry"):
        m.worker(root, key)
    with pytest.raises(ValueError, match="editorial_budget_already_reserved"):
        m.prepare(*args)
    assert len(calls) == 2 and (folder / "result.json").read_bytes() == before


def test_structure_failure_consumes_only_one_proposal_and_retains_original_attempt(tmp_path, monkeypatch):
    root, folder, _, _, key, _, _ = prepared(tmp_path, monkeypatch)
    calls = backend(monkeypatch, bad_write=True)
    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        m.worker(root, key)
    assert len(calls) == 1 and (folder / "attempts/editorial-write-01.json").is_file()
    assert (folder / "failure.json").is_file() and not (folder / "result.json").exists()
    with pytest.raises(ValueError, match="worker_once_no_retry"):
        m.worker(root, key)


def test_reconstruction_never_calls_model_or_rewrites_outputs(tmp_path, monkeypatch):
    root, folder, _, _, key, _, _ = prepared(tmp_path, monkeypatch)
    backend(monkeypatch)
    m.worker(root, key)
    before = {p: p.read_bytes() for p in folder.rglob("*.json")}
    monkeypatch.setattr(m.Job, "generate_response", lambda *_a, **_k: pytest.fail("read-only inference"))
    monkeypatch.setattr(m.Job, "slot", lambda *_a, **_k: pytest.fail("read-only GPU lock"))
    assert m.verify(root, key)["new_model_calls"] == 0
    assert all(p.read_bytes() == raw for p, raw in before.items())


@pytest.mark.parametrize("change", ["parent", "input", "tokenizer", "request", "decode_schema"])
def test_changed_inputs_or_actual_requests_refuse_reconstruction(tmp_path, monkeypatch, change):
    root, folder, recipe, _, key, _, original = prepared(tmp_path, monkeypatch)
    backend(monkeypatch)
    m.worker(root, key)
    if change in ["parent", "input", "tokenizer"]:
        path = {"parent": original, "input": folder / "inputs.json", "tokenizer": root / recipe["tokenizer"]}[change]
        path.write_bytes(b"changed synthetic material")
    else:
        path = folder / "attempts/editorial-review-01.json" if change == "request" else next(
            p for p in folder.glob("response-*.json") if m.whole.read(p)["stage"] == "editorial-review")
        row = m.whole.read(path)
        row["request_sha256" if change == "request" else "schema_sha256"] = "b" * 64
        m.atomic_json(path, row)
    with pytest.raises(ValueError):
        m.verify(root, key)


def test_cpu_fallback_is_rejected_before_article_acceptance(tmp_path, monkeypatch):
    root, folder, _, _, key, _, _ = prepared(tmp_path, monkeypatch)
    calls = backend(monkeypatch, cpu=True)
    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        m.worker(root, key)
    assert len(calls) == 1 and not (folder / "result.json").exists()


@pytest.mark.parametrize("reason", ["temperature", "disk"])
def test_private_guard_stops_preflight_without_hot_resume(tmp_path, monkeypatch, reason):
    root, folder, _, _, _, _, _ = prepared(tmp_path, monkeypatch)

    def sample():
        if reason == "disk":
            raise RuntimeError("course_archive_32gib_reserve_reached")
        return reading(70)

    kwargs = {"sample": sample, "launch": lambda *_a, **_k: pytest.fail("unsafe worker launch")}
    if reason == "temperature":
        assert m.private_guard.run(root, folder, ["synthetic"], **kwargs)["state"] == "paused_without_automatic_resume"
    else:
        with pytest.raises(RuntimeError):
            m.private_guard.run(root, folder, ["synthetic"], **kwargs)
    with pytest.raises(ValueError, match="once_no_auto_retry"):
        m.private_guard.run(root, folder, ["synthetic"], sample=lambda: reading(40))


def test_private_guard_preserves_existing_controls_and_standard_guard(tmp_path, monkeypatch):
    root, folder, _, _, _, _, _ = prepared(tmp_path, monkeypatch)
    other = root / "extraction-thermal-guard.json"
    other.write_bytes(b"synthetic standard guard")

    def launch(*_a, **_k):
        for name in ("pause.flag", "supervisor-stop.flag"):
            (root / name).write_bytes(b"synthetic original control")
        return SimpleNamespace(pid=123, poll=lambda: None)

    def stop(_process, _root):
        for name in ("pause.flag", "supervisor-stop.flag"):
            (root / name).write_bytes(b"synthetic overwritten control")

    result = m.private_guard.run(root, folder, ["synthetic"], sample=reading, launch=launch, stopper=stop, sleep=lambda _: None)
    assert result["state"] == "paused_without_automatic_resume"
    assert other.read_bytes() == b"synthetic standard guard"
    assert all((root / n).read_bytes() == b"synthetic original control" for n in ("pause.flag", "supervisor-stop.flag"))


def test_cli_failure_does_not_print_private_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(m, "verify", lambda *_a: (_ for _ in ()).throw(ValueError("synthetic private material")))
    monkeypatch.setattr(m.sys, "argv", ["synthetic", "--root", str(tmp_path), "verify", "--job", "a" * 32])
    with pytest.raises(SystemExit):
        m.main()
    assert "synthetic private material" not in capsys.readouterr().out


def test_parent_disposition_adapter_is_explicit_scoped_and_restored_on_failure(tmp_path, monkeypatch):
    active = []

    @contextmanager
    def hooks():
        active.append(True)
        try:
            yield
        finally:
            active.pop()

    class SyntheticStop(Exception):
        pass

    def reconstruct(*_args, adapter, **_kwargs):
        assert active and adapter == "continuation"
        raise SyntheticStop()

    monkeypatch.setattr(m.mapping, "hooks", hooks)
    monkeypatch.setattr(m.coverage, "inputs", reconstruct)
    with pytest.raises(SyntheticStop):
        m.source_inputs(tmp_path, "a" * 32, "section-0013", tmp_path / "tokenizer.json",
            adapter="disposition", recovery_section="section-0007")
    assert not active


def test_pending_disposition_and_unique_citations_are_constrained_before_first_decode():
    ledger = {"points": {key: {"citations": ["O00000"]} for key in ["P0", "U0"]},
        "pending_source_cards": {"U0": {"reference": "O00000", "neighbors": []}}}
    schema = m.candidate_schema(["P0", "U0"], ledger, {"O00000": {"kind": "speech", "text": "Synthetic"}})
    value = candidate()
    value["coverage"]["U0"] = {"teaching": False, "paragraphs": [0], "reason": "Synthetic exclusion"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, schema)
    value["coverage"]["U0"]["paragraphs"] = []
    jsonschema.validate(value, schema)
    value["overview"]["citations"] *= 2
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, schema)
