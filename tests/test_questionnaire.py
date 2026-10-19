"""问卷向导后台：四块写入、白名单、乐观锁、脱敏 audit、onboarded 判据、免责确认、GET /api/settings。全部虚构。"""
import json
import pytest
from support import make_env, course_settings, ORIGIN
from waybeside.storage import connect

BASIC = {'nickname': '测试昵称', 'helper_mode': 'with_helper', 'age_band': '40_64', 'locale': 'zh-CN'}
DIET = {'custom_tags': [{'id': 'my_tag_1', 'label': '测试标签'}], 'requirement_tags': ['soft_texture', 'my_tag_1'],
        'allergy_reminders': ['测试提醒'], 'free_note': '测试备注'}
HABIT = {'sleep_usual': 'regular', 'activity_usual': 'light', 'meditation_usual': {'time': '07:30', 'minutes': 15}}

def block(env, name, answers=None, version=0, skipped=False):
    return env[2]('onboarding/' + name, {'answers': answers or {}, 'version': version, 'skipped': skipped})

def test_fresh_directory_is_not_onboarded_and_has_no_disclaimer(tmp_path):
    e = make_env(tmp_path)
    state = e[3]().json
    assert state['onboarded'] is False and state['disclaimer_accepted'] is False
    s = e[3]('settings').json
    assert s['versions'] == {'basic': 0, 'treatment': 0, 'diet': 0, 'habit': 0} and s['onboarded'] is False
    assert s['basic'] == {'helper_mode': 'self', 'locale': 'zh-CN'}      # 默认值，不代表用户答过

def test_onboarded_requires_all_four_blocks_answered_or_skipped(tmp_path):
    e = make_env(tmp_path)
    assert block(e, 'basic', BASIC).status_code == 200
    assert block(e, 'diet', DIET).status_code == 200
    assert block(e, 'habit', HABIT).status_code == 200
    assert e[3]().json['onboarded'] is False                     # treatment 还没处理
    r = block(e, 'treatment', skipped=True)
    assert r.status_code == 200 and r.json['onboarded'] is True
    assert e[3]().json['onboarded'] is True and e[3]().json['treatment_course'] == []

def test_treatment_block_reuses_the_course_write_path(tmp_path):
    e = make_env(tmp_path)
    r = block(e, 'treatment', course_settings(settings_version=0))
    assert r.status_code == 200
    state = e[3]().json
    assert state['treatment_course'][0]['total_count'] == 6 and state['settings_version'] == 1
    # 设置版本过期 → 409；总数低于已记录数 → 400（与设置页同一套规则）
    assert block(e, 'treatment', course_settings(settings_version=0), version=1).status_code == 409
    assert block(e, 'treatment', course_settings(settings_version=1, total_count=0), version=1).status_code == 400
    # 问卷表里 treatment 只留完成标记，不重复存值
    with connect(e[0].config['DB_PATH']) as db:
        assert json.loads(db.execute("SELECT content FROM questionnaire_answer WHERE block='treatment'").fetchone()[0]) == {'projected': True}

@pytest.mark.parametrize('name,extra', [(b, k) for b in ('basic', 'diet', 'habit', 'treatment')
                                         for k in ('name', 'full_name', 'id_number', 'phone', 'weight', 'height', 'hospital')])
def test_exact_whitelist_rejects_identity_and_body_fields(tmp_path, name, extra):
    e = make_env(tmp_path)
    base = {'basic': BASIC, 'diet': DIET, 'habit': HABIT, 'treatment': course_settings(settings_version=0)}[name]
    assert block(e, name, dict(base, **{extra: 'x'})).status_code == 400
    with connect(e[0].config['DB_PATH']) as db:
        assert db.execute('SELECT COUNT(*) FROM questionnaire_answer').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM treatment_course').fetchone()[0] == 0

def test_request_envelope_is_strict(tmp_path):
    e = make_env(tmp_path)
    assert e[2]('onboarding/basic', {'answers': BASIC, 'version': 0, 'name': 'x'}).status_code == 400
    assert e[2]('onboarding/basic', {'answers': BASIC, 'version': '0'}).status_code == 400
    assert e[2]('onboarding/basic', {'answers': BASIC, 'version': 0, 'skipped': 'yes'}).status_code == 400
    assert e[2]('onboarding/basic', {'answers': BASIC, 'version': 0, 'skipped': True}).status_code == 400    # 跳过不能带答案
    assert e[2]('onboarding/unknown_block', {'answers': {}, 'version': 0}).status_code == 404

@pytest.mark.parametrize('name,bad', [
    ('basic', {'helper_mode': 'robot'}), ('basic', {}), ('basic', {'helper_mode': 'self', 'nickname': 'x' * 21}),
    ('basic', {'helper_mode': 'self', 'age_band': '30'}), ('basic', {'helper_mode': 'self', 'locale': 'en'}),
    ('diet', {'requirement_tags': ['Soft']}), ('diet', {'requirement_tags': ['not_in_vocabulary']}),
    ('diet', {'allergy_reminders': ['x' * 21]}), ('diet', {'free_note': 'x' * 501}),
    ('diet', {'custom_tags': [{'id': 'soft_texture', 'label': '重名'}]}), ('diet', {'custom_tags': [{'id': 'A B', 'label': 'x'}]}),
    ('habit', {'sleep_usual': 'great'}), ('habit', {'meditation_usual': {'time': '25:00', 'minutes': 5}}),
    ('habit', {'meditation_usual': {'time': None, 'minutes': 181}}), ('habit', {'meditation_usual': {'time': None, 'minutes': True}}),
])
def test_field_validation(tmp_path, name, bad):
    assert block(make_env(tmp_path), name, bad).status_code == 400

