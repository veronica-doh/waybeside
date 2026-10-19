"""五餐菜单库启用后的外部助手正门：只从菜单库已有 ID 里挑，不改克数、不造菜。"""
import copy
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4
from flask import abort, jsonify, request
from . import rules as rule_filter
from .storage import connect, encode, audit, effective_flags, assistant_mode, questionnaire
from .menu_library import MEALS, NUTRIENTS, active, frozen, read_day, save_day, recommend, check_locks

class CandidateError(ValueError):
    def __init__(self, code, status='FAILED'):
        self.code, self.status = code, status
        super().__init__(code)


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CandidateError('DUPLICATE_KEY')
            result[key] = value
        return result
    def constant(_):
        raise CandidateError('NONFINITE_NUMBER')
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise CandidateError('INVALID_JSON') from error


PROTOCOL = 'waybeside-menu-candidate-v1'
REASONS = ('USER_PREFERENCE', 'VARIETY', 'KEEP_CURRENT')
QUESTIONS = ('VERIFY_MENU', 'CONFIRM_SCHEDULE')
COMPOSED = '自选'
LIMIT = 65536
ALLERGY_NOTICE = '本应用不能作为过敏原规避工具；未标注的食材不等于不含该成分。'

def _today():
    return os.environ.get('WAYBESIDE_TODAY') or date.today().isoformat()


def meal_id(group_id, meal):
    return f'{group_id}:{meal}'

def library_groups(lib):
    return [str(g['group_id']) for g in json.loads(lib['content'])['days']]

def preferences(db):
    return {r['group_id']: dict(r) for r in db.execute('SELECT * FROM menu_preference ORDER BY group_id')}

def shared_values(db, flags, mode=None):
    """按类别开关取出“可出包”的值；关闭的类别一律不取。字段与位置以本函数为准。
    助手拿到全部打理权（full）时，另给用户原话的过敏提醒和固定提示语。"""
    out = {}
    if mode == 'full':
        reminders = questionnaire(db, 'diet').get('allergy_reminders', [])
        out['allergy_reminders'] = [r for r in reminders if isinstance(r, str)] if isinstance(reminders, list) else []
        out['allergy_notice'] = ALLERGY_NOTICE
    if flags['requirement_tags']:
        tags = questionnaire(db, 'diet').get('requirement_tags', [])
        out['requirement_tags'] = tags if isinstance(tags, list) else []
    if flags['age_band']:
        band = questionnaire(db, 'basic').get('age_band')
        out['age_band'] = band if isinstance(band, str) else None
    if flags['course_progress']:
        course = db.execute('SELECT total_count FROM treatment_course WHERE id=1').fetchone()
        done = db.execute("SELECT COUNT(*) FROM treatment_session WHERE status='completed'").fetchone()[0]
        out['course_progress'] = {'completed_count': done, 'total_count': course['total_count'] if course else None}
    return out

def versions(db, lib, target, path):
    """只暴露聚合摘要：库内容哈希 + 一个 state_digest（菜单修订、偏好、开关与已共享值的合并哈希）。
    不放 menu_revision / preferences_digest 明细，类别关闭时不得由版本号反推其内容。"""
    state, revision = read_day(db, target)
    prefs = encode([[k, bool(v['favorite']), bool(v['avoided'])] for k, v in sorted(preferences(db).items())])
    flags = effective_flags(db)
    mode = assistant_mode(db)
    # 规则文件变化（rules_digest）并入聚合摘要：旧候选随之 STALE。
    rules_digest = rule_filter.context(db, path, lib)['digest']
    digest = hashlib.sha256(encode([lib['version'], revision, prefs, flags, mode, shared_values(db, flags, mode), rules_digest]).encode()).hexdigest()
    return {'library_hash': lib['digest'], 'state_digest': digest}

def adopted_ids(state):
    adopted = state['adopted']
    if not adopted:
        return {}
    composed = adopted.get('composed_from')
    return dict(composed) if composed else {m['meal']: meal_id(adopted['group_id'], m['meal']) for m in adopted['meals']}

def now():
    return datetime.now(timezone.utc).isoformat()

