// 可配置疗程的浏览器验收（全部虚构）：设置界面、两种配置的小路格数、治疗日外确认流程、设置变更后的重排流程。
// 每个场景用全新临时数据目录与回环服务；截图强制 reduced-motion，存到 WAYBESIDE_TEST_ARTIFACTS/waybeside-course-config-shots。
const fs=require('fs'),os=require('os'),path=require('path'),cp=require('child_process'),net=require('net');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),ART=require('./helpers/artifacts.cjs'),pyPath=require('./helpers/python.cjs')(root),passGate=require('./helpers/gate.cjs');
const outDir=path.join(ART,'waybeside-course-config-shots');
const checks=[];const add=(id,描述,ok,证据)=>{checks.push({id,描述,结果:ok?'pass':'fail',证据});if(!ok){console.log('FAIL',id,JSON.stringify(证据));process.exitCode=1;}};
const delay=ms=>new Promise(r=>setTimeout(r,ms));

async function scenario(name,body){
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'waybeside-cfg-'));let server,browser;const errors=[];
  try{
    const port=await new Promise(res=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const n=s.address().port;s.close(()=>res(n));});});
    const ready=path.join(tmp,'ready.json');
    server=cp.spawn(pyPath,[path.join(root,'run.py'),'--port',String(port),'--no-browser','--ready-file',ready],{env:{...process.env,WAYBESIDE_DATA_DIR:path.join(tmp,'data'),WAYBESIDE_TODAY:'2031-03-04'},stdio:'ignore'});
    for(let i=0;i<100&&!fs.existsSync(ready);i++)await delay(50);
    const url=JSON.parse(fs.readFileSync(ready)).url;
    browser=await chromium.launch({channel:'chrome',headless:true});
    const ctx=await browser.newContext({reducedMotion:'reduce',viewport:{width:1366,height:900}});
    await ctx.route('**/*',route=>{const u=new URL(route.request().url());u.hostname==='127.0.0.1'?route.continue():route.abort();});
    const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));
    await p.goto(url);await passGate(p);
    const nav=async h=>{await p.evaluate(h=>{if(location.hash==='#'+h)render();else location.hash=h;},h);await p.waitForFunction(h=>document.querySelector('#main').dataset.view===h,h);};
    const shot=async n=>{await p.locator('#notice').evaluate(e=>e.classList.remove('show'));await p.screenshot({path:path.join(outDir,name+'-'+n+'.png'),fullPage:true});};
    const state=()=>p.evaluate(async()=>(await (await api('state')).json()));
    const settle=()=>p.waitForFunction(()=>!document.querySelector('dialog[open]'));
    await body({p,nav,shot,state,settle});
    add(name+'-runtime','无脚本错误',errors.length===0,errors);
  }catch(e){add(name+'-error','执行异常',false,String(e.stack));}
  finally{if(browser)await browser.close();if(server&&server.exitCode===null){const d=new Promise(r=>server.once('exit',r));server.kill('SIGTERM');await d;}fs.rmSync(tmp,{recursive:true,force:true});}
}
const fillSettings=async(p,o)=>{
  await p.selectOption('#course-type',o.type);await p.selectOption('#course-unit',o.unit||'session');
  await p.fill('#course-total',o.total==null?'':String(o.total));
  await p.check(`input[name=schedule_kind][value=${o.kind}]`);
  if(o.kind==='weekdays')for(const w of [0,1,2,3,4,5,6])await p.setChecked(`input[name=weekday][value="${w}"]`,o.weekdays.includes(w));
  await p.fill('#course-start',o.start||'');
};
const stones=p=>p.locator('.journey-dots .stone').count();
const cols=p=>p.locator('.stones').first().evaluate(e=>getComputedStyle(e).gridTemplateColumns.split(' ').length);

