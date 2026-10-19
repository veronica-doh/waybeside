// 助手使用方式的浏览器验收（全部虚构数据）：首开必先免责、再选方式；两条路径各走一遍；设置页换方式；
// 「助手记录」标注；桌面与 375 宽各验一遍。每个场景用全新临时数据目录与回环服务。
const fs=require('fs'),os=require('os'),path=require('path'),cp=require('child_process'),net=require('net');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),ART=require('./helpers/artifacts.cjs'),py=require('./helpers/python.cjs')(root);
const outDir=path.join(ART,'waybeside-assistant-mode-shots');
const checks=[];const add=(id,描述,ok,证据)=>{checks.push({id,描述,结果:ok?'pass':'fail',证据});if(!ok){console.log('FAIL',id,JSON.stringify(证据));process.exitCode=1;}};
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const SENT={nick:'SENT_NICK_41c7',note:'SENT_NOTE_41c7',lab:'SENT_LAB_41c7'};
const TODAY='2031-03-04',TOMORROW='2031-03-05';

// 选择页文案：逐字对照需求文字（不从界面文案包里取，免得改坏了测试也跟着变）。
const TEXT={
  title:'你希望 AI 助手帮你做多少？',
  intro:'帮你装好这个程序的 AI 助手，接下来会和你一起用它。选一种方式就行，以后随时可以在「设置」里改。',
  full:['让助手帮我全部打理','助手能看到：你在这里的所有记录，包括检验结果、身体感受、过敏和备注','助手能替你做：记录每天的情况、录入检验结果、定好明天的菜单、标记哪次治疗做完了','你自己要做：偶尔来看看。助手录的每一条都会标上「助手记录」'],
  menu_only:['我自己记，助手只帮挑菜单','助手能看到：只有你勾选允许的内容。选这种方式时，检验结果、身体感受和备注助手看不到；以后如果改成第一种，之前记下的这些也会让它看到','助手能替你做：从你的菜单里挑出明天吃什么，等你点头','你自己要做：每天的情况和检验结果由你或家人在这里记；助手挑的菜单要你点「采用」才算数'],
  tip:'拿不准就先选第二种。以后放宽很容易，但助手已经看过的内容收不回来。助手看到的内容会不会传给它背后的 AI 服务，取决于帮你装它的人怎么设置。',
  handover:'问卷可以交给助手填，你也可以现在自己填',
};
const squash=s=>s.replace(/\s+/g,'');

async function scenario(name,width,body){
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'waybeside-mode-'));let server,browser;const errors=[];
  const data=path.join(tmp,'data');fs.mkdirSync(path.join(data,'rules'),{recursive:true});
  const env={...process.env,WAYBESIDE_DATA_DIR:data,WAYBESIDE_TODAY:TODAY};
  let n=0;
  const cli=(...a)=>{const r=cp.spawnSync(py,['-m','waybeside.agent_door',...a],{cwd:root,env,encoding:'utf8'});return {code:r.status,out:r.stdout.trim()?JSON.parse(r.stdout):null,raw:r.stdout};};
  const agent=(method,url,payload)=>{const args=['api',method,url];if(payload!==undefined){const f=path.join(tmp,`body-${++n}.json`);fs.writeFileSync(f,JSON.stringify(payload));args.push('--json',f);}return cli(...args);};
  const ok=(r,what)=>{if(r.code!==0)throw new Error(what+' '+r.raw);return r.out.body;};
  try{
    const port=await new Promise(res=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const x=s.address().port;s.close(()=>res(x));});});
    const ready=path.join(tmp,'ready.json');
    server=cp.spawn(py,[path.join(root,'run.py'),'--port',String(port),'--no-browser','--ready-file',ready],{cwd:root,env,stdio:'ignore'});
    for(let i=0;i<100&&!fs.existsSync(ready);i++)await delay(50);
    const url=JSON.parse(fs.readFileSync(ready)).url;
    browser=await chromium.launch({channel:'chrome',headless:true});
    const ctx=await browser.newContext({reducedMotion:'reduce',viewport:{width,height:width<500?812:900}});
    await ctx.route('**/*',route=>{new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort();});
    const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));
    const label=width<500?'375':'desktop';
    const shot=async(file,el)=>{await p.locator('#notice').evaluate(e=>e.classList.remove('show'));const target=path.join(outDir,file+'-'+label+'.png');if(el)await p.locator(el).screenshot({path:target});else await p.screenshot({path:target,fullPage:true});};
    const nav=async h=>{await p.evaluate(h=>{if(location.hash==='#'+h)render();else location.hash=h;},h);await p.waitForFunction(h=>document.querySelector('#main').dataset.view===h,h,{timeout:15000});await delay(250);};
    const stage=()=>p.evaluate(()=>{const g=document.querySelector('#gate');return g.hidden?null:g.dataset.stage+(g.dataset.block?':'+g.dataset.block:'');});
    const wait=(fn,arg)=>p.waitForFunction(fn,arg,{timeout:15000});
    const noOverflow=()=>p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
    const api=(a,d)=>p.evaluate(async([x,y])=>(await (await window.api(x,y)).json()),[a,d]);
    await body({p,url,cli,agent,ok,shot,nav,stage,wait,noOverflow,api,tmp,data,label,tag:`${name}-${label}`});
    add(`${name}-${label}-runtime`,'无脚本错误',errors.length===0,errors);
  }catch(e){add(`${name}-${width}-error`,'执行异常',false,String(e.stack));}
  finally{if(browser)await browser.close();if(server&&server.exitCode===null){const d=new Promise(r=>server.once('exit',r));server.kill('SIGTERM');await d;}fs.rmSync(tmp,{recursive:true,force:true});}
}

