"""登录码过期文件的清理、事务内方式检查的写法无关性、文案与文档补充。"""
import json
from pathlib import Path
import time

import pytest
from flask import Flask, g
from support import make_env, choose_mode, Cli, TODAY, course_settings
from waybeside import login
from waybeside.storage import connect, initialize

pytestmark = pytest.mark.filterwarnings('ignore')


def test_expired_unused_login_code_files_are_swept(tmp_path, monkeypatch, capsys):
    data = tmp_path / 'data'
    data.mkdir()
    env = make_env(data, mode=None)
    choose_mode(env, 'full')
    code_file = data / login.FILE
    # 1) 签发新码时清掉旧的过期码（这里先造一个过期的，再签发：文件被新码替换，不残留旧哈希）
    login.issue(data, ttl=-1)
    old = code_file.read_text()
    login.issue(data)
    assert code_file.read_text() != old
    # 2) 直接 expire：过期的删，未过期的留
    login.expire(data)
    assert code_file.exists()
    login.issue(data, ttl=-1)
    login.expire(data)
    assert not code_file.exists()
    # 3) 助手命令行（status / api 等）顺带清理
    login.issue(data, ttl=-1)
    cli = Cli(monkeypatch, capsys, data)
    code, out = cli('status')
    assert code == 0 and not code_file.exists()
    # 4) 校验时发现过期也会删
    login.issue(data, ttl=-1)
    assert login.consume(data, 'whatever') is False and not code_file.exists()


