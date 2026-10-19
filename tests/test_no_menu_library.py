"""全新目录没有菜单库：首启不自动导入；菜单入口如实报告；agent_door 返回 NO_MENU_LIBRARY。"""
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
from support import make_env, ORIGIN, open_shares
from waybeside.agent_door import export_package, submit_candidate
from waybeside.menu_door import CandidateError
from waybeside.storage import connect
ROOT=Path(__file__).resolve().parents[1]

def test_fresh_directory_state_and_menu(tmp_path):
    e=make_env(tmp_path)
    state=e[3]().json
    assert state['treatment_session']==[] and state['treatment_course']==[] and state['demo'] is False
    assert e[3]('menu').json['enabled'] is False
    with connect(e[0].config['DB_PATH']) as db:
        for table in ('menu_library','menu_active','menu_day','menu_candidate'):
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0

def test_export_and_submit_return_error_code(tmp_path):
    e=make_env(tmp_path)
    path=tmp_path/'waybeside.sqlite'
    open_shares(e[2],e[3],('menu_library',))
    with pytest.raises(CandidateError,match='NO_MENU_LIBRARY'):export_package(path,'2031-03-04')
    file=tmp_path/'c.json';file.write_text(json.dumps({'job_id':'x'}))
    with pytest.raises(CandidateError,match='NO_MENU_LIBRARY'):submit_candidate(path,file)
    assert not (tmp_path/'agent_inbox').exists()

def run(tmp_path,*args):
    return subprocess.run([sys.executable,'-m','waybeside.agent_door',*args],cwd=ROOT,env={**os.environ,'WAYBESIDE_DATA_DIR':str(tmp_path)},capture_output=True,text=True)

def test_cli_exit_code_json_no_traceback(tmp_path):
    e=make_env(tmp_path)
    open_shares(e[2],e[3],('menu_library',))
    out=run(tmp_path,'export','--date','2031-03-04')
    result=json.loads(out.stdout)
    assert out.returncode==2 and {k:result[k] for k in ('status','error')}=={'status':'FAILED','error':'NO_MENU_LIBRARY'} and result['demo'] is False and result['data_dir']
    assert 'Traceback' not in out.stderr and out.stderr==''
    file=tmp_path/'c.json';file.write_text(json.dumps({'job_id':'x'}))
    out=run(tmp_path,'submit',str(file))
    assert out.returncode==2 and json.loads(out.stdout)['error']=='NO_MENU_LIBRARY' and 'Traceback' not in out.stderr

def test_real_first_start_has_no_content_and_no_import(tmp_path):
    import socket, time, urllib.request, http.cookiejar
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    ready=tmp_path/'ready.json';data=tmp_path/'data'
    proc=subprocess.Popen([sys.executable,str(ROOT/'run.py'),'--port',str(port),'--no-browser','--ready-file',str(ready)],env={**os.environ,'WAYBESIDE_DATA_DIR':str(data)},stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    try:
        for _ in range(100):
            if ready.exists():break
            assert proc.poll() is None,proc.stdout.read()
            time.sleep(.05)
        base=f'http://127.0.0.1:{port}'
        client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        token=json.loads(ready.read_text())['url'].split('#login=')[1]
        login=urllib.request.Request(base+'/api/login',data=json.dumps({'token':token}).encode(),headers={'Origin':base,'Content-Type':'application/json'})
        client.open(login,timeout=5).read()
        state=json.load(client.open(base+'/api/state',timeout=5))
        menu=json.load(client.open(base+'/api/menu',timeout=5))
        assert state['onboarded'] is False and state['demo'] is False and menu['enabled'] is False
        assert state['treatment_session']==[] and state['audit']==[]
        with connect(data/'waybeside.sqlite') as db:
            assert db.execute('SELECT COUNT(*) FROM menu_library').fetchone()[0]==0
            assert db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]==0
    finally:
        proc.terminate();proc.wait(timeout=10);proc.stdout.close()
