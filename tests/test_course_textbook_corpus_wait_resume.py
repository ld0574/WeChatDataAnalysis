import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_corpus_wait_resume as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_fresh_authoring import fake_job


@pytest.mark.parametrize("artifact", ["progress.json", "ledger.json", "attempts/role-01.json"])
def test_old_ledger_work_must_not_be_restarted(tmp_path, artifact):
    folder = tmp_path / 'batch'
    folder.mkdir()
    job = tmp_path / 'wiki/textbook-keyed-jobs/ledger'
    path = job / artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{}')
    with pytest.raises(ValueError, match='original_ledger_roles_still_unused'):
        m.unstarted(tmp_path, folder, [{'job': 'ledger'}])


def test_original_started_item_or_predecessor_proof_blocks_reexecution(tmp_path):
    (tmp_path / 'item-00-ledger-started.json').write_text('{}')
    with pytest.raises(ValueError, match='no_original_item_started'):
        m.unstarted(tmp_path, tmp_path, [])


@pytest.mark.parametrize("state,returncode", [('paused', None), ('worker_exited', 1)])
def test_failed_or_thermal_predecessor_never_authorizes_new_model_roles(tmp_path, monkeypatch, state, returncode):
    atomic_json(tmp_path / 'guard.json', {'pid': 10, 'created': 20, 'state': state, 'returncode': returncode})
    monkeypatch.setattr(m.original.private_guard, 'checkpoint', lambda root: None)
    with pytest.raises(ValueError, match='replacement_paused|replacement_successful_exit'):
        m.wait(tmp_path, {'predecessor_guard_path': 'guard.json'},
            {'replacement_guard': {'pid': 10, 'created': 20}, 'replacement_worker': {'pid': 11, 'created': 21}},
            live=lambda value: False, sleep=lambda seconds: pytest.fail('Must not silently retry an exited failure'))


def test_guard_pid_reuse_or_replacement_is_rejected(tmp_path, monkeypatch):
    atomic_json(tmp_path / 'guard.json', {'pid': 10, 'created': 999, 'state': 'worker_exited', 'returncode': 0})
    monkeypatch.setattr(m.original.private_guard, 'checkpoint', lambda root: None)
    with pytest.raises(ValueError, match='exact_replacement_guard_identity'):
        m.wait(tmp_path, {'predecessor_guard_path': 'guard.json'},
            {'replacement_guard': {'pid': 10, 'created': 20}, 'replacement_worker': {'pid': 11, 'created': 21}}, live=lambda row: False)


def test_real_successful_exit_only_ends_wait_and_does_not_run_any_writer(tmp_path, monkeypatch):
    atomic_json(tmp_path / 'guard.json', {'pid': 10, 'created': 20, 'state': 'worker_exited', 'returncode': 0})
    monkeypatch.setattr(m.original.private_guard, 'checkpoint', lambda root: None)
    assert m.wait(tmp_path, {'predecessor_guard_path': 'guard.json'},
        {'replacement_guard': {'pid': 10, 'created': 20}, 'replacement_worker': {'pid': 11, 'created': 21}}, live=lambda row: False) is None


def test_first_writer_uses_bounded_schema_from_the_first_role_and_keeps_original_complete_packet(tmp_path, monkeypatch):
    _, inventory, working, packet, context, raw = fake_job(tmp_path, monkeypatch)
    extra, contract, execution = {'synthetic': True}, {'synthetic_contract': True}, {'corpus': True}
    monkeypatch.setattr(m, 'first_execution', lambda *args: extra)
    job = m.first_type(contract, extra, execution, read_only=False)(None, inventory, working, None)
    raw['paragraphs'].append({'text': 'Synthetic second explanatory paragraph', 'citations': ['O00002']})
    generated, values = [], {}

    def generate(self, name, prompt, schema, supplied, validate, **kwargs):
        generated.append(name)
        body = schema['oneOf'][0]['properties']
        assert body['paragraphs']['minItems'] == body['paragraphs']['maxItems'] == 2
        assert 'point_ids' not in body['paragraphs']['items']['properties']
        assert kwargs['evidence_packet'] == m.fresh.continuation.scope_for(['U0'], working, inventory, context, packet)['expanded']
        validate(raw)
        values[name] = copy.deepcopy(raw)
        return raw

    monkeypatch.setattr(m.fresh.BASE_JOB, 'stage', generate)
    monkeypatch.setattr(job.reader, 'stage', lambda name, *args, **kwargs: values[name])
    value = job.stage('section-0000-write', m.writing.author.WRITE_PROMPT,
        m.writing.section_schema(['U0'], working), context, lambda row: None, evidence_packet=packet)
    assert value['coverage'] == raw['coverage']
    assert generated == ['section-0000-write']
    assert tmp_path / m.FIRST in job.reader.paths


def test_unselected_inventory_ledger_pair_is_rejected_before_any_first_job_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'load', lambda *args: (tmp_path, None, None, {'items': [{'inventory': 'selected'}]},
        None, [{'job': 'original-ledger'}], {}))
    monkeypatch.setattr(m.fresh, 'bound', lambda *args: pytest.fail('Wrong ledger must not be bound'))
    with pytest.raises(ValueError, match='selected_original_inventory_and_ledger_only'):
        m.first_bound(tmp_path, 'batch', 'selected', 'another-ledger')


def test_first_worker_once_guard_runs_before_any_installation_or_inference(tmp_path, monkeypatch):
    (tmp_path / 'progress.json').write_text('{}')
    monkeypatch.setattr(m, 'first_bound', lambda *args: (tmp_path, None, SimpleNamespace(root=tmp_path), {}, {}, {}))
    monkeypatch.setattr(m.original.private_guard, 'checkpoint', lambda root: None)
    monkeypatch.setattr(m.fresh, 'installed', lambda *args: pytest.fail('Existing first worker must not be restarted'))
    with pytest.raises(ValueError, match='first_worker_once'):
        m.first_worker(tmp_path, 'batch', 'inventory', 'ledger')


def test_code_binding_names_are_stable_between_cli_and_imported_recovery():
    assert Path(m.__file__).stem in m.bindings()
    assert '__main__' not in m.bindings()
