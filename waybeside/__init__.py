"""仅回环运行的合成本地记录 API。"""
from datetime import date, datetime
import io
import json
import secrets
import sqlite3
import tempfile
from pathlib import Path
from uuid import uuid4
from flask import Flask, abort, g, jsonify, make_response, request, send_file
from . import login as login_code
from .access import AGENT_ENDPOINTS
from .storage import (FieldError, field, initialize, connect, audit, encode, backup, assistant_mode, current_actor,
                      mode_error, MODE_SAYS)

LAB_KEYS = ('date', 'metric', 'unit', 'lab', 'value', 'low', 'high')

FIELDS = {
    **{k: {'unknown', 'done', 'partial', 'not_done'} for k in ('diet', 'exercise', 'meditation')},
    'sleep': {'unknown', 'good', 'poor'},
    'mood': {'unknown', '平静', '有点担心', '心情不错', '说不清'},
    'energy': {'unknown', '还可以', '有点累', '想多休息'},
}

def day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('日期格式不正确')
    return date.fromisoformat(value)

def exact(data, keys):
    if not isinstance(data, dict):
        raise ValueError('拒绝未知字段')
    unknown = sorted(str(k) for k in set(data) - set(keys))
    if unknown:
        raise FieldError(', '.join(unknown[:5]))     # 只报多出来的字段名，不报值

