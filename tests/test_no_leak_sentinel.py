"""反向哨兵测试：问卷类答案只入后台，不出现在不该出现的出口。

组 1 · 永不出包字段：昵称 / 过敏提醒 / 备注 / 习惯默认值 / 谁在用（枚举）——
  在 /api/state、/api/summary、除 GET /api/settings 外的所有 GET、audit 全表、agent 包（七类全开）里都不得出现。
  检验与症状备注是用户自己的健康记录，/api/state 与 /api/summary 按白名单本来就含 observation，
  所以它们的哨兵只对 agent 包、非记录类 GET、问卷类 audit 断言（见 test_record_sentinels_*）。
组 2 · 可出包字段：关则不在、开则恰在约定位置；备份 SQLite 本身含全部数据，不在断言范围。
"""
import json
import re
from pathlib import Path
import pytest
from support import make_env, open_shares, SHARE_CATEGORIES
from waybeside.agent_door import export_package
from waybeside.menu_library import synthetic_library, import_library
from waybeside.storage import connect, REDACTED_ENTITIES

TARGET = '2026-10-17'
S = dict(nick='SENTINEL_NICK_7f3a', allergy='SENT_ALLERGY_7f3a', note='SENTINEL_NOTE_7f3a',
         taglabel='SENT_TAGLABEL_7f3a', lab='SENTINEL_LAB_7f3a', obs='SENTINEL_OBSNOTE_7f3a')
TAG = 'sentinel_tag_7f3a'
HABIT_TIME, HABIT_MINUTES = '03:17', 173
STRINGS = [S['nick'], S['allergy'], S['note'], S['taglabel'], 'with_helper']      # 问卷类、永不出包（helper_mode 取枚举值）
RECORD_STRINGS = [S['lab'], S['obs']]                                              # 用户自己的检验 / 症状记录

