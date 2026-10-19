"""真实进程：run.py 服务 + 网页客户端 + 助手命令行子进程同时写同一个库。"""
import http.cookiejar
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from support import course_settings, TODAY
from waybeside import login, service

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Web:
    """像浏览器一样：用一次性登录码换 cookie，之后带 Origin 与 CSRF 令牌写入。"""
    def __init__(self, base, code):
        self.base, self.csrf = base, None
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.login_status, body = self.call('POST', '/api/login', {'token': code})
        if self.login_status == 200:
            self.csrf = body['csrf']

    def cookie_values(self):
        return [c.value for c in self.jar]

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {'Origin': self.base, 'Content-Type': 'application/json'}
        if self.csrf:
            headers['X-CSRF-Token'] = self.csrf
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw)
            except ValueError:
                return error.code, None


@pytest.fixture
def service_process(tmp_path):
    data = tmp_path / 'data'
    ready = tmp_path / 'ready.json'
    port = free_port()
    env = dict(os.environ, WAYBESIDE_DATA_DIR=str(data), WAYBESIDE_TODAY=TODAY)
    env.pop('BROWSER', None)
    process = subprocess.Popen([sys.executable, str(ROOT / 'run.py'), '--no-browser', '--port', str(port), '--ready-file', str(ready)],
                               cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(200):
            if ready.exists() and ready.stat().st_size:
                break
            time.sleep(0.05)
        info = json.loads(ready.read_text())
        yield dict(process=process, data=data, base=f"http://127.0.0.1:{port}", code=info['url'].split('#login=')[1], port=port, env=env, tmp=tmp_path)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def run_cli(svc, *argv, extra_env=None):
    env = dict(svc['env'], **(extra_env or {}))
    done = subprocess.run([sys.executable, '-m', 'waybeside.agent_door', *argv], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    return done.returncode, json.loads(done.stdout), done.stderr


def cli_api(svc, method, path, body=None, extra_env=None):
    args = ['api', method, path]
    if body is not None:
        svc['count'] = svc.get('count', 0) + 1
        file = svc['tmp'] / f"cli-body-{svc['count']}-{threading.get_ident()}.json"
        file.write_text(json.dumps(body))
        args += ['--json', str(file)]
    return run_cli(svc, *args, extra_env=extra_env)


def files_under(directory):
    return sorted(str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file())


def test_login_code_lifecycle_and_service_info(service_process):
    svc = service_process
    data = svc['data']
    code_file = data / login.FILE
    assert code_file.exists() and stat.S_IMODE(code_file.stat().st_mode) == 0o600
    assert svc['code'] not in code_file.read_text()                     # 文件里只有哈希
    info = json.loads((data / service.FILE).read_text())
    assert set(info) == {'pid', 'port', 'today', 'today_pinned'} and info['port'] == svc['port'] and info['pid'] == svc['process'].pid
    assert info['today'] == TODAY and info['today_pinned'] == TODAY
    web = Web(svc['base'], svc['code'])
    assert web.login_status == 200
    assert Web(svc['base'], svc['code']).login_status == 401            # 用过一次再用
    assert not code_file.exists()
    # 服务运行期间数据目录里没有任何文件含登录码或会话 cookie
    secrets_in_play = [svc['code'], *web.cookie_values()]
    assert all(secrets_in_play)
    for name in files_under(data):
        blob = (data / name).read_bytes()
        assert not any(secret.encode() in blob for secret in secrets_in_play), name
    assert not any('login' in n or 'token' in n for n in files_under(data))
    code, out, err = run_cli(svc, 'status')
    assert code == 0 and out['service'] == {'running': True, 'port': svc['port'], 'pid': svc['process'].pid} and err == ''
    # 退出后 service.json 与登录码文件都不留
    web.call('POST', '/api/exit', {})
    svc['process'].wait(timeout=10)
    assert not (data / service.FILE).exists() and not code_file.exists()


def test_open_url_issues_a_fresh_working_code_through_the_browser_only(service_process, tmp_path):
    svc = service_process
    web = Web(svc['base'], svc['code'])
    assert web.call('POST', '/api/disclaimer', {'accepted': True})[0] == 200
    sink = tmp_path / 'opened.txt'
    launcher = tmp_path / 'fake-browser.sh'
    launcher.write_text(f'#!/bin/sh\necho "$1" >> {sink}\n')
    launcher.chmod(0o755)
    code, out, err = run_cli(svc, 'open-url', extra_env={'BROWSER': f'{launcher} %s'})
    assert code == 0 and out['opened'] is True
    url = sink.read_text().strip()
    secret = url.split('#login=')[1]
    assert url.startswith(f"http://127.0.0.1:{svc['port']}/#login=")
    assert secret not in json.dumps(out) and secret not in err and secret != svc['code']
    assert secret not in (svc['data'] / login.FILE).read_text()
    second = Web(svc['base'], secret)
    assert second.login_status == 200 and second.call('GET', '/api/state')[0] == 200
    assert Web(svc['base'], secret).login_status == 401
    assert not (svc['data'] / login.FILE).exists()


def test_page_writes_and_assistant_writes_at_the_same_time(service_process):
    svc = service_process
    web = Web(svc['base'], svc['code'])
    assert web.call('POST', '/api/disclaimer', {'accepted': True})[0] == 200
    assert web.call('POST', '/api/assistant-mode', {'mode': 'full', 'version': 0})[0] == 200
    failures = []

    def page_writes():
        for n in range(15):
            status, body = web.call('POST', '/api/observations', dict(date='2031-12-30', time=f'08:{n:02}', note=f'页面{n}'))
            if status != 200:
                failures.append(('web', n, status))
    worker = threading.Thread(target=page_writes)
    worker.start()
    for n in range(5):
        code, out, err = cli_api(svc, 'POST', '/api/observations', dict(date='2031-12-29', time=f'09:{n:02}', note=f'助手{n}'))
        if code != 0 or out['status'] != 200:
            failures.append(('cli', n, out))
        assert err == ''
    worker.join(timeout=60)
    assert failures == []
    with sqlite3.connect(svc['data'] / 'waybeside.sqlite') as db:
        by = dict(db.execute("SELECT recorded_by,COUNT(*) FROM observation WHERE kind='daily' GROUP BY recorded_by").fetchall())
    assert by == {'local-user': 15, 'agent': 5}


def test_revision_and_row_version_conflicts_across_processes(service_process):
    svc = service_process
    web = Web(svc['base'], svc['code'])
    web.call('POST', '/api/disclaimer', {'accepted': True})
    web.call('POST', '/api/assistant-mode', {'mode': 'full', 'version': 0})
    assert web.call('POST', '/api/course', course_settings(total_count=None, schedule_kind='manual', weekdays=[]))[0] == 200
    revision = web.call('GET', '/api/state')[1]['course_revision']
    # 新增类：同一个 expected_revision，助手先到，页面后到 -> 页面 409
    code, out, _ = cli_api(svc, 'POST', '/api/sessions', {'action': 'schedule', 'date': '2032-05-01', 'expected_revision': revision})
    assert code == 0 and out['status'] == 201
    assert web.call('POST', '/api/sessions', {'action': 'schedule', 'date': '2032-05-02', 'expected_revision': revision})[0] == 409
    # 反过来：页面先到，助手后到 -> 助手 409（命令行退出码 2，不吐 HTML）
    revision = web.call('GET', '/api/state')[1]['course_revision']
    assert web.call('POST', '/api/sessions', {'action': 'schedule', 'date': '2032-05-03', 'expected_revision': revision})[0] == 201
    code, out, err = cli_api(svc, 'POST', '/api/sessions', {'action': 'schedule', 'date': '2032-05-04', 'expected_revision': revision})
    assert code == 2 and out['status'] == 409 and '<' not in json.dumps(out) and err == ''
    # 行 version：页面改过之后，助手拿旧 version 改同一条 -> 409
    created = web.call('POST', '/api/observations', dict(date='2031-12-30', time='08:00', note='v1'))[1]
    assert web.call('POST', '/api/observations', dict(date='2031-12-30', time='08:05', note='v2', id=created['id'], version=created['version']))[0] == 200
    code, out, _ = cli_api(svc, 'POST', '/api/observations', dict(date='2031-12-30', time='08:10', note='stale', id=created['id'], version=created['version']))
    assert code == 2 and out['status'] == 409
    code, out, _ = cli_api(svc, 'POST', '/api/observations', dict(date='2031-12-30', time='08:10', note='fresh', id=created['id'], version=created['version'] + 1))
    assert code == 0 and out['status'] == 200
    status, body = web.call('POST', '/api/observations', dict(date='2031-12-30', time='08:20', note='late', id=created['id'], version=created['version'] + 1))
    assert status == 409                                                # 页面拿旧版本，同样被拒


def test_lock_held_by_another_connection_gives_storage_busy_and_leaves_the_file_alone(service_process):
    svc = service_process
    web = Web(svc['base'], svc['code'])
    web.call('POST', '/api/disclaimer', {'accepted': True})
    web.call('POST', '/api/assistant-mode', {'mode': 'full', 'version': 0})
    database = svc['data'] / 'waybeside.sqlite'
    holder = sqlite3.connect(database, isolation_level=None)
    holder.execute('BEGIN IMMEDIATE')
    try:
        started = time.time()
        code, out, err = cli_api(svc, 'POST', '/api/observations', dict(date='2031-12-30', time='08:00', note='x'),
                                 extra_env={'WAYBESIDE_BUSY_TIMEOUT_S': '1'})
        assert code == 2 and out['status'] == 503 and out['error'] == 'STORAGE_BUSY' and err == ''
        assert time.time() - started < 20
        assert '<' not in json.dumps(out) and 'Traceback' not in json.dumps(out)
    finally:
        holder.execute('ROLLBACK')
        holder.close()
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM observation WHERE kind='daily'").fetchone()[0] == 0
    code, out, _ = cli_api(svc, 'POST', '/api/observations', dict(date='2031-12-30', time='08:00', note='x'))
    assert code == 0 and out['status'] == 200


def test_today_must_agree_with_the_running_service(service_process):
    svc = service_process
    web = Web(svc['base'], svc['code'])
    web.call('POST', '/api/disclaimer', {'accepted': True})
    web.call('POST', '/api/assistant-mode', {'mode': 'full', 'version': 0})
    code, out, _ = run_cli(svc, 'api', 'GET', '/api/state', extra_env={'WAYBESIDE_TODAY': '2031-01-01'})
    assert code == 2 and out['error'] == 'TODAY_MISMATCH'
    code, out, _ = run_cli(svc, 'api', 'GET', '/api/state')
    assert code == 0 and out['today_source'] == 'service' and out['today'] == TODAY
