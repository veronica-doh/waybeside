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
