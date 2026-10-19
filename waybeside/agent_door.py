"""本机助手的命令行入口。

所有子命令输出单个 JSON 对象，含 data_dir（解析后的绝对路径）与 demo（布尔），供助手核对写的是哪个库。
本入口只读检查数据库：不迁移、不建库；升级只在启动服务时发生。
"""
import argparse
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlsplit
import webbrowser

from . import login, service
from .access import AGENT_ENDPOINTS
from .menu_door import CandidateError
from .storage import (audit, MODE_SAYS, MODES, check_demo_directory, connect, data_directory, encode, is_demo,
                      schema_version, backup as backup_database)

SAYS = dict(MODE_SAYS, **{
    'NO_DATABASE': '这里还没有记录库：请先启动 WayBeside 服务，并在网页里完成第一步。',
    'NOT_WAYBESIDE': '这个目录里的库不是 WayBeside 的，请确认数据目录设置。',
    'SCHEMA_MISMATCH': '记录库的版本和程序不一致，请重启服务完成升级。',
    'STORAGE_BUSY': '记录正在被网页或另一个操作使用，请稍后再试。',
    'TODAY_MISMATCH': '助手和网页服务对“今天”的设置不一致，请用同一个 WAYBESIDE_TODAY 设置后重试。',
    'SERVICE_NOT_RUNNING': 'WayBeside 服务没有在运行，请先启动服务。',
    'ROUTE_NOT_ALLOWED': '这个接口不对助手开放，或者接口名写错了。请对照 AGENTS.md 里的接口列表；需要人做的事，请转告他在网页上做。',
    'SHARE_DISABLED': '你还没有开启“菜单库”共享。请到网页「设置 → 导出给助手」里打开它，我才能挑菜单。',
    'OUTPUT_INSIDE_REPO': '这个输出位置在应用目录或代码仓库里，资料不能放在那里。请换到仓库以外的工作目录。',
    'JOB_NOT_WAITING': '这个任务已经不在等待提交了（可能已经提交过、被新的 export 替换或已失效）。请重新 export。',
    'DEMO_DIR_NOT_SAFE': '演示数据不能放在真实数据目录里，未执行。',
    'OUTPUT_EXISTS': '输出文件已存在，请换一个新文件名。',
    'FILE_OR_DATE_ERROR': '文件或日期不正确。',
})
OPEN_MODES = {'status', 'open-url'}
ASSISTANT_MODES = {'menu_only': {'export', 'submit'}, 'full': {'export', 'submit', 'api', 'dump', 'backup'}}
START_COMMAND = 'python run.py'


def export_package(path, target, output=None):
    """菜单库正门；没有菜单库时返回 NO_MENU_LIBRARY。"""
    target = date.fromisoformat(target).isoformat()
    from . import menu_door
    return menu_door.export_package(path, target, output)


def submit_candidate(path, filename):
    from . import menu_door
    return menu_door.submit_candidate(path, filename)


def pinned_today():
    return os.environ.get('WAYBESIDE_TODAY') or None


def effective_today():
    return pinned_today() or date.today().isoformat()


class Failure(Exception):
    def __init__(self, code, status='FAILED', **extra):
        self.code, self.status, self.extra = code, status, extra
        super().__init__(code)


def inspect(path):
    """只读检查：库存在、是 WayBeside 的、版本与代码一致。返回库里的 metadata。"""
    if not path.is_file():
        raise Failure('NO_DATABASE')
    try:
        with connect(path.resolve().as_uri() + '?mode=ro') as db:
            meta = dict(db.execute('SELECT key,value FROM metadata').fetchall())
    except sqlite3.OperationalError as error:
        if 'locked' in str(error) or 'busy' in str(error):
            raise Failure('STORAGE_BUSY', status=503) from error
        raise Failure('NOT_WAYBESIDE') from error
    except sqlite3.DatabaseError as error:
        raise Failure('NOT_WAYBESIDE') from error
    if meta.get('app_id') != 'waybeside':
        raise Failure('NOT_WAYBESIDE')
    if meta.get('schema_version') != str(schema_version()):
        raise Failure('SCHEMA_MISMATCH')
    return meta


def mode_of(meta):
    return meta.get('assistant_mode') if meta.get('assistant_mode') in MODES else 'unset'