(async()=>{
  fs.mkdirSync(outDir,{recursive:true});
  // 场景 1：按星期排期（总数 10、周一三五），含治疗日外确认与设置变更后的重排
  await scenario('weekdays10',async({p,nav,shot,state,settle})=>{
    add('empty-state','全新目录：今天页与旅程页都是去设置的引导，没有任何格子',await p.locator('#goto-settings').count()===1&&await stones(p)===0,'');
    await nav('settings');
    await p.fill('#course-total','999');await p.selectOption('#course-type','other');await p.check('input[name=schedule_kind][value=manual]');
    add('weekday-field-hidden','选“逐次添加”时隐藏治疗星期',await p.locator('#weekday-field').isHidden(),'');
    await fillSettings(p,{type:'radiotherapy',total:10,kind:'weekdays',weekdays:[0,2,4],start:'2031-03-03'});
    add('weekday-field-shown','选“按每周固定几天”时显示治疗星期',await p.locator('#weekday-field').isVisible(),'');
    await p.locator('#course-form button[type=submit]').click();
    await p.waitForFunction(()=>document.querySelector('#main').dataset.view==='course'&&document.querySelector('#generate-open'));
    const blanks=await p.locator('.journey-dots .stone.blank').count();
    add('weekdays10-blank','未排日期时画 10 个“日期还没填”格子，每行 3 格',blanks===10&&await cols(p)===3,{blanks,cols:await cols(p)});
    await shot('before-generate');
    await p.locator('#generate-open').click();
    await p.waitForFunction(()=>document.querySelectorAll('.journey-dots .stone:not(.blank)').length===10);
    const weeks=await p.locator('.journey-dots .week').count();
    add('weekdays10-trail','排出后恰为 10 格、4 周（3+3+3+1）、每周 3 列',await stones(p)===10&&weeks===4&&await cols(p)===3,{stones:await stones(p),weeks,cols:await cols(p)});
    const dates=(await state()).treatment_session.map(s=>s.planned_date);
    add('weekdays10-dates','10 个日期全部落在周一/三/五，从 2031-03-03 起',dates[0]==='2031-03-03'&&dates.every(d=>[1,3,5].includes(new Date(d+'T12:00:00Z').getUTCDay())),dates);
    add('weekdays10-progress','进度写 0 / 10 次，进度条分母 10',await p.locator('.path-count').first().innerText().then(t=>/0\s*\/\s*10\s*次/.test(t))&&await p.locator('[role=progressbar]').first().getAttribute('aria-valuemax')==='10','');
    await shot('course');
    await nav('today');add('weekdays10-today','今天页小路同为 10 格，并写明治疗星期',await stones(p)===10&&await p.locator('.path-aside').innerText().then(t=>t.includes('每周一、三、五治疗')),'');await shot('today');
    // 治疗日外确认（CONFIRM_REQUIRED）：2031-03-04 是周二
    await nav('course');
    const completeOffDay=async()=>{
      await p.fill('#private-date-1','2031-03-04');
      await p.locator('[data-private-id="1"][data-private-action=complete]').click();
      await p.locator('button[value=confirm]').click();
      await p.waitForFunction(()=>document.querySelector('dialog[open]')&&document.querySelector('#dialog-copy').textContent.includes('这天不是你设定的治疗日，仍要记下吗？'));
    };
    await completeOffDay();
    await shot('off-schedule-confirm');
    await p.locator('button[value=cancel]').click();await settle();await delay(300);
    let s1=(await state()).treatment_session[0];
    add('confirm-cancel','治疗日外：选“先不改”后不写入，状态仍是待确认',s1.status==='planned'&&s1.actual_date===null,s1);
    await completeOffDay();
    await p.locator('button[value=confirm]').click();await settle();
    await p.waitForFunction(()=>document.querySelectorAll('.journey-dots .stone.complete').length===1);
    const st=await state();s1=st.treatment_session[0];
    add('confirm-accept','确认后带确认参数重发：已完成、实际日 2031-03-04，审计标 off_schedule',s1.status==='completed'&&s1.actual_date==='2031-03-04'&&st.audit.at(-1).new_value.off_schedule===true,{s1,audit:st.audit.at(-1).new_value});
    // 总数小于已记录数：400，界面给出原因
    await nav('settings');await p.fill('#course-total','3');await p.locator('#course-form button[type=submit]').click();
    await p.waitForFunction(()=>document.querySelector('#course-error').textContent.length>0);
    add('total-below-recorded','总数改成 3（已记录 10）被拒并说明原因，设置未变',await p.locator('#course-error').innerText().then(t=>t.includes('总数小于已记录'))&&(await state()).treatment_course[0].total_count===10,'');
    // 改设置（周二四）后：已排日期不动，出现重排提示
    const before=(await state()).treatment_session.map(s=>[s.id,s.planned_date,s.status,s.actual_date,s.version]);
    await fillSettings(p,{type:'radiotherapy',total:10,kind:'weekdays',weekdays:[1,3],start:'2031-03-03'});
    await p.locator('#course-form button[type=submit]').click();
    await p.waitForFunction(()=>document.querySelector('#main').dataset.view==='course'&&document.querySelector('#sync-banner'));
    const afterSave=(await state());
    add('out-of-sync-banner','保存后不静默改动已排日期，旅程页出现“设置已改，已排日期还是旧的，要不要重排？”',await p.locator('#sync-banner').innerText().then(t=>t.includes('设置已改，已排日期还是旧的，要不要重排？'))&&JSON.stringify(before)===JSON.stringify(afterSave.treatment_session.map(s=>[s.id,s.planned_date,s.status,s.actual_date,s.version]))&&afterSave.schedule_out_of_sync===true,'');
    await shot('out-of-sync');
    await nav('today');add('out-of-sync-today','今天页也有指向旅程页的提示',await p.locator('#sync-hint').count()===1,'');await nav('course');
    await p.locator('#resync-open').click();await p.locator('button[value=confirm]').click();await settle();
    await p.waitForFunction(()=>!document.querySelector('#sync-banner'));
    const after=await state();const planned=after.treatment_session.filter(s=>s.status==='planned');const done=after.treatment_session[0];
    add('resync-applied','确认重排后：未发生的预约都落在周二/四，不早于今天；提示消失',after.schedule_out_of_sync===false&&planned.length===9&&planned.every(s=>[2,4].includes(new Date(s.planned_date+'T12:00:00Z').getUTCDay())&&s.planned_date>='2031-03-04'),planned.map(s=>s.planned_date));
    add('resync-completed-untouched','已完成条目的日期、实际日、状态、版本逐字段不变',JSON.stringify([done.id,done.planned_date,done.status,done.actual_date,done.version])===JSON.stringify(before[0]),done);
    await shot('after-resync');
  });
  // 场景 2：逐次添加（化疗、6 个周期）
  await scenario('manual6',async({p,nav,shot,state,settle})=>{
    await nav('settings');
    await fillSettings(p,{type:'chemotherapy',unit:'cycle',total:6,kind:'manual',start:'2031-03-03'});
    await p.locator('#course-form button[type=submit]').click();
    await p.waitForFunction(()=>document.querySelector('#main').dataset.view==='course');
    add('manual6-blank','未添加预约时画 6 个空格，不推算日期，没有“补排”按钮',await stones(p)===6&&await p.locator('.stone.blank').count()===6&&await p.locator('#generate-open').count()===0,'');
    await shot('before-add');
    for(const d of ['2031-03-03','2031-03-24','2031-04-14','2031-05-05','2031-05-26','2031-06-16']){
      const n=await p.locator('.stone:not(.blank)').count();
      await p.fill('#private-session-form [name=date]',d);await p.locator('#private-session-form button[type=submit]').click();
      await p.waitForFunction(n=>document.querySelectorAll('.stone:not(.blank)').length===n+1,n);
    }
    const labelsText=await p.locator('.journey-dots .stone').evaluateAll(a=>a.map(x=>x.getAttribute('aria-label')||x.textContent));
    add('manual6-trail','6 个周期恰为 6 格（无空格），写“周期”而非“次”，每周一格按 ISO 周分组',await stones(p)===6&&await p.locator('.stone.blank').count()===0&&await p.locator('.journey-dots .week').count()===6&&labelsText.every(t=>t.includes('个周期')),labelsText);
    add('manual6-weeks','第 1、4、7、10、13、16 周（每 3 周一轮）',await p.locator('.journey-dots .week p').allInnerTexts().then(a=>a.map(t=>t.split(' ')[1]).join(',')==='1,4,7,10,13,16'),await p.locator('.journey-dots .week p').allInnerTexts());
    add('manual6-progress','进度写 0 / 6 个周期',await p.locator('.path-count').first().innerText().then(t=>/0\s*\/\s*6\s*个周期/.test(t)),'');
    await shot('course');await nav('today');await shot('today');
    add('manual6-today','今天页小路 6 格',await stones(p)===6,'');
  });
  // 场景 3：总数不知道、开始日不知道
  await scenario('unknown-total',async({p,nav,shot})=>{
    await nav('settings');
    await fillSettings(p,{type:'other',total:null,kind:'weekdays',weekdays:[0,3],start:''});
    await p.locator('#course-form button[type=submit]').click();
    await p.waitForFunction(()=>document.querySelector('#main').dataset.view==='course');
    add('unknown-total-trail','总数未知：不画格子、不画进度条，只显示“已记录 0 次完成”',await stones(p)===0&&await p.locator('[role=progressbar]').count()===0&&await p.locator('.path-count').first().innerText().then(t=>t.includes('已记录')),'');
    add('unknown-total-no-generate','没有开始日与总数时不提供“补排”',await p.locator('#generate-open').count()===0,'');
  });
  fs.writeFileSync(path.join(ART,'waybeside-course-config-checks.json'),JSON.stringify({summary:{pass:checks.filter(c=>c.结果==='pass').length,total:checks.length},checks},null,2)+'\n');
  console.log(JSON.stringify({pass:checks.filter(c=>c.结果==='pass').length,total:checks.length}));
})();
