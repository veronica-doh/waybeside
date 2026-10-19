"""助手使用方式（还没选 / 我自己记 / 让助手全部打理）与命令行通道：后端行为。全部使用虚构数据。"""
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
from pathlib import Path

import pytest
from support import (make_env, choose_mode, Cli, open_shares, course_settings, set_mode, TODAY, ORIGIN)
from waybeside import create_app, agent_door, login, service, storage
from waybeside.access import AGENT_ENDPOINTS, EXCLUDED_ENDPOINTS
from waybeside.menu_door import ALLERGY_NOTICE
from waybeside.menu_library import synthetic_library, import_library
from waybeside.storage import connect

ROOT = Path(__file__).resolve().parents[1]
TARGET = '2032-01-01'           # TODAY 的次日
MODES = ('unset', 'menu_only', 'full')
NOT_CHOSEN = '请打开「伴程」的网页，先看一下免责声明，再选一下你希望我帮你做多少。'
NOT_ALLOWED = '你选的是自己记录、我只帮挑菜单，所以这件事要你在网页上做。想换方式可以在设置里改。'


@pytest.fixture
def env(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    return make_env(data, mode=None)


@pytest.fixture
def cli(env, tmp_path, monkeypatch, capsys):
    return Cli(monkeypatch, capsys, tmp_path / 'data')


def db_path(env):
    return env[0].config['DB_PATH']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rows(env, query, *args):
    with connect(db_path(env)) as db:
        return [dict(r) for r in db.execute(query, args)]


def audit_rows(env, entity=None, after=0):
    query = 'SELECT * FROM audit WHERE id>?' + (' AND entity=?' if entity else '') + ' ORDER BY id'
    return rows(env, query, after, *([entity] if entity else []))


def last_audit_id(env):
    return rows(env, 'SELECT COALESCE(MAX(id),0) AS n FROM audit')[0]['n']


def switch(env, mode):
    version = env[3]().json['assistant_mode_version']
    r = env[2]('assistant-mode', {'mode': mode, 'version': version})
    assert r.status_code == 200, r.data


def library_ready(env, shares=('menu_library',)):
    import_library(db_path(env), synthetic_library())
    if shares:
        open_shares(env[2], env[3], shares)


def export_to(cli, name, date=TARGET):
    code, out = cli('export', '--date', date, '--output', cli.work / name)
    return code, out


def candidate(cli, package_file, **changes):
    package = json.loads(Path(package_file).read_text(encoding='utf-8'))
    file = cli.work / f'cand-{cli.count}.json'
    cli.count += 1
    file.write_text(json.dumps({**package['candidate_template'], **changes}), encoding='utf-8')
    return file


def menu_action(env, op, target=TARGET, **extra):
    state = env[3]('menu?date=' + target).json['state']
    return env[2]('menu/action', dict(action=op, target_date=target, revision=state['revision'],
                                      library_hash=state['library_hash'], **extra))


# ---------------------------------------------------------------- 接口契约
def test_assistant_mode_contract(env):
    post, get = env[2], env[3]
    state = get().json
    assert state['assistant_mode'] == 'unset' and state['disclaimer_accepted'] is False and state['assistant_mode_version'] == 0
    r = post('assistant-mode', {'mode': 'full', 'version': 0})
    assert r.status_code == 409 and r.json['code'] == 'DISCLAIMER_REQUIRED'      # 没确认免责不能选
    assert post('disclaimer', {'accepted': True}).status_code == 200
    for bad in ({'mode': 'unset', 'version': 0}, {'mode': 'other', 'version': 0}, {'mode': 'full'}, {'version': 0},
                {'mode': 'full', 'version': 0, 'extra': 1}, {'mode': 'full', 'version': '0'}, {'mode': 'full', 'version': True},
                {'mode': ['full'], 'version': 0}):
        assert post('assistant-mode', bad).status_code == 400, bad
    assert post('assistant-mode', {'mode': 'full', 'version': 5}).status_code == 409
    r = post('assistant-mode', {'mode': 'full', 'version': 0})
    assert r.status_code == 200 and r.json == {'assistant_mode': 'full', 'assistant_mode_version': 1}
    state = get().json
    assert state['assistant_mode'] == 'full' and state['assistant_mode_version'] == 1
    assert post('assistant-mode', {'mode': 'menu_only', 'version': 0}).status_code == 409   # 另一个标签页的旧版本
    assert post('assistant-mode', {'mode': 'menu_only', 'version': 1}).status_code == 200
    assert post('assistant-mode', {'mode': 'menu_only', 'version': 2}).status_code == 200   # 同值：不改也不记
    assert get().json['assistant_mode_version'] == 2


def test_assistant_mode_audit_records_only_the_enum(env):
    choose_mode(env, 'full')
    switch(env, 'menu_only')
    found = audit_rows(env, 'assistant_mode')
    assert len(found) == 2
    for row in found:
        assert row['old_value'] is None and row['new_value'] is None and row['actor'] == 'local-user'
    assert [json.loads(r['detail'])['to'] for r in found] == ['full', 'menu_only']
    assert json.loads(found[1]['detail'])['from'] == 'full'
    assert 'assistant_mode' in storage.REDACTED_ENTITIES
    assert all(a['entity'] != 'assistant_mode' for a in env[3]().json['audit'])


def test_assistant_mode_needs_origin_cookie_and_csrf(env):
    env[2]('disclaimer', {'accepted': True})
    body = {'mode': 'full', 'version': 0}
    csrf = env[3]('session').json['csrf']
    assert env[1].post('/api/assistant-mode', base_url=ORIGIN, json=body, headers={'X-CSRF-Token': csrf}).status_code == 403
    assert env[1].post('/api/assistant-mode', base_url=ORIGIN, json=body, headers={'Origin': ORIGIN}).status_code == 403
    assert env[1].post('/api/assistant-mode', base_url=ORIGIN, json=body, headers={'Origin': 'http://evil.example'}).status_code == 403
    stranger = env[0].test_client()
    assert stranger.post('/api/assistant-mode', base_url=ORIGIN, json=body, headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf}).status_code == 401
    assert env[3]().json['assistant_mode'] == 'unset'


