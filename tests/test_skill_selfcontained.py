"""nutrition-plan 技能与流程文档自洽：引用的路径都存在、命令能跑、不含原场景指纹、必引免责。"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / '.agents/skills/nutrition-plan/SKILL.md'
WORKFLOW = ROOT / 'docs/nutrition-baseline-workflow.md'
FILES = [SKILL, WORKFLOW]
# 自洽性检查不列举任何场景词：只查本机绝对路径、用户目录、厂商/运行时目录等“不可移植”痕迹。
NON_PORTABLE = re.compile('|'.join([
    '/' + 'Users/', '/' + 'home/[a-z]', r'[A-Za-z]:\\' + 'Users', '~/' + r'\.', r'\.' + 'co' + 'dex', r'\.' + 'cl' + 'aude',
    'Open' + 'AI', 'Anthro' + 'pic', 'Cl' + 'aude', 'Co' + 'dex']))
PATH_TOKEN = re.compile(r'(?<![\w/.])((?:tools|docs|examples|waybeside|\.agents)/[\w./-]*\w)')


@pytest.mark.parametrize('file', FILES, ids=lambda f: f.name)
def test_no_machine_specific_or_vendor_traces(file):
    assert NON_PORTABLE.findall(file.read_text(encoding='utf8')) == []


@pytest.mark.parametrize('file', FILES, ids=lambda f: f.name)
def test_disclaimer_referenced_and_exists(file):
    assert 'DISCLAIMER.md' in file.read_text(encoding='utf8')
    assert (ROOT / 'DISCLAIMER.md').is_file()


@pytest.mark.parametrize('file', FILES, ids=lambda f: f.name)
def test_markdown_links_resolve(file):
    for target in re.findall(r'\]\(([^)]+)\)', file.read_text(encoding='utf8')):
        if re.match(r'[a-z]+://', target):
            continue
        assert (file.parent / target.split('#')[0]).resolve().is_file(), (file.name, target)


@pytest.mark.parametrize('file', FILES, ids=lambda f: f.name)
def test_repo_relative_paths_exist(file):
    text = file.read_text(encoding='utf8')
    for token in set(PATH_TOKEN.findall(text)):
        if '$' in token:
            continue
        assert (ROOT / token).exists(), (file.name, token)


def test_skill_root_is_three_levels_up():
    assert SKILL.parents[3] == ROOT          # SKILL.md 里“向上三级定位仓库根目录”


def test_verify_menu_runs_on_example(tmp_path):
    out = tmp_path / 'check.json'
    r = subprocess.run([sys.executable, 'tools/verify_menu.py', '--menu', 'examples/menu/demo-menu-fictional.json',
                        '--out', str(out)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    report = json.loads(out.read_text(encoding='utf8'))
    assert report['mechanical_status'] == 'PASS' and report['clinical_status'] == 'NOT_ASSESSED'


def test_unittest_command_in_skill_passes():
    assert "python3 -m unittest discover -s tools -p 'test_verify_menu.py'" in SKILL.read_text(encoding='utf8')
    r = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tools', '-p', 'test_verify_menu.py'],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_example_menu_also_passes_menu_library_validate():
    from waybeside.menu_library import load_file
    load_file(ROOT / 'examples/menu/demo-menu-fictional.json')
