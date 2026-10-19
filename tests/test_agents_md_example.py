"""AGENTS.md 里的每条命令都要在真实环境里执行：两种使用方式各跑一遍（真实服务进程 + 真实命令行子进程）。

文档里的代码块用“语言 + 标记”区分：
  sh install / sh start     安装与启动（启动在这里真实执行；安装见 test_install_commands）
  sh any / full / menu_only 助手命令，按方式分章
  json <文件名> / python <文件名>   命令引用的文件，测试把它们原样写进工作目录
唯一的替换：解释器 .venv/bin/python -> 当前解释器；启动命令里的端口 8765 -> 一个空闲端口。
"""
import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path

import pytest
from support import make_env, choose_mode, open_shares
from waybeside.menu_library import load_file, import_library
from waybeside import service

ROOT = Path(__file__).resolve().parents[1]
AGENTS = (ROOT / 'AGENTS.md').read_text(encoding='utf8')
VENDOR = re.compile('|'.join(['Cl' + 'aude', 'Cod' + 'ex', 'Open' + 'AI', 'Anthr' + 'opic', 'OPENAI' + '_API_KEY', 'Co-Authored' + '-By']))
ERRORS = ('SHARE_DISABLED', 'GROUP_NOT_ALLOWED', 'NO_MENU_LIBRARY', 'SCHEMA_FIELDS', 'IDENTITY_MISMATCH', 'UNKNOWN_GROUP',
          'UNKNOWN_MEAL', 'MEAL_SET', 'MEAL_SLOT_MISMATCH', 'LOCKED_MEAL', 'AVOIDED_MENU', 'RULE_EXCLUDED', 'REASON_CODE',
          'QUESTION_CODE', 'OPEN_QUESTIONS', 'DUPLICATE_KEY', 'INVALID_JSON', 'OUTPUT_LIMIT', 'DEPENDENCIES_CHANGED',
          'JOB_NOT_WAITING', 'UNKNOWN_JOB', 'EXPORT_REQUIRED',
          'MODE_NOT_CHOSEN', 'MODE_NOT_ALLOWED', 'NO_DATABASE', 'NOT_WAYBESIDE', 'SCHEMA_MISMATCH', 'SERVICE_NOT_RUNNING',
          'TODAY_MISMATCH', 'ROUTE_NOT_ALLOWED', 'DEMO_DIR_NOT_SAFE', 'OUTPUT_EXISTS', 'STORAGE_BUSY',
          'INVALID_FIELD', 'INVALID_JSON', 'CONFIRM_REQUIRED', 'RULE_CONFIRM_REQUIRED', 'CANNOT_GENERATE', 'NOTHING_TO_GENERATE', 'DISCLAIMER_REQUIRED')
TODAY = '2026-10-20'
PYTHON = '.venv/bin/python'
SH_KINDS = {'install', 'start', 'any', 'full', 'menu_only'}
DOC_ALLERGY = '示例过敏原话'


def blocks():
    return [(m.group(1), m.group(2), m.group(3))
            for m in re.finditer(r'^```(\w*)[ \t]*(\S*)[ \t]*\n(.*?)^```', AGENTS, re.S | re.M)]


def commands(*kinds):
    """按文档顺序给出 (kind, 命令行)。"""
    return [(tag, line.strip()) for lang, tag, body in blocks() if lang == 'sh' and tag in kinds
            for line in body.splitlines() if line.strip()]


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def test_agents_md_is_vendor_neutral_and_lists_codes():
    assert VENDOR.findall(AGENTS) == []
    assert 'DISCLAIMER.md' in AGENTS and '默认' in AGENTS and '待审' in AGENTS
    assert '程序升级后先重新 `export`' in AGENTS and 'STALE' in AGENTS
    for code in ERRORS + ('STALE', 'FILE_OR_DATE_ERROR', 'READY_TO_REVIEW'):
        assert code in AGENTS, code
    for category in ('menu_library', 'adoption_state', 'preferences', 'recent_meal_status', 'requirement_tags', 'age_band', 'course_progress'):
        assert category in AGENTS


