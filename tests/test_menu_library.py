"""只用自造的虚构菜单，验证事务、冻结事实和菜单导入身份规则（有 digest 才能换版本）。"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from waybeside import create_app
from waybeside.menu_library import synthetic_library, validate, import_library, active, frozen
from waybeside.storage import connect, backup, restore

@pytest.fixture
def env(tmp_path):
    path=tmp_path/'waybeside.sqlite'
    app=create_app(path,today='2026-10-16')
    app.testing=True
    import_library(path,synthetic_library())
    client=app.test_client()
    origin='http://127.0.0.1:8765'
    login=client.post('/api/login',base_url=origin,json={'token':app.config['LOGIN_CODE']},headers={'Origin':origin})
    headers={'Origin':origin,'X-CSRF-Token':login.json['csrf']}
    def get(target='2026-10-17'):
        response=client.get('/api/menu?date='+target,base_url=origin)
        assert response.status_code==200
        return response.json
    def post(action, state=None, **extra):
        state=state or get()['state']
        return client.post('/api/menu/action',base_url=origin,headers=headers,json=dict(action=action,
            target_date=state['target_date'],revision=state['revision'],library_hash=state['library_hash'],**extra))
    yield app,path,client,get,post,origin,headers

@pytest.mark.parametrize('mutation',[
    lambda d:d['days'].clear(),                                  # 放宽后：0 组仍拒绝（原 pop() 变 24 组，现为合法）
    lambda d:d['days'][0]['meals'].pop(),                        # 餐次槽位仍须五个
    lambda d:d['days'][0].update(group_id='02'),
    lambda d:d['days'][0]['meals'][0].update(meal='午餐'),
    lambda d:d['days'][0]['meals'][0]['ingredients'][0].update(g=0),
    lambda d:d['food_reference']['foods']['demo']['per_100g'].update(energy_kcal=float('nan')),
    lambda d:d['food_reference']['foods']['demo']['per_100g'].update(protein_g=-1),
    lambda d:d['food_reference']['foods']['demo'].update(source=''),
])
def test_validation_rejects(mutation):
    data=synthetic_library();mutation(data)
    with pytest.raises(ValueError):validate(data)

@pytest.mark.parametrize('count',[1,2,6,7,8,40])
def test_group_count_is_free_but_at_least_one(count):
    data=synthetic_library()
    base=data['days'][0]
    data['days']=[dict(copy.deepcopy(base),day=i,group_id=f'{i:02}') for i in range(1,count+1)]
    assert len(validate(data)['days'])==count

def test_import_atomic_idempotent_and_unknown(env):
    _,path,_,get,*_=env
    data=synthetic_library()
    assert import_library(path,data,active_digest(path))['idempotent']
    data['days'][0]['meals'][0]['ingredients'][0]['g']=99
    with pytest.raises(ValueError):import_library(path,data,active_digest(path))
    group=get()['groups'][0]
    assert group['nutrition']['fibre_g'] is None
    assert group['nutrition']['protein_g']==pytest.approx(12.75)
    assert group['meals'][0]['nutrition']['protein_g']==pytest.approx(2.55)

def test_stable_draft_adopt_undo(env):
    _,_,_,get,post,*_=env
    state=get()['state'];assert state['recommendation']['group_id']=='01'
    assert get()['state']==state
    assert post('select',state,group_id='03').status_code==200
    state=get()['state'];assert state['adopted'] is None
    assert post('adopt',state,group_id='03').status_code==200
    assert post('adopt',state,group_id='03').status_code==409
    assert get('2026-10-18')['state']['recommendation']['group_id']=='01'
    assert post('undo',get()['state']).status_code==200
    assert get()['state']['adopted'] is None

def test_lock_actual_cross_midnight_and_history(env):
    app,_,_,get,post,*_=env
    assert post('adopt',get()['state'],group_id='01').status_code==200
    app.config['TODAY']='2026-10-17'
    state=get()['state']
    assert state['adopted']['group_id']=='01'
    assert post('actual',state,meal='早餐',status='partial',note='未称重').status_code==200
    state=get()['state']
    assert state['actual']['早餐']['intake_nutrition'] is None
    assert post('actual',state,meal='早餐',status='done').status_code==409
    assert post('select',state,group_id='02').status_code==200
    state=get()['state']
    assert post('adopt',state,group_id='02').status_code==409
    assert get()['state']==state
    assert post('undo',state).status_code==409
    assert post('lock',state,meal='早餐',locked=False).status_code==200
    assert post('undo',get()['state']).status_code==200
    assert get()['state']['actual']['早餐']['status']=='partial'
    assert post('actual',get('2026-10-18')['state'],meal='午餐',status='done').status_code==400
    app.config['TODAY']='2026-10-18'
    assert post('adopt',get()['state'],group_id='02').status_code==400

def test_library_change_requires_new_preview_and_freezes_history(env):
    _,path,_,get,post,*_=env
    assert post('adopt',group_id='01').status_code==200
    old=get()['state']['adopted']
    assert post('select',group_id='02').status_code==200
    stale=get()['state']
    data=synthetic_library();data['version']='synthetic-7-v2'
    data['food_reference']['foods']['demo']['per_100g']['protein_g']=20
    with connect(path) as db:expected=active(db)['digest']
    import_library(path,data,expected)
    assert post('adopt',stale,group_id='02').status_code==409
    assert get()['state']['adopted']==old
    assert post('adopt',get()['state'],group_id='02').status_code==409
    assert post('select',group_id='02').status_code==200
    assert post('adopt',group_id='02').status_code==200

def test_all_avoided_manual_selection(env):
    _,_,_,get,post,*_=env
    for i in range(1,8):
        assert post('preference',group_id=f'{i:02}',favorite=False,avoided=True).status_code==200
    assert get()['state']['recommendation']['group_id']=='01'
    future=get('2026-10-18')['state'];assert future['recommendation']['group_id'] is None
    assert post('select',future,group_id='07').status_code==200
    assert post('adopt',get('2026-10-18')['state'],group_id='07').status_code==200

def test_concurrent_duplicate_one_wins(env):
    app,_,_,get,_,origin,headers=env
    state=get()['state']
    data=dict(action='adopt',target_date=state['target_date'],revision=state['revision'],library_hash=state['library_hash'],group_id='01')
    def adopt(_):
        c=app.test_client();c.set_cookie('waybeside_session',app.config['TOKEN'],domain='127.0.0.1')
        return c.post('/api/menu/action',base_url=origin,headers=headers,json=data).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(adopt,range(2)))==[200,409]
    assert get()['state']['revision']==state['revision']+1

def test_backup_restore_shopping_and_old_route_guard(env,tmp_path):
    app,path,client,get,post,origin,headers=env
    assert post('adopt',group_id='01').status_code==200
    assert post('preference',group_id='02',favorite=True,avoided=False).status_code==200
    result=client.get('/api/menu/shopping?date=2026-10-17',base_url=origin)
    assert result.json['items'][0]['g']==255
    assert result.json['actual_intake'] is False
    app.config['TODAY']='2026-10-17'
    assert post('actual',get()['state'],meal='晚餐',status='other',note='自造测试').status_code==200
    assert post('select',get()['state'],group_id='02').status_code==200
    snapshot=get()
    target=tmp_path/'backup.sqlite';backup(path,target);restore(target,tmp_path/'restored')
    restored=create_app(tmp_path/'restored/waybeside.sqlite',today='2026-10-16')
    with connect(path) as left,connect(tmp_path/'restored/waybeside.sqlite') as right:
        for table in ('menu_library','menu_active','menu_day','menu_event','menu_preference'):
            assert [tuple(r) for r in left.execute('SELECT * FROM '+table)]==[tuple(r) for r in right.execute('SELECT * FROM '+table)]
    assert client.post('/api/proposals/not-a-job/adopt',base_url=origin,headers=headers,json={'confirmed':True}).status_code in (404,405)
    assert client.post('/api/plans/undo',base_url=origin,headers=headers,json={'target_date':'2026-10-17','plan_id':1}).status_code in (404,405)

def newer_data(version='replacement-v1'):
    data=synthetic_library();data.pop('fictional');data['version']=version
    data['role']='自造虚构替换库'
    return data

def test_import_preview_confirm_and_expected_digest(env):
    _,path,client,get,post,origin,headers=env
    data=newer_data()
    preview=client.post('/api/menu/import-preview',base_url=origin,headers=headers,json=data)
    assert preview.status_code==200 and preview.json['expected']==active_digest(path)
    body=dict(menu=data,digest=preview.json['digest'],expected=preview.json['expected'],confirmed=True)
    assert client.post('/api/menu/import-confirm',base_url=origin,headers=headers,json=body).status_code==200
    assert get()['synthetic'] is False                              # 库内不再标 fictional：横幅关闭
    assert client.post('/api/menu/import-confirm',base_url=origin,headers=headers,json=body).status_code==409   # 旧预览失效
    assert post('select',group_id='02').status_code==200
    assert post('adopt',group_id='02').status_code==200

def active_digest(path):
    with connect(path) as db:return active(db)['digest']

def test_replace_without_expected_digest_is_refused(env):
    _,path,_,get,_,_,_=env
    before=get()['state']
    with pytest.raises(Exception) as caught:import_library(path,newer_data())
    assert getattr(caught.value,'code',None)==409
    with pytest.raises(Exception) as caught:import_library(path,newer_data(),expected='0'*64)
    assert getattr(caught.value,'code',None)==409
    assert get()['state']==before
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM menu_library').fetchone()[0]==1

def test_import_over_existing_facts_is_allowed_and_keeps_history(env):
    """旧规则“已有演示记录不可导入/拒绝合成身份”已删除；换库不改已采用历史（冻结副本）。"""
    _,path,_,get,post,_,_=env
    assert post('adopt',group_id='01').status_code==200
    assert post('preference',group_id='02',favorite=True,avoided=False).status_code==200
    old=get()['state']['adopted']
    import_library(path,newer_data(),active_digest(path))
    assert get()['state']['adopted']==old
    assert get()['preferences'][0]['group_id']=='02'

def test_new_version_keeps_adopted_snapshot(env):
    _,path,_,get,post,_,_=env
    import_library(path,newer_data(),active_digest(path))
    assert post('adopt',get()['state'],group_id='01').status_code==200
    old=get()['state']['adopted']
    newer=newer_data('replacement-v2');newer['days'][0]['meals'][0]['title']='新版本自造餐名'
    import_library(path,newer,active_digest(path))
    assert get()['state']['adopted']==old

def test_favorite_only_changes_future_recommendation(env):
    _,_,_,get,post,_,_=env
    first=get()['state']
    assert post('preference',first,group_id='06',favorite=True,avoided=False).status_code==200
    assert get()['state']['recommendation']==first['recommendation']
    assert get('2026-10-18')['state']['recommendation']['group_id']=='06'

def test_recommendation_ignores_revoked_adoption(env):
    _,_,_,get,post,_,_=env
    assert post('adopt',group_id='01').status_code==200
    assert post('undo',get()['state']).status_code==200
    assert get('2026-10-18')['state']['recommendation']['group_id']=='01'

def test_recommendation_ignores_replaced_adoption(env):
    _,_,_,get,post,_,_=env
    assert post('adopt',group_id='01').status_code==200
    assert post('select',group_id='02').status_code==200
    assert post('adopt',group_id='02').status_code==200
    assert get('2026-10-18')['state']['recommendation']['group_id']=='01'

def test_cancel_preview_keeps_adoption_locks_and_actual(env):
    app,_,_,get,post,_,_=env
    assert post('adopt',group_id='01').status_code==200
    app.config['TODAY']='2026-10-17'
    assert post('actual',meal='早餐',status='partial',note='自造记录').status_code==200
    before=get()['state']
    assert post('select',group_id='02').status_code==200
    assert get()['state']['draft']['group_id']=='02'
    stale=get()['state']
    assert post('cancel_draft',stale).status_code==200
    after=get()['state']
    assert after['draft'] is None
    for key in ('adopted','actual','locks','previous','recommendation'):
        assert after[key]==before[key]
    assert post('cancel_draft',stale).status_code==409
    assert post('adopt',after,group_id='02').status_code==409

def test_menu_view_preview_and_record_controls():
    import shutil, subprocess
    node=shutil.which('node')
    if not node:
        pytest.skip('node not installed')
    result=subprocess.run([node,str(Path(__file__).with_name('menu-view-unit.cjs'))],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert '三个页面的草稿、采用对象与事实控件隔离：通过' in result.stdout
