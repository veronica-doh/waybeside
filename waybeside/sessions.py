"""单轨的疗程设置与治疗记录：设置、批量排期、预约/补记/完成/更正/改期/取消/恢复。

并发控制有两个计数器：整体 course_revision（新增类动作用 expected_revision）
与每行 version（更新类动作用 version）。
"""
from datetime import date, datetime, timedelta, timezone
import json
from flask import abort, jsonify, request
from .storage import (connect, encode, audit, REDACTED_ENTITIES, is_onboarded, disclaimer_accepted,
                      assistant_mode, assistant_mode_version, current_actor)

TYPES = ('radiotherapy', 'chemotherapy', 'other')

def revision(db):
    return db.execute('SELECT revision FROM course_revision WHERE id=1').fetchone()[0]

def bump(db):
    db.execute('UPDATE course_revision SET revision=revision+1 WHERE id=1')

def course_row(db):
    row = db.execute('SELECT * FROM treatment_course WHERE id=1').fetchone()
    return dict(row) if row else None

def public_course(course):
    return dict(course, weekdays=json.loads(course['weekdays'])) if course else None

def off_schedule(course, target):
    """weekdays 模式下，日期不在治疗星期或早于开始日。"""
    if not course or course['schedule_kind'] != 'weekdays':
        return False
    if target.weekday() not in json.loads(course['weekdays']):
        return True
    return bool(course['start_date'] and target.isoformat() < course['start_date'])

def out_of_sync(db, course):
    """设置与尚未发生的预约日期不一致（只看 planned；已完成的事实不算）。"""
    if not course or course['schedule_kind'] != 'weekdays':
        return False
    for row in db.execute("SELECT planned_date FROM treatment_session WHERE status='planned'"):
        if off_schedule(course, date.fromisoformat(row['planned_date'])):
            return True
    return False

def counts(db):
    """已记录的 session 数：不含已取消。"""
    return db.execute("SELECT COUNT(*) FROM treatment_session WHERE status!='cancelled'").fetchone()[0]

# /api/state 的显式白名单：问卷答案、agent_share 永远不在内；audit 按 entity 过滤。
STATE_TABLES = ('treatment_course', 'treatment_session', 'calendar', 'observation', 'audit')
# /api/summary 的独立白名单：疗程配置、session、observation（含检验）；不含问卷、不含 audit。
SUMMARY_FIELDS = ('treatment_course', 'treatment_session', 'observation', 'completed')

def table_rows(db, table):
    if table == 'audit':
        marks = ','.join('?' * len(REDACTED_ENTITIES))
        query = f'SELECT id,actor,occurred_at,entity,entity_id,old_value,new_value FROM audit WHERE entity NOT IN ({marks}) ORDER BY rowid'
        cursor = db.execute(query, REDACTED_ENTITIES)
    else:
        cursor = db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')
    rows = [dict(r) for r in cursor]
    for row in rows:
        for field in ('content', 'old_value', 'new_value', 'snapshot', 'candidate'):
            if field in row and row[field] is not None:
                row[field] = json.loads(row[field])
        if table == 'treatment_course':
            row['weekdays'] = json.loads(row['weekdays'])
    return rows

def state(db, today):
    result = {key: table_rows(db, key) for key in STATE_TABLES}
    course = course_row(db)
    result.update(
        synthetic=False, today=today,
        # 首次设置完成 = 问卷四块都已填写或明确跳过。
        onboarded=is_onboarded(db), disclaimer_accepted=disclaimer_accepted(db),
        assistant_mode=assistant_mode(db), assistant_mode_version=assistant_mode_version(db),
        upgrade_notice=db.execute("SELECT 1 FROM metadata WHERE key='upgrade_notice'").fetchone() is not None,
        demo=bool((db.execute("SELECT value FROM metadata WHERE key='demo'").fetchone() or ['0'])[0] == '1'),
        completed=sum(s['status'] == 'completed' for s in result['treatment_session']),
        course_revision=revision(db), settings_version=course['version'] if course else 0,
        schedule_out_of_sync=out_of_sync(db, course))
    return result

