"""五餐菜单库外部正门：只用自造菜单；文档示例必须真实通过。"""
import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path
import pytest
from waybeside import create_app
from waybeside.agent_door import export_package, submit_candidate
from waybeside.menu_door import CandidateError
from waybeside.menu_library import synthetic_library, import_library, active
from waybeside.storage import connect
from support import open_shares, set_mode
ROOT = Path(__file__).resolve().parents[1]
TARGET = '2026-10-17'

def private_library():
    data = synthetic_library()
    data.pop('fictional')
    data.update(version='fictional-replacement-door-v1')
    for day in data['days']:
        for meal in day['meals']:
            meal['title'] = f"示例{meal['meal']}组{day['group_id']}"
    return data

@pytest.fixture(params=['fictional', 'replaced'])
def env(tmp_path, request):
    path = tmp_path / 'waybeside.sqlite'
    app = create_app(path, today='2026-10-16')
    app.testing = True
    set_mode(path, 'menu_only')
    import_library(path, synthetic_library())
    if request.param == 'replaced':
        with connect(path) as db:
            digest = active(db)['digest']
        import_library(path, private_library(), expected=digest)
    client = app.test_client()
    origin = 'http://127.0.0.1:8765'
    login = client.post('/api/login', base_url=origin, json={'token':app.config['LOGIN_CODE']}, headers={'Origin': origin})
    headers = {'Origin': origin, 'X-CSRF-Token': login.json['csrf']}
    def post(target, data):
        return client.post('/api/' + target, base_url=origin, json=data, headers=headers)
    def get(target):
        return client.get('/api/' + target, base_url=origin)
    open_shares(post, get)
    def menu():
        return get('menu?date=' + TARGET).json
    def action(op, **extra):
        state = menu()['state']
        return post('menu/action', dict(action=op, target_date=TARGET, revision=state['revision'], library_hash=state['library_hash'], **extra))
    yield path, post, get, menu, action, tmp_path

def exported(env):
    path = env[0]
    result = export_package(path, TARGET)
    return result, json.loads(Path(result['package']).read_text())

def write(tmp, data):
    file = tmp / 'candidate.json'
    file.write_text(data if isinstance(data, str) else json.dumps(data))
    return file

def dump(path):
    with connect(path) as db:
        return [l for l in db.iterdump()]

def candidate_for(package, **changes):
    return {**package['candidate_template'], **changes}

def test_export_contract_no_secret(env, monkeypatch):
    monkeypatch.setenv('EXAMPLE_API_KEY', 'secret-never-export')
    path = env[0]
    result, package = exported(env)
    snap = package['snapshot']
    assert package['protocol'] == 'waybeside-menu-candidate-v1'
    assert len(snap['groups']) == 7 and all(len(g['meals']) == 5 for g in snap['groups'])
    assert set(snap['snapshot_versions']) == {'library_hash', 'state_digest'}
    text = json.dumps(package)
    assert all(s not in text for s in ('secret-never-export', str(ROOT), str(path)))
    assert export_package(path, TARGET)['job_id'] == result['job_id']

def test_document_example_submit_adopt_undo(env):
    path, post, get, menu, action, tmp = env
    result, package = exported(env)
    code = re.findall(r'```python make_candidate\.py\n(.*?)```', (ROOT / 'AGENTS.md').read_text(), re.S)[0]
    file = tmp / 'example.json'
    run = subprocess.run([sys.executable, '-c', code, result['package'], str(file)], capture_output=True)
    assert run.returncode == 0, run.stderr
    assert submit_candidate(path, file) == {'job_id': result['job_id'], 'status': 'READY_TO_REVIEW', 'adopted': False}
    assert menu()['state']['adopted'] is None
    view = get('menu/candidate?target_date=' + TARGET).json['candidate']
    assert view['status'] == 'READY_TO_REVIEW' and view['source'] == 'external'
    assert post('menu/candidate/' + view['id'] + '/adopt', {'confirmed': False}).status_code == 400
    assert post('menu/candidate/' + view['id'] + '/adopt', {'confirmed': True}).status_code == 200
    assert menu()['state']['adopted']['group_id'] == view['group']['group_id']
    assert get('menu/candidate?target_date=' + TARGET).json['candidate'] is None
    with connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM audit WHERE entity IN ('menu_candidate','menu_adopt')").fetchone()[0] >= 3
    assert action('undo').status_code == 200 and menu()['state']['adopted'] is None

