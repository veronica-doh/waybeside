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
    assert 'say' in cli('status')[1] and cli('status')[1]['say'].startswith('请打开 WayBeside 的网页')
    choose_mode(env, 'full')
    assert 'say' not in cli('status')[1]
    bad = cli.work / 'bad.json'
    bad.write_text('{not json')
    code, out = cli('api', 'POST', '/api/observations', '--json', bad)
    assert code == 2 and out['error'] == 'INVALID_JSON' and out['say']
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