def compose(lib, mapping):
    """mapping: 餐次 -> 菜单库餐 ID；返回与整组同结构的冻结组。"""
    sources = {m: i.split(':', 1)[0] for m, i in mapping.items()}
    if len(set(sources.values())) == 1:
        return frozen(lib, next(iter(sources.values())))
    meals = []
    for slot in MEALS:
        group = frozen(lib, sources[slot])
        meals.append(next(m for m in group['meals'] if m['meal'] == slot))
    nutrition = {k: None if any(m['nutrition'][k] is None for m in meals) else sum(m['nutrition'][k] for m in meals) for k in NUTRIENTS}
    return {'group_id': COMPOSED, 'meals': meals, 'nutrition': nutrition, 'composed_from': dict(mapping),
            'library_version': lib['version'], 'library_hash': lib['digest']}

def meal_index(lib):
    index = {}
    for g in json.loads(lib['content'])['days']:
        gid = str(g['group_id'])
        for m in g['meals']:
            index[meal_id(gid, m['meal'])] = (gid, m['meal'], m['title'])
    return index

def excluded_groups(db, path, lib):
    ctx = rule_filter.context(db, path, lib)
    rule_filter.record_digest(db, ctx)
    return set(ctx['result']['excluded'])

def materialize(db, lib, target, hidden=()):
    state, revision = read_day(db, target)
    stale_pick = (state['recommendation'] or {}).get('group_id') in hidden and state['draft'] is None and state['adopted'] is None
    if (state['recommendation'] is None and state['draft'] is None) or stale_pick:
        state['recommendation'] = {'group_id': recommend(db, lib, hidden), 'library_hash': lib['digest']}
        save_day(db, target, state, revision)

def safe_group(db, lib, state, hidden=()):
    prefs = preferences(db)
    locked = {m: i for m, i in adopted_ids(state).items() if state['locks'].get(m)}
    for gid in library_groups(lib):
        if prefs.get(gid, {}).get('avoided') or gid in hidden:
            continue
        if all(meal_id(gid, m) == i for m, i in locked.items()):
            return gid
    return None

def require_mode(db):
    """export/submit 在事务内先查方式：还没选就不给；menu_only 与 full 都可用。"""
    mode = assistant_mode(db)
    if mode == 'unset':
        raise CandidateError('MODE_NOT_CHOSEN')
    return mode

def require_share(db):
    """导出入口：menu_library 未开启则直接 SHARE_DISABLED（甚至不透露有没有菜单库）。full 时七类全开。"""
    flags = effective_flags(db)
    if not flags['menu_library']:
        raise CandidateError('SHARE_DISABLED')
    return flags

