"""迁移 007：旧库升级、恢复备份、演示库三种情形下的方式初值；升级前后逐表一致。"""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from support import ORIGIN, TODAY
from waybeside import create_app
from waybeside.demo import populate
from waybeside.menu_door import versions, shared_values
from waybeside.menu_library import synthetic_library, digest
from waybeside.storage import connect, encode, restore, migration_files, effective_flags, share_flags
from waybeside import rules as rule_filter

TARGET = '2032-01-01'


def build_schema6(path, disclaimer):
    """用 001-006 迁移建出一个带数据的旧库（不经过现行 initialize）。"""
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)')
        for file in migration_files():
            version = int(file.name.split('_')[0])
            if version <= 6:
                db.executescript(file.read_text())
                db.execute('INSERT INTO schema_migrations VALUES(?)', (version,))
        db.executemany('INSERT INTO metadata VALUES(?,?)', [('app_id', 'waybeside'), ('schema_version', '6')])
        if disclaimer:
            db.execute("INSERT INTO metadata VALUES('disclaimer_ack','2031-01-01T00:00:00+00:00')")
        db.execute("INSERT INTO treatment_course(id,treatment_type,label,count_unit,total_count,schedule_kind,weekdays,start_date,version) "
                   "VALUES(1,'radiotherapy','示例疗程','session',3,'weekdays','[0,2,4]','2031-03-03',1)")
        for n, day in enumerate(('2031-03-03', '2031-03-05', '2031-03-07'), 1):
            db.execute("INSERT INTO treatment_session(course_id,ordinal,planned_date,status,confirmation_source,version) VALUES(1,?,?,'planned','local-user',1)", (n, day))
            db.execute("INSERT INTO calendar(session_id,date,kind,version) VALUES(?,?,'treatment',1)", (n, day))
        db.execute("INSERT INTO observation VALUES('d1',NULL,'2031-03-04T08:00','daily',?,2)", (encode({'date': '2031-03-04', 'note': '示例', 'symptoms': {'示例症状': 'some'}}),))
        labs = [dict(date='2031-03-01', metric='示例指标', unit='示例单位', lab='虚构实验室', value=v, low=1, high=9) for v in (3, 4)]
        db.execute("INSERT INTO observation VALUES('aux',NULL,'2031-03-01','aux',?,3)", (encode({'labs': labs, 'question': '', 'rhythm': {'blood': None, 'chemistry': None}, 'celebration': False}),))
        db.execute("INSERT INTO audit(actor,occurred_at,entity,entity_id,old_value,new_value) VALUES('local-user','2031-03-04T08:00','observation','d1',NULL,'{}')")
        db.execute("INSERT INTO questionnaire_answer VALUES('basic',?,1,2)", (encode({'nickname': '示例', 'helper_mode': 'self', 'locale': 'zh-CN'}),))
        db.execute("UPDATE agent_share SET enabled=1,version=2 WHERE category='menu_library'")
        library = synthetic_library()
        db.execute('INSERT INTO menu_library VALUES(?,?,?,1)', (library['version'], digest(library), encode(library)))
        db.execute('INSERT INTO menu_active VALUES(1,?)', (library['version'],))
    return library


def snapshot(path):
    with connect(path) as db:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: [dict(r) for r in db.execute(f'SELECT * FROM {n} ORDER BY rowid')] for n in names}


def metadata(path):
    with connect(path) as db:
        return dict(db.execute('SELECT key,value FROM metadata').fetchall())


@pytest.mark.parametrize('disclaimer,expected', [(True, 'menu_only'), (False, 'unset')])
def test_upgrade_sets_the_initial_mode_from_the_disclaimer(tmp_path, disclaimer, expected):
    path = tmp_path / 'waybeside.sqlite'
    build_schema6(path, disclaimer)
    before = snapshot(path)
    app = create_app(path, today=TODAY)
    after = snapshot(path)
    meta = metadata(path)
    assert meta['assistant_mode'] == expected and meta['assistant_mode_version'] == '0' and meta['schema_version'] == '7'
    assert meta.get('disclaimer_ack') == (None if not disclaimer else meta['disclaimer_ack'])
    # 逐表一致：除 observation 的新增列、检验行的新增字段、metadata 新增键、schema_migrations 新增一行
    for table, old_rows in before.items():
        if table in ('observation', 'metadata', 'schema_migrations'):
            continue
        assert after[table] == old_rows, table
    assert [r['version'] for r in after['schema_migrations']] == [r['version'] for r in before['schema_migrations']] + [7]
    for old, new in zip(before['observation'], after['observation']):
        assert new.pop('recorded_by') == 'local-user'
        if old['kind'] == 'aux':
            labs = json.loads(new['content'])['labs']
            assert [l.pop('recorded_by') for l in labs] == ['local-user', 'local-user']
            new['content'] = encode({**json.loads(new['content']), 'labs': labs})
            old = dict(old, content=encode(json.loads(old['content'])))
        else:
            new['content'] = encode(json.loads(new['content']))
            old = dict(old, content=encode(json.loads(old['content'])))
        assert new == old
    # /api/state 与迁移前的各表一致（audit 取不被遮盖的列）
    client = app.test_client()
    code = app.config['LOGIN_CODE']
    assert client.post('/api/login', base_url=ORIGIN, json={'token': code}, headers={'Origin': ORIGIN}).status_code == 200
    state = client.get('/api/state', base_url=ORIGIN).json
    assert state['assistant_mode'] == expected and state['disclaimer_accepted'] is disclaimer
    assert [s['id'] for s in state['treatment_session']] == [1, 2, 3]
    assert all(s['confirmation_source'] == 'local-user' for s in state['treatment_session'])
    assert [a['entity_id'] for a in state['audit']] == ['d1']


