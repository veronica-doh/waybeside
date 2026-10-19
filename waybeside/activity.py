"""给网页用的只读端点：助手最近做了什么（只有动作与时间，不含值），以及哪些内容最近由助手改过。"""
import json
from flask import jsonify
from .storage import connect, BLOCKS

RECENT_LIMIT = 30


def kind_of(entity, entity_id, new_value):
    """把一条 audit 归成界面用的动作类别；只看动作名，不读任何健康内容。"""
    if entity == 'observation':
        return 'labs' if entity_id == 'aux' else 'daily'
    if entity in ('treatment_session', 'treatment_course', 'menu_candidate'):
        try:
            detail = json.loads(new_value) if new_value else {}
        except ValueError:
            detail = {}
        label = detail.get('action') if entity != 'menu_candidate' else detail.get('status')
        return f'{entity}.{label}' if isinstance(label, str) and label.isidentifier() else entity
    return entity


def activity(db):
    recent = [{'kind': kind_of(r['entity'], r['entity_id'], r['new_value']), 'at': r['occurred_at']}
              for r in db.execute("SELECT occurred_at,entity,entity_id,new_value FROM audit WHERE actor='agent' ORDER BY rowid DESC LIMIT ?", (RECENT_LIMIT,))]
    questionnaire, course, adopt, overrides, off_schedule = {b: False for b in BLOCKS}, False, {}, [], {}
    for r in db.execute("SELECT actor,entity,entity_id,new_value FROM audit WHERE entity IN ('questionnaire','treatment_course','treatment_session','menu_adopt','menu_undo','menu_rule_override') ORDER BY rowid"):
        by_agent = r['actor'] == 'agent'
        if r['entity'] == 'questionnaire' and r['entity_id'] in questionnaire:
            questionnaire[r['entity_id']] = by_agent
        elif r['entity'] == 'treatment_course':
            course = by_agent
        elif r['entity'] == 'treatment_session':
            detail = json.loads(r['new_value'] or '{}')
            if by_agent and detail.get('off_schedule') and detail.get('action') in ('complete', 'reschedule', 'postpone'):
                off_schedule[r['entity_id']] = 'complete' if detail['action'] == 'complete' else 'reschedule'
            else:
                off_schedule.pop(r['entity_id'], None)
        elif r['entity'] in ('menu_adopt', 'menu_undo'):
            adopt[r['entity_id']] = by_agent and r['entity'] == 'menu_adopt'
        elif by_agent:
            detail = json.loads(r['new_value'] or '{}')
            if detail.get('op') == 'adopt':
                overrides.append({'target_date': r['entity_id'], 'group_id': detail.get('group_id')})
    return {'recent': recent, 'by_agent': {'questionnaire': questionnaire, 'course': course, 'menu_adopt': adopt}, 'rule_overrides': overrides, 'off_schedule': off_schedule}


def register(app):
    path = app.config['DB_PATH']

    @app.get('/api/assistant-activity')
    def assistant_activity():
        with connect(path) as db:
            db.execute('BEGIN')
            return jsonify(activity(db))