def export_package(path, target, output=None):
    target = date.fromisoformat(target).isoformat()
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        mode = require_mode(db)
        flags = require_share(db)
        lib = active(db)
        if lib is None:
            raise CandidateError('NO_MENU_LIBRARY')
        hidden = excluded_groups(db, path, lib)
        # 仅当 requirement_tags 已共享时，规则排除才体现在导出里；否则 groups 保持全集，
        # 否则缺席的组会泄露未共享的标签。
        visible_hidden = hidden if flags['requirement_tags'] else set()
        materialize(db, lib, target, hidden)
        state, _ = read_day(db, target)
        ver = versions(db, lib, target, path)
        snap = None
        for row in db.execute("SELECT * FROM menu_candidate WHERE target_date=? AND status='WAITING_EXTERNAL' ORDER BY rowid DESC", (target,)).fetchall():
            old = json.loads(row['snapshot'])
            if old['snapshot_versions'] == ver:
                snap = old
                break
            db.execute("UPDATE menu_candidate SET status='STALE' WHERE id=?", (row['id'],))
        if snap is None:
            prefs = preferences(db)
            # groups 永远是菜单库全集：规则筛选不得影响导出内容，否则可由缺席的组反推标签。
            groups = [{'group_id': str(g['group_id']), 'meals': [{'meal': m['meal'], 'meal_id': meal_id(g['group_id'], m['meal']), 'title': m['title']} for m in g['meals']]}
                      for g in json.loads(lib['content'])['days'] if str(g['group_id']) not in visible_hidden]
            snap = {'job_id': str(uuid4()), 'target_date': target, 'source': 'external', 'snapshot_versions': ver,
                    'meal_slots': list(MEALS), 'groups': groups}
            if flags['adoption_state']:
                current = (state['draft'] or state['adopted'])
                selected = (current or {}).get('group_id') or (state['recommendation'] or {}).get('group_id')
                snap['current'] = {'selected_group_id': selected, 'adopted_group_id': (state['adopted'] or {}).get('group_id'),
                                   'adopted_meal_ids': adopted_ids(state), 'locked_meals': [m for m in MEALS if state['locks'].get(m)]}
            if flags['preferences']:
                snap['avoided_group_ids'] = sorted(g for g, v in prefs.items() if v['avoided'])
                snap['favorite_group_ids'] = sorted(g for g, v in prefs.items() if v['favorite'])
            if flags['recent_meal_status']:
                recent = []
                for row in db.execute('SELECT target,content FROM menu_day WHERE target<? ORDER BY target DESC LIMIT 7', (_today(),)):
                    actual = json.loads(row['content']).get('actual', {})
                    if actual:
                        recent.append({'date': row['target'], 'meals': {m: v['status'] for m, v in actual.items()}})
                snap['recent_records'] = recent
            snap.update(shared_values(db, flags, mode))
            # 采用状态未开启时，模板不预填组（不得由已采用/锁定算出）。
            snap['_template_group'] = safe_group(db, lib, state, visible_hidden) if flags['adoption_state'] else None
            db.execute('INSERT INTO menu_candidate VALUES(?,?,?,?,?,?)', (snap['job_id'], target, 'WAITING_EXTERNAL', encode(snap), None, now()))
            audit(db, 'menu_candidate', snap['job_id'], None, {'status': 'WAITING_EXTERNAL', 'source': 'external'}, actor='agent')
        public = {k: v for k, v in snap.items() if not k.startswith('_')}
        template = {'job_id': snap['job_id'], 'target_date': target, 'snapshot_versions': snap['snapshot_versions'],
                    'group_id': snap['_template_group'], 'reason_code': 'KEEP_CURRENT', 'open_questions': []}
        package = {'protocol': PROTOCOL, 'snapshot': public, 'candidate_template': template,
                   'candidate_format': {
                       'identity': '原样复制模板的 job_id、target_date、snapshot_versions',
                       'choose_one': {'group_id': '整组：snapshot.groups 中的 group_id（字符串）',
                                      'meals': '{"早餐": "<meal_id>", ... 五个餐次各一项}；每项 meal_id 必须取自 snapshot.groups 且餐次相同；可跨组'},
                       'reason_code': list(REASONS), 'open_questions': list(QUESTIONS)},
                   'allowed_changes': ['只能选择菜单库已有的组或餐；锁定餐次必须保持当前已采用的餐',
                                       '被标记「暂不想吃」的组（及其中的餐）会被拒绝',
                                       '不能改克数、不能新造菜；不接收自由文本、药物、医嘱、运动、冥想字段',
                                       'open_questions 非空表示需本人澄清，候选不能采用']}
        destination = Path(output) if output else Path(path).parent / 'agent_inbox' / f"{snap['job_id']}.json"
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if output is None and destination.is_file() and destination.read_text(encoding='utf-8') == encode(package):
            pass
        else:
            with destination.open('x', encoding='utf-8') as stream:
                stream.write(encode(package))
            destination.chmod(0o400)
    return {'status': 'WAITING_EXTERNAL', 'job_id': snap['job_id'], 'package': str(destination)}