def test_cli_cannot_reach_assistant_mode_or_disclaimer(env, cli):
    library_ready(env)
    choose_mode(env, 'full')
    before = sha(db_path(env))
    for path, body in (('/api/assistant-mode', {'mode': 'full', 'version': 2}), ('/api/disclaimer', {'accepted': True}),
                       ('/api/agent-share', {'category': 'preferences', 'enabled': True, 'version': 1})):
        code, out = cli.api('POST', path, body)
        assert code == 2 and out['error'] == 'ROUTE_NOT_ALLOWED', path
    assert sha(db_path(env)) == before


def test_disclaimer_cannot_become_true_through_the_command_line(env, cli, tmp_path):
    for argv in (('status',), ('export',), ('open-url',), ('api', 'GET', '/api/state'), ('dump', '--output', cli.work / 'x.json'),
                 ('backup', '--output', cli.work / 'y.sqlite')):
        cli(*argv)
    code, out = cli.api('POST', '/api/disclaimer', {'accepted': True})
    assert code == 2 and out['error'] == 'MODE_NOT_CHOSEN'            # 没选方式，api 整个不放行
    state = env[3]().json
    assert state['disclaimer_accepted'] is False and state['assistant_mode'] == 'unset'
    assert not (cli.work / 'x.json').exists() and not (cli.work / 'y.sqlite').exists()


# ---------------------------------------------------------------- 三状态 x 子命令
@pytest.mark.parametrize('mode', MODES)
def test_state_matrix_every_subcommand(env, cli, tmp_path, mode):
    library_ready(env)
    if mode != 'unset':
        choose_mode(env, mode)
    work = cli.work
    code, out = cli('status')
    assert code == 0 and out['assistant_mode'] == mode and out['service'] == {'running': False}
    assert Path(out['data_dir']) == (tmp_path / 'data').resolve() and out['demo'] is False
    code, out = cli('open-url')                      # 三种状态都放行；服务没跑所以是它自己的错误码
    assert code == 2 and out['error'] == 'SERVICE_NOT_RUNNING' and 'start_command' in out
    # export / submit
    code, out = export_to(cli, f'{mode}.json')
    if mode == 'unset':
        assert code == 2 and out['error'] == 'MODE_NOT_CHOSEN' and out['say'] == NOT_CHOSEN
        assert not (work / f'{mode}.json').exists()
        junk = work / 'junk.json'
        junk.write_text('{"job_id":"x"}')
        code, out = cli('submit', junk)
        assert code == 2 and out['error'] == 'MODE_NOT_CHOSEN'
    else:
        assert code == 0 and out['status'] == 'WAITING_EXTERNAL'
        code, out = cli('submit', candidate(cli, work / f'{mode}.json', group_id='01'))
        assert code == 0 and out['status'] == 'READY_TO_REVIEW' and out['adopted'] is False
    # api / dump / backup
    results = {'api': cli.api('GET', '/api/state'), 'dump': cli('dump', '--output', work / 'd.json'),
               'backup': cli('backup', '--output', work / 'b.sqlite')}
    for name, (code, out) in results.items():
        if mode == 'full':
            assert code == 0 and out.get('status') in (200, 'OK'), (name, out)
        elif mode == 'menu_only':
            assert code == 2 and out['error'] == 'MODE_NOT_ALLOWED' and out['say'] == NOT_ALLOWED, name
        else:
            assert code == 2 and out['error'] == 'MODE_NOT_CHOSEN' and out['say'] == NOT_CHOSEN, name
        assert out['data_dir'] and out['demo'] is False
    assert (work / 'd.json').exists() == (work / 'b.sqlite').exists() == (mode == 'full')


def test_menu_only_changes_nothing_about_the_old_door(env, cli):
    """menu_only 下 export 仍受七类开关约束：menu_library 关就是 SHARE_DISABLED。"""
    import_library(db_path(env), synthetic_library())
    choose_mode(env, 'menu_only')
    code, out = cli('export', '--date', TARGET)
    assert code == 2 and out['error'] == 'SHARE_DISABLED'


# ---------------------------------------------------------------- effective_flags
def test_full_exports_everything_regardless_of_switches(env, cli):
    library_ready(env, shares=())
    choose_mode(env, 'menu_only')
    assert cli('export', '--date', TARGET)[1]['error'] == 'SHARE_DISABLED'
    switch(env, 'full')
    flags = {r['category']: r['enabled'] for r in env[3]('agent-share').json}
    assert not any(flags.values())                              # 开关本身仍全关、保留不动
    code, out = export_to(cli, 'full.json')
    assert code == 0
    snap = json.loads(Path(out['package']).read_text())['snapshot']
    for key in ('current', 'avoided_group_ids', 'favorite_group_ids', 'recent_records', 'requirement_tags', 'age_band',
                'course_progress', 'allergy_reminders', 'allergy_notice'):
        assert key in snap, key
    assert snap['allergy_notice'] == ALLERGY_NOTICE
    assert ALLERGY_NOTICE in (ROOT / 'waybeside/static/menu.js').read_text(encoding='utf-8')
    switch(env, 'menu_only')
    assert {r['category']: r['enabled'] for r in env[3]('agent-share').json} == flags
    assert cli('export', '--date', TARGET)[1]['error'] == 'SHARE_DISABLED'


def test_submit_error_codes_are_specific_in_full_and_generic_in_menu_only(env, cli):
    library_ready(env)
    choose_mode(env, 'full')
    assert menu_action(env, 'preference', group_id='03', favorite=False, avoided=True).status_code == 200
    code, out = export_to(cli, 'a.json')
    assert cli('submit', candidate(cli, out['package'], group_id='03'))[1]['error'] == 'AVOIDED_MENU'
    switch(env, 'menu_only')                                    # preferences 开关仍关
    code, out = export_to(cli, 'b.json')
    assert code == 0
    assert cli('submit', candidate(cli, out['package'], group_id='03'))[1]['error'] == 'GROUP_NOT_ALLOWED'