def summary(db, today):
    full = state(db, today)
    return {key: full[key] for key in SUMMARY_FIELDS}

def log(db, entity, identifier, old, new):
    audit(db, entity, identifier, old, new)

def conflict(code, message, status=409):
    return jsonify(code=code, error=message), status

def treatment_days(weekdays, start, occupied):
    """从 start 起依次产出治疗星期内、且不在 occupied 里的日期。"""
    cursor = start
    while True:
        if cursor.weekday() in weekdays and cursor.isoformat() not in occupied:
            yield cursor
        cursor += timedelta(days=1)

def insert_session(db, ordinal, target, status, course):
    identifier = db.execute(
        'INSERT INTO treatment_session(course_id,ordinal,planned_date,actual_date,status,confirmation_source,version) VALUES(?,?,?,?,?,?,1)',
        (1 if course else None, ordinal, target, target if status == 'completed' else None, status, current_actor())).lastrowid
    db.execute('INSERT INTO calendar(session_id,date,kind,version) VALUES(?,?,?,1)', (identifier, target, 'treatment'))
    return identifier

def adjusted_by_hand(db, identifier):
    return db.execute("SELECT 1 FROM audit WHERE entity='treatment_session' AND entity_id=? AND json_extract(new_value,'$.action') IN ('postpone','reschedule') LIMIT 1",
                      (str(identifier),)).fetchone() is not None

def reschedule(db, course, today):
    """只动 planned 且从未手动改期的条目；其余日期视为已占用。返回被改条目 id 列表。"""
    rows = [dict(r) for r in db.execute('SELECT * FROM treatment_session ORDER BY ordinal')]
    eligible = [r for r in rows if r['status'] == 'planned' and not adjusted_by_hand(db, r['id'])]
    ids = {r['id'] for r in eligible}
    occupied = {d for r in rows if r['id'] not in ids for d in (r['planned_date'], r['actual_date']) if d}
    start = max(date.fromisoformat(course['start_date']), date.fromisoformat(today))
    days = treatment_days(set(json.loads(course['weekdays'])), start, occupied)
    plan = [(r, next(days).isoformat()) for r in eligible]
    changed = [(r, d) for r, d in plan if d != r['planned_date']]
    for r, _ in changed:
        db.execute('UPDATE treatment_session SET planned_date=? WHERE id=?', ('~' + str(r['id']), r['id']))
    for r, d in changed:
        db.execute('UPDATE treatment_session SET planned_date=?,version=version+1 WHERE id=?', (d, r['id']))
        db.execute('UPDATE calendar SET date=?,version=version+1 WHERE session_id=?', (d, r['id']))
    return [r['id'] for r, _ in changed]

class CourseError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(code)

def parse_settings(data, day, exact):
    exact(data, ['treatment_type', 'label', 'count_unit', 'total_count', 'schedule_kind', 'weekdays',
                 'start_date', 'settings_version', 'reschedule', 'expected_revision'])
    if data['treatment_type'] not in TYPES or data.get('count_unit', 'session') not in ('session', 'cycle'):
        raise ValueError('疗程类型')
    label = data.get('label')
    if label is not None and (not isinstance(label, str) or len(label) > 60):
        raise ValueError('名称')
    total = data.get('total_count')
    if total is not None and (type(total) is not int or not 1 <= total <= 200):
        raise ValueError('总次数')
    kind = data['schedule_kind']
    if kind not in ('weekdays', 'manual'):
        raise ValueError('排期方式')
    weekdays = data.get('weekdays', [])
    if not isinstance(weekdays, list) or any(type(w) is not int or not 0 <= w <= 6 for w in weekdays) or len(set(weekdays)) != len(weekdays):
        raise ValueError('治疗星期')
    if kind == 'weekdays' and not weekdays:
        raise ValueError('治疗星期不能为空')
    start = data.get('start_date')
    if start is not None:
        day(start)
    if type(data.get('reschedule', False)) is not bool:
        raise ValueError('重排确认')
    return dict(treatment_type=data['treatment_type'], label=label, count_unit=data.get('count_unit', 'session'),
                total_count=total, schedule_kind=kind, weekdays=encode(sorted(weekdays)), start_date=start)