def validate(raw, snap, db, lib, state, flags, hidden=()):
    data = decode(raw)
    base = {'job_id', 'target_date', 'snapshot_versions', 'reason_code', 'open_questions'}
    if not isinstance(data, dict) or set(data) not in (base | {'group_id'}, base | {'meals'}):
        raise CandidateError('SCHEMA_FIELDS')
    for k in ('job_id', 'target_date', 'snapshot_versions'):
        if data[k] != snap[k]:
            raise CandidateError('IDENTITY_MISMATCH')
    if not isinstance(data['reason_code'], str) or data['reason_code'] not in REASONS:
        raise CandidateError('REASON_CODE')
    if not isinstance(data['open_questions'], list) or any(not isinstance(q, str) or q not in QUESTIONS for q in data['open_questions']):
        raise CandidateError('QUESTION_CODE')
    prefs = preferences(db)
    index = meal_index(lib)
    if 'group_id' in data:
        gid = data['group_id']
        if not isinstance(gid, str) or gid not in library_groups(lib):
            raise CandidateError('UNKNOWN_GROUP')
        mapping = {m: meal_id(gid, m) for m in MEALS}
    else:
        meals = data['meals']
        if not isinstance(meals, dict) or set(meals) != set(MEALS):
            raise CandidateError('MEAL_SET')
        mapping = {}
        for slot, ident in meals.items():
            if not isinstance(ident, str) or ident not in index:
                raise CandidateError('UNKNOWN_MEAL')
            if index[ident][1] != slot:
                raise CandidateError('MEAL_SLOT_MISMATCH')
            mapping[slot] = ident
    current = adopted_ids(state)
    for slot, ident in mapping.items():
        # 对应类别未开启时只返回通用错误，不说明原因（否则可由错误码反推未共享的信息）。
        if state['locks'].get(slot) and current.get(slot) != ident:
            raise CandidateError('LOCKED_MEAL' if flags['adoption_state'] else 'GROUP_NOT_ALLOWED')
        if prefs.get(index[ident][0], {}).get('avoided'):
            raise CandidateError('AVOIDED_MENU' if flags['preferences'] else 'GROUP_NOT_ALLOWED')
        # 规则排除：requirement_tags 已共享才说明原因，否则只返回通用错误。
        if index[ident][0] in hidden:
            raise CandidateError('RULE_EXCLUDED' if flags['requirement_tags'] else 'GROUP_NOT_ALLOWED')
    if data['open_questions']:
        raise CandidateError('OPEN_QUESTIONS', 'NEEDS_CLARIFICATION')
    return {'reason_code': data['reason_code'], 'open_questions': [], 'mapping': mapping,
            **({'group_id': data['group_id']} if 'group_id' in data else {'meals': data['meals']})}

def submit_candidate(path, filename):
    with Path(filename).open('rb') as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise CandidateError('OUTPUT_LIMIT')
    data = decode(raw)
    if not isinstance(data, dict) or not isinstance(data.get('job_id'), str):
        raise CandidateError('SCHEMA_FIELDS')
    if not Path(path).is_file():
        raise CandidateError('EXPORT_REQUIRED')
    stale = False
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        require_mode(db)
        require_share(db)
        lib = active(db)
        if lib is None:
            raise CandidateError('NO_MENU_LIBRARY')
        row = db.execute('SELECT * FROM menu_candidate WHERE id=?', (data['job_id'],)).fetchone()
        if row is None:
            raise CandidateError('UNKNOWN_JOB')
        if row['status'] != 'WAITING_EXTERNAL':
            raise CandidateError('JOB_NOT_WAITING', row['status'])
        snap = json.loads(row['snapshot'])
        target = row['target_date']
        if versions(db, lib, target, path) != snap['snapshot_versions']:
            db.execute("UPDATE menu_candidate SET status='STALE' WHERE id=?", (row['id'],))
            audit(db, 'menu_candidate', row['id'], {'status': row['status']}, {'status': 'STALE'}, actor='local-program')
            stale = True
        else:
            state, _ = read_day(db, target)
            candidate = validate(raw, snap, db, lib, state, effective_flags(db), excluded_groups(db, path, lib))
            db.execute("UPDATE menu_candidate SET status='SUPERSEDED' WHERE target_date=? AND status='READY_TO_REVIEW'", (target,))
            db.execute("UPDATE menu_candidate SET status='READY_TO_REVIEW',candidate=? WHERE id=?", (encode(candidate), row['id']))
            audit(db, 'menu_candidate', row['id'], {'status': 'WAITING_EXTERNAL'}, {'status': 'READY_TO_REVIEW'}, actor='agent')
    if stale:
        raise CandidateError('DEPENDENCIES_CHANGED', 'STALE')
    return {'job_id': row['id'], 'status': 'READY_TO_REVIEW', 'adopted': False}