def test_full_switch_toggles_do_not_stale_but_mode_switch_does(env, cli):
    library_ready(env)
    choose_mode(env, 'full')
    code, out = export_to(cli, 'a.json')
    file = candidate(cli, out['package'], group_id='01')
    assert env[2]('agent-share', {'category': 'preferences', 'enabled': True,
                                  'version': next(r for r in env[3]('agent-share').json if r['category'] == 'preferences')['version']}).status_code == 200
    code, res = cli('submit', file)
    assert code == 0 and res['status'] == 'READY_TO_REVIEW'                    # 拨开关不让候选失效
    view = env[3]('menu/candidate?target_date=' + TARGET).json['candidate']
    assert view['status'] == 'READY_TO_REVIEW'
    switch(env, 'menu_only')                                    # 切方式：失效
    assert env[3]('menu/candidate?target_date=' + TARGET).json['candidate']['status'] == 'STALE'
    # 等待中的候选也一样：切方式后 submit 返回 STALE
    switch(env, 'full')
    code, out = export_to(cli, 'c.json', date='2032-01-02')
    file = candidate(cli, out['package'], group_id='01')
    switch(env, 'menu_only')
    code, res = cli('submit', file)
    assert code == 2 and res['status'] == 'STALE' and res['error'] == 'DEPENDENCIES_CHANGED'


def test_switching_to_menu_only_after_the_request_started_rejects_the_agent_write(env, monkeypatch):
    monkeypatch.setenv('WAYBESIDE_TODAY', TODAY)
    choose_mode(env, 'full')
    path = db_path(env)
    app, client, origin, headers = agent_door.make_client(path)

    def flip_after_gate():
        with sqlite3.connect(path) as db:
            db.execute("UPDATE metadata SET value='menu_only' WHERE key='assistant_mode'")
    app.before_request_funcs.setdefault(None, []).append(flip_after_gate)     # 排在方式检查之后
    body = dict(date='2031-12-30', time='08:00', note='不应写入')
    response = client.post('/api/observations', base_url=origin, json=body, headers=headers)
    assert response.status_code == 403 and response.json['code'] == 'MODE_NOT_ALLOWED'
    with connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM observation WHERE kind='daily'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM audit WHERE entity='observation'").fetchone()[0] == 0


def test_agent_reads_inside_a_transaction_are_also_gated(env):
    choose_mode(env, 'full')
    path = db_path(env)
    app, client, origin, headers = agent_door.make_client(path)
    app.before_request_funcs.setdefault(None, []).append(lambda: sqlite3.connect(path).execute(
        "UPDATE metadata SET value='unset' WHERE key='assistant_mode'").connection.commit())
    response = client.get('/api/state', base_url=origin)
    assert response.status_code == 403 and response.json['code'] == 'MODE_NOT_CHOSEN'


# ---------------------------------------------------------------- recorded_by / actor
def lab(metric, value):
    return dict(date='2031-12-01', metric=metric, unit='示例单位', lab='虚构实验室', value=value, low=1, high=9)


def aux_body(labs, version):
    return {'labs': labs, 'question': '', 'rhythm': {'blood': None, 'chemistry': None}, 'celebration': False, 'version': version}


def aux_row(env):
    return next(r for r in env[3]().json['observation'] if r['id'] == 'aux')


def test_lab_rows_are_attributed_row_by_row(env, cli):
    choose_mode(env, 'full')
    assert env[2]('aux', aux_body([lab('A', 3), lab('B', 4)], 0)).status_code == 200
    assert [x['recorded_by'] for x in aux_row(env)['content']['labs']] == ['local-user', 'local-user']
    code, out = cli.api('POST', '/api/aux', aux_body([lab('A', 3), lab('B', 5)], 1))        # 助手只改 B
    assert code == 0 and out['status'] == 200
    row = aux_row(env)
    assert {x['metric']: x['recorded_by'] for x in row['content']['labs']} == {'A': 'local-user', 'B': 'agent'}
    assert row['recorded_by'] == 'agent'
    assert env[2]('aux', aux_body([lab('A', 3), lab('B', 5), lab('C', 6)], 2)).status_code == 200
    assert {x['metric']: x['recorded_by'] for x in aux_row(env)['content']['labs']} == {'A': 'local-user', 'B': 'agent', 'C': 'local-user'}
    # 重复行按个数比对
    assert env[2]('aux', aux_body([lab('A', 3), lab('A', 3)], 3)).status_code == 200
    assert [x['recorded_by'] for x in aux_row(env)['content']['labs']] == ['local-user', 'local-user']


def test_recorded_by_in_a_request_is_always_rejected(env, cli):
    choose_mode(env, 'full')
    tagged = dict(lab('A', 3), recorded_by='agent')
    assert env[2]('aux', aux_body([tagged], 0)).status_code == 400
    assert env[2]('aux', dict(aux_body([lab('A', 3)], 0), recorded_by='agent')).status_code == 400
    assert env[2]('observations', dict(date='2031-12-30', time='08:00', recorded_by='agent')).status_code == 400
    code, out = cli.api('POST', '/api/aux', aux_body([tagged], 0))
    assert code == 2 and out['status'] == 400
    code, out = cli.api('POST', '/api/observations', dict(date='2031-12-30', time='08:00', recorded_by='local-user'))
    assert code == 2 and out['status'] == 400
    assert rows(env, "SELECT COUNT(*) AS n FROM observation")[0]['n'] == 0


def test_observation_and_session_attribution(env, cli):
    choose_mode(env, 'full')
    created = env[2]('observations', dict(date='2031-12-30', time='08:00', note='人记的')).json
    assert rows(env, 'SELECT recorded_by FROM observation WHERE id=?', created['id'])[0]['recorded_by'] == 'local-user'
    code, out = cli.api('POST', '/api/observations', dict(date='2031-12-30', time='09:00', note='助手改的', id=created['id'], version=created['version']))
    assert code == 0
    assert rows(env, 'SELECT recorded_by FROM observation WHERE id=?', created['id'])[0]['recorded_by'] == 'agent'
    env[2]('course', course_settings())
    revision = env[3]().json['course_revision']
    code, out = cli.api('POST', '/api/course/generate', {'expected_revision': revision})
    assert out['status'] == 201
    sessions = {s['id']: s for s in env[3]().json['treatment_session']}
    assert {s['confirmation_source'] for s in sessions.values()} == {'agent'}
    first = sessions[1]
    code, out = cli.api('POST', '/api/sessions/1', {'action': 'complete', 'date': first['planned_date'], 'version': first['version']})
    assert out['status'] == 200 and out['body']['confirmation_source'] == 'agent'
    second = sessions[2]
    r = env[2]('sessions/2', {'action': 'complete', 'date': second['planned_date'], 'version': second['version']})
    assert r.status_code == 200 and r.json['confirmation_source'] == 'local-user'
    code, out = cli.api('POST', '/api/sessions/2', {'action': 'correct', 'date': second['planned_date'], 'version': 2})
    assert out['body']['confirmation_source'] == 'agent'


