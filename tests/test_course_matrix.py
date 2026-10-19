"""参数化：总数 × 排期方式 × 治疗类型，以及总数/开始日未知、星期 [0,2,4]、手动逐条添加。全部虚构。"""
import datetime
import pytest
from support import make_env, course_settings, make_course, act

TYPES = ['radiotherapy', 'chemotherapy', 'other']

@pytest.mark.parametrize('total', [5, 10, 30])
@pytest.mark.parametrize('kind', ['weekdays', 'manual'])
@pytest.mark.parametrize('treatment', TYPES)
def test_matrix(tmp_path, total, kind, treatment):
    e = make_env(tmp_path)
    unit = 'cycle' if treatment == 'chemotherapy' else 'session'
    if kind == 'weekdays':
        state = make_course(e, total_count=total, treatment_type=treatment, count_unit=unit)
        rows = state['treatment_session']
        assert len(rows) == total
        assert all(datetime.date.fromisoformat(r['planned_date']).weekday() in (0, 2, 4) for r in rows)
        assert rows[0]['planned_date'] == '2031-03-03'
    else:
        r = e[2]('course', course_settings(total_count=total, treatment_type=treatment, count_unit=unit,
                                            schedule_kind='manual', weekdays=[]))
        assert r.status_code == 200
        state = e[3]().json
        assert state['treatment_session'] == []
        for n in range(total):
            day = (datetime.date(2031, 3, 3) + datetime.timedelta(days=(21 if total <= 10 else 7) * n)).isoformat()
            assert e[2]('sessions', {'action': 'schedule', 'date': day, 'expected_revision': e[3]().json['course_revision']}).status_code == 201
        state = e[3]().json
    assert state['treatment_course'][0]['total_count'] == total
    assert state['treatment_course'][0]['treatment_type'] == treatment
    # 里程碑 20% / 50% / 100%
    for pct in (20, 50, 100):
        target = max(1, total * pct // 100)
        for s in e[3]().json['treatment_session'][:target]:
            if s['status'] != 'completed':
                assert act(e, s['id'], 'complete', s['planned_date'], confirm_off_schedule=True).status_code == 200
        assert e[3]().json['completed'] == target

def test_total_unknown_generates_nothing(tmp_path):
    e = make_env(tmp_path)
    assert e[2]('course', course_settings(total_count=None)).status_code == 200
    r = e[2]('course/generate', {'expected_revision': e[3]().json['course_revision']})
    assert r.status_code == 400 and r.json['code'] == 'CANNOT_GENERATE'
    assert e[3]().json['treatment_session'] == []

def test_start_date_unknown_generates_nothing(tmp_path):
    e = make_env(tmp_path)
    assert e[2]('course', course_settings(start_date=None)).status_code == 200
    r = e[2]('course/generate', {'expected_revision': e[3]().json['course_revision']})
    assert r.status_code == 400 and r.json['code'] == 'CANNOT_GENERATE'

def test_weekday_set_is_respected_for_any_choice(tmp_path):
    e = make_env(tmp_path)
    state = make_course(e, total_count=7, weekdays=[1, 6], start_date='2031-03-03')
    assert {datetime.date.fromisoformat(r['planned_date']).weekday() for r in state['treatment_session']} == {1, 6}

def test_manual_has_no_weekday_check(tmp_path):
    e = make_env(tmp_path)
    e[2]('course', course_settings(schedule_kind='manual', weekdays=[]))
    r = e[2]('sessions', {'action': 'schedule', 'date': '2031-03-08', 'expected_revision': 0})   # 周六
    assert r.status_code == 201
    assert act(e, r.json['id'], 'complete', '2031-03-08').status_code == 200

@pytest.mark.parametrize('days', [0, 366, 1.5, '7', True])
def test_lab_rhythm_free_days_validated(tmp_path, days):
    e = make_env(tmp_path)
    aux = {'version': 0, 'labs': [], 'question': '', 'rhythm': {'blood': days, 'chemistry': None}, 'celebration': True}
    assert e[2]('aux', aux).status_code == 400

def test_lab_rhythm_free_days_accepted(tmp_path):
    e = make_env(tmp_path)
    aux = {'version': 0, 'labs': [], 'question': '', 'rhythm': {'blood': 10, 'chemistry': 365}, 'celebration': True}
    assert e[2]('aux', aux).status_code == 200
