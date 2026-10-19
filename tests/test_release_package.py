"""发行包可启动：构建 ZIP -> 解到临时目录 -> 用当前 python 起服务 -> 冒烟请求 -> 退出。"""
import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging'))
import build_release  # noqa: E402


def test_zip_contents_and_whitelist(tmp_path):
    out = tmp_path / 'release.zip'
    receipt = build_release.build(out)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    inner = {n.split('/', 1)[1] for n in names}
    for need in ('waybeside/i18n/zh-CN.json', 'examples/menu/demo-menu-fictional.json', 'LICENSE', 'NOTICE',
                 'README.md', 'DISCLAIMER.md', 'MANIFEST.json', 'tools/verify_menu.py'):
        assert need in inner, need
    assert any(n.startswith('.agents/') for n in inner) and any(n.startswith('docs/') for n in inner)
    assert not any(n.startswith(('tests/', 'node_modules/', 'agent_adapter/', '.git/')) for n in inner)
    assert not any('__pycache__' in n or n.endswith(('.pyc', '.sqlite')) for n in inner)
    assert receipt['file_count'] == len(names)
    assert 'seed' not in ' '.join(inner).lower()


def test_release_package_starts_and_answers(tmp_path):
    out = tmp_path / 'release.zip'
    build_release.build(out)
    target = tmp_path / 'unzipped'
    with zipfile.ZipFile(out) as z:
        z.extractall(target)
    app_dir = target / 'waybeside'
    assert (app_dir / 'run.py').is_file()
    ready = tmp_path / 'ready.json'
    env = {**os.environ, 'WAYBESIDE_DATA_DIR': str(tmp_path / 'data'), 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('PYTHONPATH', None)
    server = subprocess.Popen([sys.executable, 'run.py', '--no-browser', '--port', str(_free_port()),
                               '--ready-file', str(ready)], cwd=app_dir, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(200):
            if ready.exists() and ready.stat().st_size:
                break
            time.sleep(0.05)
        info = json.loads(ready.read_text())
        base = f"http://127.0.0.1:{info['port']}"
        token = info['url'].split('#login=')[1]
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        headers = {'Origin': base, 'Content-Type': 'application/json'}
        login = opener.open(urllib.request.Request(base + '/api/login', json.dumps({'token': token}).encode(), headers))
        csrf = json.loads(login.read())['csrf']
        assert opener.open(base + '/api/session').status == 200
        pack = opener.open(base + '/i18n-data.js').read().decode()
        assert pack.startswith('window.I18N=') and '伴程' in pack
        assert opener.open(base + '/').status == 200
        opener.open(urllib.request.Request(base + '/api/exit', b'{}', {**headers, 'X-CSRF-Token': csrf}))
        server.wait(timeout=10)
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()
    assert server.returncode is not None


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def test_untracked_files_never_enter_the_package(tmp_path):
    stray = ROOT / 'examples' / 'menu' / 'untracked-private.json'
    stray2 = ROOT / 'docs' / 'untracked-private-note.md'
    try:
        stray.write_text('{}', encoding='utf8')
        stray2.write_text('x', encoding='utf8')
        out = tmp_path / 'release.zip'
        build_release.build(out)
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
    finally:
        stray.unlink(missing_ok=True)
        stray2.unlink(missing_ok=True)
    assert not any('untracked-private' in n for n in names)
    assert any(n.endswith('examples/menu/demo-menu-fictional.json') for n in names)