def test_meals_mode_cross_group_adopt(env):
    path, post, get, menu, action, tmp = env
    result, package = exported(env)
    meals = {m: f'{"02" if m == "午餐" else "01"}:{m}' for m in package['snapshot']['meal_slots']}
    submit_candidate(path, write(tmp, {k: v for k, v in candidate_for(package, meals=meals, reason_code='VARIETY').items() if k != 'group_id'}))
    view = get('menu/candidate?target_date=' + TARGET).json['candidate']
    assert view['group']['group_id'] == '自选' and view['group']['composed_from']['午餐'] == '02:午餐'
    assert post('menu/candidate/' + view['id'] + '/adopt', {'confirmed': True}).status_code == 200
    adopted = menu()['state']['adopted']
    assert adopted['group_id'] == '自选' and '02' in adopted['meals'][2]['title'] and '01' in adopted['meals'][0]['title']
    assert action('undo').status_code == 200

def bad_cases(package):
    base = package['candidate_template']
    no_group = {k: v for k, v in base.items() if k != 'group_id'}
    slots = package['snapshot']['meal_slots']
    ok_meals = {m: f'01:{m}' for m in slots}
    return {
        'extra': ({**base, 'medication': '越权'}, 'SCHEMA_FIELDS'),
        'both': ({**base, 'meals': ok_meals}, 'SCHEMA_FIELDS'),
        'missing': ({k: v for k, v in base.items() if k != 'reason_code'}, 'SCHEMA_FIELDS'),
        'grams': ({**no_group, 'meals': ok_meals, 'g': 99}, 'SCHEMA_FIELDS'),
        'identity': ({**base, 'target_date': '2026-10-18'}, 'IDENTITY_MISMATCH'),
        'versions': ({**base, 'snapshot_versions': {**base['snapshot_versions'], 'state_digest': 'x'}}, 'IDENTITY_MISMATCH'),
        'unknown_group': ({**base, 'group_id': '99'}, 'UNKNOWN_GROUP'),
        'group_type': ({**base, 'group_id': 1}, 'UNKNOWN_GROUP'),
        'unknown_meal': ({**no_group, 'meals': {**ok_meals, '早餐': '99:早餐'}}, 'UNKNOWN_MEAL'),
        'slot': ({**no_group, 'meals': {**ok_meals, '早餐': '02:午餐'}}, 'MEAL_SLOT_MISMATCH'),
        'meal_set': ({**no_group, 'meals': {k: v for k, v in ok_meals.items() if k != '晚餐'}}, 'MEAL_SET'),
        'html': ({**base, 'reason_code': '<script>alert(1)</script>'}, 'REASON_CODE'),
        'command': ({**no_group, 'meals': {**ok_meals, '午餐': '$(touch x):午餐'}}, 'UNKNOWN_MEAL'),
        'question': ({**base, 'open_questions': ['rm -rf /']}, 'QUESTION_CODE'),
        'open': ({**base, 'open_questions': ['VERIFY_MENU']}, 'OPEN_QUESTIONS'),
    }

@pytest.mark.parametrize('name', ['extra', 'both', 'missing', 'grams', 'identity', 'versions', 'unknown_group', 'group_type', 'unknown_meal', 'slot', 'meal_set', 'html', 'command', 'question', 'open', 'invalid', 'duplicate', 'oversize'])
def test_reject_leaves_everything_unchanged(env, name):
    path, post, get, menu, action, tmp = env
    result, package = exported(env)
    code = None
    if name in ('invalid', 'duplicate', 'oversize'):
        code = {'invalid': 'INVALID_JSON', 'duplicate': 'DUPLICATE_KEY', 'oversize': 'OUTPUT_LIMIT'}[name]
        text = {'invalid': '{bad', 'duplicate': '{"job_id":"x",' + json.dumps(package['candidate_template'])[1:], 'oversize': ' ' * 65537}[name]
        file = write(tmp, text)
    else:
        data, code = bad_cases(package)[name]
        file = write(tmp, data)
    before = dump(path)
    with pytest.raises(CandidateError, match=code):
        submit_candidate(path, file)
    assert dump(path) == before
    assert menu()['state']['adopted'] is None