def apply_course(db, data, today_value, day, exact):
    """保存疗程设置（设置页与问卷向导的“治疗方案”一块共用同一条写入路径）。
    调用者须已在 BEGIN IMMEDIATE 事务内；返回 (当前疗程行, 被重排的条目 id)。"""
    new = parse_settings(data, day, exact)
    redo = data.get('reschedule', False)
    old = course_row(db)
    if type(data.get('settings_version')) is not int or data['settings_version'] != (old['version'] if old else 0):
        abort(409)
    if redo and (type(data.get('expected_revision')) is not int or data['expected_revision'] != revision(db)):
        abort(409)
    if new['total_count'] is not None and new['total_count'] < counts(db):
        raise CourseError('TOTAL_BELOW_RECORDED', '总数小于已记录的次数，请先取消多余的预约。')
    if redo and (new['schedule_kind'] != 'weekdays' or not new['start_date']):
        raise CourseError('CANNOT_RESCHEDULE', '需要按星期排期并填写开始日才能重排。')
    if old:
        db.execute('UPDATE treatment_course SET treatment_type=?,label=?,count_unit=?,total_count=?,schedule_kind=?,weekdays=?,start_date=?,version=version+1 WHERE id=1',
                   tuple(new.values()))
    else:
        db.execute('INSERT INTO treatment_course(treatment_type,label,count_unit,total_count,schedule_kind,weekdays,start_date,id,version) VALUES(?,?,?,?,?,?,?,1,1)',
                   tuple(new.values()))
    current = course_row(db)
    moved = []
    if redo:
        moved = reschedule(db, current, str(today_value))
        bump(db)
    log(db, 'treatment_course', 1, public_course(old), dict(public_course(current), action='reschedule' if redo else 'settings', moved=moved))
    return current, moved

