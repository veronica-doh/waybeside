"""构建无个人数据的源码发行包；白名单见 INCLUDE。Windows 运行未实机验证。"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
# 白名单（pathspec 风格的 glob）：目录整树收录，根文件逐个列出；其余一律不进包。
INCLUDE = (
    'waybeside/**', 'examples/**', 'tools/**', 'docs/**', '.agents/**',
    'packaging/*.cmd', 'packaging/*.md',
    'run.py', 'requirements.txt', 'LICENSE', 'NOTICE', 'README.md', 'AGENTS.md',
    'DISCLAIMER.md', 'PRIVACY.md', 'SECURITY.md', 'CONTRIBUTING.md',
)
# 即使落在白名单目录内也剔除（缓存、系统杂文件、本机数据库）。
EXCLUDE_PARTS = {'__pycache__', '.DS_Store', 'node_modules', '.pytest_cache'}
EXCLUDE_SUFFIXES = ('.pyc', '.sqlite', '.db', '.sqlite3')
# 缺任何一项即构建失败，防止悄悄产出残缺包。
REQUIRED = (
    'run.py', 'requirements.txt', 'LICENSE', 'NOTICE', 'README.md', 'DISCLAIMER.md', 'PRIVACY.md',
    'waybeside/i18n/zh-CN.json', 'waybeside/static/index.html', 'waybeside/migrations/001_initial.sql',
    'examples/menu/demo-menu-fictional.json',
)


def tracked():
    """只信任 git 索引：未被跟踪的工作区文件（可能是维护者的私人文件）永远不进包。"""
    try:
        raw = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError('必须在 git 仓库内构建发行包（只收录已跟踪文件）') from error
    return {name for name in raw.decode('utf-8').split('\0') if name}


def files():
    index = tracked()
    result = set()
    for pattern in INCLUDE:
        glob = pattern + '/*' if pattern.endswith('/**') else pattern   # pathlib 的 ** 只匹配目录，需补 /*
        result.update(path for path in ROOT.glob(glob) if path.is_file() or path.is_symlink())
    kept = []
    for item in result:
        relative = item.relative_to(ROOT)
        if str(relative) not in index:
            continue
        if EXCLUDE_PARTS & set(relative.parts) or item.name.endswith(EXCLUDE_SUFFIXES):
            continue
        if item.is_symlink() or not item.resolve().is_relative_to(ROOT):
            raise ValueError(f'发行白名单文件路径异常：{relative}')
        kept.append(item)
    names = {str(p.relative_to(ROOT)) for p in kept}
    missing = [name for name in REQUIRED if name not in names]
    if missing:
        raise ValueError('发行包缺少必需文件：' + '、'.join(missing))
    return sorted(kept)


def build(destination):
    destination = Path(destination)
    receipt_path = destination.with_suffix(destination.suffix + '.sha256.json')
    if destination.exists() or receipt_path.exists():
        raise ValueError('发行包已存在，请使用新文件名，不覆盖旧版本')
    entries = {str(p.relative_to(ROOT)): p.read_bytes() for p in files()}
    manifest = {
        'format': 1, 'product': '伴程 · WayBeside', 'runtime': 'Python 3.11',
        'windows_validation': 'NOT_RUN', 'data': '不含任何数据；演示内容经 --demo 在本机生成',
        'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in entries.items()},
    }
    entries['MANIFEST.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in entries.items():
            item = zipfile.ZipInfo('waybeside/' + name, date_time=(2026, 10, 16, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            archive.writestr(item, raw)
    receipt = {
        'file': destination.name,
        'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
        'file_count': len(entries), 'windows_validation': 'NOT_RUN',
    }
    with receipt_path.open('x', encoding='utf-8') as out:
        json.dump(receipt, out, ensure_ascii=False, indent=2)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False))