@pytest.mark.parametrize('begin', ['BEGIN', 'begin', 'BEGIN IMMEDIATE', 'BEGIN IMMEDIATE;', '  BEGIN DEFERRED', 'BEGIN EXCLUSIVE TRANSACTION;'])
def test_the_in_transaction_mode_check_does_not_depend_on_how_begin_is_spelled(tmp_path, begin):
    path = tmp_path / 'waybeside.sqlite'
    initialize(path)
    with connect(path) as db:
        db.execute("INSERT INTO metadata VALUES('assistant_mode','menu_only') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    app = Flask(__name__)
    with app.test_request_context('/'):
        g.actor = 'agent'
        from werkzeug.exceptions import HTTPException
        with connect(path) as db:
            with pytest.raises(HTTPException) as refused:
                db.execute(begin)
        assert refused.value.response.status_code == 403
        assert refused.value.response.get_json()['code'] == 'MODE_NOT_ALLOWED'
    with app.test_request_context('/'):
        g.actor = 'local-user'                                     # 人的请求不受影响
        with connect(path) as db:
            db.execute(begin)


def test_a_backup_with_the_previous_application_name_is_not_restored(tmp_path):
    import sqlite3
    from waybeside.storage import restore
    source = tmp_path / 'old.sqlite'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute("INSERT INTO metadata VALUES('app_id',?)", ('care' + 'course',))
    with pytest.raises(ValueError):
        restore(source, tmp_path / 'target')


def test_validation_errors_name_the_field_but_never_echo_values(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    env = make_env(data, mode=None)
    post = env[2]
    secret = 'SENTINEL_VALUE_55aa'
    r = post('onboarding/basic', {'answers': {'helper_mode': secret}, 'version': 0})
    assert r.status_code == 400 and r.json['code'] == 'INVALID_FIELD' and r.json['field'] == 'helper_mode'
    assert secret not in json.dumps(r.json)
    assert set(r.json) == {'code', 'field', 'error'}
    r = post('onboarding/basic', {'answers': {'nickname': 'x'}, 'version': 0})             # 缺必填项
    assert r.json['field'] == 'helper_mode'
    r = post('onboarding/basic', {'answers': {'helper_mode': 'self', secret: 1}, 'version': 0})
    assert r.json['field'] == secret or r.json['code'] == 'INVALID_FIELD'
    r = post('onboarding/basic', {'answers': {'helper_mode': 'self', 'age_band': 'x'}, 'version': 0})
    assert r.json['field'] == 'age_band'
    r = post('onboarding/habit', {'answers': {'sleep_usual': 'x'}, 'version': 0})
    assert r.json['field'] == 'sleep_usual'
    r = post('observations', dict(date='2031-12-30', time='08:00', mood=secret))
    assert r.status_code == 400 and r.json['field'] == 'mood' and secret not in json.dumps(r.json)
    assert post('onboarding/basic', {'answers': {'helper_mode': 'with_helper'}, 'version': 0}).status_code == 200


def test_documented_enumerations_match_the_code():
    from pathlib import Path
    from waybeside.questionnaire import HELPER_MODES, AGE_BANDS
    from waybeside import FIELDS
    doc = (Path(__file__).resolve().parents[1] / 'AGENTS.md').read_text(encoding='utf8')
    for value in HELPER_MODES + AGE_BANDS:
        assert f'`{value}`' in doc, value
    for values in FIELDS.values():
        for value in values:
            assert value in doc, value
    assert '`with_helper`' in doc and '代为记录' in doc


def test_dump_and_backup_show_up_as_assistant_actions_and_nothing_else_leaks(tmp_path, monkeypatch, capsys):
    data = tmp_path / 'data'
    data.mkdir()
    env = make_env(data, mode=None)
    choose_mode(env, 'full')
    cli = Cli(monkeypatch, capsys, data)
    assert cli('dump', '--output', cli.work / 'd.json')[0] == 0
    assert cli('backup', '--output', cli.work / 'b.sqlite')[0] == 0
    assert cli('dump', '--output', cli.work / 'd.json')[1]['error'] == 'OUTPUT_EXISTS'          # 失败不留记录
    recent = env[3]('assistant-activity').json['recent']
    assert [r['kind'] for r in recent] == ['assistant_export_backup', 'assistant_export_dump']
    assert all(set(r) == {'kind', 'at'} for r in recent)
    state = env[3]().json
    assert not [a for a in state['audit'] if a['entity'].startswith('assistant_export')]       # 不进页面的通用历史
    with connect(data / 'waybeside.sqlite') as db:
        rows = db.execute("SELECT actor,old_value,new_value FROM audit WHERE entity LIKE 'assistant_export_%'").fetchall()
    assert [tuple(r) for r in rows] == [('agent', None, None)] * 2


def test_say_lines_for_the_assistant_to_relay(tmp_path, monkeypatch, capsys):
    data = tmp_path / 'data'
    data.mkdir()
    env = make_env(data, mode=None)
    cli = Cli(monkeypatch, capsys, data)
    assert 'say' in cli('status')[1] and cli('status')[1]['say'].startswith('请打开「伴程」的网页')
    choose_mode(env, 'full')
    assert 'say' not in cli('status')[1]
    bad = cli.work / 'bad.json'
    bad.write_text('{not json')
    code, out = cli('api', 'POST', '/api/observations', '--json', bad)
    assert code == 2 and out['error'] == 'INVALID_JSON' and out['line'] == 1 and out['column'] >= 1 and 'say' not in out
    code, out = cli('api', 'GET', '/api/nothing-here')
    assert out['error'] == 'ROUTE_NOT_ALLOWED' and '接口名写错了' in out['say']
    from support import set_mode
    set_mode(data / 'waybeside.sqlite', 'menu_only')
    code, out = cli('export')
    assert out['error'] == 'SHARE_DISABLED' and '导出给助手' in out['say']


def test_candidate_endpoint_flags_a_replaced_suggestion(tmp_path, monkeypatch, capsys):
    from test_operator_core import library_ready, candidate, TARGET
    data = tmp_path / 'data'
    data.mkdir()
    env = make_env(data, mode=None)
    choose_mode(env, 'full')
    library_ready(env)
    cli = Cli(monkeypatch, capsys, data)
    code, out = cli('export', '--date', TARGET, '--output', cli.work / 'p1.json')
    assert env[3](f'menu/candidate?target_date={TARGET}').json['replaced'] is False
    assert cli('submit', candidate(cli, out['package'], group_id='06'))[1]['status'] == 'READY_TO_REVIEW'
    assert env[3](f'menu/candidate?target_date={TARGET}').json['replaced'] is False
    cli('export', '--date', TARGET, '--output', cli.work / 'p2.json')
    seen = env[3](f'menu/candidate?target_date={TARGET}').json
    assert seen['replaced'] is True and seen['candidate']['status'] == 'WAITING_EXTERNAL'
    assert set(seen) == {'candidate', 'replaced'}


def test_import_preview_message_is_plain():
    import inspect
    import waybeside.menu_library as lib
    source = inspect.getsource(lib)
    assert '保持冻结' not in source and '完整菜单库' not in source


# ---------------------------------------------------------------- 第二轮用户旅程
AUX_OK = {'labs': [], 'question': '', 'rhythm': {'blood': None, 'chemistry': None}, 'celebration': True, 'version': 0}


@pytest.fixture
def web(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    env = make_env(data, mode=None)
    choose_mode(env, 'full')
    return env


def bad(env, path, body):
    r = env[2](path, body)
    assert r.status_code == 400, (path, body, r.data)
    assert r.json['code'] == 'INVALID_FIELD' and set(r.json) == {'code', 'field', 'error'}, r.json
    return r.json['field']


def test_aux_names_every_missing_or_malformed_field(web):
    for name in ('labs', 'question', 'rhythm', 'celebration', 'version'):
        body = {k: v for k, v in AUX_OK.items() if k != name}
        assert bad(web, 'aux', body) == name
    assert bad(web, 'aux', dict(AUX_OK, celebration='yes')) == 'celebration'
    assert bad(web, 'aux', dict(AUX_OK, rhythm={'blood': 0, 'chemistry': None})) == 'rhythm'
    assert bad(web, 'aux', dict(AUX_OK, rhythm=7)) == 'rhythm'
    assert bad(web, 'aux', dict(AUX_OK, question=5)) == 'question'
    assert bad(web, 'aux', dict(AUX_OK, version='0')) == 'version'
    assert bad(web, 'aux', dict(AUX_OK, labs=[{'date': '2031-01-01'}])) == 'labs[0].high'
    assert bad(web, 'aux', dict(AUX_OK, extra=1)) == 'extra'
    assert bad(web, 'aux', dict(AUX_OK, labs=[{'date': '2031-01-01', 'metric': 'm', 'unit': 'u', 'lab': 'l', 'value': 1, 'low': None, 'high': None, 'extra': 1}])) == 'labs[0].extra'
    assert web[2]('aux', AUX_OK).status_code == 200


def test_other_write_endpoints_name_the_field_too(web):
    post = web[2]
    assert bad(web, 'observations', {'time': '08:00'}) == 'date'
    assert bad(web, 'observations', {'date': '2031-12-30', 'time': ''}) == 'time'
    assert bad(web, 'observations', {'date': '2031-12-30', 'time': 'unknown'}) == 'time'
    assert bad(web, 'observations', {'date': 'nope', 'time': '08:00'}) == 'date'
    assert bad(web, 'observations', {'date': '2031-12-30', 'time': '25:00'}) == 'time'
    assert bad(web, 'observations', {'date': '2031-12-30', 'time': '08:00', 'minutes': 'x'}) == 'minutes'
    assert bad(web, 'observations', {'date': '2031-12-30', 'time': '08:00', 'note': 5}) == 'note'
    assert bad(web, 'course', {'schedule_kind': 'manual'}) == 'treatment_type'
    assert bad(web, 'course', {'treatment_type': 'other', 'schedule_kind': 'x'}) == 'schedule_kind'
    assert bad(web, 'course', {'treatment_type': 'other', 'schedule_kind': 'manual', 'total_count': 0}) == 'total_count'
    assert bad(web, 'course', {'treatment_type': 'other', 'schedule_kind': 'weekdays', 'weekdays': []}) == 'weekdays'
    assert bad(web, 'course', {'treatment_type': 'other', 'schedule_kind': 'manual', 'start_date': 'x'}) == 'start_date'
    assert bad(web, 'sessions', {'action': 'schedule'}) == 'date'
    assert bad(web, 'sessions', {'action': 'nope', 'date': '2031-12-30', 'expected_revision': 0}) == 'action'
    assert bad(web, 'onboarding/basic', {'answers': {'helper_mode': 'self'}}) == 'version'
    assert bad(web, 'onboarding/habit', {'answers': {'x': 1}, 'version': 0}) == 'x'
    assert bad(web, 'agent-share', {'category': 'menu_library', 'version': 1}) == 'enabled'
    assert bad(web, 'agent-share', {'category': 'nope', 'enabled': True, 'version': 1}) == 'category'
    assert bad(web, 'assistant-mode', {'mode': 'weird', 'version': 1}) == 'mode'
    assert bad(web, 'menu/import-confirm', {'menu': {}}) == 'confirmed'
    assert bad(web, 'menu/import-preview', {'nothing': 1}) == 'menu'


def test_output_inside_the_application_or_a_git_workspace_is_refused(tmp_path, monkeypatch, capsys):
    from waybeside import agent_door
    data = tmp_path / 'data'
    data.mkdir()
    env = make_env(data, mode=None)
    choose_mode(env, 'full')
    cli = Cli(monkeypatch, capsys, data)
    db_file = data / 'waybeside.sqlite'
    before = db_file.read_bytes()
    app_inside = agent_door.APP_DIR / 'dump-test-output.json'
    repo = tmp_path / 'work-repo'
    (repo / '.git').mkdir(parents=True)
    (repo / 'sub').mkdir()
    for command in ('dump', 'backup'):
        for target in (app_inside, repo / 'out.bin', repo / 'sub' / 'out.bin', agent_door.APP_DIR / 'x' / 'nested.out'):
            code, out = cli(command, '--output', target)
            assert code == 2 and out['error'] == 'OUTPUT_INSIDE_REPO' and out['say'], (command, target, out)
            assert not target.exists()
    assert db_file.read_bytes() == before                                   # 库一字节都没动（连 audit 也没写）
    assert not list(agent_door.APP_DIR.glob('dump-test-output.json'))
    # 仓库外：正常；通过符号链接进入仓库也算
    link = tmp_path / 'link'
    link.symlink_to(repo, target_is_directory=True)
    assert cli('dump', '--output', link / 'sneaky.json')[1]['error'] == 'OUTPUT_INSIDE_REPO'
    assert cli('dump', '--output', tmp_path / 'ok' / 'dump.json')[1].get('error') in (None, 'FILE_OR_DATE_ERROR')
    (tmp_path / 'fine').mkdir()
    code, out = cli('dump', '--output', tmp_path / 'fine' / 'dump.json')
    assert code == 0 and out['status'] == 'OK'
    assert cli('backup', '--output', tmp_path / 'fine' / 'backup.sqlite')[0] == 0


def test_gitignore_has_a_safety_net_for_exports():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / '.gitignore').read_text(encoding='utf8')
    assert 'dump*.json' in text and 'backup*.sqlite' in text


def test_export_says_when_it_replaces_a_pending_suggestion(tmp_path, monkeypatch, capsys):
    from test_operator_core import library_ready, candidate, TARGET
    data = tmp_path / 'data'
    data.mkdir()
    env = make_env(data, mode=None)
    choose_mode(env, 'full')
    library_ready(env)
    cli = Cli(monkeypatch, capsys, data)
    code, out = cli('export', '--date', TARGET, '--output', cli.work / 'p1.json')
    assert 'replaces_pending' not in out
    assert cli('submit', candidate(cli, out['package'], group_id='06'))[1]['status'] == 'READY_TO_REVIEW'
    code, out = cli('export', '--date', TARGET, '--output', cli.work / 'p2.json')
    assert out['replaces_pending'] is True and '被这次的新任务替换' in out['say']


def test_open_url_message_depends_on_whether_the_mode_was_chosen(tmp_path, monkeypatch, capsys):
    from waybeside import service
    import os
    data = tmp_path / 'data'
    data.mkdir()
    env = make_env(data, mode=None)
    monkeypatch.setenv('BROWSER', 'true')
    service.write(data, 18999, None, TODAY)
    cli = Cli(monkeypatch, capsys, data)
    assert '先看免责声明，再选一种使用方式' in cli('open-url')[1]['say']
    choose_mode(env, 'menu_only')
    again = cli('open-url')[1]['say']
    assert '重新打开了' in again and '免责' not in again


def test_export_output_inside_a_repository_is_refused_but_the_default_still_works(tmp_path, monkeypatch, capsys):
    from waybeside import agent_door
    from test_operator_core import library_ready, TARGET
    data = tmp_path / 'data'
    data.mkdir()
    env = make_env(data, mode=None)
    choose_mode(env, 'full')
    library_ready(env)
    cli = Cli(monkeypatch, capsys, data)
    db_file = data / 'waybeside.sqlite'
    repo = tmp_path / 'repo'
    (repo / '.git').mkdir(parents=True)
    from sqlite3 import connect as raw
    def counts():
        with raw(db_file) as db:
            return db.execute('SELECT (SELECT COUNT(*) FROM menu_candidate),(SELECT COUNT(*) FROM audit)').fetchone()
    before = counts()
    for target in (agent_door.APP_DIR / 'package-test-output.json', repo / 'package.json', agent_door.APP_DIR / 'x' / 'p.json'):
        code, out = cli('export', '--date', TARGET, '--output', target)
        assert code == 2 and out['error'] == 'OUTPUT_INSIDE_REPO' and out['say'], (target, out)
        assert not target.exists()
    assert counts() == before                                              # 没有生成任务，也没有 audit
    link = tmp_path / 'link'
    link.symlink_to(repo, target_is_directory=True)
    assert cli('export', '--date', TARGET, '--output', link / 'p.json')[1]['error'] == 'OUTPUT_INSIDE_REPO'
    # 仓库外正常；不传 --output 时照旧写到数据目录下的 agent_inbox
    code, out = cli('export', '--date', TARGET, '--output', tmp_path / 'outside' / 'p.json')
    assert code == 0 or out.get('error') == 'FILE_OR_DATE_ERROR'
    (tmp_path / 'fine').mkdir()
    code, out = cli('export', '--date', TARGET, '--output', tmp_path / 'fine' / 'p.json')
    assert code == 0 and Path(out['package']).is_file()
    code, out = cli('export', '--date', TARGET)
    assert code == 0 and Path(out['package']).parent == data / 'agent_inbox'


# ---------------------------------------------------------------- 第三轮用户旅程
def test_observation_time_may_be_left_out_and_is_stored_as_unknown(web):
    from waybeside.storage import connect as conn
    post, get = web[2], web[3]
    day = '2031-12-30'
    a = post('observations', dict(date=day, diet='partial')).json                       # 不写 time
    b = post('observations', dict(date=day, time=None, note='x')).json                  # time 为 null
    c = post('observations', dict(date=day, time='08:30', note='known')).json
    assert a['time'] == '' and b['time'] == '' and c['time'] == '08:30'
    with conn(web[0].config['DB_PATH']) as db:
        stamps = [r[0] for r in db.execute("SELECT occurred_at FROM observation WHERE kind='daily' ORDER BY rowid")]
    assert stamps == [day + 'T', day + 'T', day + 'T08:30']                             # 未知时间 = 只有日期，排在同一天任何已知时间之前
    rows = [o for o in get().json['observation'] if o['kind'] == 'daily']
    assert [o['content']['time'] for o in rows] == ['', '', '08:30']
    # 更新一条未知时间的记录：仍可改，也可以补上时间
    again = post('observations', dict(date=day, id=a['id'], version=a['version'], diet='done')).json
    assert again['time'] == '' and again['version'] == 2
    filled = post('observations', dict(date=day, id=a['id'], version=again['version'], time='21:05')).json
    assert filled['time'] == '21:05'
    # 与治疗记录关联不依赖时间（跨午夜关联靠治疗记录编号）
    assert web[2]('course', course_settings()).status_code == 200
    first = get().json['course_revision']
    assert post('course/generate', {'expected_revision': first}).status_code == 201
    session = get().json['treatment_session'][0]
    linked = post('observations', dict(date='2031-12-31', session=str(session['id']))).json
    assert linked['session'] == str(session['id']) and linked['time'] == ''
    summary = get('summary').json
    assert any(o['content'].get('time') == '' for o in summary['observation'] if o['kind'] == 'daily')


def test_failed_submit_never_reports_a_pending_status(tmp_path, monkeypatch, capsys):
    from test_operator_core import library_ready, candidate, TARGET
    data = tmp_path / 'data'
    data.mkdir()
    env = make_env(data, mode=None)
    choose_mode(env, 'full')
    library_ready(env)
    cli = Cli(monkeypatch, capsys, data)
    code, out = cli('export', '--date', TARGET, '--output', cli.work / 'p1.json')
    first = candidate(cli, out['package'], group_id='06')
    assert cli('submit', first)[1]['status'] == 'READY_TO_REVIEW'
    code, out2 = cli('export', '--date', TARGET, '--output', cli.work / 'p2.json')
    code, res = cli('submit', first)                                        # 已被替换的任务
    assert code == 2 and res['error'] == 'JOB_NOT_WAITING' and res['status'] == 'FAILED' and res['job_status'] == 'READY_TO_REVIEW'
    # 各种失败路径：status 恒为 FAILED
    broken = cli.work / 'broken.json'
    for text, error in (('{"job_id": "x"}', 'UNKNOWN_JOB'), ('{not json', 'INVALID_JSON'), ('[]', 'SCHEMA_FIELDS')):
        broken.write_text(text)
        code, res = cli('submit', broken)
        assert code == 2 and res['error'] == error and res['status'] == 'FAILED', res
    code, res = cli('submit', cli.work / 'missing.json')
    assert code == 2 and res['status'] == 'FAILED'
    # 字段缺失 / 多余时指出是哪个
    package = json.loads(Path(out2['package']).read_text(encoding='utf-8'))
    wrong = {k: v for k, v in dict(package['candidate_template'], surprise=1, group_id='06').items() if k != 'reason_code'}
    broken.write_text(json.dumps(wrong))
    code, res = cli('submit', broken)
    assert res['error'] == 'SCHEMA_FIELDS' and res['status'] == 'FAILED' and 'surprise' in res['extra'] and 'reason_code' in res['missing']
    # 过期（资料已变）
    cli('export', '--date', TARGET, '--output', cli.work / 'p3.json')
    env[2]('menu/action', dict(action='preference', target_date=TARGET, revision=env[3]('menu?date=' + TARGET).json['state']['revision'],
                               library_hash=env[3]('menu?date=' + TARGET).json['state']['library_hash'], group_id='05', favorite=True, avoided=False))
    package3 = json.loads((cli.work / 'p3.json').read_text(encoding='utf-8'))
    stale = cli.work / 'stale.json'
    stale.write_text(json.dumps(dict(package3['candidate_template'], group_id='06', reason_code='VARIETY')))
    code, res = cli('submit', stale)
    assert res['error'] == 'DEPENDENCIES_CHANGED' and res['status'] == 'FAILED' and res['job_status'] == 'STALE'


def test_state_remembers_that_full_was_ever_chosen(tmp_path):
    from test_operator_core import switch
    data = tmp_path / 'data'
    data.mkdir()
    env = make_env(data, mode=None)
    choose_mode(env, 'menu_only')
    assert env[3]().json['ever_full'] is False
    switch(env, 'full')
    assert env[3]().json['ever_full'] is True
    switch(env, 'menu_only')
    state = env[3]().json
    assert state['assistant_mode'] == 'menu_only' and state['ever_full'] is True      # 换回去也记得
    other = tmp_path / 'other'
    other.mkdir()
    fresh = make_env(other, mode=None)
    choose_mode(fresh, 'menu_only')
    assert fresh[3]().json['ever_full'] is False                                      # 从来没选过