def register(app, day, exact):
    path = app.config['DB_PATH']

    def today():
        return day(app.config.get('TODAY') or date.today().isoformat())

    @app.post('/api/course')
    def save_course():
        data = request.get_json()
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                current, moved = apply_course(db, data, today(), day, exact)
            except CourseError as error:
                return conflict(error.code, error.message, error.status)
            return jsonify(course=public_course(current), course_revision=revision(db), rescheduled=moved,
                           schedule_out_of_sync=out_of_sync(db, current))

    @app.post('/api/course/generate')
    def generate_sessions():
        data = request.get_json()
        exact(data, ['expected_revision'])
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            course = course_row(db)
            if type(data.get('expected_revision')) is not int or data['expected_revision'] != revision(db):
                abort(409)
            if not course or course['schedule_kind'] != 'weekdays' or not course['total_count'] or not course['start_date']:
                return conflict('CANNOT_GENERATE', '需要先填好按星期排期、总次数和开始日。', 400)
            need = course['total_count'] - counts(db)
            if need <= 0:
                return conflict('NOTHING_TO_GENERATE', '已有的次数已达到总数。', 400)
            rows = [dict(r) for r in db.execute('SELECT planned_date,actual_date FROM treatment_session')]
            occupied = {d for r in rows for d in r.values() if d}
            start = date.fromisoformat(course['start_date'])
            if occupied:
                start = max(start, date.fromisoformat(max(occupied)) + timedelta(days=1))
            ordinal = db.execute('SELECT COALESCE(MAX(ordinal),0) FROM treatment_session').fetchone()[0]
            days = treatment_days(set(json.loads(course['weekdays'])), start, occupied)
            ids = []
            for _ in range(need):
                ordinal += 1
                ids.append(insert_session(db, ordinal, next(days).isoformat(), 'planned', course))
            bump(db)
            log(db, 'treatment_session', 'batch', None, {'action': 'generate', 'ids': ids})
            return jsonify(created=ids, course_revision=revision(db)), 201

    @app.post('/api/sessions')
    def create_session():
        data = request.get_json()
        exact(data, ['action', 'date', 'expected_revision'])
        target = day(data['date'])
        action = data['action']
        if action not in ('schedule', 'record') or (action == 'record' and target > today()):
            raise ValueError('请明确填写预约日或实际完成日')
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            if type(data.get('expected_revision')) is not int or data['expected_revision'] != revision(db):
                abort(409)
            if db.execute('SELECT 1 FROM treatment_session WHERE planned_date=? OR actual_date=?', (str(target), str(target))).fetchone():
                abort(409)
            ordinal = db.execute('SELECT COALESCE(MAX(ordinal),0)+1 FROM treatment_session').fetchone()[0]
            identifier = insert_session(db, ordinal, str(target), 'completed' if action == 'record' else 'planned', course_row(db))
            bump(db)
            row = dict(db.execute('SELECT * FROM treatment_session WHERE id=?', (identifier,)).fetchone())
            log(db, 'treatment_session', identifier, None, dict(row, action=action))
        return jsonify(row), 201

    @app.post('/api/sessions/<int:identifier>')
    def update_session(identifier):
        data = request.get_json()
        exact(data, ['action', 'date', 'version', 'confirm_off_schedule'])
        confirm = data.get('confirm_off_schedule', False)
        if type(confirm) is not bool:
            raise ValueError('确认字段类型')
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM treatment_session WHERE id=?', (identifier,)).fetchone()
            if not old:
                abort(404)
            old = dict(old)
            if type(data.get('version')) is not int or old['version'] != data['version']:
                abort(409)
            new = dict(old)
            action = data.get('action')
            course = course_row(db)
            off = False
            if action == 'complete':
                target = day(data['date'])
                if target > today():
                    raise ValueError('未来不能记为完成')
                if old['status'] in ('completed', 'cancelled'):
                    abort(409)
                if db.execute("SELECT 1 FROM treatment_session WHERE id!=? AND actual_date=? AND status='completed'", (identifier, str(target))).fetchone():
                    abort(409)
                off = off_schedule(course, target)
                new.update(status='completed', actual_date=str(target))
            elif action == 'correct':
                if old['status'] != 'completed':
                    abort(409)
                new.update(status='planned', actual_date=None)
            elif action in ('postpone', 'reschedule'):
                target = day(data['date'])
                if old['status'] in ('completed', 'cancelled') or str(target) == old['planned_date']:
                    abort(409)
                if db.execute('SELECT 1 FROM treatment_session WHERE id!=? AND (planned_date=? OR actual_date=?)', (identifier, str(target), str(target))).fetchone():
                    abort(409)
                off = off_schedule(course, target)
                new.update(planned_date=str(target), status='postponed', actual_date=None)
            elif action == 'restore':
                if old['status'] != 'cancelled':
                    abort(409)
                new.update(status='planned', actual_date=None)
            elif action == 'cancel':
                if old['status'] in ('completed', 'cancelled'):
                    abort(409)
                new.update(status='cancelled', actual_date=None)
            else:
                raise ValueError('未知操作')
            if off and not confirm:
                return conflict('CONFIRM_REQUIRED', '这一天不在设定的治疗日，需要确认后才会保存。')
            if action in ('postpone', 'reschedule'):
                db.execute('UPDATE calendar SET date=?,version=version+1 WHERE session_id=?', (new['planned_date'], identifier))
            new['version'] += 1
            if action in ('complete', 'correct', 'postpone', 'reschedule'):
                new['confirmation_source'] = current_actor()
            db.execute('UPDATE treatment_session SET planned_date=?,actual_date=?,status=?,version=?,confirmation_source=? WHERE id=?',
                       (new['planned_date'], new['actual_date'], new['status'], new['version'], new['confirmation_source'], identifier))
            bump(db)
            log(db, 'treatment_session', identifier, old, dict(new, action=action, **({'off_schedule': True} if off else {})))
        return jsonify(new)