def test_upgrade_makes_waiting_and_ready_candidates_stale(tmp_path):
    path = tmp_path / 'waybeside.sqlite'
    library = build_schema6(path, True)
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        lib = dict(db.execute('SELECT * FROM menu_library').fetchone())
        flags = share_flags(db)
        # 旧版本的 state_digest：不含方式
        state, revision = (None, 0)
        prefs = encode([])
        rules_digest = rule_filter.context(db, path, lib)['digest']
        old_digest = hashlib.sha256(encode([lib['version'], revision, prefs, flags, shared_values(db, flags), rules_digest]).encode()).hexdigest()
        snap = {'snapshot_versions': {'library_hash': lib['digest'], 'state_digest': old_digest}}
        for ident, status in (('job-waiting', 'WAITING_EXTERNAL'), ('job-ready', 'READY_TO_REVIEW')):
            db.execute('INSERT INTO menu_candidate VALUES(?,?,?,?,?,?)',
                       (ident, TARGET if status == 'READY_TO_REVIEW' else '2032-01-02', status, encode(snap),
                        encode({'reason_code': 'KEEP_CURRENT', 'open_questions': [], 'mapping': {}}) if status == 'READY_TO_REVIEW' else None, '2031-03-04'))
    app = create_app(path, today=TODAY)
    client = app.test_client()
    client.post('/api/login', base_url=ORIGIN, json={'token': app.config['LOGIN_CODE']}, headers={'Origin': ORIGIN})
    ready = client.get('/api/menu/candidate?target_date=' + TARGET, base_url=ORIGIN).json['candidate']
    waiting = client.get('/api/menu/candidate?target_date=2032-01-02', base_url=ORIGIN).json['candidate']
    # 升级后方式是 menu_only，其摘要多了一项方式 -> 与旧摘要不同
    with connect(path) as db:
        db.execute('BEGIN')
        new_digest = versions(db, lib, TARGET, path)['state_digest']
    assert new_digest != old_digest
    assert ready['status'] == 'STALE'
    assert waiting is not None and waiting['status'] == 'WAITING_EXTERNAL'      # 等待中的由下一次 export / submit 标记为 STALE
    from waybeside import agent_door
    from waybeside.menu_door import CandidateError
    cand = tmp_path / 'c.json'
    cand.write_text(json.dumps({'job_id': 'job-waiting'}))
    with pytest.raises(CandidateError) as error:
        agent_door.submit_candidate(path, cand)
    assert error.value.status == 'STALE'


def test_restore_of_an_older_backup_starts_unset_with_the_disclaimer_kept(tmp_path):
    source = tmp_path / 'old.sqlite'
    build_schema6(source, True)
    target = tmp_path / 'restored'
    restore(source, target)
    assert metadata(target / 'waybeside.sqlite')['assistant_mode'] == 'unset'
    assert 'disclaimer_ack' in metadata(target / 'waybeside.sqlite')
    create_app(target / 'waybeside.sqlite', today=TODAY)                # 由新版本启动：升级不改回 menu_only
    meta = metadata(target / 'waybeside.sqlite')
    assert meta['assistant_mode'] == 'unset' and meta['schema_version'] == '7' and 'disclaimer_ack' in meta


def test_restore_of_a_current_backup_resets_the_mode_too(tmp_path):
    path = tmp_path / 'a' / 'waybeside.sqlite'
    create_app(path, today=TODAY)
    with connect(path) as db:
        db.execute("UPDATE metadata SET value='full' WHERE key='assistant_mode'")
        db.execute("INSERT INTO metadata VALUES('disclaimer_ack','x')")
    from waybeside.storage import backup
    backup(path, tmp_path / 'b.sqlite')
    restore(tmp_path / 'b.sqlite', tmp_path / 'r')
    meta = metadata(tmp_path / 'r' / 'waybeside.sqlite')
    assert meta['assistant_mode'] == 'unset' and meta['disclaimer_ack'] == 'x'


