"""问卷四块（基本信息 / 治疗方案 / 饮食要求 / 日常习惯）的后台存储、设置读取、免责确认与 agent 共享开关。

数据面规则：
- 问卷答案只入后台表 questionnaire_answer；没有任何“档案总览”读取接口。
- 唯一读取口是 GET /api/settings，且只返回前端“可编辑”的字段。
- 写入统一走 POST /api/onboarding/<block>：exact() 白名单、version 乐观锁、脱敏 audit（只记字段名）。
- treatment 块的真实值只存 treatment_course（同一条写入路径，见 sessions.apply_course），
  questionnaire_answer 里只留完成标记，避免两份真相。
"""
import json
import re
from datetime import datetime
from flask import abort, g, jsonify, request
from .storage import (connect, encode, audit, BLOCKS, SHARE_CATEGORIES, is_onboarded, share_flags,
                      questionnaire, disclaimer_accepted, assistant_mode, assistant_mode_version)
from .sessions import apply_course, course_row, public_course, CourseError, conflict

TAG = re.compile(r'^[a-z0-9_]{1,40}$')
# 内置词表只是“显示名 ↔ 键”，不带任何阈值、食物清单或临床含义。
BUILTIN_TAGS = {'soft_texture': '软烂质地', 'small_frequent_meals': '少食多餐', 'avoid_spicy': '避免辛辣', 'avoid_raw': '避免生食'}
AGE_BANDS = ('under_18', '18_39', '40_64', '65_plus')

# 每块允许的键。其余一律由 exact() 拒绝——姓名、证件号、体重、身高等字段不在其中。
BLOCK_KEYS = {
    'basic': ('nickname', 'age_band', 'helper_mode', 'locale'),
    'diet': ('requirement_tags', 'custom_tags', 'allergy_reminders', 'free_note'),
    'habit': ('sleep_usual', 'activity_usual', 'meditation_usual'),
}
TREATMENT_KEYS = ('treatment_type', 'label', 'count_unit', 'total_count', 'schedule_kind', 'weekdays',
                  'start_date', 'settings_version', 'reschedule', 'expected_revision')


def is_unknown(value):
    return isinstance(value, dict) and value == {'unknown': True}

def text(value, limit, minimum=0):
    if not isinstance(value, str) or not minimum <= len(value) <= limit:
        raise ValueError('文字类型或长度不正确')
    return value

def enum(value, choices):
    if not isinstance(value, str) or value not in choices:
        raise ValueError('选项不在范围内')
    return value

def string_list(value, limit_items, limit_chars):
    if not isinstance(value, list) or len(value) > limit_items:
        raise ValueError('列表类型或条数不正确')
    for item in value:
        text(item, limit_chars, 1)
    if len(set(value)) != len(value):
        raise ValueError('列表有重复项')
    return value


def clean_basic(a, exact):
    exact(a, BLOCK_KEYS['basic'])
    out = {}
    if 'nickname' in a:
        out['nickname'] = text(a['nickname'], 20)
    if 'age_band' in a:
        out['age_band'] = a['age_band'] if is_unknown(a['age_band']) else enum(a['age_band'], AGE_BANDS)
    if 'helper_mode' not in a:
        raise ValueError('需要选择谁在使用')
    out['helper_mode'] = enum(a['helper_mode'], ('self', 'with_helper'))
    out['locale'] = enum(a.get('locale', 'zh-CN'), ('zh-CN',))
    return out

