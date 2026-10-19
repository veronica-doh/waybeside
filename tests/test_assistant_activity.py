"""网页用的只读端点：助手最近做了什么 + 哪些内容最近由助手修改。全部使用虚构数据。"""
import json

import pytest
from waybeside import create_app
from support import make_env, choose_mode, Cli, course_settings, ORIGIN
from test_operator_core import aux_body, lab, library_ready, TARGET

SENTINEL = 'SENTINEL_VALUE_9b2e'


@pytest.fixture
def env(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    return make_env(data, mode=None)


@pytest.fixture
def cli(env, tmp_path, monkeypatch, capsys):
    return Cli(monkeypatch, capsys, tmp_path / 'data')


def activity(env):
    r = env[3]('assistant-activity')
    assert r.status_code == 200, r.data
    return r.json


def test_endpoint_needs_login_and_is_not_open_to_the_command_line(env, cli):
    choose_mode(env, 'full')
    anonymous = create_app(env[0].config['DB_PATH'], today='2031-12-31', issue_code=False)
    assert anonymous.test_client().get('/api/assistant-activity', base_url=ORIGIN).status_code == 401
    code, out = cli.api('GET', '/api/assistant-activity')
    assert code == 2 and out['error'] == 'ROUTE_NOT_ALLOWED'


def test_recent_list_has_actions_and_dates_but_never_values(env, cli):
    choose_mode(env, 'full')
    assert activity(env) == {'recent': [], 'by_agent': {'questionnaire': {b: False for b in ('basic', 'treatment', 'diet', 'habit')},
                                                       'course': False, 'menu_adopt': {}}, 'rule_overrides': [], 'off_schedule': {}}
    assert cli.api('POST', '/api/onboarding/basic', {'answers': {'nickname': SENTINEL, 'helper_mode': 'self'}, 'version': 0})[0] == 0
    assert cli.api('POST', '/api/observations', dict(date='2031-12-30', time='08:00', note=SENTINEL))[0] == 0
    assert cli.api('POST', '/api/aux', aux_body([lab(SENTINEL, 5)], 0))[0] == 0
    body = activity(env)
    assert [r['kind'] for r in body['recent']] == ['labs', 'daily', 'questionnaire']       # 最新在前
    assert all(set(r) == {'kind', 'at'} for r in body['recent'])
    assert SENTINEL not in json.dumps(body, ensure_ascii=False)
    assert body['by_agent']['questionnaire']['basic'] is True and body['by_agent']['questionnaire']['diet'] is False


def test_recent_list_is_capped_at_thirty_and_only_counts_the_agent(env, cli):
    choose_mode(env, 'full')
    for n in range(35):
        assert cli.api('POST', '/api/observations', dict(date='2031-12-30', time=f'08:{n:02}', note='x'))[0] == 0
    assert env[2]('observations', dict(date='2031-12-29', time='09:00')).status_code == 200      # 人的记录不进列表
    assert len(activity(env)['recent']) == 30


def test_a_later_human_edit_clears_the_recently_changed_by_assistant_flags(env, cli):
    choose_mode(env, 'full')
    assert cli.api('POST', '/api/onboarding/basic', {'answers': {'helper_mode': 'self'}, 'version': 0})[0] == 0
    assert cli.api('POST', '/api/onboarding/treatment', {'answers': course_settings(), 'version': 0})[0] == 0
    flags = activity(env)['by_agent']
    assert flags['questionnaire']['basic'] and flags['questionnaire']['treatment'] and flags['course']
    assert env[2]('onboarding/basic', {'answers': {'helper_mode': 'self'}, 'version': 1}).status_code == 200
    state = env[3]().json
    mine = dict(course_settings(), settings_version=state['settings_version'])
    assert env[2]('course', mine).status_code == 200
    flags = activity(env)['by_agent']
    assert flags['questionnaire']['basic'] is False and flags['questionnaire']['treatment'] is True and flags['course'] is False


def test_session_actions_and_off_schedule_note(env, cli):
    choose_mode(env, 'full')
    assert env[2]('course', course_settings()).status_code == 200
    revision = env[3]().json['course_revision']
    assert env[2]('course/generate', {'expected_revision': revision}).status_code == 201
    first = env[3]().json['treatment_session'][0]
    off_day = '2031-03-04'          # 周二，不在 [周一, 周三, 周五]
    code, out = cli.api('POST', f"/api/sessions/{first['id']}", {'action': 'complete', 'date': off_day, 'version': first['version'], 'confirm_off_schedule': True})
    assert code == 0, out
    body = activity(env)
    assert body['recent'][0]['kind'] == 'treatment_session.complete'
    assert body['off_schedule'] == {str(first['id']): 'complete'}
    session = env[3]().json['treatment_session'][0]
    assert session['confirmation_source'] == 'agent'
    # 人更正后，说明消失
    assert env[2](f"sessions/{first['id']}", {'action': 'correct', 'date': off_day, 'version': session['version']}).status_code == 200
    assert activity(env)['off_schedule'] == {}


def test_menu_adoption_flag_and_hidden_group_note(tmp_path, monkeypatch, capsys):
    from test_rules import setup as rules_setup, example, TOMORROW
    environment, path = rules_setup(tmp_path, {'demo.json': example()})
    choose_mode(environment, 'full')
    run = Cli(monkeypatch, capsys, tmp_path)
    state = run.api('GET', f'/api/menu?date={TOMORROW}')[1]['body']['state']
    base = dict(target_date=TOMORROW, revision=state['revision'], library_hash=state['library_hash'], group_id='02')
    assert run.api('POST', '/api/menu/action', dict(base, action='select', rule_confirmed=True))[0] == 0
    state = run.api('GET', f'/api/menu?date={TOMORROW}')[1]['body']['state']
    assert run.api('POST', '/api/menu/action', dict(base, revision=state['revision'], action='adopt', rule_confirmed=True))[0] == 0
    body = environment[3]('assistant-activity').json
    assert body['by_agent']['menu_adopt'] == {TOMORROW: True}
    assert body['rule_overrides'] == [{'target_date': TOMORROW, 'group_id': '02'}]
    # 人撤销之后，标注消失
    state = environment[3]('menu?date=' + TOMORROW).json['state']
    r = environment[2]('menu/action', dict(action='undo', target_date=TOMORROW, revision=state['revision'], library_hash=state['library_hash']))
    assert r.status_code == 200
    assert environment[3]('assistant-activity').json['by_agent']['menu_adopt'] == {TOMORROW: False}


def test_export_and_submit_show_up_as_the_assistants_actions(env, cli):
    choose_mode(env, 'full')
    library_ready(env)
    code, out = cli('export', '--date', TARGET, '--output', cli.work / 'p.json')
    assert code == 0
    package = json.loads((cli.work / 'p.json').read_text(encoding='utf-8'))
    candidate = cli.work / 'c.json'
    candidate.write_text(json.dumps({**package['candidate_template'], 'group_id': package['snapshot']['groups'][0]['group_id'], 'reason_code': 'VARIETY'}), encoding='utf-8')
    code, res = cli('submit', candidate)
    assert code == 0 and res['status'] == 'READY_TO_REVIEW'
    kinds = [r['kind'] for r in activity(env)['recent']]
    assert kinds[:2] == ['menu_candidate.READY_TO_REVIEW', 'menu_candidate.WAITING_EXTERNAL']