def test_demo_directory_starts_in_menu_only(tmp_path):
    path = tmp_path / 'waybeside.sqlite'
    assert populate(path, TODAY) is True
    assert metadata(path)['assistant_mode'] == 'menu_only'
    labs = next(r for r in snapshot(path)['observation'] if r['id'] == 'aux')
    assert {l['recorded_by'] for l in json.loads(labs['content'])['labs']} == {'local-user'}
    assert populate(path, TODAY) is False                                  # 再次启动不重复写
    with connect(path) as db:
        db.execute("UPDATE metadata SET value='unset' WHERE key='assistant_mode'")
    populate(path, TODAY)
    assert metadata(path)['assistant_mode'] == 'unset'                     # 演示只在新建时设定一次


def test_effective_flags_follow_the_mode(tmp_path):
    path = tmp_path / 'waybeside.sqlite'
    create_app(path, today=TODAY)
    with connect(path) as db:
        assert not any(effective_flags(db).values())
        db.execute("UPDATE metadata SET value='full' WHERE key='assistant_mode'")
        assert all(effective_flags(db).values()) and len(effective_flags(db)) == 7
        assert not any(share_flags(db).values())


# ---------------------------------------------------------------- 升级提示（只提示一次）
def test_upgrade_notice_only_for_upgraded_libraries_that_became_menu_only(tmp_path):
    old = tmp_path / 'old' / 'waybeside.sqlite'
    old.parent.mkdir()
    build_schema6(old, True)
    create_app(old, today=TODAY)
    assert metadata(old).get('upgrade_notice') == 'pending'
    never = tmp_path / 'never' / 'waybeside.sqlite'
    never.parent.mkdir()
    build_schema6(never, False)
    create_app(never, today=TODAY)
    assert 'upgrade_notice' not in metadata(never)                          # 没确认过免责 -> unset，不提示
    fresh = tmp_path / 'fresh' / 'waybeside.sqlite'
    create_app(fresh, today=TODAY)
    assert 'upgrade_notice' not in metadata(fresh)
    demo = tmp_path / 'demo' / 'waybeside.sqlite'
    populate(demo, TODAY)
    assert 'upgrade_notice' not in metadata(demo)


def test_restore_never_carries_the_notice(tmp_path):
    source = tmp_path / 'old.sqlite'
    build_schema6(source, True)
    restore(source, tmp_path / 'r1')
    create_app(tmp_path / 'r1' / 'waybeside.sqlite', today=TODAY)
    assert 'upgrade_notice' not in metadata(tmp_path / 'r1' / 'waybeside.sqlite')
    upgraded = tmp_path / 'u' / 'waybeside.sqlite'
    upgraded.parent.mkdir()
    build_schema6(upgraded, True)
    create_app(upgraded, today=TODAY)
    assert metadata(upgraded)['upgrade_notice'] == 'pending'
    from waybeside.storage import backup
    backup(upgraded, tmp_path / 'b.sqlite')
    restore(tmp_path / 'b.sqlite', tmp_path / 'r2')
    assert 'upgrade_notice' not in metadata(tmp_path / 'r2' / 'waybeside.sqlite')


def logged_in(path):
    app = create_app(path, today=TODAY)
    app.config['TESTING'] = True
    client = app.test_client()
    login = client.post('/api/login', base_url=ORIGIN, json={'token': app.config['LOGIN_CODE']}, headers={'Origin': ORIGIN})
    headers = {'Origin': ORIGIN, 'X-CSRF-Token': login.json['csrf']}
    return (lambda p, d: client.post('/api/' + p, base_url=ORIGIN, json=d, headers=headers),
            lambda p='state': client.get('/api/' + p, base_url=ORIGIN))


def test_notice_shows_once_and_goes_away_when_seen_or_when_the_mode_is_handled(tmp_path):
    for how in ('seen', 'same-mode', 'switch'):
        path = tmp_path / how / 'waybeside.sqlite'
        path.parent.mkdir()
        build_schema6(path, True)
        post, get = logged_in(path)
        assert get().json['upgrade_notice'] is True and get().json['assistant_mode'] == 'menu_only'
        if how == 'seen':
            assert post('upgrade-notice', {}).json == {'upgrade_notice': False}
        else:
            mode = 'menu_only' if how == 'same-mode' else 'full'
            assert post('assistant-mode', {'mode': mode, 'version': 0}).status_code == 200
        assert get().json['upgrade_notice'] is False
        post2, get2 = logged_in(path)                                         # 重启后仍不提示
        assert get2().json['upgrade_notice'] is False


def test_notice_endpoint_is_strict_and_human_only(tmp_path):
    path = tmp_path / 'waybeside.sqlite'
    build_schema6(path, True)
    post, get = logged_in(path)
    assert post('upgrade-notice', {'x': 1}).status_code == 400
    assert get().json['upgrade_notice'] is True
    from waybeside.access import EXCLUDED_ENDPOINTS
    assert 'seen_upgrade_notice' in EXCLUDED_ENDPOINTS