def clean_diet(a, exact):
    exact(a, BLOCK_KEYS['diet'])
    out = {}
    custom = a.get('custom_tags', [])
    if not isinstance(custom, list) or len(custom) > 30:
        raise ValueError('自加标签')
    for item in custom:
        exact(item, ('id', 'label'))
        if set(item) != {'id', 'label'} or not isinstance(item['id'], str) or not TAG.match(item['id']):
            raise ValueError('自加标签键')
        text(item['label'], 20, 1)
    ids = [i['id'] for i in custom]
    if len(set(ids)) != len(ids) or set(ids) & set(BUILTIN_TAGS):
        raise ValueError('自加标签重复')
    out['custom_tags'] = custom
    if 'requirement_tags' in a:
        tags = a['requirement_tags']
        if is_unknown(tags):
            out['requirement_tags'] = tags
        else:
            string_list(tags, 50, 40)
            if any(not TAG.match(t) or (t not in BUILTIN_TAGS and t not in ids) for t in tags):
                raise ValueError('标签须来自内置词表或自加标签')
            out['requirement_tags'] = tags
    if 'allergy_reminders' in a:
        value = a['allergy_reminders']
        out['allergy_reminders'] = value if is_unknown(value) else string_list(value, 30, 20)
    if 'free_note' in a:
        out['free_note'] = text(a['free_note'], 500)
    return out

def clean_habit(a, exact):
    exact(a, BLOCK_KEYS['habit'])
    out = {}
    for key, choices in (('sleep_usual', ('unknown', 'regular', 'irregular')), ('activity_usual', ('none', 'light', 'regular'))):
        if key in a:
            out[key] = a[key] if is_unknown(a[key]) else enum(a[key], choices)
    if 'meditation_usual' in a:
        value = a['meditation_usual']
        if is_unknown(value):
            out['meditation_usual'] = value
        else:
            exact(value, ('time', 'minutes'))
            if set(value) != {'time', 'minutes'}:
                raise ValueError('冥想习惯')
            if value['time'] is not None:
                text(value['time'], 5)
                datetime.strptime(value['time'], '%H:%M')
            minutes = value['minutes']
            if minutes is not None and (type(minutes) is not int or not 0 <= minutes <= 180):
                raise ValueError('冥想分钟数')
            out['meditation_usual'] = value
    return out

CLEANERS = {'basic': clean_basic, 'diet': clean_diet, 'habit': clean_habit}


def editable_settings(db):
    """GET /api/settings 的内容：只含前端可编辑字段。"""
    basic = dict({'helper_mode': 'self', 'locale': 'zh-CN'}, **questionnaire(db, 'basic'))
    diet = dict({'custom_tags': []}, **questionnaire(db, 'diet'))
    versions = {r['block']: r['version'] for r in db.execute('SELECT block,version FROM questionnaire_answer')}
    completed = {r['block']: bool(r['completed']) for r in db.execute('SELECT block,completed FROM questionnaire_answer')}
    return {'basic': basic, 'diet': diet, 'habit': questionnaire(db, 'habit'),
            'treatment': public_course(course_row(db)),
            'builtin_tags': BUILTIN_TAGS,
            'versions': {b: versions.get(b, 0) for b in BLOCKS},
            'completed': {b: completed.get(b, False) for b in BLOCKS},
            'onboarded': is_onboarded(db)}


