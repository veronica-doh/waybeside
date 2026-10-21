// 首次启动端到端（全部虚构）：全新数据目录 → 免责确认 → 选使用方式 → 四块向导 → 主界面 → 设置里修改；
// 以及“导出给助手”七类开关的首次引导。每个场景用全新临时数据目录与回环服务；截图强制 reduced-motion，
// 存到 WAYBESIDE_TEST_ARTIFACTS/waybeside-onboarding-shots。
const fs=require('fs'),os=require('os'),path=require('path'),cp=require('child_process'),net=require('net');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),ART=require('./helpers/artifacts.cjs'),py=require('./helpers/python.cjs')(root);
const outDir=path.join(ART,'waybeside-onboarding-shots');
const checks=[];const add=(id,描述,ok,证据)=>{checks.push({id,描述,结果:ok?'pass':'fail',证据});if(!ok){console.log('FAIL',id,JSON.stringify(证据));process.exitCode=1;}};
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const NICK='SENTINEL_NICK_7f3a',ALLERGY='SENT_ALLERGY_7f3a',NOTE='SENTINEL_NOTE_7f3a';

async function scenario(name,body){
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'waybeside-onb-'));let server,browser;const errors=[];
  const env={...process.env,WAYBESIDE_DATA_DIR:path.join(tmp,'data'),WAYBESIDE_TODAY:'2031-03-04'};
  const cli=(...a)=>{const r=cp.spawnSync(py,['-m','waybeside.agent_door',...a],{cwd:root,env,encoding:'utf8'});return {code:r.status,out:r.stdout.trim()?JSON.parse(r.stdout):null,raw:r.stdout};};
  try{
    const port=await new Promise(res=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const n=s.address().port;s.close(()=>res(n));});});
    const ready=path.join(tmp,'ready.json');
    server=cp.spawn(py,[path.join(root,'run.py'),'--port',String(port),'--no-browser','--ready-file',ready],{cwd:root,env,stdio:'ignore'});
    for(let i=0;i<100&&!fs.existsSync(ready);i++)await delay(50);
    const url=JSON.parse(fs.readFileSync(ready)).url;
    browser=await chromium.launch({headless:true});
    const ctx=await browser.newContext({reducedMotion:'reduce',viewport:{width:1366,height:900}});
    await ctx.route('**/*',route=>{const u=new URL(route.request().url());u.hostname==='127.0.0.1'?route.continue():route.abort();});
    const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));
    const shot=async n=>{await p.locator('#notice').evaluate(e=>e.classList.remove('show'));await p.screenshot({path:path.join(outDir,name+'-'+n+'.png'),fullPage:true});};
    const nav=async h=>{await p.evaluate(h=>{if(location.hash==='#'+h)render();else location.hash=h;},h);await p.waitForFunction(h=>document.querySelector('#main').dataset.view===h,h);};
    const api=(pathName,data)=>p.evaluate(async([a,d])=>(await (await window.api(a,d)).json()),[pathName,data]);
    await body({p,url,shot,nav,api,cli,tmp,env});
    add(name+'-runtime','无脚本错误',errors.length===0,errors);
  }catch(e){add(name+'-error','执行异常',false,String(e.stack));}
  finally{if(browser)await browser.close();if(server&&server.exitCode===null){const d=new Promise(r=>server.once('exit',r));server.kill('SIGTERM');await d;}fs.rmSync(tmp,{recursive:true,force:true});}
}
const gateStage=p=>p.evaluate(()=>{const g=document.querySelector('#gate');return g.hidden?null:g.dataset.stage+(g.dataset.block?':'+g.dataset.block:'');});
const wait=(p,fn,arg)=>p.waitForFunction(fn,arg,{timeout:15000});
const target=p=>p.evaluate(()=>document.querySelector('#main').dataset.view);