def test_http_requests_ignore_every_actor_hint(env):
    choose_mode(env, 'full')
    csrf = env[3]('session').json['csrf']
    headers = {'Origin': ORIGIN, 'X-CSRF-Token': csrf, 'X-Actor': 'agent', 'Actor': 'agent', 'X-Forwarded-User': 'agent',
               'X-Recorded-By': 'agent', 'User-Agent': 'agent'}
    before = last_audit_id(env)
    r = env[1].post('/api/observations?actor=agent&recorded_by=agent', base_url=ORIGIN, headers=headers,
                    json=dict(date='2031-12-30', time='08:00', note='x'))
    assert r.status_code == 200
    assert {a['actor'] for a in audit_rows(env, after=before)} == {'local-user'}
    assert rows(env, "SELECT recorded_by FROM observation WHERE kind='daily'")[0]['recorded_by'] == 'local-user'
    r = env[1].post('/api/observations', base_url=ORIGIN, headers=headers, json=dict(date='2031-12-30', time='08:00', actor='agent'))
    assert r.status_code == 400


def test_agent_actor_is_only_set_by_the_constructing_program(env):
    app = create_app(db_path(env), migrate=False, issue_code=False)
    assert app.config['ACTOR'] == 'local-user'
    assert agent_door.make_client(db_path(env))[0].config['ACTOR'] == 'agent'
    assert not agent_door.make_client(db_path(env))[0].config['TESTING']
    # 请求之外写 audit 的是程序自己
    with connect(db_path(env)) as db:
        db.execute('BEGIN IMMEDIATE')
        storage.audit(db, 'probe', 'x', None, {'k': 1})
    assert audit_rows(env, 'probe')[0]['actor'] == 'local-program'


def test_every_agent_write_endpoint_audits_as_agent(env, cli):
    choose_mode(env, 'full')
    before = last_audit_id(env)

    def ok(method, path, body=None, status=(200, 201)):
        code, out = cli.api(method, path, body)
        assert out['status'] in status, (path, out)
        return out['body']

    ok('POST', '/api/onboarding/basic', {'answers': {'nickname': '小示例', 'helper_mode': 'self'}, 'version': 0})
    ok('POST', '/api/onboarding/treatment', {'answers': course_settings(), 'version': 0})
    ok('POST', '/api/onboarding/diet', {'answers': {'requirement_tags': ['soft_texture']}, 'version': 0})
    ok('POST', '/api/onboarding/habit', {'answers': {'sleep_usual': 'regular'}, 'version': 0})
    ok('POST', '/api/course', course_settings(settings_version=1, total_count=7))
    revision = ok('GET', '/api/state')['course_revision']
    ok('POST', '/api/course/generate', {'expected_revision': revision})
    revision = ok('GET', '/api/state')['course_revision']
    ok('POST', '/api/sessions', {'action': 'schedule', 'date': '2032-06-01', 'expected_revision': revision})
    first = ok('GET', '/api/state')['treatment_session'][0]
    ok('POST', f"/api/sessions/{first['id']}", {'action': 'complete', 'date': first['planned_date'], 'version': first['version']})
    ok('POST', '/api/observations', dict(date='2031-12-30', time='08:00', note='助手记'))
    ok('POST', '/api/aux', aux_body([lab('A', 3)], 0))
    menu = synthetic_library()
    preview = ok('POST', '/api/menu/import-preview', menu)
    ok('POST', '/api/menu/import-confirm', {'menu': menu, 'digest': preview['digest'], 'expected': preview['expected'], 'confirmed': True})
    view = ok('GET', '/api/menu?date=' + TARGET)
    state = view['state']
    base = dict(target_date=TARGET, library_hash=state['library_hash'])
    ok('POST', '/api/menu/action', dict(base, action='preference', revision=state['revision'], group_id='05', favorite=True, avoided=False))
    state = ok('GET', '/api/menu?date=' + TARGET)['state']
    gid = state['recommendation']['group_id']
    ok('POST', '/api/menu/action', dict(base, action='select', revision=state['revision'], group_id=gid))
    state = ok('GET', '/api/menu?date=' + TARGET)['state']
    ok('POST', '/api/menu/action', dict(base, action='adopt', revision=state['revision'], group_id=gid))
    # 候选：采用与拒绝
    for date, verb in ((TARGET, 'adopt'), ('2032-01-02', 'decline')):
        code, out = export_to(cli, f'{verb}.json', date=date)
        code, res = cli('submit', candidate(cli, out['package'], group_id='06'))
        assert res['status'] == 'READY_TO_REVIEW'
        found = ok('GET', f'/api/menu/candidate?target_date={date}')['candidate']
        ok('POST', f"/api/menu/candidate/{found['id']}/{verb}", {'confirmed': True} if verb == 'adopt' else {})
    new = [a for a in audit_rows(env, after=before) if a['actor'] != 'local-program']
    assert new and {a['actor'] for a in new} == {'agent'}, {a['actor'] for a in new}
    assert {'questionnaire', 'treatment_course', 'treatment_session', 'observation', 'menu_library', 'menu_preference',
            'menu_select', 'menu_adopt', 'menu_candidate'} <= {a['entity'] for a in new}
    # export / submit 是助手发起的动作，同样记为 agent（让“助手最近做了什么”覆盖到）
    assert {a['actor'] for a in audit_rows(env, after=before)} == {'agent'}
    exported = [a for a in audit_rows(env, after=before) if a['entity'] == 'menu_candidate']
    assert exported and {a['actor'] for a in exported} == {'agent'}


@pytest.mark.parametrize('who', ['agent', 'local-user'])
def test_get_side_effect_audits_carry_the_requester(env, cli, tmp_path, who):
    library_ready(env)
    choose_mode(env, 'full')
    code, out = export_to(cli, 'a.json')
    assert cli('submit', candidate(cli, out['package'], group_id='01'))[1]['status'] == 'READY_TO_REVIEW'
    assert menu_action(env, 'preference', group_id='02', favorite=True, avoided=False).status_code == 200   # 使候选过期
    rules_dir = tmp_path / 'data' / 'rules'
    rules_dir.mkdir()
    shutil.copy(ROOT / 'examples/rules/demo-fictional-rules.json', rules_dir / 'demo.json')      # 新规则文件：GET /api/menu 会记一行摘要
    before = last_audit_id(env)
    if who == 'agent':
        code, res = cli.api('GET', f'/api/menu/candidate?target_date={TARGET}')
        assert res['body']['candidate']['status'] == 'STALE'
        cli.api('GET', f'/api/menu?date={TARGET}')
    else:
        assert env[3](f'menu/candidate?target_date={TARGET}').json['candidate']['status'] == 'STALE'
        env[3](f'menu?date={TARGET}')
    new = audit_rows(env, after=before)
    stale = [a for a in new if a['entity'] == 'menu_candidate']
    assert stale and json.loads(stale[0]['new_value']) == {'status': 'STALE'}
    assert {a['entity'] for a in new} >= {'menu_candidate', 'rules'}
    assert {a['actor'] for a in new} == {who}