def text(value, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('文字类型或长度不正确')
    return value

def create_app(db_path, port=8765, token=None, today=None, actor='local-user', migrate=True, issue_code=True):
    app = Flask(__name__, static_url_path='', static_folder='static')
    app.config.update(MAX_CONTENT_LENGTH=2*1024*1024, DB_PATH=Path(db_path),
                      TOKEN=token or secrets.token_urlsafe(32), CSRF=secrets.token_urlsafe(32),
                      LOCAL_HOST=f'127.0.0.1:{port}', TODAY=today, ACTOR=actor, LOGIN_CODE=None)
    if migrate:
        initialize(db_path)
    # 一次性登录码只在服务（或测试）里发放；命令行进程内调用的 app 不发码，也不碰已在跑的服务的码。
    if issue_code:
        app.config['LOGIN_CODE'] = login_code.issue(Path(db_path).parent)
    from .menu_library import register as register_menu
    register_menu(app)
    from .menu_door import register as register_menu_door
    register_menu_door(app)
    from .sessions import register as register_sessions, state as course_state, summary as course_summary
    register_sessions(app,day,exact)
    from .questionnaire import register as register_questionnaire
    register_questionnaire(app,day,exact)
    from .activity import register as register_activity
    register_activity(app)

    def current_day():
        return app.config['TODAY'] or date.today().isoformat()

    @app.before_request
    def security():
        # 身份只由构造 app 的程序决定；HTTP 请求里的任何头、参数、正文都不参与。
        g.actor = app.config['ACTOR']
        if request.host != app.config['LOCAL_HOST']:
            abort(403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if request.headers.get('Origin') != 'http://' + app.config['LOCAL_HOST']:
                abort(403)
            if not request.is_json:
                abort(415)
        if request.path.startswith('/api/') and request.path != '/api/login':
            if not secrets.compare_digest(request.cookies.get('waybeside_session', ''), app.config['TOKEN']):
                abort(401)
            if request.method not in ('GET', 'HEAD') and not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), app.config['CSRF']):
                abort(403)
        if g.actor == 'agent':
            # 命令行 api 通道：端点白名单 + 方式检查（写入端点在事务内还会再查一次）。
            if request.endpoint not in AGENT_ENDPOINTS:
                return make_response(jsonify(code='ROUTE_NOT_ALLOWED', error='这个接口不对助手开放。'), 403)
            with connect(db_path) as db:
                mode = assistant_mode(db)
            if mode != 'full':
                code = mode_error(mode)
                return make_response(jsonify(code=code, error=MODE_SAYS[code], say=MODE_SAYS[code]), 403)

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff',
            'Referrer-Policy':'no-referrer', 'X-Frame-Options':'DENY',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"})
        return response

    @app.errorhandler(FieldError)
    def invalid_field(error):
        return jsonify(code='INVALID_FIELD', field=error.field, error='资料不符合记录约束，请检查日期、字段、版本和关联。'), 400

    @app.errorhandler(ValueError)
    @app.errorhandler(TypeError)
    @app.errorhandler(KeyError)
    @app.errorhandler(sqlite3.IntegrityError)
    def invalid(error):
        return jsonify(error='资料不符合记录约束，请检查日期、字段、版本和关联。'), 400

    @app.get('/i18n-data.js')
    def i18n_data():
        import json
        raw = json.loads((Path(__file__).parent / 'i18n' / 'zh-CN.json').read_text(encoding='utf-8'))
        body = 'window.I18N=' + json.dumps(raw, ensure_ascii=False).replace('</', '<\\/') + ';'
        return app.response_class(body, mimetype='text/javascript')

    @app.get('/')
    def index():
        return app.send_static_file('index.html')

    @app.post('/api/login')
    def login():
        data = request.get_json()
        exact(data, ['token'])
        # token 字段里放的是一次性登录码：对且未过期则立即作废。
        if g.actor != 'local-user' or not login_code.consume(Path(db_path).parent, data.get('token')):
            abort(401)
        response = jsonify(csrf=app.config['CSRF'])
        response.set_cookie('waybeside_session', app.config['TOKEN'], httponly=True, samesite='Strict')
        return response

    @app.get('/api/session')
    def session():
        return jsonify(csrf=app.config['CSRF'])

    def state(db):
        db.execute('BEGIN')
        return course_state(db,current_day())

    @app.get('/api/state')
    def get_state():
        with connect(db_path) as db:
            return jsonify(state(db))

    @app.post('/api/observations')
    def observation():
        data = request.get_json()
        exact(data, [*FIELDS, 'id','version','date','time','session','minutes','note','symptoms'])
        observed = day(data['date'])
        if observed > day(current_day()):
            raise ValueError('不能记录未来事实')
        clock = text(data['time'], 5)
        datetime.strptime(clock, '%H:%M')
        if len(clock) != 5:
            raise ValueError('时间格式')
        content = {k: data.get(k, 'unknown') for k in FIELDS}
        # 症状条目由用户自选自加：名称为用户自填文字（≤20 字，最多 20 项），状态只有 未记录/没有/有。
        symptoms = data.get('symptoms', {})
        if not isinstance(symptoms, dict) or len(symptoms) > 20:
            raise ValueError('症状条目')
        for name, state in symptoms.items():
            if not 1 <= len(name.strip()) <= 20 or name != name.strip() or not isinstance(state, str) or state not in ('unknown', 'none', 'some'):
                raise ValueError('症状条目')
        content['symptoms'] = symptoms
        for k, values in FIELDS.items():
            if content[k] not in values:
                raise FieldError(k)
        minutes = data.get('minutes', '')
        if minutes != '' and (not isinstance(minutes, str) or not minutes.isdigit() or not 0 <= int(minutes) <= 1440):
            raise ValueError('分钟数格式')
        session_id = int(data['session']) if data.get('session') else None
        content.update(date=str(observed), time=clock, session=str(session_id) if session_id else '',
                       minutes=minutes, note=text(data.get('note',''),500))
        identifier = data.get('id') or str(uuid4())
        with connect(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            if session_id and not db.execute('SELECT 1 FROM treatment_session WHERE id=?',(session_id,)).fetchone():
                raise ValueError('没有这条治疗记录')
            old = db.execute('SELECT * FROM observation WHERE id=?', (identifier,)).fetchone()
            if data.get('id') and (not old or old['kind'] != 'daily' or type(data.get('version')) is not int or old['version'] != data.get('version')):
                abort(409)
            version = old['version']+1 if old else 1
            db.execute('INSERT INTO observation(id,session_id,occurred_at,kind,content,version,recorded_by) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET session_id=excluded.session_id, occurred_at=excluded.occurred_at, content=excluded.content, version=excluded.version, recorded_by=excluded.recorded_by',
                       (identifier,session_id,f'{observed}T{clock}','daily',encode(content),version,current_actor()))
            audit(db,'observation',identifier,dict(old) if old else None,dict(content,id=identifier,version=version))
        return jsonify(**content,id=identifier,version=version)

    @app.post('/api/aux')
    def auxiliary():
        data = request.get_json()
        exact(data, ['labs','question','rhythm','celebration','version'])
        text(data['question'],1200)
        if type(data['celebration']) is not bool:
            raise ValueError('设置格式')
        exact(data['rhythm'], ['blood','chemistry'])
        # 检查节奏由用户自填天数（1-365），留空即未设置；不内置任何医学节奏。
        for key in ('blood','chemistry'):
            value=data['rhythm'].get(key)
            if value is not None and (type(value) is not int or not 1<=value<=365):
                raise ValueError('节奏格式')
        if not isinstance(data['labs'],list) or len(data['labs'])>1000:
            raise ValueError('检查条数')
        for row in data['labs']:
            exact(row,['date','metric','unit','lab','value','low','high'])
            if day(row['date'])>day(current_day()):
                raise ValueError('未来检查')
            if not text(row['metric'],40).strip():
                raise ValueError('指标')   # 检验指标名由用户自填（≤40 字），不设白名单
            for key,limit in (('unit',30),('lab',60)):
                if not text(row[key],limit).strip():
                    raise ValueError('必填')
            for key in ('value','low','high'):
                value=row[key]
                if value is not None and (type(value) not in (int,float) or not 0<=value<1e9):
                    raise ValueError('数值')
            if (row['low'] is None)!=(row['high'] is None) or (row['low'] is not None and row['low']>row['high']):
                raise ValueError('区间')
        with connect(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute("SELECT * FROM observation WHERE id='aux'").fetchone()
            if type(data.get('version')) is not int or data['version']!=(old['version'] if old else 0):
                abort(409)
            version=(old['version'] if old else 0)+1
            content={k:v for k,v in data.items() if k!='version'}
            # 检验逐条标注：与旧表里完全相同的行保留原记录者，新增或改动的行写当前身份。
            pool=[dict(r) for r in (json.loads(old['content']).get('labs',[]) if old else [])]
            labs=[]
            for row in data['labs']:
                same=next((i for i,r in enumerate(pool) if all(r.get(k)==row[k] for k in LAB_KEYS)),None)
                labs.append(dict(row,recorded_by=pool.pop(same).get('recorded_by','local-user') if same is not None else current_actor()))
            content['labs']=labs
            db.execute('INSERT INTO observation(id,session_id,occurred_at,kind,content,version,recorded_by) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET content=excluded.content,version=excluded.version,recorded_by=excluded.recorded_by',('aux',None,current_day(),'aux',encode(content),version,current_actor()))
            audit(db,'observation','aux',dict(old) if old else None,dict(content,version=version))
        return jsonify(version=version)

    @app.get('/api/summary')
    def summary():
        with connect(db_path) as db:
            db.execute('BEGIN')
            result = course_summary(db,current_day())
        return send_file(io.BytesIO(encode(result).encode()),mimetype='application/json',as_attachment=True,download_name='course-summary.json')

    @app.post('/api/backup')
    def export_backup():
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'backup.sqlite'
            backup(db_path,target)
            content=target.read_bytes()
        return send_file(io.BytesIO(content),mimetype='application/octet-stream',as_attachment=True,download_name='waybeside-backup.sqlite')

    @app.post('/api/exit')
    def exit_service():
        callback=app.config.get('STOP')
        if callback:
            callback()
        return jsonify(stopped=True)

    return app