def leaves(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from leaves(v)
    elif isinstance(value, list):
        for v in value:
            yield from leaves(v)
    else:
        yield value

def assert_clean(text, strings, where):
    for needle in strings:
        assert needle not in text, f'{needle} 出现在 {where}'
    try:
        data = json.loads(text)
    except ValueError:
        return
    found = set(map(str, leaves(data)))
    assert HABIT_TIME not in found and str(HABIT_MINUTES) not in found, f'习惯默认值出现在 {where}'

@pytest.fixture
def env(tmp_path):
    e = make_env(tmp_path, today='2026-10-16')
    path = tmp_path / 'waybeside.sqlite'
    import_library(path, synthetic_library())
    posts = []
    def post(path_, body):
        r = e[2](path_, body); assert r.status_code in (200, 201), (path_, r.data); posts.append(r.get_data(as_text=True)); return r
    post('disclaimer', {'accepted': True})
    post('onboarding/basic', {'answers': {'nickname': S['nick'], 'helper_mode': 'with_helper', 'age_band': '40_64'}, 'version': 0})
    post('onboarding/treatment', {'answers': dict(treatment_type='chemotherapy', label='测试疗程', count_unit='cycle', total_count=3,
                                                  schedule_kind='manual', weekdays=[], start_date=None, settings_version=0), 'version': 0})
    post('onboarding/diet', {'answers': {'custom_tags': [{'id': TAG, 'label': S['taglabel']}], 'requirement_tags': [TAG],
                                         'allergy_reminders': [S['allergy']], 'free_note': S['note']}, 'version': 0})
    post('onboarding/habit', {'answers': {'sleep_usual': 'irregular', 'activity_usual': 'light',
                                          'meditation_usual': {'time': HABIT_TIME, 'minutes': HABIT_MINUTES}}, 'version': 0})
    post('aux', {'version': 0, 'labs': [{'date': '2026-10-15', 'metric': '示例指标', 'unit': S['lab'], 'lab': S['lab'], 'value': 4.2, 'low': None, 'high': None}],
                 'question': S['obs'], 'rhythm': {'blood': None, 'chemistry': None}, 'celebration': True})
    post('observations', {'date': '2026-10-15', 'time': '12:00', 'note': S['obs']})
    return e, path, posts

def get_routes(app):
    skip = {'static', 'get_settings'}
    return sorted({r.rule for r in app.url_map.iter_rules()
                   if 'GET' in r.methods and not r.arguments and r.endpoint not in skip})

# ---------------- 组 1 ----------------
def test_group1_questionnaire_sentinels_not_in_state_summary_or_any_other_get(env):
    e, path, posts = env
    get = e[3]
    assert_clean(get('state').get_data(as_text=True), STRINGS, 'GET /api/state')
    assert_clean(get('summary').get_data(as_text=True), STRINGS, 'GET /api/summary')
    seen = []
    for rule in get_routes(e[0]):
        if rule in ('/', '/api/state', '/api/summary'):
            continue
        for query in ('', f'?date={TARGET}&target_date={TARGET}'):
            r = e[1].get(rule + query, base_url='http://127.0.0.1:8765')
            seen.append(rule)
            assert_clean(r.get_data(as_text=True), STRINGS + RECORD_STRINGS, f'GET {rule}{query}')
    assert '/api/menu' in seen and '/api/agent-share' in seen and '/api/session' in seen
    for body in posts:
        assert_clean(body, STRINGS, 'POST 响应')

def test_group1_settings_is_the_only_readback_and_holds_only_editable_fields(env):
    e = env[0]
    s = e[3]('settings').json
    assert s['basic']['nickname'] == S['nick'] and s['diet']['free_note'] == S['note']        # 对应页面读取：允许
    assert set(s) == {'basic', 'treatment', 'diet', 'habit', 'builtin_tags', 'versions', 'completed', 'onboarded'}

def test_group1_audit_tables_are_redacted_everywhere(env):
    e, path, _ = env
    # 再产生一批问卷类与开关类 audit
    open_shares(e[2], e[3], SHARE_CATEGORIES)
    e[2]('onboarding/diet', {'answers': {'free_note': S['note'] + 'x'}, 'version': 1})
    with connect(path) as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM audit')]
    classified = [r for r in rows if r['entity'] in REDACTED_ENTITIES]
    assert len(classified) >= 12
    for r in classified:
        assert r['old_value'] is None and r['new_value'] is None
    whole = json.dumps(rows, ensure_ascii=False)
    assert_clean(whole, STRINGS, 'audit 全表')
    assert_clean(json.dumps(classified, ensure_ascii=False), STRINGS + RECORD_STRINGS, '问卷类 audit')

def test_group1_agent_package_with_all_seven_categories_on(env):
    e, path, _ = env
    open_shares(e[2], e[3], SHARE_CATEGORIES)
    package = json.loads(Path(export_package(path, TARGET)['package']).read_text())
    text = json.dumps(package, ensure_ascii=False)
    assert_clean(text, STRINGS + RECORD_STRINGS, 'agent 资料包')
    allowed = {'protocol', 'snapshot', 'candidate_template', 'candidate_format', 'allowed_changes'}
    assert set(package) == allowed

def test_record_sentinels_only_where_the_user_reads_their_own_records(env):
    """检验 / 症状记录按白名单只出现在 state 与 summary（用户自己的记录页读取），其余出口一律没有。"""
    e, path, _ = env
    assert S['lab'] in e[3]('state').get_data(as_text=True)
    assert S['lab'] in e[3]('summary').get_data(as_text=True)
    with connect(path) as db:
        classified = json.dumps([dict(r) for r in db.execute("SELECT * FROM audit WHERE entity IN ('questionnaire','agent_share','settings')")], ensure_ascii=False)
    assert_clean(classified, RECORD_STRINGS, '问卷类 audit')

# ---------------- 组 2 ----------------
def package(env_):
    return json.loads(Path(export_package(env_[1], TARGET)['package']).read_text())

def test_group2_shareable_fields_absent_when_off_exact_when_on(env):
    e, path, _ = env
    open_shares(e[2], e[3], ('menu_library',))
    off = package(env)
    off_text = json.dumps(off, ensure_ascii=False)
    for needle in (TAG, '40_64'):
        assert needle not in off_text
    assert len(off['snapshot']['groups']) == len(synthetic_library()['days'])          # 关闭时 groups 为全集
    for key in ('requirement_tags', 'age_band', 'course_progress'):
        assert key not in off['snapshot']

    open_shares(e[2], e[3], ('requirement_tags',))
    on = package(env)
    on_text = json.dumps(on, ensure_ascii=False)
    assert on['snapshot']['requirement_tags'] == [TAG] and on_text.count(TAG) == 1
    assert len(on['snapshot']['groups']) == len(synthetic_library()['days'])
    assert 'age_band' not in on['snapshot'] and '40_64' not in on_text and S['taglabel'] not in on_text

    open_shares(e[2], e[3], ('age_band',))
    band = package(env)
    assert band['snapshot']['age_band'] == '40_64' and json.dumps(band).count('40_64') == 1

    open_shares(e[2], e[3], ('course_progress',))
    progress = package(env)
    assert progress['snapshot']['course_progress'] == {'completed_count': 0, 'total_count': 3}
    assert not re.search(r'\d{4}-\d{2}-\d{2}', json.dumps(progress['snapshot']['course_progress']))

def test_group2_turning_a_category_back_off_removes_it(env):
    e, path, _ = env
    open_shares(e[2], e[3], ('menu_library', 'requirement_tags'))
    assert TAG in json.dumps(package(env))
    row = next(r for r in e[3]('agent-share').json if r['category'] == 'requirement_tags')
    assert e[2]('agent-share', {'category': 'requirement_tags', 'enabled': False, 'version': row['version']}).status_code == 200
    assert TAG not in json.dumps(package(env))
