"""备份恢复认 metadata.app_id；新备份无需任何种子标记即可恢复。"""
import sqlite3
import pytest
from support import make_env, make_course, act, ORIGIN
from waybeside import create_app
from waybeside.menu_library import synthetic_library, import_library
from waybeside.storage import connect, restore, schema_version

OLD_MARK='_'.join(('synthetic','seed'))      # 旧版用来判断“是本应用备份”的标记名

def tables(path):
    with connect(path) as db:
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n:[tuple(r) for r in db.execute(f'SELECT * FROM {n} ORDER BY rowid')] for n in names}

def test_new_directory_with_data_backup_and_restore_to_empty_dir(tmp_path):
    e=make_env(tmp_path/'a',today='2031-12-31')
    path=e[0].config['DB_PATH']
    make_course(e)                                                  # 测试直接用设置接口建立疗程
    assert act(e,1,'complete').status_code==200
    assert e[2]('observations',dict(date='2031-03-04',time='08:00',session='1',note='虚构')).status_code==200
    import_library(path,synthetic_library())
    backup=e[2]('backup',{})
    assert backup.status_code==200
    file=tmp_path/'backup.sqlite';file.write_bytes(backup.data)
    target=tmp_path/'restored'
    restore(file,target)
    restored=target/'waybeside.sqlite'
    left,right=tables(path),tables(restored)
    assert dict(right['metadata'])['assistant_mode']=='unset'      # 恢复后方式重置，免责确认等其余照旧
    left['metadata']=[r for r in left['metadata'] if not r[0].startswith('assistant_mode')]
    right['metadata']=[r for r in right['metadata'] if not r[0].startswith('assistant_mode')]
    assert left==right and left['metadata'] and any(k=='app_id' for k,_ in left['metadata'])
    assert len(left['treatment_session'])==6 and left['menu_library']
    app=create_app(restored,today='2031-12-31')                   # 恢复后的库可直接启动，读回一致
    c=app.test_client()
    c.post('/api/login',base_url=ORIGIN,headers={'Origin':ORIGIN},json={'token':app.config['LOGIN_CODE']})
    state=c.get('/api/state',base_url=ORIGIN).json
    assert state['completed']==1 and state['course_revision']==left['course_revision'][0][1]

def test_empty_new_directory_backup_restores(tmp_path):
    e=make_env(tmp_path/'a')
    file=tmp_path/'b.sqlite';file.write_bytes(e[2]('backup',{}).data)
    restore(file,tmp_path/'r')
    restored,original=tables(tmp_path/'r'/'waybeside.sqlite'),tables(e[0].config['DB_PATH'])
    assert dict(restored.pop('metadata'))['assistant_mode']=='unset'
    original.pop('metadata')
    assert restored==original

def plain_sqlite(path,statements):
    with sqlite3.connect(path) as db:
        for s in statements:db.execute(s)
    db.close()

@pytest.mark.parametrize('statements',[
    [],                                                                                        # 空库，无 metadata 表
    ['CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)'],                      # 无 app_id
    ['CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)',"INSERT INTO metadata VALUES('app_id','other-app')"],
    ['CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)',f"INSERT INTO metadata VALUES('{OLD_MARK}','v1')"],   # 旧判据不再被承认
])
def test_foreign_or_unmarked_database_is_refused(tmp_path,statements):
    file=tmp_path/'x.sqlite';plain_sqlite(file,statements)
    with pytest.raises(ValueError,match='不是本应用备份'):restore(file,tmp_path/'r')
    assert not (tmp_path/'r'/'waybeside.sqlite').exists()

def test_non_sqlite_file_is_refused(tmp_path):
    file=tmp_path/'x.sqlite';file.write_text('not a database')
    with pytest.raises(Exception):restore(file,tmp_path/'r')
    assert not (tmp_path/'r'/'waybeside.sqlite').exists()

def test_backup_from_newer_schema_is_refused(tmp_path):
    e=make_env(tmp_path/'a')
    with connect(e[0].config['DB_PATH']) as db:
        db.execute("UPDATE metadata SET value=? WHERE key='schema_version'",(str(schema_version()+1),))
    file=tmp_path/'new.sqlite'
    from waybeside.storage import backup
    backup(e[0].config['DB_PATH'],file)
    with pytest.raises(ValueError,match='更新版本'):restore(file,tmp_path/'r')

def test_restore_refuses_non_empty_directory(tmp_path):
    e=make_env(tmp_path/'a')
    file=tmp_path/'b.sqlite';file.write_bytes(e[2]('backup',{}).data)
    (tmp_path/'r').mkdir();(tmp_path/'r'/'keep.txt').write_text('x')
    with pytest.raises(ValueError):restore(file,tmp_path/'r')
