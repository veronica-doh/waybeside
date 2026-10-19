"""登录码过期文件的清理、事务内方式检查的写法无关性、文案与文档补充。"""
import json
import time

import pytest
from flask import Flask, g
from support import make_env, choose_mode, Cli, TODAY
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
    assert bad(web, 'aux', dict(AUX_OK, labs=[{'date': '2031-01-01'}])) == 'labs'
    assert bad(web, 'aux', dict(AUX_OK, extra=1)) == 'extra'
    assert web[2]('aux', AUX_OK).status_code == 200


def test_other_write_endpoints_name_the_field_too(web):
    post = web[2]
    assert bad(web, 'observations', {'time': '08:00'}) == 'date'
    assert bad(web, 'observations', {'date': '2031-12-30'}) == 'time'
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