# ---------------------------------------------------------------- 规则隐藏组的采用
# ---------------------------------------------------------------- 错误码
def test_no_database_creates_nothing(tmp_path, monkeypatch, capsys):
    data = tmp_path / 'nothing-here'
    data.mkdir()
    run = Cli(monkeypatch, capsys, data)
    for argv in (('status',), ('export',), ('submit', data), ('dump', '--output', data.parent / 'o.json'),
                 ('backup', '--output', data.parent / 'o.sqlite')):
        code, out = run(*argv)
        assert code == 2 and out['error'] == 'NO_DATABASE', argv
    assert run.api('GET', '/api/state')[1]['error'] == 'NO_DATABASE'
    assert list(data.iterdir()) == []
    assert not (data.parent / 'o.json').exists()


@pytest.mark.parametrize('kind', ['foreign-sqlite', 'not-a-database', 'wrong-app-id', 'previous-app-id'])
def test_not_waybeside_database_is_left_alone(tmp_path, monkeypatch, capsys, kind):
    data = tmp_path / 'data'
    data.mkdir()
    target = data / 'waybeside.sqlite'
    if kind == 'not-a-database':
        target.write_text('plain text, not sqlite')
    else:
        with sqlite3.connect(target) as db:
            if kind == 'foreign-sqlite':
                db.execute('CREATE TABLE other(x)')
            else:
                db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                # 旧的应用名不再被认作合法库（名字拆开写，免得仓库里再出现旧名）
                old_name = 'care' + 'course'
                db.execute("INSERT INTO metadata VALUES('app_id',?)", (old_name if kind == 'previous-app-id' else 'something-else',))
    before = sha(target)
    run = Cli(monkeypatch, capsys, data)
    for result in (run('export'), run.api('GET', '/api/state'), run('status')):
        assert result[0] == 2 and result[1]['error'] == 'NOT_WAYBESIDE'
    assert sha(target) == before and sorted(p.name for p in data.iterdir()) == ['waybeside.sqlite']


def test_schema_mismatch_asks_to_restart_the_service(env, cli):
    choose_mode(env, 'full')
    with sqlite3.connect(db_path(env)) as db:
        db.execute("UPDATE metadata SET value='6' WHERE key='schema_version'")
    before = sha(db_path(env))
    for result in (cli('export'), cli.api('GET', '/api/state'), cli('dump', '--output', cli.work / 'd.json'),
                   cli('backup', '--output', cli.work / 'b.sqlite'), cli('status')):
        assert result[0] == 2 and result[1]['error'] == 'SCHEMA_MISMATCH' and '重启服务完成升级' in result[1]['say']
    assert sha(db_path(env)) == before
    with sqlite3.connect(db_path(env)) as db:
        db.execute("UPDATE metadata SET value=? WHERE key='schema_version'", (str(storage.schema_version()),))
    assert cli('status')[0] == 0                                  # 命令行自己从不升级


def test_today_must_match_a_running_service(env, cli, tmp_path):
    choose_mode(env, 'full')
    data = tmp_path / 'data'
    before = sha(db_path(env))
    code, out = cli.api('GET', '/api/state')
    assert code == 0 and out['today_source'] == 'cli'            # 服务没跑：跳过检查并标注来源
    service.write(data, 8765, '2031-01-01', '2031-01-01')
    code, out = cli.api('GET', '/api/state')
    assert code == 2 and out['error'] == 'TODAY_MISMATCH'
    assert cli('dump', '--output', cli.work / 'd.json')[1]['error'] == 'TODAY_MISMATCH'
    assert cli('export')[1]['error'] == 'TODAY_MISMATCH'
    assert sha(db_path(env)) == before
    service.write(data, 8765, TODAY, TODAY)
    code, out = cli.api('GET', '/api/state')
    assert code == 0 and out['today_source'] == 'service'
    cli.today = None                                              # 服务钉了日期而命令行没有：同样不一致
    assert cli.api('GET', '/api/state')[1]['error'] == 'TODAY_MISMATCH'
    process = subprocess.Popen(['true'])
    process.wait()
    (data / service.FILE).write_text(json.dumps({'pid': process.pid, 'port': 8765, 'today': '2031-01-01', 'today_pinned': '2031-01-01'}))
    assert service.read(data) is None                             # pid 不存活：视为服务未运行
    cli.today = TODAY
    assert cli.api('GET', '/api/state')[1]['today_source'] == 'cli'


def test_storage_busy_for_api_and_export(env, cli, monkeypatch):
    library_ready(env)
    choose_mode(env, 'full')
    monkeypatch.setattr(storage, 'BUSY_TIMEOUT', 0.2)
    before = sha(db_path(env))
    holder = sqlite3.connect(db_path(env), isolation_level=None)
    holder.execute('BEGIN IMMEDIATE')
    try:
        code, out = cli.api('POST', '/api/observations', dict(date='2031-12-30', time='08:00', note='x'))
        assert code == 2 and out['status'] == 503 and out['error'] == 'STORAGE_BUSY' and 'body' not in out
        code, out = cli('export', '--date', TARGET)
        assert code == 2 and out['error'] == 'STORAGE_BUSY' and out['say']
        assert 'Traceback' not in json.dumps(out) and '<html' not in json.dumps(out).lower()
    finally:
        holder.execute('ROLLBACK')
        holder.close()
    assert sha(db_path(env)) == before
    assert cli.api('POST', '/api/observations', dict(date='2031-12-30', time='08:00', note='x'))[0] == 0