def test_every_shell_block_is_tagged_and_commands_use_the_documented_interpreter():
    shell = [(tag, body) for lang, tag, body in blocks() if lang == 'sh']
    assert shell and all(tag in SH_KINDS for tag, _ in shell), [t for t, _ in shell]
    for kind, line in commands('any', 'full', 'menu_only'):
        assert line.startswith(PYTHON + ' '), line
    names = [tag for lang, tag, _ in blocks() if lang in ('json', 'python')]
    assert len(names) == len(set(names)) and all(names)


def test_no_private_nicknames_in_documents():
    banned = re.compile('|'.join(['管' + '家', '小时' + '工']))
    for path in list(ROOT.glob('*.md')) + list((ROOT / 'waybeside' / 'static').glob('*')) + [ROOT / 'waybeside' / 'i18n' / 'zh-CN.json']:
        assert banned.findall(path.read_text(encoding='utf8')) == [], path


def test_install_commands():
    """安装块：venv 真实创建；pip install 不联网执行，改验“依赖都已按版本装好”（等价于 pip 无事可做）。"""
    install = commands('install')
    assert [l.split()[0] for _, l in install] == ['python3.11', '.venv/bin/pip']
    assert (ROOT / 'requirements.txt').is_file() and (ROOT / 'run.py').is_file()
    for line in (ROOT / 'requirements.txt').read_text().splitlines():
        name, _, version = line.partition('==')
        assert metadata.version(name) == version, line
    assert sys.version_info[:2] == (3, 11), '文档要求 Python 3.11'


@pytest.fixture(params=['full', 'menu_only'])
def world(request, tmp_path):
    mode = request.param
    data = tmp_path / 'data'
    data.mkdir()
    work = tmp_path / 'work'
    work.mkdir()
    # 人做的事：确认免责、选使用方式（走网页同一套接口）；方式二下人还导入了菜单并打开了共享。
    human = make_env(data, today=TODAY, mode=None)
    choose_mode(human, mode)
    if mode == 'menu_only':
        import_library(data / 'waybeside.sqlite', load_file(ROOT / 'examples/menu/demo-menu-fictional.json'))
        open_shares(human[2], human[3])
    # 命令引用的文件
    for lang, tag, body in blocks():
        if lang in ('json', 'python') and tag:
            (work / tag).write_text(body, encoding='utf8')
    (work / 'examples/menu').mkdir(parents=True)
    shutil.copy(ROOT / 'examples/menu/demo-menu-fictional.json', work / 'examples/menu/demo-menu-fictional.json')
    shim = tmp_path / 'browser.sh'
    shim.write_text('#!/bin/sh\nprintf "%s" "$1" > "$URL_FILE"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, WAYBESIDE_DATA_DIR=str(data), WAYBESIDE_TODAY=TODAY, PYTHONPATH=str(ROOT),
               BROWSER=str(shim), URL_FILE=str(tmp_path / 'url.txt'), PYTHONDONTWRITEBYTECODE='1')
    # 文档里的启动命令，真实起一个服务进程（只替换端口）。
    (start,) = [line for _, line in commands('start')]
    port = free_port()
    argv = shlex.split(start.replace(PYTHON, sys.executable).replace('8765', str(port)))
    server = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(200):
            if service.read(data):
                break
            assert server.poll() is None, '服务没有起来'
            time.sleep(0.05)
        assert service.read(data)['port'] == port
        yield dict(mode=mode, data=data, work=work, env=env, human=human, tmp=tmp_path, port=port)
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


def sh(world, line, split_pipeline=False):
    if split_pipeline:
        line = re.split(r' \| | > ', line)[0]
    command = line.replace(PYTHON, shlex.quote(sys.executable))
    r = subprocess.run(['/bin/bash', '-c', 'set -o pipefail; ' + command], cwd=world['work'], env=world['env'],
                       capture_output=True, text=True)
    return r