// 公共：免责 -> 选择页，核对页面文案与不预选。
async function openToModePage({p,url,shot,stage,wait,noOverflow,api,tag}){
  await p.goto(url);await wait(()=>!document.querySelector('#gate').hidden);
  add(tag+'-first-disclaimer','首开必先出现免责确认，此时没有任何方式选项',await stage()==='disclaimer'&&await p.locator('#accept-disclaimer').isVisible()&&await p.locator('.mode-option').count()===0,await stage());
  const st=await api('state');
  add(tag+'-unset','选之前方式为 unset，免责未确认',st.assistant_mode==='unset'&&st.disclaimer_accepted===false,{m:st.assistant_mode,d:st.disclaimer_accepted});
  await p.click('#accept-disclaimer');await wait(()=>document.querySelector('#gate').dataset.stage==='mode');
  add(tag+'-then-mode','免责之后才出现方式选择',await stage()==='mode',await stage());
  const text=squash(await p.locator('#gate').innerText());
  const fullOk=TEXT.full.every(s=>text.includes(squash(s))),menuOk=TEXT.menu_only.every(s=>text.includes(squash(s)));
  add(tag+'-copy','选择页文案与需求逐字一致（标题、说明、两个选项的三问、提示）',text.includes(squash(TEXT.title))&&text.includes(squash(TEXT.intro))&&fullOk&&menuOk&&text.includes(squash(TEXT.tip)),{fullOk,menuOk});
  add(tag+'-order','选项一在前、选项二在后',text.indexOf(squash(TEXT.full[0]))<text.indexOf(squash(TEXT.menu_only[0])),'');
  add(tag+'-no-preselect','两项都不预选，不选不能继续',await p.locator('input[name=assistant-mode]:checked').count()===0&&await p.locator('#mode-continue').isDisabled(),'');
  add(tag+'-no-private-words','界面里没有内部代号或比喻',!new RegExp(['管'+'家','小时'+'工','menu_only','\\bfull\\b'].join('|')).test(await p.locator('#gate').innerText()),'');
  add(tag+'-no-overflow','选择页没有横向溢出',await noOverflow(),'');
  await shot('mode-page');
}