# ---------------------------------------------------------------- 白名单
def test_every_api_route_is_classified(env):
    app = env[0]
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules() if rule.rule.startswith('/api/')}
    assert endpoints == AGENT_ENDPOINTS | EXCLUDED_ENDPOINTS, endpoints ^ (AGENT_ENDPOINTS | EXCLUDED_ENDPOINTS)
    assert not AGENT_ENDPOINTS & EXCLUDED_ENDPOINTS
    for name in ('login', 'session', 'exit_service', 'accept_disclaimer', 'get_agent_share', 'set_agent_share',
                 'set_assistant_mode', 'export_backup'):
        assert name in EXCLUDED_ENDPOINTS


BYPASS = ['/api/login', '/api/login/', '//api/exit', '/api/exit', '/api/%2Fexit', '/api/%65xit', '/api/session',
          '/api/./exit', '/api/state/../exit', '/api/../api/exit', '/api/disclaimer', '/api/agent-share',
          '/api/assistant-mode', '/api/backup', '/api/exit;x', '/api/state\\..\\exit', 'api/state', '/api', '/',
          'http://127.0.0.1:8765/api/state', '//127.0.0.1/api/state', '/api/state#frag', '/api/nope', '/index.html',
          '/i18n-data.js', '/api/sessions/1/../../exit', '/api/menu/candidate/x/../../../exit']


@pytest.mark.parametrize('path', BYPASS)
def test_path_tricks_never_reach_excluded_routes(env, cli, path):
    choose_mode(env, 'full')
    for method in ('POST', 'GET'):
        code, out = cli.api(method, path, {} if method == 'POST' else None)
        assert code == 2 and out['error'] == 'ROUTE_NOT_ALLOWED', (method, path, out)
    # 即便绕过命令行的字符串检查，app 自己也按 endpoint 名挡住
    app, client, origin, headers = agent_door.make_client(db_path(env))
    for target in ('/api/exit', '/api/login', '/api/%2Fexit', '//api/exit', '/api/exit/'):
        r = client.post(target, base_url=origin, json={}, headers=headers)
        assert r.status_code in (301, 308, 403, 404, 405), (target, r.status_code)
        assert r.status_code != 200


def test_in_app_whitelist_covers_methods_and_unknown_paths(env):
    choose_mode(env, 'full')
    app, client, origin, headers = agent_door.make_client(db_path(env))
    r = client.post('/api/exit', base_url=origin, json={}, headers=headers)
    assert r.status_code == 403 and r.json['code'] == 'ROUTE_NOT_ALLOWED'
    r = client.get('/api/does-not-exist', base_url=origin)
    assert r.status_code == 403 and r.json['code'] == 'ROUTE_NOT_ALLOWED'
    assert client.get('/api/state', base_url=origin).status_code == 200
    for method in ('PUT', 'DELETE', 'PATCH'):
        assert not agent_door.route_allowed(app, method, '/api/state')


def test_cli_opens_no_network_socket(env, cli, monkeypatch, tmp_path):
    library_ready(env)
    choose_mode(env, 'full')
    hits = []

    def refuse(*args, **kwargs):
        hits.append(args)
        raise OSError('socket 被禁用')
    monkeypatch.setattr(socket.socket, 'bind', refuse)
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(socket.socket, 'listen', refuse)
    for method, path, body in (('GET', '/api/state', None), ('GET', '/api/summary', None), ('GET', '/api/settings', None),
                               ('POST', '/api/observations', dict(date='2031-12-30', time='08:00', note='x')),
                               ('GET', f'/api/menu?date={TARGET}', None), ('POST', '/api/aux', aux_body([lab('A', 3)], 0))):
        code, out = cli.api(method, path, body)
        assert code == 0 and out['status'] in (200, 201), (path, out)
    assert cli('dump', '--output', cli.work / 'd.json')[0] == 0
    assert cli('backup', '--output', cli.work / 'b.sqlite')[0] == 0
    assert cli('export', '--date', TARGET)[0] == 0
    assert hits == []


# ---------------------------------------------------------------- 出口矩阵
SENTINELS = {'nick': 'SENT_NICK', 'allergy': 'SENT_ALLERGY', 'note': 'SENT_NOTE', 'lab': 'SENT_LAB',
             'symptom': 'SENT_SYMPTOM', 'record': 'SENT_RECORD_NOTE'}
QUESTIONNAIRE = ('SENT_NICK', 'SENT_ALLERGY', 'SENT_NOTE')
HEALTH = ('SENT_LAB', 'SENT_SYMPTOM', 'SENT_RECORD_NOTE')


def seed_sentinels(env):
    library_ready(env)
    post = env[2]
    assert post('onboarding/basic', {'answers': {'nickname': 'SENT_NICK', 'age_band': '65_plus', 'helper_mode': 'with_helper'}, 'version': 0}).status_code == 200
    assert post('onboarding/diet', {'answers': {'requirement_tags': ['soft_texture'], 'allergy_reminders': ['SENT_ALLERGY'],
                                                'free_note': 'SENT_NOTE'}, 'version': 0}).status_code == 200
    assert post('onboarding/habit', {'answers': {'sleep_usual': 'regular'}, 'version': 0}).status_code == 200
    assert post('observations', dict(date='2031-12-30', time='08:00', note='SENT_RECORD_NOTE', symptoms={'SENT_SYMPTOM': 'some'})).status_code == 200
    assert post('aux', aux_body([lab('SENT_LAB', 3)], 0)).status_code == 200


def text_of(response):
    return response.data.decode() if hasattr(response, 'data') else json.dumps(response, ensure_ascii=False)