def agent_out(result):
    return json.loads(result.stdout)


def test_every_command_in_agents_md_runs_in_both_modes(world):
    mode = world['mode']
    outputs = []
    for kind, line in commands('any', 'full', 'menu_only'):
        if kind == 'full' and mode == 'menu_only':
            # 方式二下，方式一章的命令必须被拒，并带可转述的话。
            if 'waybeside.agent_door' not in line:
                continue
            r = sh(world, line, split_pipeline=True)
            out = agent_out(r)
            assert r.returncode == 2 and out['error'] == 'MODE_NOT_ALLOWED', (line, r.stdout, r.stderr)
            assert out['say'].startswith('你选的是自己记录、我只帮挑菜单')
            continue
        r = sh(world, line)
        assert r.returncode == 0, (line, r.stdout, r.stderr)
        if '> ' not in line and 'waybeside.agent_door' in line:
            out = agent_out(r)
            assert out['data_dir'] == str(world['data']) and out['demo'] is False
            outputs.append((line, out))
    by = {line: out for line, out in outputs}
    status = next(out for line, out in outputs if line.endswith('agent_door status'))
    assert status['assistant_mode'] == mode and status['service']['running'] is True and status['service']['port'] == world['port']
    # open-url：登录码只交给浏览器，命令自己不打印
    opened = next(out for line, out in outputs if line.endswith('open-url'))
    url = (world['tmp'] / 'url.txt').read_text()
    code = url.split('#login=')[1]
    assert opened['opened'] is True and f":{world['port']}/#login=" in url and code not in json.dumps(opened)
    export = next(out for line, out in outputs if ' export ' in line + ' ' and '--output' in line)
    assert Path(export['package']).is_file()
    submit = next(out for line, out in outputs if ' submit ' in line)
    assert submit['status'] == 'READY_TO_REVIEW' and submit['adopted'] is False
    # 免责与方式始终是人设的；命令没有改动它们
    state = world['human'][3]().json
    assert state['disclaimer_accepted'] is True and state['assistant_mode'] == mode
    if mode == 'full':
        check_full_effects(world, by)


def check_full_effects(world, by):
    human = world['human']
    state = human[3]().json
    assert state['onboarded'] is True
    assert state['course_revision'] >= 1
    first = next(s for s in state['treatment_session'] if s['id'] == 1)
    assert first['status'] == 'completed' and first['confirmation_source'] == 'agent'
    aux = next(o for o in state['observation'] if o['kind'] == 'aux')
    assert [r['recorded_by'] for r in aux['content']['labs']] == ['agent']
    daily = [o for o in state['observation'] if o['kind'] == 'daily']
    assert daily and {o['recorded_by'] for o in daily} == {'agent'}
    menu = human[3]('menu?date=2026-10-21').json['state']
    assert menu['adopted']['group_id'] == '01'
    dump = json.loads((world['work'] / 'dump.json').read_text(encoding='utf8'))
    assert dump['settings']['diet']['allergy_reminders'] == [DOC_ALLERGY]
    assert next(o for o in dump['summary']['observation'] if o['id'] == 'aux')['content']['labs'][0]['metric'] == '示例指标 A'
    backup = world['work'] / 'backup.sqlite'
    assert backup.is_file() and backup.stat().st_size > 0
    # 助手的这些写入，页面“助手最近做了什么”都能看到，且只有动作与日期
    recent = human[3]('assistant-activity').json['recent']
    assert {'questionnaire', 'treatment_session.complete', 'daily', 'labs', 'menu_adopt'} <= {r['kind'] for r in recent}


def test_documented_error_exit_code(tmp_path):
    make_env(tmp_path)
    env = dict(os.environ, WAYBESIDE_DATA_DIR=str(tmp_path), PYTHONPATH=str(ROOT))
    r = subprocess.run([sys.executable, '-m', 'waybeside.agent_door', 'export', '--date', '2026-10-17'],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 2 and json.loads(r.stdout)['error'] == 'SHARE_DISABLED'