(async()=>{
  fs.mkdirSync(outDir,{recursive:true});

  // 场景 1：全新目录 → 免责 → 四块向导（含治疗方案复用设置表单）→ 主界面 → 设置里改
  await scenario('wizard',async({p,url,shot,nav,api})=>{
    await p.goto(url);await wait(p,()=>!document.querySelector('#gate').hidden);
    add('first-gate-disclaimer','全新目录首次启动：先是全屏免责确认，不进主界面',await gateStage(p)==='disclaimer'&&await p.locator('.shell').isHidden(),await gateStage(p));
    const copy=await p.locator('#gate').innerText();
    add('disclaimer-copy','免责大白话：不是医生、不是医疗建议、以医护意见为准、文件未加密',/不是医生/.test(copy)&&/医生、护士或营养师/.test(copy)&&/没有加密/.test(copy),copy.slice(0,120));
    add('disclaimer-button-size','确认按钮 ≥56px 高',await p.locator('#accept-disclaimer').evaluate(e=>e.getBoundingClientRect().height)>=56,'');
    await shot('1-disclaimer');
    let st=await api('state');add('state-before','确认前 /api/state：disclaimer_accepted=false，onboarded=false',st.disclaimer_accepted===false&&st.onboarded===false,{d:st.disclaimer_accepted,o:st.onboarded});
    await p.click('#accept-disclaimer');await wait(p,()=>document.querySelector('#gate').dataset.stage==='mode');await p.check('#mode-menu_only');await p.click('#mode-continue');await wait(p,()=>document.querySelector('#gate').dataset.stage==='wizard');
    add('wizard-step1','确认后进入向导第 1 步（关于你）',await gateStage(p)==='wizard:basic'&&/第 1 步，共 4 步/.test(await p.locator('#gate').innerText()),await gateStage(p));
    // 刷新后向导从未完成的一步接着走，免责不再弹出
    await p.reload();await wait(p,()=>!document.querySelector('#gate').hidden);
    add('reload-no-disclaimer','刷新后不再出现免责确认（只确认一次）',await gateStage(p)==='wizard:basic',await gateStage(p));
    await p.fill('#q-nickname',NICK);await p.check('#q-helper-with_helper');await p.selectOption('#q-age','40_64');
    await shot('2-basic');
    await p.click('#wizard-next');await wait(p,()=>document.querySelector('#gate').dataset.block==='treatment');
    add('wizard-step2','第 2 步是治疗方案，复用设置页的疗程表单（同样的字段）',await p.locator('#course-type').count()===1&&await p.locator('input[name=schedule_kind]').count()===2&&await p.locator('#wizard-next').count()===1,'');
    // 必填未选 → 前端提示，不前进
    await p.click('#wizard-next');await delay(200);
    add('treatment-required','治疗类型必填：不选不能进下一步',await gateStage(p)==='wizard:treatment',await gateStage(p));
    await p.selectOption('#course-type','radiotherapy');await p.fill('#course-total','10');
    await p.check('input[name=schedule_kind][value=weekdays]');
    for(const w of [0,1,2,3,4,5,6])await p.setChecked(`input[name=weekday][value="${w}"]`,[0,2,4].includes(w));
    await p.fill('#course-start','2031-03-03');
    await shot('3-treatment');
    await p.click('#wizard-next');await wait(p,()=>document.querySelector('#gate').dataset.block==='diet');
    st=await api('state');
    add('treatment-projected','治疗方案经向导写入 treatment_course：10 次、周一三五、2031-03-03 起',st.treatment_course[0]?.total_count===10&&JSON.stringify(st.treatment_course[0].weekdays)==='[0,2,4]'&&st.treatment_course[0].start_date==='2031-03-03',st.treatment_course);
    await p.check('#q-tag-soft_texture');
    await p.fill('#q-newtag','测试标签');await p.click('#q-addtag');
    add('custom-tag-added','自加标签出现在可勾选列表里，并带“移除”',await p.locator('#q-tag-my_tag_1').isChecked()&&await p.locator('[aria-label="移除标签 测试标签"]').count()===1,'');
    await p.fill('#q-newallergy',ALLERGY);await p.click('#q-addallergy');
    await p.fill('#q-freenote',NOTE);
    await shot('4-diet');
    await p.click('#wizard-next');await wait(p,()=>document.querySelector('#gate').dataset.block==='habit');
    await p.check('#q-sleep-regular');await p.check('#q-activity-light');await p.fill('#q-medtime','07:30');await p.fill('#q-medmin','15');
    await shot('5-habit');
    add('last-button-label','最后一步按钮写“完成，进入伴程”',(await p.locator('#wizard-next').innerText())==='完成，进入伴程','');
    await p.click('#wizard-next');await wait(p,()=>document.querySelector('#gate').hidden&&document.querySelector('#main').dataset.view);
    st=await api('state');
    add('onboarded','四块完成后 onboarded=true，进入主界面（今天页）',st.onboarded===true&&await target(p)==='today'&&await p.locator('.shell').isVisible(),{o:st.onboarded,v:await target(p)});
    const settings=await api('settings');
    add('answers-saved','后台已存四块答案（版本都是 1），标签/提醒/备注原样保存',Object.values(settings.versions).every(v=>v===1)&&Object.keys(settings.versions).length===4&&settings.diet.allergy_reminders[0]===ALLERGY&&settings.diet.requirement_tags.includes('soft_texture')&&settings.habit.meditation_usual.minutes===15,settings.versions);
    add('greeting','今天页的问候语显示昵称，并按“有人帮忙”改称“你们”',(await p.locator('.path-right .eyebrow').innerText()).startsWith('你们好，'+NICK),await p.locator('.path-right .eyebrow').innerText());
    add('habit-quick','习惯默认值只以可点击的快捷按钮出现（不做只读展示），点了才记',await p.locator('[data-habit-meditation="15"]').count()===1&&(await api('state')).observation.filter(o=>o.kind==='daily').length===0,'');
    await p.click('[data-habit-meditation="15"]');await wait(p,()=>!document.querySelector('[data-habit-meditation]'));
    const obs=(await api('state')).observation.filter(o=>o.kind==='daily');
    add('habit-quick-recorded','点击后记下一条冥想：已完成、15 分钟',obs.length===1&&obs[0].content.meditation==='done'&&obs[0].content.minutes==='15',obs);
    await shot('6-today');
    await p.reload();await wait(p,()=>document.querySelector('#main').dataset.view);
    add('reload-main','完成后刷新直接进主界面，不再出现门',await gateStage(p)===null,await gateStage(p));

    // 完成后在设置里修改（没有只读档案页）
    await nav('settings');
    add('settings-editable','设置页有可编辑的三块（关于你/饮食要求/日常习惯）和疗程表单',await p.locator('#settings-basic,#settings-diet,#settings-habit,#course-form').count()===4,'');
    const nickValue=await p.locator('#settings-basic #q-nickname').inputValue();
    add('settings-prefill','答案以输入框的值回显，可直接改',nickValue===NICK&&await p.locator('#settings-diet #q-freenote').inputValue()===NOTE&&await p.locator('#settings-habit #q-medmin').inputValue()==='15','');
    await p.fill('#settings-basic #q-nickname',NICK+'2');
    await p.click('#save-basic');await delay(500);
    const after=await api('settings');
    add('settings-edit','在设置里改昵称，保存后版本 +1',after.basic.nickname===NICK+'2'&&after.versions.basic===2,after.versions);
    await p.locator('#settings-diet [aria-label="移除标签 测试标签"]').click();
    await p.click('#save-diet');await delay(500);
    const after2=await api('settings');
    add('settings-remove-tag','移除自加标签并保存：标签与勾选一并去掉',!after2.diet.custom_tags.length&&!after2.diet.requirement_tags.includes('my_tag_1'),after2.diet);
    await shot('7-settings');

    // 免责页脚：我的方案 / 导出给助手
    await nav('plan');
    add('footer-plan','“我的方案”页脚常驻免责一行',await p.locator('#page-disclaimer').isVisible()&&/不是医疗建议/.test(await p.locator('#page-disclaimer').innerText()),'');
    await nav('today');
    add('footer-today-hidden','今天页不显示这行页脚（只在方案/规则筛选/导出页常驻）',await p.locator('#page-disclaimer').isHidden(),'');

    // 没有只读档案：逐页检查 DOM
    const pages=['today','record','tomorrow','plan','labs','course','history','help','settings','export'];
    const report={};
    for(const pg of pages){
      await nav(pg);await delay(150);
      report[pg]=await p.evaluate(([allergy,note])=>{
        const text=document.body.innerText;
        const inEditable=[allergy,note].map(s=>{
          const hits=[...document.querySelectorAll('body *')].filter(el=>el.children.length===0&&el.textContent.includes(s));
          return hits.every(el=>el.closest('form')&&el.closest('.editable-row,textarea,input,select'));
        });
        return {archive:/档案|我的资料|profile/i.test(text),leak:[allergy,note].map(s=>text.includes(s)),inEditable};
      },[ALLERGY,NOTE]);
    }
    add('no-archive-text','任何页面都没有“档案/profile”字样',pages.every(pg=>!report[pg].archive),report);
    add('answers-only-in-editors','过敏提醒与备注只出现在设置页的可编辑控件里；其他页面一律不显示',pages.every(pg=>pg==='settings'||report[pg].leak.every(v=>!v)),report);
    add('nav-has-no-profile','导航没有任何档案/资料项',!/档案|资料/.test(await p.locator('.nav').innerText()),await p.locator('.nav').innerText());
    add('backend-hides-profile-endpoints','没有只读档案接口',await p.evaluate(async()=>(await fetch('/api/profile-preview')).status)===404,'');
  });

  // 场景 2：整步跳过也能走完；跳过的治疗方案落到“去设置疗程”的引导
  await scenario('skip-all',async({p,url,shot,api})=>{
    await p.goto(url);await wait(p,()=>!document.querySelector('#gate').hidden);
    await p.click('#accept-disclaimer');await wait(p,()=>document.querySelector('#gate').dataset.stage==='mode');await p.check('#mode-menu_only');await p.click('#mode-continue');await wait(p,()=>document.querySelector('#gate').dataset.stage==='wizard');
    for(const b of ['basic','treatment','diet','habit']){
      add('skip-'+b,`第 ${b} 步可以先跳过`,await gateStage(p)==='wizard:'+b,await gateStage(p));
      await p.click('#wizard-skip');
      if(b!=='habit')await wait(p,x=>document.querySelector('#gate').dataset.block!==x,b);
    }
    await wait(p,()=>document.querySelector('#gate').hidden&&document.querySelector('#main').dataset.view);
    const st=await api('state');
    add('skip-onboarded','四步全跳过也算完成；没有疗程，今天页引导去设置',st.onboarded===true&&st.treatment_course.length===0&&await p.locator('#goto-settings').count()>=1,'');
    const s=await api('settings');
    add('skip-defaults','跳过的块不写入任何答案（昵称为空，helper 默认为自己用）',!s.basic.nickname&&s.basic.helper_mode==='self'&&JSON.stringify(s.diet.custom_tags)==='[]','');
    await shot('skip-today');
  });

  // 场景 3：导出给助手——首次进入逐项引导，默认全关，开关生效到 agent_door
  await scenario('agent-share',async({p,url,shot,nav,api,cli,env})=>{
    await p.goto(url);await wait(p,()=>!document.querySelector('#gate').hidden);
    await p.evaluate(async()=>{await api('disclaimer',{accepted:true});await api('assistant-mode',{mode:'menu_only',version:0});await refreshState();for(const b of BLOCK_ORDER){await api('onboarding/'+b,{answers:{},version:settingsData.versions[b],skipped:true});await refreshState();}render();});
    await wait(p,()=>document.querySelector('#main').dataset.view);
    // 放一份虚构菜单库，才有东西可导出
    cp.spawnSync(py,['-c',"from waybeside.menu_library import import_library,synthetic_library;from waybeside.storage import data_directory;import_library(data_directory()/'waybeside.sqlite',synthetic_library())"],{cwd:root,env,encoding:'utf8'});
    add('polish-nav-no-export','主导航没有「导出给助手」',!(await p.locator('.nav').innerText()).includes('导出给助手')&&await p.locator('.nav a').count()===5,await p.locator('.nav').innerText());
    await nav('settings');
    add('polish-settings-entry','设置页内有「导出给助手」入口区块，点开进入导出页并保留首次引导',await p.locator('#export-entry').count()===1&&/还有 7 项没决定/.test(await p.locator('#export-entry').innerText()),'');
    await shot('polish-settings-entry');
    await p.click('#open-export');await wait(p,()=>document.querySelector('#main').dataset.view==='export');
    add('polish-entry-opens-export','从设置页入口进入导出页，引导仍在',await p.locator('#share-guide').count()===1,'');
    await nav('export');
    add('share-default-off','第一次进入：七类全部“还没决定”，且实际都是关闭',await p.locator('[data-state=undecided]').count()===7&&(await api('agent-share')).every(r=>r.enabled===false&&r.version===1),'');
    add('share-guide','出现逐项引导：还有 7 项没决定',/还有 7 项没决定/.test(await p.locator('#share-guide').innerText()),'');
    add('share-footer','导出页脚常驻免责一行',await p.locator('#page-disclaimer').isVisible(),'');
    add('share-never','页面写明昵称/过敏提醒/备注/检验/症状永不给助手',/昵称、过敏提醒、备注、检验和症状记录都不会给助手/.test(await p.locator('#export-page').innerText()),'');
    await shot('share-1-first-visit');
    let r=cli('export','--date','2031-03-05');
    add('share-cli-disabled','关闭时 agent_door export 返回 SHARE_DISABLED（退出码 2）',r.code===2&&r.out.error==='SHARE_DISABLED',r.out);
    // 逐项：菜单库开、采用状态“不给”、偏好开
    await p.click('#share-menu_library [data-share=on]');await wait(p,()=>document.querySelector('#share-menu_library [data-state=on]'));
    add('share-one-on','开启菜单库后该项显示“已开启”，引导剩 6 项',/还有 6 项没决定/.test(await p.locator('#share-guide').innerText()),'');
    await p.click('#share-adoption_state [data-share=off]');await wait(p,()=>document.querySelector('#share-adoption_state [data-state=off]'));
    add('share-explicit-off','点“不给”：显示“已关闭”，不再算没决定（引导剩 5 项）',/还有 5 项没决定/.test(await p.locator('#share-guide').innerText()),'');
    r=cli('export','--date','2031-03-05');
    const pkg=r.out&&r.out.package?JSON.parse(fs.readFileSync(r.out.package)):null;
    add('share-export-minimal','只开菜单库：导出成功，只有 groups，没有 current/偏好/年龄/疗程进度，模板不预填组',r.code===0&&pkg&&pkg.snapshot.groups.length>0&&!['current','avoided_group_ids','favorite_group_ids','recent_records','requirement_tags','age_band','course_progress'].some(k=>k in pkg.snapshot)&&pkg.candidate_template.group_id===null,r.out);
    await shot('share-2-partly-decided');
    for(const c of ['preferences','recent_meal_status','requirement_tags','age_band','course_progress']){
      await p.click(`#share-${c} [data-share=off]`);await wait(p,x=>document.querySelector(`#share-${x} [data-state=off]`),c);
    }
    add('share-guide-gone','七项都决定后引导消失',await p.locator('#share-guide').count()===0,'');
    // 之后可以改：点“开启”
    await p.click('#share-age_band [data-share=on]');await wait(p,()=>document.querySelector('#share-age_band [data-state=on]'));
    const rows=await api('agent-share');
    add('share-final','最终状态：菜单库、年龄段开；其余关；版本都已前进',rows.filter(x=>x.enabled).map(x=>x.category).sort().join()==='age_band,menu_library'&&rows.every(x=>x.version>=2),rows);
    const st=await api('state');
    add('share-audit-hidden','开关的 audit 不进 /api/state',!st.audit.some(a=>a.entity==='agent_share'),'');
    await shot('share-3-final');
  });

  fs.writeFileSync(path.join(ART,'waybeside-onboarding-checks.json'),JSON.stringify(checks,null,1));
  console.log(JSON.stringify({pass:checks.filter(c=>c.结果==='pass').length,total:checks.length}));
})();