@pytest.mark.parametrize('mode', ['menu_only', 'full'])
def test_exit_matrix_with_sentinels(env, cli, mode):
    seed_sentinels(env)
    choose_mode(env, mode)
    # 网页出口：state 含检验与症状、不含问卷；settings 含问卷；summary 不含问卷（两种方式一致）
    state, settings, summary = (text_of(env[3](p)) for p in ('state', 'settings', 'summary'))
    for s in HEALTH:
        assert s in state and s in summary
    for s in QUESTIONNAIRE:
        assert s not in state and s not in summary and s in settings
    assert 'SENT_NICK' not in json.dumps(env[3]().json['audit'])
    assert env[1].post('/api/backup', base_url=ORIGIN, json={}, headers={'Origin': ORIGIN, 'X-CSRF-Token': env[3]('session').json['csrf']}).status_code == 200
    # 审计值列：问卷类与 assistant_mode 只记字段名或枚举
    for row in rows(env, "SELECT old_value,new_value,detail FROM audit WHERE entity IN ('questionnaire','assistant_mode','settings','agent_share')"):
        blob = json.dumps(row, ensure_ascii=False)
        assert not any(s in blob for s in QUESTIONNAIRE + ('65_plus', 'with_helper', 'soft_texture'))
    # 资料包
    if mode == 'menu_only':
        code, out = export_to(cli, 'default.json')                      # 只开了菜单库
        snap = json.loads(Path(out['package']).read_text())['snapshot']
        assert not {'requirement_tags', 'age_band', 'course_progress', 'current', 'avoided_group_ids', 'recent_records',
                    'allergy_reminders', 'allergy_notice'} & set(snap)
        open_shares(env[2], env[3], tuple(c for c in storage.SHARE_CATEGORIES if c != 'menu_library'))
    code, out = export_to(cli, 'all.json')
    package = Path(out['package']).read_text()
    snap = json.loads(package)['snapshot']
    for s in QUESTIONNAIRE + HEALTH + ('with_helper',):
        if mode == 'full' and s == 'SENT_ALLERGY':
            continue
        assert s not in package, s
    assert 'helper_mode' not in package and 'nickname' not in package and 'free_note' not in package
    assert snap['requirement_tags'] == ['soft_texture'] and snap['age_band'] == '65_plus'
    if mode == 'full':
        assert snap['allergy_reminders'] == ['SENT_ALLERGY'] and snap['allergy_notice'] == ALLERGY_NOTICE
    else:
        assert 'allergy_reminders' not in package and 'allergy_notice' not in package
    # 命令行出口
    for name, result in (('api', cli.api('GET', '/api/settings')), ('dump', cli('dump', '--output', cli.work / 'd.json')),
                         ('backup', cli('backup', '--output', cli.work / 'b.sqlite'))):
        if mode == 'menu_only':
            assert result[1]['error'] == 'MODE_NOT_ALLOWED', name
    if mode == 'menu_only':
        return
    api_settings = cli.api('GET', '/api/settings')[1]['body']
    assert api_settings['basic']['nickname'] == 'SENT_NICK' and api_settings['diet']['allergy_reminders'] == ['SENT_ALLERGY']
    api_state = json.dumps(cli.api('GET', '/api/state')[1]['body'], ensure_ascii=False)
    assert all(s in api_state for s in HEALTH) and not any(s in api_state for s in QUESTIONNAIRE)
    api_summary = json.dumps(cli.api('GET', '/api/summary')[1]['body'], ensure_ascii=False)
    assert all(s in api_summary for s in HEALTH) and not any(s in api_summary for s in QUESTIONNAIRE)
    dump = json.loads((cli.work / 'd.json').read_text(encoding='utf-8'))
    dump_text = json.dumps(dump, ensure_ascii=False)
    assert all(s in dump_text for s in QUESTIONNAIRE + HEALTH) and set(dump) == {'summary', 'settings'}
    backup_out = cli('backup', '--output', cli.work / 'b2.sqlite')[1]
    assert backup_out['sha256'] == sha(cli.work / 'b2.sqlite')
    with sqlite3.connect(cli.work / 'b2.sqlite') as copy:
        assert copy.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == 2
    assert cli('dump', '--output', cli.work / 'd.json')[1]['error'] == 'OUTPUT_EXISTS'


# ---------------------------------------------------------------- 采用路径与规则
def test_rule_confirmed_is_an_intent_marker_for_the_agent(tmp_path, monkeypatch, capsys):
    from test_rules import setup as rules_setup, example, TOMORROW
    environment, path = rules_setup(tmp_path, {'demo.json': example()})
    choose_mode(environment, 'full')
    run = Cli(monkeypatch, capsys, tmp_path)
    state = run.api('GET', f'/api/menu?date={TOMORROW}')[1]['body']['state']
    base = dict(target_date=TOMORROW, revision=state['revision'], library_hash=state['library_hash'], group_id='02')
    code, out = run.api('POST', '/api/menu/action', dict(base, action='select'))
    assert code == 2 and out['status'] == 409 and out['body']['code'] == 'RULE_CONFIRM_REQUIRED'
    before = last_audit_id(environment)
    code, out = run.api('POST', '/api/menu/action', dict(base, action='select', rule_confirmed=True))
    assert code == 0
    state = run.api('GET', f'/api/menu?date={TOMORROW}')[1]['body']['state']
    code, out = run.api('POST', '/api/menu/action', dict(base, revision=state['revision'], action='adopt', rule_confirmed=True))
    assert code == 0 and out['body']['adopted']['group_id'] == '02'
    marks = [a for a in audit_rows(environment, 'menu_rule_override', before)]
    assert [json.loads(a['new_value'])['op'] for a in marks] == ['select', 'adopt'] and {a['actor'] for a in marks} == {'agent'}
    # 人做同样的事不产生这条标注
    other = rules_setup(tmp_path / 'human', {'demo.json': example()})[0]
    state = other[3]('menu?date=' + TOMORROW).json['state']
    r = other[2]('menu/action', dict(base, revision=state['revision'], library_hash=state['library_hash'], action='select', rule_confirmed=True))
    assert r.status_code == 200 and audit_rows(other, 'menu_rule_override') == []
    # submit 通道维持硬拒
    code, out = export_to(run, 'r.json', date=TOMORROW)
    code, res = run('submit', candidate(run, out['package'], group_id='02'))
    assert res['error'] == 'RULE_EXCLUDED'


# ---------------------------------------------------------------- 一次性登录码
def test_login_code_is_single_use_and_never_stored_in_plain(tmp_path):
    app = create_app(tmp_path / 'waybeside.sqlite', today=TODAY)
    code = app.config['LOGIN_CODE']
    file = tmp_path / login.FILE
    assert file.exists() and (file.stat().st_mode & 0o777) == 0o600 and code not in file.read_text()
    client = app.test_client()
    post = lambda value: client.post('/api/login', base_url=ORIGIN, json={'token': value}, headers={'Origin': ORIGIN})
    assert post('wrong').status_code == 401 and file.exists()
    assert post(code).status_code == 200 and not file.exists()
    assert post(code).status_code == 401
    for found in tmp_path.rglob('*'):
        if found.is_file():
            data = found.read_bytes()
            assert code.encode() not in data and app.config['TOKEN'].encode() not in data


