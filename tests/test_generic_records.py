"""检验、症状、演示数据的通用化验收。"""
import json
import re
from pathlib import Path
import pytest
from support import make_env
from waybeside.demo import populate
from waybeside.storage import connect

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'waybeside' / 'static'


def aux(**labs):
    row = dict(date='2030-01-06', metric='示例指标 Z', unit='示例单位', lab='虚构实验室', value=1.5, low=None, high=None)
    row.update(labs)
    return {'version': 0, 'labs': [row], 'question': '', 'rhythm': {'blood': None, 'chemistry': None}, 'celebration': True}


# ---------------- 检验指标名：用户自填，无白名单 ----------------
@pytest.mark.parametrize('metric', ['示例指标 Z', 'abc', 'x' * 40])
def test_any_metric_name_up_to_40_chars_is_accepted(tmp_path, metric):
    e = make_env(tmp_path)
    assert e[2]('aux', aux(metric=metric)).status_code == 200


@pytest.mark.parametrize('metric', ['', '   ', 'x' * 41, 5, None])
def test_empty_or_overlong_metric_is_rejected(tmp_path, metric):
    e = make_env(tmp_path)
    assert e[2]('aux', aux(metric=metric)).status_code == 400


def test_rhythm_default_is_empty_and_nothing_is_derived(tmp_path):
    e = make_env(tmp_path)
    assert e[2]('aux', aux()).status_code == 200
    content = next(r['content'] for r in e[3]().json['observation'] if r['id'] == 'aux')
    assert content['rhythm'] == {'blood': None, 'chemistry': None}
    assert 'reminders' not in json.dumps(e[3]().json) and 'next_check' not in json.dumps(e[3]().json)


# ---------------- 症状：用户自选自加 ----------------
def obs(**extra):
    return dict(date='2030-01-06', time='08:00', session='', **extra)


def test_symptoms_free_names_and_three_states(tmp_path):
    e = make_env(tmp_path)
    r = e[2]('observations', obs(symptoms={'食欲': 'some', '口腔': 'none', '皮肤': 'unknown'}))
    assert r.status_code == 200 and r.json['symptoms'] == {'食欲': 'some', '口腔': 'none', '皮肤': 'unknown'}
    assert e[2]('observations', obs()).json['symptoms'] == {}


@pytest.mark.parametrize('bad', [
    {'食欲': 'normal'}, {'': 'some'}, {' 食欲': 'some'}, {'字' * 21: 'some'}, {'食欲': 3},
    {f'项{i}': 'none' for i in range(21)}, ['食欲'], 'x'])
def test_bad_symptoms_are_rejected(tmp_path, bad):
    e = make_env(tmp_path)
    assert e[2]('observations', obs(symptoms=bad)).status_code == 400


@pytest.mark.parametrize('old', ['symptom_a', 'symptom_b', 'fatigue'])
def test_fixed_symptom_keys_are_not_accepted(tmp_path, old):
    e = make_env(tmp_path)
    assert e[2]('observations', obs(**{old: 'none'})).status_code == 400


# ---------------- 前端不再内置任何检验数据与固定数值 ----------------
LAB_LITERAL = re.compile(r"""(metric|low|high)['"]?\s*:\s*(['"][^'"]+['"]|[0-9.]+)""")


def test_frontend_has_no_builtin_lab_or_fixed_symptom_data():
    for js in STATIC.glob('*'):
        if js.suffix in ('.js', '.html'):
            assert LAB_LITERAL.search(js.read_text(encoding='utf-8')) is None, js.name


def test_frontend_default_symptoms_and_free_metric_input():
    app = (STATIC / 'app.js').read_text(encoding='utf-8')
    assert "DEFAULT_SYMPTOMS=['食欲','恶心','疲劳','疼痛']" in app
    journey = (STATIC / 'journey.js').read_text(encoding='utf-8')
    assert '<input name="metric"' in journey and 'name="metric"><option' not in journey
    assert 'let labRhythm = {blood:null,chemistry:null}' in journey and '尚未设置时不推算检查日期' in journey


# ---------------- --demo 数据：明确虚构，不含任何真实疗程特征 ----------------
def test_demo_data_is_clearly_fictional_and_has_no_fingerprint(tmp_path):
    path = tmp_path / 'waybeside.sqlite'
    populate(path, '2031-12-31')
    with connect(path) as db:
        course = dict(db.execute('SELECT * FROM treatment_course').fetchone())
        starts = [r['planned_date'] for r in db.execute('SELECT planned_date FROM treatment_session ORDER BY ordinal')]
        aux_row = json.loads(db.execute("SELECT content FROM observation WHERE id='aux'").fetchone()['content'])
    assert course['treatment_type'] == 'chemotherapy' and course['schedule_kind'] == 'manual' and course['total_count'] == 6
    assert len(starts) == 6 and all(d.startswith('2031-') for d in starts)
    assert {l['metric'] for l in aux_row['labs']} == {'示例指标 A', '示例指标 B'}
    assert {(l['unit'], l['lab'], l['low'], l['high']) for l in aux_row['labs']} == {('示例单位', '虚构实验室', 10, 20)}
    assert aux_row['rhythm'] == {'blood': None, 'chemistry': None}
    assert 'radiotherapy' not in json.dumps(aux_row, ensure_ascii=False)


def test_demo_labs_survive_a_normal_edit_roundtrip(tmp_path):
    """演示检验的日期不得晚于“今天”，否则用户在界面里一保存就被未来日期校验挡住。"""
    path = tmp_path / 'waybeside.sqlite'
    populate(path, '2031-12-31')
    e = make_env(tmp_path, today='2031-12-31')
    state = e[3]().json
    row = next(r for r in state['observation'] if r['id'] == 'aux')
    body = dict(row['content'], version=row['version'])
    assert e[2]('aux', body).status_code == 400            # 检验行带 recorded_by 一律拒绝
    body['labs'] = [{k: v for k, v in lab.items() if k != 'recorded_by'} for lab in body['labs']]
    assert e[2]('aux', body).status_code == 200


def test_examples_dir_has_no_october_2026_dates():
    hits = [p for p in (ROOT / 'examples').rglob('*') if p.is_file() and '2026-10' in p.read_text(encoding='utf-8')]
    assert hits == []