def gate(command, meta):
    """方式矩阵：unset 只放行 status/open-url；menu_only 只放行 export/submit；full 全部。"""
    mode = mode_of(meta)
    if command in OPEN_MODES:
        return mode
    if mode == 'unset':
        raise Failure('MODE_NOT_CHOSEN')
    if command not in ASSISTANT_MODES.get(mode, ()):
        raise Failure('MODE_NOT_ALLOWED')
    return mode


def check_today(directory):
    """服务在跑且与本命令的“今天”设置不同就拒绝；服务没跑则跳过，标注日期来源是命令行。"""
    info = service.read(directory)
    if info is None:
        return {'today_source': 'cli', 'today': effective_today()}
    if info.get('today_pinned') != pinned_today():
        raise Failure('TODAY_MISMATCH')
    return {'today_source': 'service', 'today': effective_today()}


def make_client(path):
    """进程内调用同一个 app：身份是 agent，其余校验（Origin、cookie、CSRF）与网页完全相同。"""
    from . import create_app
    app = create_app(path, today=pinned_today(), actor='agent', migrate=False, issue_code=False)
    app.logger.disabled = True
    client = app.test_client()
    client.set_cookie('waybeside_session', app.config['TOKEN'], domain='127.0.0.1')
    origin = 'http://' + app.config['LOCAL_HOST']
    return app, client, origin, {'Origin': origin, 'X-CSRF-Token': app.config['CSRF']}


def route_allowed(app, method, raw):
    parts = urlsplit(raw)
    bad = (parts.scheme or parts.netloc or parts.fragment or not parts.path.startswith('/api/')
           or any(c in parts.path for c in '%\\;') or '//' in parts.path or '/.' in parts.path)
    if bad or method not in ('GET', 'POST'):
        return False
    from werkzeug.exceptions import HTTPException
    try:
        endpoint, _ = app.url_map.bind('127.0.0.1').match(parts.path, method=method)
    except HTTPException:
        return False
    return endpoint in AGENT_ENDPOINTS


def call(client, origin, headers, method, raw, body=None):
    kwargs = {'method': method, 'base_url': origin, 'headers': headers}
    if method == 'POST':
        kwargs['json'] = {} if body is None else body
    response = client.open(raw, **kwargs)
    if response.status_code >= 500:
        raise Failure('STORAGE_BUSY', status=503)
    payload = response.get_json(silent=True)
    if payload is None:
        payload = {'error': f'HTTP_{response.status_code}'}
    return response.status_code, payload


def read_body(source):
    text = sys.stdin.read() if source == '-' else Path(source).read_text(encoding='utf-8')
    return json.loads(text)


APP_DIR = Path(__file__).resolve().parents[1]


def output_is_inside_a_repository(output):
    """输出文件落在应用目录内，或任何 Git 工作区内（向上找 .git）：健康资料不能写到那里。"""
    target = Path(os.path.realpath(Path(output).expanduser()))
    if target == APP_DIR or APP_DIR in target.parents:
        return True
    return any((folder / '.git').exists() for folder in target.parents)


def record_export(path, kind):
    """dump / backup 成功后留一条助手动作的 audit（只记动作名，不记内容），让网页“助手最近做了什么”看得到。"""
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        audit(db, 'assistant_export_' + kind, kind, None, None, actor='agent', fields=[kind], version=1)