def test_login_code_expires(tmp_path, monkeypatch):
    app = create_app(tmp_path / 'waybeside.sqlite', today=TODAY)
    code = app.config['LOGIN_CODE']
    real = login.time.time()
    monkeypatch.setattr(login.time, 'time', lambda: real + login.TTL_SECONDS + 1)
    r = app.test_client().post('/api/login', base_url=ORIGIN, json={'token': code}, headers={'Origin': ORIGIN})
    assert r.status_code == 401 and not (tmp_path / login.FILE).exists()
    fresh = login.issue(tmp_path)
    monkeypatch.undo()
    assert login.consume(tmp_path, fresh) is True


def test_command_line_app_never_touches_the_running_services_login_code(env, cli, tmp_path):
    choose_mode(env, 'full')
    file = tmp_path / 'data' / login.FILE
    code = login.issue(tmp_path / 'data')
    before = file.read_bytes()
    assert cli.api('GET', '/api/state')[0] == 0 and cli('dump', '--output', cli.work / 'd.json')[0] == 0
    assert file.read_bytes() == before
    assert login.consume(tmp_path / 'data', code) is True


def test_open_url_hands_a_fresh_code_only_to_the_browser(env, cli, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(agent_door.webbrowser, 'open', lambda url: opened.append(url) or True)
    data = tmp_path / 'data'
    service.write(data, 8765, None, TODAY)
    code, out = cli('open-url')                                   # 还没选方式也放行
    assert code == 0 and out['status'] == 'OK' and out['opened'] is True
    assert len(opened) == 1 and opened[0].startswith('http://127.0.0.1:8765/#login=')
    secret = opened[0].split('#login=')[1]
    assert secret not in json.dumps(out) and secret not in (data / login.FILE).read_text()
    assert (data / login.FILE).stat().st_mode & 0o777 == 0o600
    assert login.consume(data, secret) is True and login.consume(data, secret) is False
    info = json.loads((data / service.FILE).read_text())
    assert set(info) == {'pid', 'port', 'today', 'today_pinned'}


# ---------------------------------------------------------------- 从空库用命令行走完整流程
def test_whole_course_through_the_command_line_after_the_human_chooses(env, cli):
    choose_mode(env, 'full')
    code, status = cli('status')
    assert status['assistant_mode'] == 'full'

    def ok(method, path, body=None):
        code, out = cli.api(method, path, body)
        assert out['status'] in (200, 201), (path, out)
        return out['body']

    for block, answers in (('basic', {'nickname': '小示例', 'age_band': '40_64', 'helper_mode': 'self'}),
                           ('treatment', course_settings()),
                           ('diet', {'requirement_tags': ['soft_texture'], 'allergy_reminders': ['示例过敏'], 'free_note': '示例备注'}),
                           ('habit', {'sleep_usual': 'regular'})):
        ok('POST', f'/api/onboarding/{block}', {'answers': answers, 'version': 0})
    ok('POST', '/api/course/generate', {'expected_revision': ok('GET', '/api/state')['course_revision']})
    ok('POST', '/api/observations', dict(date='2031-12-30', time='08:00', diet='done', symptoms={'示例症状': 'some'}))
    ok('POST', '/api/aux', aux_body([lab('示例指标', 5)], 0))
    menu = synthetic_library()
    preview = ok('POST', '/api/menu/import-preview', menu)
    ok('POST', '/api/menu/import-confirm', {'menu': menu, 'digest': preview['digest'], 'expected': preview['expected'], 'confirmed': True})
    state = ok('GET', f'/api/menu?date={TARGET}')['state']
    gid = state['recommendation']['group_id']
    base = dict(target_date=TARGET, library_hash=state['library_hash'], group_id=gid)
    ok('POST', '/api/menu/action', dict(base, action='select', revision=state['revision']))
    state = ok('GET', f'/api/menu?date={TARGET}')['state']
    adopted = ok('POST', '/api/menu/action', dict(base, action='adopt', revision=state['revision']))
    assert adopted['adopted']['group_id'] == gid
    code, out = cli('dump', '--output', cli.work / 'all.json')
    assert code == 0
    dump = json.loads((cli.work / 'all.json').read_text(encoding='utf-8'))
    assert dump['settings']['basic']['nickname'] == '小示例' and dump['settings']['diet']['allergy_reminders'] == ['示例过敏']
    assert dump['settings']['completed'] == {b: True for b in ('basic', 'treatment', 'diet', 'habit')}
    labs = next(r for r in dump['summary']['observation'] if r['id'] == 'aux')['content']['labs']
    assert labs[0]['metric'] == '示例指标' and labs[0]['recorded_by'] == 'agent'
    assert env[3]().json['disclaimer_accepted'] is True                 # 免责是人点的，全程没被命令行改动
    assert {a['actor'] for a in audit_rows(env, 'questionnaire')} == {'agent'}


def test_backup_and_dump_only_write_new_files(env, cli):
    choose_mode(env, 'full')
    target = cli.work / 'exists.json'
    target.write_text('keep')
    assert cli('dump', '--output', target)[1]['error'] == 'OUTPUT_EXISTS'
    assert cli('backup', '--output', target)[1]['error'] == 'OUTPUT_EXISTS'
    assert target.read_text() == 'keep'
    out = cli('backup', '--output', cli.work / 'ok.sqlite')[1]
    assert out['sha256'] == sha(cli.work / 'ok.sqlite') and Path(out['output']) == (cli.work / 'ok.sqlite').resolve()
    with connect(cli.work / 'ok.sqlite') as copy:
        assert copy.execute("SELECT value FROM metadata WHERE key='assistant_mode'").fetchone()[0] == 'full'


def test_demo_flag_and_directory_rule_apply_to_the_command_line(tmp_path, monkeypatch, capsys):
    from waybeside.demo import populate
    data = tmp_path / 'demo-data'
    populate(data / 'waybeside.sqlite', TODAY)
    run = Cli(monkeypatch, capsys, data)
    code, out = run('status')
    assert code == 0 and out['demo'] is True and out['assistant_mode'] == 'menu_only'
    real = tmp_path / 'real'
    real.mkdir()
    monkeypatch.setattr(storage, 'default_directory', lambda demo=False: real)
    shutil.copytree(data, real, dirs_exist_ok=True)
    code, out = Cli(monkeypatch, capsys, real)('status')
    assert code == 2 and out['error'] == 'DEMO_DIR_NOT_SAFE' and out['demo'] is True
