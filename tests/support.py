"""测试公用夹具：临时库、已登录客户端、按星期排期的虚构疗程。全部在 pytest 临时目录。"""
import json
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waybeside import create_app

ORIGIN = 'http://127.0.0.1:8765'
WEEKDAYS = [0, 2, 4]
START = '2031-03-03'          # 周一
TODAY = '2031-12-31'

def set_mode(db_path, mode):
    """测试夹具：直接设定助手方式（不经网页，不产生 audit）。"""
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO metadata VALUES('assistant_mode',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (mode,))

def make_env(tmp_path, today=TODAY, mode='menu_only'):
    app = create_app(tmp_path / 'waybeside.sqlite', today=today)
    if mode:
        set_mode(tmp_path / 'waybeside.sqlite', mode)
    app.config['TESTING'] = True
    client = app.test_client()
    login = client.post('/api/login', base_url=ORIGIN, json={'token':app.config['LOGIN_CODE']}, headers={'Origin': ORIGIN})
    assert login.status_code == 200
    headers = {'Origin': ORIGIN, 'X-CSRF-Token': login.json['csrf']}
    def post(path, data, **kw):
        return client.post('/api/' + path, base_url=ORIGIN, json=data, headers=kw.get('headers', headers))
    def get(path='state'):
        return client.get('/api/' + path, base_url=ORIGIN)
    return app, client, post, get

def course_settings(**override):
    data = dict(treatment_type='radiotherapy', label='测试疗程（虚构）', count_unit='session', total_count=6,
                schedule_kind='weekdays', weekdays=WEEKDAYS, start_date=START, settings_version=0)
    data.update(override)
    return data

def make_course(env, **override):
    """建立疗程并一次生成全部 session；返回 /api/state。"""
    r = env[2]('course', course_settings(**override))
    assert r.status_code == 200, r.data
    state = env[3]().json
    if override.get('schedule_kind', 'weekdays') == 'weekdays' and override.get('total_count', 6):
        g = env[2]('course/generate', {'expected_revision': state['course_revision']})
        assert g.status_code == 201, g.data
    return env[3]().json

def session(state, identifier):
    return next(s for s in state['treatment_session'] if s['id'] == identifier)

def act(env, identifier, action, day=None, **extra):
    s = session(env[3]().json, identifier)
    return env[2](f'sessions/{identifier}', dict({'action': action, 'date': day or s['planned_date'], 'version': s['version']}, **extra))

SHARE_CATEGORIES = ('menu_library', 'adoption_state', 'preferences', 'recent_meal_status',
                    'requirement_tags', 'age_band', 'course_progress')

def open_shares(post, get, categories=SHARE_CATEGORIES):
    """经 HTTP 接口打开 agent 共享类别（agent_door 命令行没有任何修改开关的子命令）。"""
    rows = {r['category']: r for r in get('agent-share').json}
    for category in categories:
        r = post('agent-share', {'category': category, 'enabled': True, 'version': rows[category]['version']})
        assert r.status_code == 200, r.data


def choose_mode(env, mode):
    """像网页那样：先确认免责，再选方式（带版本锁）。"""
    assert env[2]('disclaimer', {'accepted': True}).status_code == 200
    version = env[3]().json['assistant_mode_version']
    r = env[2]('assistant-mode', {'mode': mode, 'version': version})
    assert r.status_code == 200, r.data
    return r


class Cli:
    """进程内调用助手命令行（同一份 main），读取它打印的 JSON。"""
    def __init__(self, monkeypatch, capsys, directory, today=TODAY):
        self.monkeypatch, self.capsys, self.directory, self.today = monkeypatch, capsys, Path(directory), today
        self.work = self.directory.parent / (self.directory.name + '-work')
        self.work.mkdir(exist_ok=True)
        self.count = 0

    def __call__(self, *argv):
        from waybeside import agent_door
        self.monkeypatch.setenv('WAYBESIDE_DATA_DIR', str(self.directory))
        if self.today:
            self.monkeypatch.setenv('WAYBESIDE_TODAY', self.today)
        else:
            self.monkeypatch.delenv('WAYBESIDE_TODAY', raising=False)
        self.capsys.readouterr()
        code = agent_door.main([str(a) for a in argv])
        out = self.capsys.readouterr().out
        return code, json.loads(out)

    def api(self, method, path, body=None):
        args = ['api', method, path]
        if body is not None:
            self.count += 1
            file = self.work / f'body-{self.count}.json'
            file.write_text(json.dumps(body), encoding='utf-8')
            args += ['--json', file]
        return self(*args)