def write_new(output, content):
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write(content)


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args, directory, path):
    """返回要打印的结果字典（不含 data_dir / demo）。"""
    command = args.command
    login.expire(directory)      # 顺带清掉过期没用的登录码文件
    if command in OPEN_MODES:
        info = service.read(directory)
        if command == 'status':
            running = {'running': True, 'port': info.get('port'), 'pid': info.get('pid')} if info else {'running': False}
            try:
                meta = inspect(path)
            except Failure as failure:
                failure.extra['service'] = running
                raise
            result = {'status': 'OK', 'assistant_mode': mode_of(meta), 'schema_version': int(meta['schema_version']),
                      'database': str(path), 'service': running}
            if result['assistant_mode'] == 'unset':
                result['say'] = MODE_SAYS['MODE_NOT_CHOSEN']       # 助手要转告人的话，不必先撞一次错误才拿到
            return result
        if info is None:
            raise Failure('SERVICE_NOT_RUNNING', start_command=START_COMMAND)
        # 新码只交给浏览器；本命令不打印、不自己使用。
        url = f"http://127.0.0.1:{info['port']}/#login={login.issue(directory)}"
        try:
            chosen = mode_of(inspect(path)) != 'unset'
        except Failure:
            chosen = False
        say = ('我已经把网页重新打开了，请在新打开的窗口里继续。' if chosen
               else '我已经把网页打开了。请你先看免责声明，再选一种使用方式，选好了告诉我。')
        return {'status': 'OK', 'opened': bool(webbrowser.open(url)), 'port': info['port'], 'say': say}
    meta = inspect(path)
    gate(command, meta)
    extra = check_today(directory)
    # export / dump / backup 的 --output 用同一个检查（不传 --output 的 export 仍写数据目录下的 agent_inbox）。
    if command in ('export', 'backup', 'dump') and getattr(args, 'output', None) and output_is_inside_a_repository(args.output):
        raise Failure('OUTPUT_INSIDE_REPO')          # 先拦，库和文件系统都不动
    if command == 'export':
        target = args.date or (date.fromisoformat(effective_today()) + timedelta(days=1)).isoformat()
        return dict(export_package(path, target, args.output), **extra)
    if command == 'submit':
        return dict(submit_candidate(path, args.file), **extra)
    if command == 'backup':
        try:
            backup_database(path, args.output)
        except FileExistsError as error:
            raise Failure('OUTPUT_EXISTS') from error
        record_export(path, 'backup')
        return {'status': 'OK', 'output': str(Path(args.output).resolve()), 'sha256': sha256_file(args.output)}
    app, client, origin, headers = make_client(path)
    if command == 'api':
        method = args.method.upper()
        if not route_allowed(app, method, args.path):
            raise Failure('ROUTE_NOT_ALLOWED', status=403)
        try:
            body = read_body(args.json) if args.json else None
        except json.JSONDecodeError as error:
            raise Failure('INVALID_JSON', line=error.lineno, column=error.colno) from error
        code, payload = call(client, origin, headers, method, args.path, body)
        return dict({'status': code, 'body': payload}, **extra)
    # dump：健康记录全量 = summary ∪ settings（嵌套放置，避免同名字段冲突）。完整迁移请用 backup。
    parts = {}
    for name in ('summary', 'settings'):
        code, parts[name] = call(client, origin, headers, 'GET', '/api/' + name)
        if code != 200:
            raise Failure('STORAGE_BUSY', status=503)
    content = encode(parts)
    try:
        write_new(args.output, content)
    except FileExistsError as error:
        raise Failure('OUTPUT_EXISTS') from error
    record_export(path, 'dump')
    return {'status': 'OK', 'output': str(Path(args.output).resolve()), 'sha256': sha256_file(args.output)}


def build_parser():
    parser = argparse.ArgumentParser(description='本机助手对接。')
    parser.add_argument('--demo', action='store_true', help='使用演示数据目录')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    sub.add_parser('open-url')
    exporter = sub.add_parser('export')
    exporter.add_argument('--date')
    exporter.add_argument('--output', help='新文件路径；已有文件不会覆盖')
    sub.add_parser('submit').add_argument('file')
    api = sub.add_parser('api')
    api.add_argument('method')
    api.add_argument('path')
    api.add_argument('--json', help='请求正文的 JSON 文件，- 表示标准输入')
    sub.add_parser('dump').add_argument('--output', required=True, help='新文件路径')
    sub.add_parser('backup').add_argument('--output', required=True, help='新文件路径')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    directory = data_directory(args.demo)
    path = directory / 'waybeside.sqlite'
    demo = args.demo
    try:
        if args.demo or is_demo(path):
            demo = True
            try:
                check_demo_directory(directory)
            except RuntimeError as error:
                raise Failure('DEMO_DIR_NOT_SAFE') from error
        result = run(args, directory, path)
    except Failure as failure:
        result = dict({'status': failure.status, 'error': failure.code}, **failure.extra)
    except CandidateError as error:
        result = {'status': error.status, 'error': error.code}
        if error.job_status:
            result['job_status'] = error.job_status
        result.update(error.details)
    except (OSError, ValueError, sqlite3.Error) as error:
        busy = isinstance(error, sqlite3.OperationalError)
        result = {'status': 503 if busy and args.command == 'api' else 'FAILED',
                  'error': 'STORAGE_BUSY' if busy else 'FILE_OR_DATE_ERROR'}
    if result.get('error') in SAYS:
        result['say'] = SAYS[result['error']]
    result.update(data_dir=str(directory), demo=demo)
    print(encode(result))
    status = result.get('status')
    return 2 if 'error' in result or (isinstance(status, int) and not 200 <= status < 300) else 0


if __name__ == '__main__':
    raise SystemExit(main())
