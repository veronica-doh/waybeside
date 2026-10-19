"""并发与状态规则验收。

夹具：weekdays=[0,2,4]、total_count=6、start=2031-03-03，已生成 6 条
（#1 03-03 · #2 03-05 · #3 03-07 · #4 03-10 · #5 03-12 · #6 03-14）。
A、B 两个“窗口”用同一个客户端模拟：B 带着 A 提交前读到的旧计数器。
"""
import json
import pytest
from support import make_env, make_course, act, session
from waybeside.storage import connect

@pytest.fixture
def env(tmp_path):
    e = make_env(tmp_path, today='2031-03-20')
    make_course(e)
    return e

def db_rows(env, sql, *args):
    with connect(env[0].config['DB_PATH']) as db:
        return [dict(r) for r in db.execute(sql, args)]

def sessions(env):
    return {r['id']: r for r in db_rows(env, 'SELECT * FROM treatment_session ORDER BY id')}

def revision(env):
    return db_rows(env, 'SELECT revision FROM course_revision')[0]['revision']

def schedule(env, day, rev):
    return env[2]('sessions', {'action': 'schedule', 'date': day, 'expected_revision': rev})

def test_C1_two_windows_schedule_same_revision(env):
    r = env[3]().json['course_revision']
    assert schedule(env, '2031-04-01', r).status_code == 201
    assert schedule(env, '2031-04-02', r).status_code == 409
    rows = sessions(env)
    assert len(rows) == 7 and rows[7]['planned_date'] == '2031-04-01' and rows[7]['ordinal'] == 7
    assert revision(env) == r + 1

def test_C2_update_invalidates_stale_schedule(env):
    r = env[3]().json['course_revision']
    assert act(env, 1, 'complete').status_code == 200
    assert revision(env) == r + 1                      # 单条更新也加整体 revision
    assert schedule(env, '2031-04-01', r).status_code == 409
    assert len(sessions(env)) == 6
    assert schedule(env, '2031-04-01', r + 1).status_code == 201

def test_C3_stale_row_version_cancel_conflicts(env):
    s = session(env[3]().json, 2)
    old = dict(action='cancel', date=s['planned_date'], version=s['version'])
    assert env[2]('sessions/2', dict(old, action='postpone', date='2031-03-24')).status_code == 200
    assert env[2]('sessions/2', old).status_code == 409
    row = sessions(env)[2]
    assert (row['status'], row['planned_date'], row['version']) == ('postponed', '2031-03-24', 2)

def test_C4_double_complete_same_version(env):
    s = session(env[3]().json, 3)
    body = dict(action='complete', date='2031-03-07', version=s['version'])
    assert env[2]('sessions/3', body).status_code == 200
    assert env[2]('sessions/3', body).status_code == 409
    assert sessions(env)[3]['version'] == 2

def test_C5_same_actual_day_only_one_completion(env):
    assert act(env, 3, 'complete', '2031-03-10').status_code == 200
    assert act(env, 4, 'complete', '2031-03-10').status_code == 409
    assert sessions(env)[4]['status'] == 'planned'

def test_C6_schedule_on_taken_date(env):
    r = env[3]().json['course_revision']
    assert schedule(env, '2031-03-03', r).status_code == 409          # 等于某条 planned_date
    assert act(env, 1, 'complete', '2031-03-04', confirm_off_schedule=True).status_code == 200
    r = env[3]().json['course_revision']
    assert schedule(env, '2031-03-04', r).status_code == 409          # 等于某条 actual_date
    assert len(sessions(env)) == 6

def test_C7_postpone_onto_occupied_planned_date(env):
    assert act(env, 5, 'postpone', '2031-03-14').status_code == 409   # #6 占用
    row = sessions(env)[5]
    assert (row['planned_date'], row['status'], row['version']) == ('2031-03-12', 'planned', 1)

def test_C8_illegal_state_transitions(env):
    assert act(env, 1, 'complete').status_code == 200
    assert act(env, 1, 'postpone', '2031-03-24').status_code == 409   # 已完成不可改期
    assert act(env, 2, 'cancel').status_code == 200
    assert act(env, 2, 'postpone', '2031-03-24').status_code == 409   # 已取消不可改期
    assert act(env, 3, 'restore').status_code == 409                  # 非 cancelled 不可恢复
    assert act(env, 4, 'correct').status_code == 409                  # 非 completed 不可更正
    rows = sessions(env)
    assert [rows[i]['status'] for i in (1, 2, 3, 4)] == ['completed', 'cancelled', 'planned', 'planned']
    assert [rows[i]['version'] for i in (3, 4)] == [1, 1]

@pytest.mark.parametrize('version', ['1', None, 1.0, True, 'missing'])
def test_C9_version_must_be_int(env, version):
    body = {'action': 'cancel', 'date': '2031-03-05', 'version': version}
    if version == 'missing':
        del body['version']
    assert env[2]('sessions/2', body).status_code == 409
    assert sessions(env)[2]['status'] == 'planned' and revision(env) == 1

def test_C10_settings_version_conflict(env):
    state = env[3]().json
    s, r = state['settings_version'], state['course_revision']
    base = {k: state['treatment_course'][0][k] for k in ('treatment_type', 'label', 'count_unit', 'total_count', 'schedule_kind', 'weekdays', 'start_date')}
    before = sessions(env)
    assert env[2]('course', dict(base, label='A 改的', settings_version=s)).status_code == 200
    assert env[2]('course', dict(base, label='B 改的', settings_version=s)).status_code == 409
    after = env[3]().json
    assert after['settings_version'] == s + 1 and after['course_revision'] == r
    assert after['treatment_course'][0]['label'] == 'A 改的'
    assert sessions(env) == before

