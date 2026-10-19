"""SQLite 事实主库；迁移和一致性备份均不依赖 Agent。首启不写入任何内容。"""
import json
import os
from pathlib import Path
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from flask import abort, g, has_request_context, jsonify, make_response

ROOT = Path(__file__).resolve().parents[1]

APP_ID = 'waybeside'

def default_directory(demo=False):
    name = 'WayBeside-Demo' if demo else 'WayBeside'
    if sys.platform == 'win32':
        return Path(os.environ['LOCALAPPDATA']) / name
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support' / name
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / name

def data_directory(demo=False):
    override = os.environ.get('WAYBESIDE_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    return default_directory(demo)

def migration_files():
    return sorted((ROOT / 'waybeside/migrations').glob('*.sql'))

def schema_version():
    return max(int(f.name.split('_')[0]) for f in migration_files())

def is_demo(path):
    """目录内的库是否带演示标记；库不存在或不可读都算否。"""
    path = Path(path)
    if not path.is_file():
        return False
    try:
        with connect(f'file:{path}?mode=ro') as db:
            row = db.execute("SELECT value FROM metadata WHERE key='demo'").fetchone()
    except sqlite3.Error:
        return False
    return bool(row and row[0] == '1')

def check_demo_directory(directory):
    """--demo 的目录保护：只允许空目录或已带演示标记的库，且绝不落在真实默认目录。"""
    directory = Path(directory).expanduser().resolve()
    if directory == default_directory(False).resolve():
        raise RuntimeError('DEMO_DIR_NOT_SAFE：演示数据不能写入真实数据目录。')
    if directory.exists() and any(p.name != '.instance.lock' for p in directory.iterdir()) and not is_demo(directory / 'waybeside.sqlite'):
        raise RuntimeError('DEMO_DIR_NOT_SAFE：该目录非空且不是演示数据目录，未启动。')
    return directory

BUSY_TIMEOUT = float(os.environ.get('WAYBESIDE_BUSY_TIMEOUT_S', '10'))
MODES = ('unset', 'menu_only', 'full')
MODE_SAYS = {
    'MODE_NOT_CHOSEN': '请打开 WayBeside 的网页，在第一页选一下你希望我帮你做多少。',
    'MODE_NOT_ALLOWED': '你选的是自己记录、我只帮挑菜单，所以这件事要你在网页上做。想换方式可以在设置里改。',
}

def assistant_mode(db):
    row = db.execute("SELECT value FROM metadata WHERE key='assistant_mode'").fetchone()
    return row[0] if row and row[0] in MODES else 'unset'

def assistant_mode_version(db):
    row = db.execute("SELECT value FROM metadata WHERE key='assistant_mode_version'").fetchone()
    try:
        return int(row[0]) if row else 0
    except ValueError:
        return 0

def mode_error(mode):
    """助手在当前方式下不能做时的错误码；unset 与 menu_only 的说法不同。"""
    return 'MODE_NOT_CHOSEN' if mode == 'unset' else 'MODE_NOT_ALLOWED'

def current_actor():
    """请求内取 g.actor（HTTP 面恒为 local-user，只有命令行注入的 app 才是 agent）；请求外是程序自己。"""
    return g.get('actor', 'local-user') if has_request_context() else 'local-program'

class Guarded(sqlite3.Connection):
    """事务一开始就再读一次方式：助手的请求在 BEGIN 之后若方式已不是 full 就整体放弃。"""
    def execute(self, sql, *args):
        cursor = super().execute(sql, *args)
        # 任何以 BEGIN 开头的语句（BEGIN / BEGIN IMMEDIATE / BEGIN DEFERRED / 带分号、大小写不同）都是事务开始。
        if isinstance(sql, str) and sql.lstrip().upper().startswith('BEGIN') \
                and has_request_context() and g.get('actor') == 'agent':
            mode = assistant_mode(self)
            if mode != 'full':
                code = mode_error(mode)
                abort(make_response(jsonify(code=code, error=MODE_SAYS[code], say=MODE_SAYS[code]), 403))
        return cursor

@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=BUSY_TIMEOUT, uri=True, factory=Guarded)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()

def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)

# 问卷类实体的 audit 只记字段名，不记值：old_value/new_value 恒为 NULL，
# 事实写在 detail：{fields, version, at, actor, ...无值的标记}。
REDACTED_ENTITIES = ('questionnaire', 'agent_share', 'settings', 'assistant_mode')

