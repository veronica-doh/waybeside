"""单轨后的治疗记录：预约/补记/完成/更正/取消/恢复、检验与观察、重启读回。全部虚构临时样本。"""
from support import make_env, make_course
from waybeside import create_app
from waybeside.storage import connect
import sys

def test_sessions_roundtrip_without_course_settings(tmp_path):
    e=make_env(tmp_path,today='2030-01-06')
    path=e[0].config['DB_PATH'];_,client,post,get=e
    s=get().json
    for k in ('treatment_course','treatment_session','observation','audit'):assert s[k]==[]
    assert not {'profile','source','baseline','habit','job','plan_input','plan_version'} & set(s)
    assert (s['course_revision'],s['settings_version'],s['demo']) == (0,0,False)
    # 未设置疗程也能记录；日期规则不适用（没有治疗星期可比）
    assert post('sessions',{'action':'record','date':'2030-01-07','expected_revision':0}).status_code==400       # 未来不能补记
    r=post('sessions',{'action':'schedule','date':'2030-01-06','expected_revision':0});assert r.status_code==201
    identifier=r.json['id']
    assert post('sessions',{'action':'schedule','date':'2030-01-07','expected_revision':0}).status_code==409     # 旧 revision
    assert post(f'sessions/{identifier}',{'action':'complete','date':'2030-01-06','version':1}).status_code==200
    assert post(f'sessions/{identifier}',{'action':'correct','date':'2030-01-06','version':1}).status_code==409  # 旧行版本
    assert post(f'sessions/{identifier}',{'action':'correct','date':'2030-01-06','version':2}).status_code==200
    aux={'version':0,'labs':[{'date':'2030-01-06','metric':'示例指标','unit':'测试单位','lab':'虚构实验室','value':4.2,'low':None,'high':None}],'question':'测试便签','rhythm':{'blood':None,'chemistry':None},'celebration':True}
    assert post('aux',aux).status_code==200
    assert post('aux',aux).status_code==409
    obs={'date':'2030-01-06','time':'12:00','session':str(identifier),'exercise':'partial','note':'虚构记录'}
    assert post('observations',{**obs,'session':'999'}).status_code==400
    assert post('observations',obs).status_code==200
    before=get().json
    app=create_app(path,today='2030-01-06')
    c=app.test_client();origin='http://127.0.0.1:8765'
    login=c.post('/api/login',base_url=origin,headers={'Origin':origin},json={'token':app.config['LOGIN_CODE']})
    after=c.get('/api/state',base_url=origin).json
    assert after==before
    assert len(after['treatment_session'])==1 and len(after['observation'])==2
    assert after['observation'][0]['content']['labs'][0]['value']==4.2
    h={'Origin':origin,'X-CSRF-Token':login.json['csrf']}
    p=lambda route,data:c.post('/api/'+route,base_url=origin,headers=h,json=data)
    assert p(f'sessions/{identifier}',{'action':'cancel','date':'2030-01-06','version':3}).status_code==200
    assert c.get('/api/state',base_url=origin).json['treatment_session'][0]['status']=='cancelled'
    assert p(f'sessions/{identifier}',{'action':'restore','date':'2030-01-06','version':4}).status_code==200
    assert p(f'sessions/{identifier}',{'action':'complete','date':'2030-01-06','version':5}).status_code==200
    assert c.get('/api/state',base_url=origin).json['completed']==1

def test_old_private_routes_are_gone(tmp_path):
    e=make_env(tmp_path)
    for route in ('private/sessions','private/observations','private/aux'):
        assert e[2](route,{}).status_code in (404,405)

def test_old_three_meal_routes_are_gone(tmp_path):
    e=make_env(tmp_path)
    for route in ('plan-inputs','plans/undo','proposals/x/adopt'):
        assert e[2](route,{}).status_code in (404,405)