def test_C11_reschedule_only_touches_untouched_planned(env):
    assert act(env, 1, 'complete').status_code == 200                      # completed
    assert act(env, 2, 'postpone', '2031-03-24').status_code == 200        # postponed
    assert act(env, 3, 'cancel').status_code == 200                        # cancelled
    state = env[3]().json
    r, s = state['course_revision'], state['settings_version']
    before = sessions(env)
    base = state['treatment_course'][0]
    body = {k: base[k] for k in ('treatment_type', 'label', 'count_unit', 'total_count', 'schedule_kind', 'start_date')}
    res = env[2]('course', dict(body, weekdays=[1, 3], settings_version=s, reschedule=True, expected_revision=r))
    assert res.status_code == 200, res.data
    after = sessions(env)
    for i in (1, 2, 3):                                                     # 逐字段相同
        assert after[i] == before[i]
    assert [after[i]['planned_date'] for i in (4, 5, 6)] == ['2031-03-20', '2031-03-25', '2031-03-27']
    assert all(after[i]['version'] == before[i]['version'] + 1 and after[i]['status'] == 'planned' for i in (4, 5, 6))
    assert [r['date'] for r in db_rows(env, 'SELECT date FROM calendar WHERE session_id IN (4,5,6) ORDER BY session_id')] == ['2031-03-20', '2031-03-25', '2031-03-27']
    assert res.json['rescheduled'] == [4, 5, 6] and revision(env) == r + 1
    assert env[3]().json['settings_version'] == s + 1
    assert schedule(env, '2031-05-01', r).status_code == 409                # 重排前的 revision 失效
    assert schedule(env, '2031-05-01', r + 1).status_code == 201
    audit = env[3]().json['audit'][-2]
    assert audit['entity'] == 'treatment_course' and audit['new_value']['moved'] == [4, 5, 6]

def test_C11b_reschedule_needs_current_revision(env):
    state = env[3]().json
    base = state['treatment_course'][0]
    body = {k: base[k] for k in ('treatment_type', 'label', 'count_unit', 'total_count', 'schedule_kind', 'start_date')}
    stale = state['course_revision']
    assert act(env, 1, 'complete').status_code == 200
    before = sessions(env)
    res = env[2]('course', dict(body, weekdays=[1, 3], settings_version=state['settings_version'], reschedule=True, expected_revision=stale))
    assert res.status_code == 409 and sessions(env) == before

def test_C12_settings_without_reschedule_changes_nothing_else(env):
    state = env[3]().json
    assert state['schedule_out_of_sync'] is False
    base = state['treatment_course'][0]
    body = {k: base[k] for k in ('treatment_type', 'label', 'count_unit', 'total_count', 'schedule_kind', 'start_date')}
    before = sessions(env)
    res = env[2]('course', dict(body, weekdays=[1, 3], settings_version=state['settings_version']))
    assert res.status_code == 200
    after = env[3]().json
    assert sessions(env) == before
    assert after['schedule_out_of_sync'] is True and after['course_revision'] == state['course_revision']

def test_C13_total_count_below_recorded(env):
    state = env[3]().json
    base = state['treatment_course'][0]
    body = {k: base[k] for k in ('treatment_type', 'label', 'count_unit', 'schedule_kind', 'weekdays', 'start_date')}
    low = env[2]('course', dict(body, total_count=5, settings_version=state['settings_version']))
    assert low.status_code == 400 and low.json['code'] == 'TOTAL_BELOW_RECORDED'
    assert env[3]().json['treatment_course'][0]['total_count'] == 6
    assert env[2]('course', dict(body, total_count=8, settings_version=state['settings_version'])).status_code == 200
    assert len(sessions(env)) == 6                                          # 增大不自动生成
    assert act(env, 6, 'cancel').status_code == 200
    assert env[2]('course', dict(body, total_count=5, settings_version=state['settings_version'] + 1)).status_code == 200  # 取消后不再占用

def test_C14_off_schedule_needs_confirmation(env):
    assert act(env, 1, 'complete', '2031-03-04').status_code == 409          # 周二，不在 [0,2,4]
    refused = act(env, 1, 'complete', '2031-03-04')
    assert refused.json['code'] == 'CONFIRM_REQUIRED'
    assert sessions(env)[1]['status'] == 'planned'
    assert act(env, 1, 'complete', '2031-03-04', confirm_off_schedule=True).status_code == 200
    row = sessions(env)[1]
    assert (row['status'], row['actual_date']) == ('completed', '2031-03-04')
    last = env[3]().json['audit'][-1]
    assert last['new_value']['off_schedule'] is True
    # 改期同样走软提示；治疗日内不要求确认，且不再要求“更晚”
    assert act(env, 2, 'postpone', '2031-03-11').json['code'] == 'CONFIRM_REQUIRED'
    assert act(env, 2, 'postpone', '2031-03-04').status_code == 409           # 与 #1 的实际日相撞
    assert act(env, 2, 'postpone', '2031-03-17').status_code == 200
    assert act(env, 2, 'postpone', '2031-03-12').status_code == 409           # 与 #5 相撞
    assert act(env, 2, 'postpone', '2031-03-31').status_code == 200           # 治疗日内的另一天
    assert act(env, 2, 'postpone', '2031-03-24').status_code == 200           # 比原日期更早也允许

def test_off_schedule_check_absent_for_manual_course(tmp_path):
    e = make_env(tmp_path, today='2031-12-31')
    make_course(e, schedule_kind='manual', weekdays=[], total_count=None)
    r = e[3]().json['course_revision']
    created = schedule(e, '2031-03-04', r)
    assert created.status_code == 201
    assert act(e, created.json['id'], 'complete', '2031-03-04').status_code == 200
