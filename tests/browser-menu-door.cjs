// 端到端：export → AGENTS.md 示例脚本 → submit → 「准备明天」看到候选 → 采用 → 「我的方案」版本 → 撤销。
// 只用虚构资料；回环服务、临时数据目录；截图强制 reduced-motion。
const fs=require('fs'),os=require('os'),path=require('path'),cp=require('child_process'),net=require('net');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),ART=require('./helpers/artifacts.cjs'),pyPath=require('./helpers/python.cjs')(root),passGate=require('./helpers/gate.cjs');
const outDir=path.join(ART,'2026-10-16-waybeside-menu-door');
const dest=path.join(ART,'2026-10-16-waybeside-menu-door-checks.json');
const checks=[];const add=(id,描述,ok,证据)=>{checks.push({id,描述,结果:ok?'pass':'fail',证据});if(!ok)console.log('FAIL',id,证据);};
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const py=pyPath;
function fictional(){const r=cp.spawnSync(py,['-c',"import json;from waybeside.menu_library import synthetic_library as s;d=s();d.pop('fictional');d['version']='fictional-private-v1'\nfor x in d['days']:\n  for m in x['meals']:m['title']='示例'+m['meal']+'组'+x['group_id']\nprint(json.dumps(d,ensure_ascii=False))"],{cwd:root,encoding:'utf8'});return JSON.parse(r.stdout);}
const example=fs.readFileSync(path.join(root,'AGENTS.md'),'utf8').match(/```python make_candidate\.py\n([\s\S]*?)```/)[1];
async function run(mode){
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'waybeside-door-'));let server,browser;const errors=[];
  const env={...process.env,WAYBESIDE_DATA_DIR:path.join(tmp,'data')};
  const cli=(...a)=>{const r=cp.spawnSync(py,['-m','waybeside.agent_door',...a],{cwd:root,env,encoding:'utf8'});return {code:r.status,out:r.stdout.trim()?JSON.parse(r.stdout):null};};
  const make=(pkg,out)=>cp.spawnSync(py,['-c',example,pkg,out],{encoding:'utf8'});
  try{
    const port=await new Promise(res=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const n=s.address().port;s.close(()=>res(n));});});
    const ready=path.join(tmp,'ready.json');
    server=cp.spawn(py,[path.join(root,'run.py'),...(mode==='demo'?['--demo']:[]),'--port',String(port),'--no-browser','--ready-file',ready],{env,stdio:'ignore'});
    for(let i=0;i<100&&!fs.existsSync(ready);i++)await delay(50);
    const url=JSON.parse(fs.readFileSync(ready)).url;
    browser=await chromium.launch({channel:'chrome',headless:true});
    const ctx=await browser.newContext({reducedMotion:'reduce',viewport:{width:1366,height:900}});
    await ctx.route('**/*',r=>{new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort();});
    const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));
    await p.goto(url);await passGate(p);await p.evaluate(async()=>{await api('assistant-mode',{mode:'menu_only',version:(await (await api('state')).json()).assistant_mode_version});});await delay(400);
    if(mode==='private'){
      const menu=fictional();
      await p.evaluate(async menu=>{const pv=await (await api('menu/import-preview',menu)).json();await api('menu/import-confirm',{menu,digest:pv.digest,expected:pv.expected,confirmed:true});await refreshState();await fetchMenu();render();},menu);
    }
    const nav=async h=>{await p.evaluate(h=>{if(location.hash==='#'+h)render();else location.hash=h;},h);await p.waitForFunction(h=>document.querySelector('#main').dataset.view===h,h);await delay(600);};
    const shot=async n=>{await p.locator('#notice').evaluate(e=>e.classList.remove('show'));await p.screenshot({path:path.join(outDir,mode+'-'+n+'.png'),fullPage:true});};
    const snapState=()=>p.evaluate(async()=>JSON.stringify((await (await api('menu?date='+TOMORROW)).json()).state));
    await nav('tomorrow');
    add(mode+'-no-mock','菜单库启用时没有旧三餐 mock 生成按钮',await p.locator('[data-action=generate]').count()===0,'');
    add(mode+'-empty','无候选时有一行空状态说明',await p.locator('#menu-candidate').innerText().then(t=>t.includes('还没有来自电脑上的助手的建议')),'');
    await shot('tomorrow-empty');
    add(mode+'-no-hint','无候选时没有提示条',await p.locator('#menu-cand-hint').count()===0,'');
    // 失败路径：越权、未知 ID、STALE，菜单状态不变
    const off=cli('export');add(mode+'-share-default-off','共享默认全关：export 返回 SHARE_DISABLED',off.code===2&&off.out.error==='SHARE_DISABLED',off.out);
    await p.evaluate(async()=>{for(const r of await (await api('agent-share')).json())await api('agent-share',{category:r.category,enabled:true,version:r.version});});
    const exp=cli('export');add(mode+'-export','export 成功（菜单库启用）',exp.code===0&&exp.out.status==='WAITING_EXTERNAL',exp.out);
    const pkgPath=exp.out.package,pkg=JSON.parse(fs.readFileSync(pkgPath));
    add(mode+'-export-contents','资料包含全部菜单组与各组餐次、锁定/暂不想吃/版本',pkg.snapshot.groups.length>0&&pkg.snapshot.groups.every(g=>g.meals.length===5)&&'locked_meals' in pkg.snapshot.current&&'avoided_group_ids' in pkg.snapshot&&!!pkg.snapshot.snapshot_versions.library_hash,'');
    const before=await snapState();
    const bad=(name,mut,code)=>{const f=path.join(tmp,name+'.json');fs.writeFileSync(f,JSON.stringify(mut(JSON.parse(JSON.stringify(pkg.candidate_template)))));const r=cli('submit',f);return r.code===2&&r.out.error===code;};
    add(mode+'-reject-extra','越权字段被拒',bad('extra',t=>({...t,medication:'x'}),'SCHEMA_FIELDS'),'');
    add(mode+'-reject-unknown','未知组 ID 被拒',bad('unk',t=>({...t,group_id:'99'}),'UNKNOWN_GROUP'),'');
    add(mode+'-reject-inject','注入内容被拒',bad('inj',t=>({...t,reason_code:'<script>alert(1)</script>'}),'REASON_CODE'),'');
    add(mode+'-reject-unchanged','被拒后菜单状态不变',await snapState()===before,'');
    await nav('tomorrow');
    add(mode+'-still-empty','被拒的提交没有出现候选卡',await p.locator('#menu-cand-adopt').count()===0,'');
    // STALE：export 后在页面换一组，再提交
    await p.locator('#menu-change').click();await delay(900);
    const f=path.join(tmp,'stale.json');make(pkgPath,f);const stale=cli('submit',f);
    add(mode+'-stale','export 后菜单变化 → STALE 被拒',stale.code===2&&stale.out.status==='FAILED'&&stale.out.job_status==='STALE'&&stale.out.error==='DEPENDENCIES_CHANGED',stale.out);
    // 正常链路：重新 export → 示例脚本 → submit
    await p.locator('#menu-cancel').click().catch(()=>{});await delay(700);
    const exp2=cli('export');const cand=path.join(tmp,'cand.json');const gen=make(exp2.out.package,cand);
    add(mode+'-example','AGENTS.md 示例脚本生成候选',gen.status===0,gen.stderr);
    const sub=cli('submit',cand);add(mode+'-submit','submit → READY_TO_REVIEW 且未采用',sub.code===0&&sub.out.status==='READY_TO_REVIEW'&&sub.out.adopted===false,sub.out);
    await p.waitForSelector('#menu-cand-adopt',{timeout:8000});
    add(mode+'-hint-visible','有候选时提示条可见',await p.locator('#menu-cand-hint').isVisible(),'');
    await p.evaluate(()=>window.scrollTo(0,0));await shot('tomorrow-hint');
    await p.locator('#menu-cand-hint').click();await delay(300);
    add(mode+'-hint-scroll','点击提示条后候选卡在视口内且获得焦点',await p.evaluate(()=>{const r=document.getElementById('menu-candidate').getBoundingClientRect();return r.top>=0&&r.top<innerHeight&&document.activeElement.id==='menu-candidate';}),'');
    add(mode+'-no-coral','候选卡与提示条内无珊瑚/红色计算样式',await p.evaluate(()=>{const bad=c=>{const m=c.match(/\d+/g);if(!m)return false;const [r,g,b]=m.map(Number);return r>150&&r-g>60&&r-b>60;};return ![...document.querySelectorAll('#menu-candidate,#menu-candidate *,#menu-cand-hint')].some(e=>{const c=getComputedStyle(e);return [c.color,c.backgroundColor,c.borderTopColor,c.borderLeftColor,c.outlineColor].some(bad)||bad(getComputedStyle(e,'::before').backgroundColor);});}),'');
    const panel=await p.locator('#menu-candidate').innerText();
    add(mode+'-card','「准备明天」自动出现候选卡：来源 + 逐餐对照 + 日期',panel.includes('由电脑上的助手提交')&&panel.includes('明天')&&await p.locator('.menu-diff-row').count()===5,panel.slice(0,120));
    add(mode+'-adopt-disabled','未勾选确认前采用按钮不可用',await p.locator('#menu-cand-adopt').isDisabled(),'');
    await shot('tomorrow-candidate');
    const group=await p.evaluate(()=>menuCandidate.group.group_id);
    await p.locator('#menu-cand-reviewed').check();await p.locator('#menu-cand-adopt').click();await p.locator('button[value=confirm]').click();await delay(1000);
    add(mode+'-adopted','采用后菜单已安排候选组，候选卡消失',await p.evaluate(g=>menuData.state.adopted?.group_id===g,group)&&await p.locator('#menu-cand-adopt').count()===0,group);
    await nav('plan');await p.locator('#menu-day-tomorrow').click();await delay(700);
    await p.locator('#menu-versions summary').click();
    const versions=await p.locator('#menu-versions').innerText();
    add(mode+'-versions','「我的方案」版本记录出现当前版本与撤销入口',versions.includes('现在用的')&&versions.includes('第 '+group)&&await p.locator('#menu-undo').count()===1,versions);
    await shot('plan-versions');
    await p.locator('#menu-undo').click();await p.locator('button[value=confirm]').click();await delay(1000);
    add(mode+'-undo','撤销后回到未采用',await p.evaluate(()=>menuData.state.adopted===null),'');
    add(mode+'-runtime','无脚本错误',errors.length===0,errors);
  }catch(e){add(mode+'-error','执行异常',false,String(e.stack));process.exitCode=1;}
  finally{if(browser)await browser.close();if(server&&server.exitCode===null){const d=new Promise(r=>server.once('exit',r));server.kill('SIGTERM');await d;}
    try{for(const f of fs.readdirSync(path.join(tmp,'data/agent_inbox')))fs.chmodSync(path.join(tmp,'data/agent_inbox',f),0o600);}catch(e){}
    fs.rmSync(tmp,{recursive:true,force:true});}
}
(async()=>{fs.mkdirSync(outDir,{recursive:true});await run('private');await run('demo');
  const summary={pass:checks.filter(c=>c.结果==='pass').length,total:checks.length};
  fs.writeFileSync(dest,JSON.stringify({summary,checks,cleanup:'浏览器与服务已退出，临时数据目录已删除'},null,2)+'\n');console.log(JSON.stringify(summary));if(summary.pass!==summary.total)process.exitCode=1;})();