def register(app, day, exact):
    path = app.config['DB_PATH']

    def today():
        return day(app.config.get('TODAY') or datetime.now().date().isoformat())

    @app.get('/api/settings')
    def get_settings():
        with connect(path) as db:
            db.execute('BEGIN')
            return jsonify(editable_settings(db))

    @app.post('/api/onboarding/<block>')
    def onboard(block):
        if block not in BLOCKS:
            abort(404)
        data = request.get_json()
        exact(data, ['answers', 'version', 'skipped'])
        skipped = data.get('skipped', False)
        if type(skipped) is not bool or type(data.get('version')) is not int:
            raise ValueError('请求格式')
        answers = data.get('answers', {})
        if not isinstance(answers, dict) or (skipped and answers):
            raise ValueError('跳过时不能带答案')
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT version FROM questionnaire_answer WHERE block=?', (block,)).fetchone()
            if data['version'] != (row['version'] if row else 0):
                abort(409)
            if skipped:
                content, fields = {'skipped': True}, []
            elif block == 'treatment':
                exact(answers, TREATMENT_KEYS)
                try:
                    apply_course(db, answers, today(), day, exact)
                except CourseError as error:
                    return conflict(error.code, error.message, error.status)
                content, fields = {'projected': True}, sorted(answers)
            else:
                content = CLEANERS[block](answers, exact)
                fields = sorted(content)
            version = data['version'] + 1
            db.execute('INSERT INTO questionnaire_answer(block,content,completed,version) VALUES(?,?,1,?) '
                       'ON CONFLICT(block) DO UPDATE SET content=excluded.content,completed=1,version=excluded.version',
                       (block, encode(content), version))
            audit(db, 'questionnaire', block, None, None, fields=fields, version=version, extra={'skipped': True} if skipped else None)
            return jsonify(block=block, version=version, onboarded=is_onboarded(db))

    @app.post('/api/disclaimer')
    def accept_disclaimer():
        data = request.get_json()
        exact(data, ['accepted'])
        if data.get('accepted') is not True:
            raise ValueError('需要确认')
        if g.actor != 'local-user':
            abort(403)
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            if not disclaimer_accepted(db):
                db.execute("INSERT INTO metadata VALUES('disclaimer_ack',?)", (datetime.now().astimezone().isoformat(),))
                audit(db, 'settings', 'disclaimer', None, None, fields=['disclaimer_ack'], version=1)
        return jsonify(disclaimer_accepted=True)

    @app.post('/api/assistant-mode')
    def set_assistant_mode():
        data = request.get_json()
        exact(data, ['mode', 'version'])
        if g.actor != 'local-user':
            abort(403)
        if not isinstance(data.get('mode'), str) or data['mode'] not in ('menu_only', 'full') or type(data.get('version')) is not int:
            raise ValueError('方式或版本不正确')
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            if not disclaimer_accepted(db):
                return conflict('DISCLAIMER_REQUIRED', '请先确认免责声明。')
            db.execute("DELETE FROM metadata WHERE key='upgrade_notice'")   # 人在设置里处理过方式，不再提示
            old = assistant_mode(db)
            if data['version'] != assistant_mode_version(db):
                abort(409)
            version = data['version'] + 1
            if data['mode'] != old:
                db.execute("INSERT INTO metadata VALUES('assistant_mode',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (data['mode'],))
                db.execute("INSERT INTO metadata VALUES('assistant_mode_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(version),))
                # 只记新旧值这一个枚举，不涉及任何健康内容。
                audit(db, 'assistant_mode', 'mode', None, None, fields=['assistant_mode'], version=version,
                      extra={'from': old, 'to': data['mode']})
            else:
                version = data['version']
            return jsonify(assistant_mode=data['mode'], assistant_mode_version=version)

    @app.post('/api/upgrade-notice')
    def seen_upgrade_notice():
        exact(request.get_json(), [])
        if g.actor != 'local-user':
            abort(403)
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("DELETE FROM metadata WHERE key='upgrade_notice'")
        return jsonify(upgrade_notice=False)

    @app.get('/api/agent-share')
    def get_agent_share():
        with connect(path) as db:
            rows = [dict(category=r['category'], enabled=bool(r['enabled']), version=r['version'])
                    for r in db.execute('SELECT * FROM agent_share ORDER BY rowid')]
        return jsonify(rows)

    @app.post('/api/agent-share')
    def set_agent_share():
        data = request.get_json()
        exact(data, ['category', 'enabled', 'version'])
        if set(data) != {'category', 'enabled', 'version'}:
            raise ValueError('缺少字段')
        if not isinstance(data['category'], str) or data['category'] not in SHARE_CATEGORIES:
            raise ValueError('未知类别')
        if type(data['enabled']) is not bool:
            raise ValueError('enabled 必须是布尔值')
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM agent_share WHERE category=?', (data['category'],)).fetchone()
            if type(data['version']) is not int or data['version'] != row['version']:
                abort(409)
            version = row['version'] + 1
            db.execute('UPDATE agent_share SET enabled=?,version=? WHERE category=?', (int(data['enabled']), version, data['category']))
            # 只记 category 与 enabled 变化这一事实，不涉及任何资料值。
            audit(db, 'agent_share', data['category'], None, None, fields=['enabled'], version=version,
                  extra={'category': data['category'], 'enabled': data['enabled'], 'was': bool(row['enabled'])})
            return jsonify(category=data['category'], enabled=data['enabled'], version=version)