def audit(db, entity, entity_id, old, new, actor=None, fields=None, version=None, extra=None):
    actor = actor or current_actor()
    at = datetime.now(timezone.utc).isoformat()
    if entity in REDACTED_ENTITIES:
        detail = dict(extra or {}, fields=sorted(fields or []), version=version, at=at, actor=actor)
        db.execute('INSERT INTO audit(actor,occurred_at,entity,entity_id,old_value,new_value,detail) VALUES(?,?,?,?,NULL,NULL,?)',
                   (actor, at, entity, str(entity_id), encode(detail)))
        return
    db.execute('INSERT INTO audit(actor,occurred_at,entity,entity_id,old_value,new_value) VALUES(?,?,?,?,?,?)',
               (actor, at, entity, str(entity_id),
                encode(old) if old is not None else None, encode(new)))

BLOCKS = ('basic', 'treatment', 'diet', 'habit')
SHARE_CATEGORIES = ('menu_library', 'adoption_state', 'preferences', 'recent_meal_status',
                    'requirement_tags', 'age_band', 'course_progress')

def is_onboarded(db):
    """问卷四块都已完成（填写或明确跳过）才算完成首次设置。"""
    return db.execute('SELECT COUNT(*) FROM questionnaire_answer WHERE completed=1').fetchone()[0] == len(BLOCKS)

def disclaimer_accepted(db):
    return db.execute("SELECT 1 FROM metadata WHERE key='disclaimer_ack'").fetchone() is not None

def share_flags(db):
    return {r['category']: bool(r['enabled']) for r in db.execute('SELECT category,enabled FROM agent_share')}

def effective_flags(db):
    """助手实际能拿到的类别：full 时七类全开（无视开关），menu_only 取本人逐项设的值。"""
    if assistant_mode(db) == 'full':
        return {c: True for c in SHARE_CATEGORIES}
    return share_flags(db)

def questionnaire(db, block):
    """读取某块已存答案（只供本进程内部使用；绝不直接返回给任何 GET）。"""
    row = db.execute('SELECT content FROM questionnaire_answer WHERE block=?', (block,)).fetchone()
    content = json.loads(row['content']) if row else {}
    return {} if content.get('skipped') or content.get('projected') else content

def initialize(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY)')
        for file in migration_files():
            version = int(file.name.split('_')[0])
            if not db.execute('SELECT 1 FROM schema_migrations WHERE version=?', (version,)).fetchone():
                db.executescript('BEGIN IMMEDIATE;\n' + file.read_text() + f'\nINSERT INTO schema_migrations VALUES({version});\nCOMMIT;')
        db.execute('BEGIN IMMEDIATE')
        db.execute("INSERT OR IGNORE INTO metadata VALUES('app_id',?)", (APP_ID,))
        db.execute("INSERT INTO metadata VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(schema_version()),))
    if os.name != 'nt':
        path.chmod(0o600)

def backup(path, destination):
    """SQLite backup API 提供一致性快照；调用者只选空白目标。"""
    destination = Path(destination)
    with destination.open('xb'):
        pass
    try:
        with connect(path) as source, connect(destination) as target:
            source.backup(target)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    if os.name != 'nt':
        destination.chmod(0o600)

def restore(source, directory):
    """仅恢复到空目录，不覆盖任何现有数据库。"""
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError('恢复目录必须为空')
    with connect(f'file:{Path(source).resolve()}?mode=ro') as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('备份完整性检查失败')
        try:
            meta = dict(db.execute('SELECT key,value FROM metadata').fetchall())
        except sqlite3.Error:
            raise ValueError('不是本应用备份')
        if meta.get('app_id') != APP_ID:
            raise ValueError('不是本应用备份')
        try:
            newer = int(meta.get('schema_version', 0)) > schema_version()
        except ValueError:
            newer = True
        if newer:
            raise ValueError('备份来自更新版本的程序，请先升级')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / 'waybeside.sqlite'
    backup(source, target)
    # 恢复后助手方式重置为 unset（免责确认保留），由人打开网页重新选一次。
    with connect(target) as db:
        db.execute("INSERT INTO metadata VALUES('assistant_mode','unset') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        db.execute("INSERT INTO metadata VALUES('assistant_mode_version','0') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        db.execute("DELETE FROM metadata WHERE key='upgrade_notice'")