def test_locked_meal_and_avoided(env):
    path, post, get, menu, action, tmp = env
    state = menu()['state']
    assert action('adopt', group_id='01').status_code == 200
    assert action('lock', meal='午餐', locked=True).status_code == 200
    result, package = exported(env)
    assert package['snapshot']['current']['locked_meals'] == ['午餐']
    before = dump(path)
    with pytest.raises(CandidateError, match='LOCKED_MEAL'):
        submit_candidate(path, write(tmp, candidate_for(package, group_id='02')))
    assert dump(path) == before
    action('preference', group_id='03', favorite=False, avoided=True)
    result, package = exported(env)
    assert package['snapshot']['avoided_group_ids'] == ['03']
    mixed = {m: f'03:{m}' if m != '午餐' else '01:午餐' for m in package['snapshot']['meal_slots']}
    before = dump(path)
    with pytest.raises(CandidateError, match='AVOIDED_MENU'):
        submit_candidate(path, write(tmp, {k: v for k, v in candidate_for(package, meals=mixed).items() if k != 'group_id'}))
    assert dump(path) == before
    # 锁定的餐保持原样时，换其他餐可通过
    ok = {m: f'02:{m}' if m != '午餐' else '01:午餐' for m in package['snapshot']['meal_slots']}
    assert submit_candidate(path, write(tmp, {k: v for k, v in candidate_for(package, meals=ok).items() if k != 'group_id'}))['status'] == 'READY_TO_REVIEW'

def test_stale_before_submit_and_after_ready(env):
    path, post, get, menu, action, tmp = env
    result, package = exported(env)
    assert action('select', group_id='05').status_code == 200
    with pytest.raises(CandidateError, match='DEPENDENCIES_CHANGED'):
        submit_candidate(path, write(tmp, package['candidate_template']))
    result2, package2 = exported(env)
    assert result2['job_id'] != result['job_id']
    submit_candidate(path, write(tmp, candidate_for(package2, group_id='06')))
    view = get('menu/candidate?target_date=' + TARGET).json['candidate']
    assert action('cancel_draft').status_code == 200
    stale = get('menu/candidate?target_date=' + TARGET).json['candidate']
    assert stale['status'] == 'STALE'
    before = menu()['state']
    assert post('menu/candidate/' + view['id'] + '/adopt', {'confirmed': True}).status_code == 409
    assert menu()['state'] == before and before['adopted'] is None

def test_decline_and_resubmit_not_allowed(env):
    path, post, get, menu, action, tmp = env
    result, package = exported(env)
    submit_candidate(path, write(tmp, candidate_for(package, group_id='07')))
    view = get('menu/candidate?target_date=' + TARGET).json['candidate']
    assert post('menu/candidate/' + view['id'] + '/decline', {}).status_code == 200
    assert menu()['state']['adopted'] is None
    with pytest.raises(CandidateError, match='JOB_NOT_WAITING'):
        submit_candidate(path, write(tmp, candidate_for(package, group_id='07')))

def test_concurrent_exports_share_one_job_and_never_overwrite(env):
    from concurrent.futures import ThreadPoolExecutor
    path = env[0]
    result, _ = exported(env)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: export_package(path, TARGET), range(8)))
    assert {r['job_id'] for r in results} == {result['job_id']}
    before = dump(path)
    with pytest.raises(FileExistsError):
        export_package(path, TARGET, output=path)
    assert dump(path) == before

def test_cli_submit_while_app_reads(env):
    path, post, get, menu, action, tmp = env
    result, package = exported(env)
    file = write(tmp, candidate_for(package, group_id='05'))
    proc = subprocess.Popen([sys.executable, '-c', 'import sys;from waybeside.agent_door import submit_candidate as s;from waybeside.storage import encode;print(encode(s(sys.argv[1]+"/waybeside.sqlite",sys.argv[2])))', str(path.parent), str(file)],
                            cwd=ROOT, env={**os.environ, 'WAYBESIDE_DATA_DIR': str(path.parent)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    while proc.poll() is None:
        assert get('menu?date=' + TARGET).status_code == 200
    out, err = proc.communicate()
    assert proc.returncode == 0, err
    assert json.loads(out)['adopted'] is False
    with connect(path) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'

def test_cli_roundtrip(env):
    path, post, get, menu, action, tmp = env
    run = lambda *a: subprocess.run([sys.executable, '-m', 'waybeside.agent_door', *a], cwd=ROOT, env={**os.environ, 'WAYBESIDE_DATA_DIR': str(path.parent)}, capture_output=True, text=True)
    out = run('export', '--date', TARGET)
    assert out.returncode == 0, out.stderr
    package = json.loads(Path(json.loads(out.stdout)['package']).read_text())
    submitted = run('submit', str(write(tmp, candidate_for(package, group_id='05'))))
    assert submitted.returncode == 0 and json.loads(submitted.stdout)['adopted'] is False
