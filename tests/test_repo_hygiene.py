"""仓库卫生：logs/ 必须被忽略且不得有已跟踪文件。"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_gitignore_has_logs():
    lines = [l.strip() for l in (ROOT / '.gitignore').read_text(encoding='utf8').splitlines()]
    assert 'logs/' in lines
    assert 'tests/artifacts/' in lines


def test_no_tracked_logs():
    if not shutil.which('git') or not (ROOT / '.git').exists():
        pytest.skip('not a git checkout')
    out = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    assert [p for p in out if p.startswith('logs/')] == []