def register(app):
    path = app.config['DB_PATH']
    def today():
        return app.config.get('TODAY') or _today()

    def view(db, lib, row):
        snap = json.loads(row['snapshot'])
        candidate = json.loads(row['candidate']) if row['candidate'] else None
        status = row['status']
        if status == 'READY_TO_REVIEW' and versions(db, lib, row['target_date'], path) != snap['snapshot_versions']:
            db.execute("UPDATE menu_candidate SET status='STALE' WHERE id=?", (row['id'],))
            audit(db, 'menu_candidate', row['id'], {'status': status}, {'status': 'STALE'})
            status = 'STALE'
        result = {'id': row['id'], 'target_date': row['target_date'], 'status': status, 'source': 'external'}
        if candidate and status == 'READY_TO_REVIEW':
            result.update(reason_code=candidate['reason_code'], group=compose(lib, candidate['mapping']))
        return result

    @app.get('/api/menu/candidate')
    def candidate_state():
        target = request.args.get('target_date', '')
        date.fromisoformat(target)
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            lib = active(db)
            row = db.execute("SELECT rowid AS rid,* FROM menu_candidate WHERE target_date=? AND status IN ('READY_TO_REVIEW','WAITING_EXTERNAL') ORDER BY rowid DESC LIMIT 1", (target,)).fetchone()
            # 助手再取了一次资料，之前提交的建议就被替换了：给网页一个“有过被替换的建议”的标记（不含内容）。
            replaced = bool(row) and db.execute("SELECT 1 FROM menu_candidate WHERE target_date=? AND (status='SUPERSEDED' OR (status='READY_TO_REVIEW' AND rowid<?)) LIMIT 1", (target, row['rid'])).fetchone() is not None
            return jsonify(candidate=view(db, lib, row) if row and lib else None, replaced=replaced)

    @app.post('/api/menu/candidate/<identifier>/adopt')
    def candidate_adopt(identifier):
        data = request.get_json()
        if data != {'confirmed': True}:
            raise ValueError('必须确认')
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            lib = active(db)
            row = db.execute('SELECT * FROM menu_candidate WHERE id=?', (identifier,)).fetchone()
            if not row or not lib or row['status'] != 'READY_TO_REVIEW':
                abort(409)
            target = row['target_date']
            snap = json.loads(row['snapshot'])
            if versions(db, lib, target, path) != snap['snapshot_versions'] or target < today():
                db.execute("UPDATE menu_candidate SET status='STALE' WHERE id=?", (identifier,))
                audit(db, 'menu_candidate', identifier, {'status': row['status']}, {'status': 'STALE'})
                db.commit()
                abort(409)
            mapping = json.loads(row['candidate'])['mapping']
            hidden = excluded_groups(db, path, lib)
            if any(i.split(':', 1)[0] in hidden for i in mapping.values()):
                db.execute("UPDATE menu_candidate SET status='STALE' WHERE id=?", (identifier,))
                audit(db, 'menu_candidate', identifier, {'status': row['status']}, {'status': 'STALE'})
                db.commit()
                abort(409)
            group = compose(lib, mapping)
            state, revision = read_day(db, target)
            before = copy.deepcopy(state)
            if state['adopted'] == group:
                abort(409)
            check_locks(state, group)
            state['previous'].append(state['adopted'])
            state['adopted'] = group
            state['draft'] = None
            save_day(db, target, state, revision)
            audit(db, 'menu_adopt', target, before, state)
            db.execute("UPDATE menu_candidate SET status='APPLIED' WHERE id=?", (identifier,))
            audit(db, 'menu_candidate', identifier, {'status': 'READY_TO_REVIEW'}, {'status': 'APPLIED'})
        return jsonify(status='APPLIED')

    @app.post('/api/menu/candidate/<identifier>/decline')
    def candidate_decline(identifier):
        if request.get_json() != {}:
            raise ValueError('不接受额外内容')
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM menu_candidate WHERE id=?', (identifier,)).fetchone()
            if not row or row['status'] != 'READY_TO_REVIEW':
                abort(409)
            db.execute("UPDATE menu_candidate SET status='DECLINED' WHERE id=?", (identifier,))
            audit(db, 'menu_candidate', identifier, {'status': row['status']}, {'status': 'DECLINED'})
        return jsonify(status='DECLINED')