(async()=>{
  fs.mkdirSync(outDir,{recursive:true});
  const library=JSON.parse(cp.spawnSync(py,['-c',"import sys,json;sys.path.insert(0,'tests');from test_rules import library;print(json.dumps(library(),ensure_ascii=False))"],{cwd:root,encoding:'utf8'}).stdout);
  for(const width of (process.env.WIDTHS||'1366,375').split(',').map(Number)){
    // ---- 路径二：我自己记，助手只帮挑菜单；再从设置页换成「全部打理」----
    await scenario('menu-only-path',width,async ctx=>{
      const {p,cli,agent,shot,nav,stage,wait,noOverflow,api,tag}=ctx;
      await openToModePage(ctx);
      await p.check('#mode-menu_only');
      add(tag+'-continue-enabled','选了一项后才能继续',await p.locator('#mode-continue').isEnabled(),'');
      await p.click('#mode-continue');await wait(()=>document.querySelector('#gate').dataset.stage==='wizard');
      add(tag+'-wizard','选了第二种，进入现有问卷向导（人填）',await stage()==='wizard:basic'&&(await api('state')).assistant_mode==='menu_only',await stage());
      const early=await p.evaluate(()=>{const keep=settingsData;settingsData=null;let err=null;try{render();}catch(e){err=String(e);}const r={err,gateHidden:document.querySelector('#gate').hidden,text:document.querySelector('#main').textContent};settingsData=keep;render();return r;});
      add(tag+'-no-wizard-before-settings','设置数据还没读到时不画向导、不抛错，只显示加载态',early.err===null&&early.gateHidden===true&&early.text.includes('正在读取本机记录'),early);
      add(tag+'-cli-menu-only','助手命令行：api 被拒（MODE_NOT_ALLOWED），状态为 menu_only',cli('api','GET','/api/state').out.error==='MODE_NOT_ALLOWED'&&cli('status').out.assistant_mode==='menu_only','');
      for(let i=0;i<4;i++){await p.click('#wizard-skip');if(i<3)await wait(x=>document.querySelector('#gate').dataset.block!==x,['basic','treatment','diet','habit'][i]);}
      await wait(()=>document.querySelector('#gate').hidden&&document.querySelector('#main').dataset.view);
      // 设置页「AI 助手」一节
      await nav('settings');
      const section=squash(await p.locator('#assistant-section').innerText());
      add(tag+'-settings-section','设置页「AI 助手」一节：当前方式的名字与同样的三问，有「换一种」',section.includes(squash(TEXT.menu_only[0]))&&TEXT.menu_only.slice(1).every(s=>section.includes(squash(s)))&&await p.locator('#assistant-change').isVisible(),section.slice(0,120));
      add(tag+'-no-recent-in-menu-only','「助手最近做了什么」只在全部打理下出现',await p.locator('#assistant-recent').count()===0,'');
      add(tag+'-menu-only-promises','我自己记：过敏与备注旁保留原来的承诺文字',/也不会给外部助手/.test(await p.locator('#settings-diet').innerText()),'');
      add(tag+'-settings-no-overflow','设置页没有横向溢出',await noOverflow(),'');
      // 换一种：必须经过同一个选择页，且不预选，可取消
      await p.click('#assistant-change');await wait(()=>document.querySelector('#gate').dataset.stage==='mode');
      add(tag+'-change-same-page','「换一种」打开同一个选择页，不预选，可先不改',await p.locator('input[name=assistant-mode]:checked').count()===0&&await p.locator('#mode-continue').isDisabled()&&await p.locator('#mode-cancel').isVisible()&&squash(await p.locator('#gate').innerText()).includes(squash(TEXT.tip)),'');
      await p.click('#mode-cancel');await wait(()=>document.querySelector('#gate').hidden);
      add(tag+'-cancel-keeps','先不改：方式不变',(await api('state')).assistant_mode==='menu_only',(await api('state')).assistant_mode);
      await p.click('#assistant-change');await wait(()=>document.querySelector('#gate').dataset.stage==='mode');
      await p.check('#mode-full');await p.click('#mode-continue');await wait(()=>document.querySelector('#gate').hidden);
      await nav('settings');
      const after=squash(await p.locator('#assistant-section').innerText());
      add(tag+'-changed-to-full','换成「全部打理」：设置页改写为第一种的三问，出现「助手最近做了什么」（空）',(await api('state')).assistant_mode==='full'&&after.includes(squash(TEXT.full[0]))&&TEXT.full.slice(1).every(s=>after.includes(squash(s)))&&await p.locator('#assistant-recent').count()===1,after.slice(0,160));
      add(tag+'-cli-full','助手命令行：api 现在可用，状态为 full',cli('api','GET','/api/state').out.status===200&&cli('status').out.assistant_mode==='full','');
      const diet=await p.locator('#settings-diet').innerText();
      add(tag+'-full-promises','全部打理：过敏与备注旁换成真实说法，不再说「不会给外部助手」',diet.includes('你选了让助手全部打理，助手能看到这一项')&&!/不会给外部助手/.test(diet),'');
      await nav('export');
      const ex=await p.locator('#export-page').innerText();
      add(tag+'-export-note','导出给助手：全部打理下写明开关现在不起作用，不再有「永远是你自己点」',ex.includes('你选了让助手全部打理，这些开关现在不起作用')&&!/永远是你自己点|都不会给助手/.test(ex),ex.slice(0,200));
      await p.evaluate(()=>{location.hash='#settings';});await wait(()=>document.querySelector('#main').dataset.view==='settings');
      await p.click('#assistant-change');await wait(()=>document.querySelector('#gate').dataset.stage==='mode');
      await p.check('#mode-menu_only');await p.click('#mode-continue');await wait(()=>document.querySelector('#gate').hidden);
      add(tag+'-back-to-menu-only','再换回第二种：助手命令行立即受限',cli('api','GET','/api/state').out.error==='MODE_NOT_ALLOWED','');
      await nav('export');
      add(tag+'-export-promise-back','换回后导出页恢复原承诺',/不管怎么设置，你的昵称、过敏提醒、备注、检验和症状记录都不会给助手/.test(await p.locator('#export-page').innerText()),'');
    });

    // ---- 路径一：让助手帮我全部打理；问卷交给助手；助手写入后的标注 ----
    await scenario('full-path',width,async ctx=>{
      const {p,url,cli,agent,ok,shot,nav,stage,wait,noOverflow,api,tag,data,tmp}=ctx;
      fs.writeFileSync(path.join(data,'rules','demo.json'),fs.readFileSync(path.join(root,'examples/rules/demo-fictional-rules.json')));
      await openToModePage(ctx);
      await p.check('#mode-full');await p.click('#mode-continue');await wait(()=>document.querySelector('#gate').dataset.stage==='handover');
      const handover=squash(await p.locator('#gate').innerText());
      add(tag+'-handover','选了第一种：显示「问卷可以交给助手填，你也可以现在自己填」和两个按钮',handover.includes(squash(TEXT.handover))&&await p.locator('#handover-self').isVisible()&&await p.locator('#handover-assistant').isVisible(),handover);
      add(tag+'-handover-no-overflow','该页没有横向溢出',await noOverflow(),'');
      await p.click('#handover-self');await wait(()=>document.querySelector('#gate').dataset.stage==='wizard');
      add(tag+'-self-fill','选「我现在自己填」进入问卷向导',await stage()==='wizard:basic','');
      add(tag+'-wizard-full-promises','向导里过敏提醒旁已换成「助手能看到这一项」',(await p.locator('#gate').innerText()).includes('你选了让助手全部打理，助手能看到这一项'),'');
      await p.evaluate(()=>sessionStorage.clear());await p.reload();await wait(()=>!document.querySelector('#gate').hidden);
      add(tag+'-handover-again','没填完就重新打开：再次问问卷谁来填',await stage()==='handover',await stage());
      await p.click('#handover-assistant');await wait(()=>document.querySelector('#gate').hidden&&document.querySelector('#main').dataset.view);
      add(tag+'-assistant-fill','选「交给助手填」：进入主界面',(await api('state')).onboarded===false&&await p.locator('.shell').isVisible(),'');
      // 助手（命令行）写入：问卷、疗程、日常、检验、菜单
      ok(agent('POST','/api/onboarding/basic',{answers:{nickname:SENT.nick,helper_mode:'self'},version:0}),'basic');
      const course={treatment_type:'radiotherapy',label:'测试疗程（虚构）',count_unit:'session',total_count:6,schedule_kind:'weekdays',weekdays:[0,2,4],start_date:'2031-03-03',settings_version:0};
      ok(agent('POST','/api/course',course),'course');
      const rev=ok(agent('GET','/api/state'),'state').course_revision;
      ok(agent('POST','/api/course/generate',{expected_revision:rev}),'generate');
      const first=ok(agent('GET','/api/state'),'state2').treatment_session[0];
      ok(agent('POST',`/api/sessions/${first.id}`,{action:'complete',date:TODAY,version:first.version,confirm_off_schedule:true}),'complete');
      ok(agent('POST','/api/observations',{date:TODAY,time:'08:00',diet:'done',note:SENT.note}),'observation');
      ok(agent('POST','/api/aux',{labs:[{date:TODAY,metric:SENT.lab,unit:'示例单位',lab:'虚构实验室',value:5,low:1,high:9}],question:'',rhythm:{blood:null,chemistry:null},celebration:true,version:0}),'aux');
      // 菜单库 + 规则：助手采用一个被规则隐藏的组
      const libFile=path.join(tmp,'library.json');fs.writeFileSync(libFile,JSON.stringify(library));
      const preview=ok(agent('POST','/api/menu/import-preview',library),'preview');
      ok(agent('POST','/api/menu/import-confirm',{menu:library,digest:preview.digest,expected:preview.expected,confirmed:true}),'confirm');
      ok(agent('POST','/api/onboarding/diet',{answers:{requirement_tags:['soft_texture']},version:0}),'diet');
      ok(agent('POST','/api/onboarding/habit',{answers:{sleep_usual:'regular'},version:0}),'habit');
      ok(agent('POST','/api/onboarding/treatment',{answers:{...course,settings_version:ok(agent('GET','/api/state'),'v').settings_version},version:0}),'treatment');
      let menu=ok(agent('GET',`/api/menu?date=${TOMORROW}`),'menu');
      const hidden=menu.rules.hidden[0].group_id;
      const base=()=>({target_date:TOMORROW,revision:menu.state.revision,library_hash:menu.state.library_hash,group_id:hidden});
      ok(agent('POST','/api/menu/action',{...base(),action:'select',rule_confirmed:true}),'select');
      menu=ok(agent('GET',`/api/menu?date=${TOMORROW}`),'menu2');
      ok(agent('POST','/api/menu/action',{...base(),action:'adopt',rule_confirmed:true}),'adopt');
      await p.reload();await wait(()=>document.querySelector('#main').dataset.view);
      // 设置页：问卷标注、AI 助手一节、最近做了什么（只有动作与日期）
      await nav('settings');
      add(tag+'-mark-questionnaire','助手改过的问卷块旁标「最近由助手修改」',(await p.locator('#settings-basic h2').innerText()).includes('最近由助手修改')&&(await p.locator('#settings-diet h2').innerText()).includes('最近由助手修改')&&(await p.locator('#settings-habit h2').innerText()).includes('最近由助手修改'),await p.locator('#settings-basic h2').innerText());
      add(tag+'-mark-course','疗程设置页标「最近由助手修改」',(await p.locator('#main .eyebrow').first().innerText()).includes('最近由助手修改'),await p.locator('#main .eyebrow').first().innerText());
      const items=await p.locator('#assistant-recent li').allInnerTexts();
      add(tag+'-recent-list','「助手最近做了什么」：每条只有动作和日期，不含任何填进去的内容',items.length>=7&&items.every(t=>/ · \d{4}-\d{2}-\d{2}$/.test(t))&&!JSON.stringify(items).match(/SENT_/),items);
      add(tag+'-recent-all-text','设置页整体没有出现检验名/备注的原文（它们不在这一节）',!(await p.locator('#assistant-recent').innerText()).match(/SENT_/),'');
      add(tag+'-settings-no-overflow','设置页没有横向溢出',await noOverflow(),'');
      await shot('settings-assistant','#assistant-section');
      // 各处标注
      await nav('labs');
      add(tag+'-mark-lab','每条检验标「助手记录」',(await p.locator('.table-scroll tbody tr').first().innerText()).includes('助手记录'),await p.locator('.table-scroll tbody tr').first().innerText());
      await nav('course');
      add(tag+'-mark-day','「往日几页」里助手记的那天标「助手记录」',await p.locator(`.past article[data-date="${TODAY}"] .agent-mark`).count()===1,'');
      const done=await p.locator('#done-group').textContent();
      add(tag+'-mark-session','治疗完成标「助手记录」，非治疗日完成另有一行说明',done.includes('助手记录')&&done.includes('助手把一个不在治疗日的日子记成了完成。'),done.slice(0,200));
      await nav('record');
      add(tag+'-mark-record','记一下页面上，助手记的这天标「助手记录」',await p.locator('#record-agent-mark').count()===1,'');
      await p.click('#record-form button[type=submit]');await wait(()=>document.querySelector('#notice').classList.contains('show'));await delay(300);
      const stateAfter=await api('state');
      const row=stateAfter.observation.find(o=>o.kind==='daily');
      add(tag+'-human-resave','人再保存这一天：保存成功，记录者变回本人，标注消失',row.recorded_by==='local-user'&&await p.locator('#record-agent-mark').count()===0,{by:row.recorded_by});
      await nav('plan');await p.click('#menu-day-tomorrow');await wait(()=>document.querySelector('#menu-day-label')?.textContent.includes('明天'));
      const note=await p.locator('#menu-agent-adopt').innerText({timeout:3000}).catch(async()=>'MISSING '+await p.evaluate(()=>JSON.stringify({a:activityData,d:menuDate,ad:menuData?.state?.adopted?.group_id})));
      add(tag+'-mark-adopt','菜单采用旁标「最近由助手修改」，采用被规则隐藏的组另有一行说明',note.includes('最近由助手修改')&&note.includes('助手采用了被你的规则隐藏的组。'),note);
      await nav('tomorrow');
      add(tag+'-candidate-promise','准备明天：全部打理下不再说「你确认之前，菜单不会改动」',!/你确认之前，菜单不会改动/.test(await p.locator('#menu-candidate').innerText())&&/直接/.test(await p.locator('#menu-candidate').innerText()),await p.locator('#menu-candidate').innerText());
      // 设置页：换成第二种后，命令行立即受限；已有标注仍在
      await nav('settings');
      await p.click('#assistant-change');await wait(()=>document.querySelector('#gate').dataset.stage==='mode');
      await p.check('#mode-menu_only');await p.click('#mode-continue');await wait(()=>document.querySelector('#gate').hidden);
      const refused=cli('api','GET','/api/state');
      add(tag+'-switch-back','换回第二种：助手命令行立即被拒，已写入的内容和标注仍在',refused.out.error==='MODE_NOT_ALLOWED'&&refused.code===2&&await p.locator('#settings-basic h2 .agent-mark').count()===1,refused.out);
      await nav('settings');
      add(tag+'-recent-hidden-menu-only','换回第二种后，设置页不再显示「助手最近做了什么」',await p.locator('#assistant-recent').count()===0,'');
    });
  }

  // ---- 演示横幅 ----
  await (async()=>{
    const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'waybeside-mode-demo-'));let server,browser;const errors=[];
    const env={...process.env,WAYBESIDE_DATA_DIR:path.join(tmp,'data'),WAYBESIDE_TODAY:TODAY};
    try{
      const port=await new Promise(res=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const x=s.address().port;s.close(()=>res(x));});});
      const ready=path.join(tmp,'ready.json');
      server=cp.spawn(py,[path.join(root,'run.py'),'--demo','--port',String(port),'--no-browser','--ready-file',ready],{cwd:root,env,stdio:'ignore'});
      for(let i=0;i<100&&!fs.existsSync(ready);i++)await delay(50);
      browser=await chromium.launch({channel:'chrome',headless:true});
      const ctx=await browser.newContext({reducedMotion:'reduce',viewport:{width:375,height:812}});
      await ctx.route('**/*',route=>{new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort();});
      const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));
      await p.goto(JSON.parse(fs.readFileSync(ready)).url);await p.waitForFunction(()=>document.querySelector('#gate')&&!document.querySelector('#gate').hidden,null,{timeout:15000});
      add('demo-first-disclaimer','演示库首开同样先出现免责',await p.evaluate(()=>document.querySelector('#gate').dataset.stage)==='disclaimer','');
      await p.click('#accept-disclaimer');await p.waitForFunction(()=>document.querySelector('#gate').hidden&&document.querySelector('#main').dataset.view,null,{timeout:15000});
      add('demo-skips-mode-page','演示库方式固定为第二种，免责后不再选方式',await p.evaluate(async()=>(await (await api('state')).json()).assistant_mode)==='menu_only','');
      add('demo-banner','演示页面顶部有「演示」横幅',await p.locator('#demo-banner').isVisible()&&(await p.locator('#demo-banner').innerText()).startsWith('演示'),await p.locator('#demo-banner').innerText());
      add('demo-banner-fits','横幅在窄屏不溢出',await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'');
      add('demo-runtime','无脚本错误',errors.length===0,errors);
    }catch(e){add('demo-error','执行异常',false,String(e.stack));}
    finally{if(browser)await browser.close();if(server&&server.exitCode===null){const d=new Promise(r=>server.once('exit',r));server.kill('SIGTERM');await d;}fs.rmSync(tmp,{recursive:true,force:true});}
  })();

  fs.writeFileSync(path.join(ART,'waybeside-assistant-mode-checks.json'),JSON.stringify(checks,null,1));
  console.log(JSON.stringify({pass:checks.filter(c=>c.结果==='pass').length,total:checks.length}));
})();