def test_unknown_is_a_distinct_value_not_empty_or_normal(tmp_path):
    e = make_env(tmp_path)
    assert block(e, 'basic', {'helper_mode': 'self', 'age_band': {'unknown': True}}).status_code == 200
    assert block(e, 'diet', {'requirement_tags': {'unknown': True}, 'allergy_reminders': {'unknown': True}}).status_code == 200
    assert block(e, 'habit', {'activity_usual': {'unknown': True}, 'meditation_usual': {'unknown': True}}).status_code == 200
    s = e[3]('settings').json
    assert s['basic']['age_band'] == {'unknown': True} and s['diet']['requirement_tags'] == {'unknown': True}
    assert block(e, 'diet', {'requirement_tags': {'unknown': False}}, version=1).status_code == 400

def test_optimistic_lock_and_roundtrip(tmp_path):
    e = make_env(tmp_path)
    assert block(e, 'diet', DIET).status_code == 200
    assert block(e, 'diet', DIET, version=0).status_code == 409                 # 旧版本
    assert block(e, 'diet', dict(DIET, free_note='改过'), version=1).json['version'] == 2
    s = e[3]('settings').json
    assert s['diet']['free_note'] == '改过' and s['versions']['diet'] == 2

def test_questionnaire_audit_has_field_names_only(tmp_path):
    e = make_env(tmp_path)
    block(e, 'basic', BASIC); block(e, 'diet', DIET); block(e, 'habit', HABIT)
    e[2]('disclaimer', {'accepted': True})
    with connect(e[0].config['DB_PATH']) as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM audit WHERE entity IN ('questionnaire','settings')")]
    assert len(rows) == 4
    for r in rows:
        assert r['old_value'] is None and r['new_value'] is None
        detail = json.loads(r['detail'])
        assert set(detail) >= {'fields', 'version', 'at', 'actor'}
    assert json.loads(next(r for r in rows if r['entity_id'] == 'diet')['detail'])['fields'] == sorted(DIET)
    dump = json.dumps(rows, ensure_ascii=False)
    for secret in ('测试昵称', '测试提醒', '测试备注', '测试标签'):
        assert secret not in dump

def test_disclaimer_is_confirmed_once(tmp_path):
    e = make_env(tmp_path)
    assert e[2]('disclaimer', {'accepted': False}).status_code == 400
    assert e[2]('disclaimer', {'accepted': 'yes'}).status_code == 400
    assert e[2]('disclaimer', {'accepted': True, 'x': 1}).status_code == 400
    assert e[2]('disclaimer', {'accepted': True}).status_code == 200 and e[3]().json['disclaimer_accepted'] is True
    assert e[2]('disclaimer', {'accepted': True}).status_code == 200            # 重复确认幂等
    with connect(e[0].config['DB_PATH']) as db:
        assert db.execute("SELECT COUNT(*) FROM audit WHERE entity='settings'").fetchone()[0] == 1

def test_state_and_summary_whitelists(tmp_path):
    from waybeside.sessions import STATE_TABLES, SUMMARY_FIELDS
    e = make_env(tmp_path)
    block(e, 'basic', BASIC); e[2]('agent-share', {'category': 'age_band', 'enabled': True, 'version': 1})
    state = e[3]().json
    assert not {'questionnaire_answer', 'agent_share', 'basic', 'diet', 'habit'} & set(state)
    assert {t for t in state} >= set(STATE_TABLES)
    assert all(a['entity'] not in ('questionnaire', 'agent_share', 'settings') for a in state['audit'])
    assert set(json.loads(e[3]('summary').data)) == set(SUMMARY_FIELDS)

def test_no_profile_endpoint_and_no_unfiltered_get_of_answers(tmp_path):
    e = make_env(tmp_path)
    assert e[3]('profile-preview').status_code == 404
    assert e[3]('onboarding/basic').status_code in (404, 405)
    assert e[3]('questionnaire').status_code == 404

def test_post_requires_csrf_and_origin(tmp_path):
    e = make_env(tmp_path)
    for path, body in (('onboarding/basic', {'answers': BASIC, 'version': 0}), ('disclaimer', {'accepted': True}),
                       ('agent-share', {'category': 'age_band', 'enabled': True, 'version': 1})):
        assert e[1].post('/api/' + path, base_url=ORIGIN, json=body, headers={'Origin': ORIGIN}).status_code == 403
        assert e[1].post('/api/' + path, base_url=ORIGIN, json=body, headers={'X-CSRF-Token': 'x'}).status_code == 403
