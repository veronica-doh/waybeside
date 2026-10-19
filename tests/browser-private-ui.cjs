// 虚构资料；private=全新目录 + 导入菜单库，demo=--demo 演示目录（虚构 6 周期）。回环服务、临时数据目录、截图强制 reduced-motion。
const fs=require('fs'),os=require('os'),path=require('path'),cp=require('child_process'),net=require('net');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),ART=require('./helpers/artifacts.cjs'),pyPath=require('./helpers/python.cjs')(root),passGate=require('./helpers/gate.cjs');
const outDir=path.join(ART,'2026-10-16-waybeside-private-ui-fix/after');
const dest=path.join(ART,'2026-10-16-waybeside-private-ui-fix-checks.json');
const checks=[];const add=(id,描述,ok,证据)=>{checks.push({id,描述,结果:ok?'pass':'fail',证据});if(!ok)console.log('FAIL',id,证据);};
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const NAV=['today','tomorrow','plan','labs','course'];
function fictionalLibrary(){
  const r=cp.spawnSync(pyPath,['-c',"import json;from waybeside.menu_library import synthetic_library as s;d=s();d.pop('fictional');d['version']='fictional-private-v1'\nfor x in d['days']:\n  for m in x['meals']:m['title']='示例'+m['meal']+'组'+x['group_id']\nprint(json.dumps(d,ensure_ascii=False))"],{cwd:root,encoding:'utf8'});
  return JSON.parse(r.stdout);
}
async function run(mode){
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'waybeside-ui-'));let server,browser;const errors=[];
  try{
    const port=await new Promise(res=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const n=s.address().port;s.close(()=>res(n));});});
    const ready=path.join(tmp,'ready.json');
    server=cp.spawn(pyPath,[path.join(root,'run.py'),...(mode==='demo'?['--demo']:[]),'--port',String(port),'--no-browser','--ready-file',ready],{env:{...process.env,WAYBESIDE_DATA_DIR:path.join(tmp,'data'),WAYBESIDE_TODAY:'2031-03-04'},stdio:'ignore'});
    for(let i=0;i<100&&!fs.existsSync(ready);i++)await delay(50);
    const url=JSON.parse(fs.readFileSync(ready)).url;
    browser=await chromium.launch({channel:'chrome',headless:true});
    const ctx=await browser.newContext({reducedMotion:'reduce',viewport:{width:1366,height:900}});
    await ctx.route('**/*',route=>{const u=new URL(route.request().url());u.hostname==='127.0.0.1'?route.continue():route.abort();});
    const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));
    await p.goto(url);await passGate(p);await delay(400);
    const priv=mode==='private';
    if(priv){
      const menu=fictionalLibrary();
      await p.evaluate(async menu=>{const pv=await (await api('menu/import-preview',menu)).json();await api('menu/import-confirm',{menu,digest:pv.digest,expected:pv.expected,confirmed:true});await refreshState();await fetchMenu();render();},menu);
      await delay(400);
    }
    const nav=async h=>{await p.evaluate(h=>{if(location.hash==='#'+h)render();else location.hash=h;},h);await p.waitForFunction(h=>document.querySelector('#main').dataset.view===h,h);await delay(500);};
    const shot=async n=>{await p.locator('#notice').evaluate(e=>e.classList.remove('show'));await p.screenshot({path:path.join(outDir,mode+'-'+n+'.png'),fullPage:true});};
    // 导航全覆盖：点击真实导航链接，主区域内容必须变化且非空
    const seen=[];let prev='';
    for(const h of NAV){
      await p.locator(`.nav a[href="#${h}"]`).click();await p.waitForFunction(h=>document.querySelector('#main').dataset.view===h,h);await delay(500);
      const t=await p.locator('#main').innerText();
      add(`${mode}-nav-${h}`,`${mode} 导航「${h}」主区域有内容且与上一页不同`,t.trim().length>40&&t!==prev&&!seen.includes(t),{chars:t.length});
      seen.push(t);prev=t;await shot('nav-'+h);
    }
    add(`${mode}-nav-count`,'导航恰为 5 项（导出给助手已移入设置页）且无资料/档案项',await p.locator('.nav a').count()===5&&!(await p.locator('.nav').innerText()).includes('导出给助手')&&!(await p.locator('.nav').innerText()).match(/资料|档案/),await p.locator('.nav').innerText());
    add(`${mode}-nav-name`,'导航名为「我的方案」，无「我的菜单」',await p.locator('.nav').innerText().then(t=>t.includes('我的方案')&&!t.includes('我的菜单')),'');
    await nav('today');
    add(`${mode}-home-density`,'首页不展开五餐（无餐卡、无营养条），仅一行入口',await p.locator('.menu-meal, .menu-nutrition, .menu-meals').count()===0&&await p.locator('#today-menu-open').innerText().then(t=>t.includes('看今天五餐')),'');
    add(`${mode}-home-entry-date`,'首页入口写明「今天 N月N日」',await p.locator('#today-menu-copy').innerText().then(t=>/今天 \d+月\d+日/.test(t)),await p.locator('#today-menu-copy').innerText());
    if(priv){add('private-home-empty','无疗程设置时首页只给去设置的引导，不画任何格子',await p.locator('.journey-dots .stone').count()===0&&await p.locator('#goto-settings').count()===1,'');}
    await shot('home');
    await nav('course');if(priv)add('private-course-empty','无疗程设置时旅程页同样只有引导、没有格子与记录表单',await p.locator('.journey-dots .stone').count()===0&&await p.locator('#course-guide').count()===1&&await p.locator('#private-session-form').count()===0,'');
    await nav('labs');
    if(priv){add('private-labs-empty','检验空状态：下拉非空白、提示明确、录入按钮突出',await p.locator('#lab-series option').allInnerTexts().then(a=>a.length===1&&a[0].includes('还没有检验记录'))&&await p.locator('[data-open-lab].primary').count()===1&&await p.locator('#main').innerText().then(t=>t.includes('点“录入一项结果”开始')),'');}
    // 配色：全页计算样式无蓝色系，主色为玉绿 #176450
    for(const h of ['today','plan','labs','course']){await nav(h);const bad=await p.evaluate(()=>{const blue=c=>{const m=c.match(/\d+(\.\d+)?/g);if(!m||(m[3]!==undefined&&+m[3]===0))return false;const [r,g,b]=m.map(Number);return b>r+25&&b>g+10;};const out=[];for(const e of document.querySelectorAll('body *')){const cs=getComputedStyle(e);for(const k of ['color','backgroundColor','borderTopColor','borderLeftColor','outlineColor'])if(blue(cs[k]))out.push(e.tagName+'.'+e.className+':'+k+'='+cs[k]);}return out.slice(0,5);});add(`${mode}-no-blue-${h}`,`${h} 页无蓝色系`,bad.length===0,bad);}
    add(`${mode}-primary-green`,'主按钮底色为 #176450',await p.evaluate(()=>{const b=document.createElement('button');b.className='primary';document.body.append(b);const c=getComputedStyle(b).backgroundColor;b.remove();return c;})==='rgb(23, 100, 80)','');
    await nav('plan');add(`${mode}-plan-title`,'「我的方案」页标题为我的方案，含饮食·菜单小标题',await p.locator('#main h1').innerText()==='我的方案'&&await p.locator('#main h2').first().innerText().then(t=>t.includes('菜单')),'');
    add(`${mode}-plan-day`,'方案页醒目标出日期',await p.locator('#menu-day-label').innerText().then(t=>/(今天|明天) \d+月\d+日/.test(t)),await p.locator('#menu-day-label').innerText());
    await p.locator('#menu-day-today').click();await delay(600);add(`${mode}-plan-today-toggle`,'可切到今天的菜单并标「今天」',await p.locator('#menu-day-label').innerText().then(t=>t.startsWith('今天')),'');
    for(const h of ['today','tomorrow','plan','labs','course']){await nav(h);const t=await p.locator('#main').innerText();add(`${mode}-no-profile-${h}`,`${h} 无只读资料/档案展示`,!/原始档案|资料与核对|profile/.test(t)&&!/版本 (synthetic|fictional)/.test(t),'');}
    // 换组：手选深处一组，预览标题必须在视口内，且内容切到该组
    await nav('tomorrow');
    await p.locator('#menu-pick').click();while(await p.locator('#menu-show-more').count())await p.locator('#menu-show-more').click();
    const ids=await p.locator('[id^=menu-select-]').evaluateAll(a=>a.map(x=>x.id));const last=p.locator('#'+ids.at(-1));
    await last.scrollIntoViewIfNeeded();await last.focus();await p.keyboard.press('Enter');await delay(900);
    const gid=ids.at(-1).replace('menu-select-','');
    add(`${mode}-pick-switch`,'点击远处分组后预览切到该组',await p.locator('.menu-group-view').first().getAttribute('data-group-id')===gid,gid);
    add(`${mode}-pick-visible`,'切换后预览标题在视口内（键盘 Enter 触发）',await p.locator('#menu-preview-title').evaluate(e=>{const r=e.getBoundingClientRect();return r.top>=-2&&r.bottom<=innerHeight;}),'');
    await shot('pick');
    const g1=await p.locator('.menu-group-view').first().getAttribute('data-group-id');await p.locator('#menu-change').click();await delay(800);
    add(`${mode}-change-feedback`,'换组后可见文字和读屏播报写明哪一天',await p.locator('.menu-feedback').innerText().then(t=>/明天 \d+月\d+日|明天\d+月\d+日|明天已换成第/.test(t)||t.includes('明天'))&&await p.locator('#agent-status').innerText().then(t=>t.includes('明天')&&t.includes('已换成第')),'');
    add(`${mode}-change`,'「换一组」切换到另一组',await p.locator('.menu-group-view').first().getAttribute('data-group-id')!==g1,'');
    // 检验：录入一项并读回
    await nav('labs');await p.locator('#lab-form').evaluate(f=>f.hidden=false);
    const before=await p.locator('#main table tbody tr, #main .raw-table tr').count();
    await p.fill('#lab-form [name=metric]','示例指标 A');await p.fill('#lab-form [name=unit]','×10⁹/L');await p.fill('#lab-form [name=lab]','虚构实验室甲');await p.fill('#lab-form [name=value]','5.2');
    await p.locator('#lab-form button[type=submit]').click();await delay(900);
    add(`${mode}-labs-record`,'检验结果可录入并显示在原始表',await p.locator('#main').innerText().then(t=>t.includes('5.2')&&t.includes('虚构实验室甲')),'');
    await p.reload();await passGate(p);await nav('labs');
    add(`${mode}-labs-persist`,'刷新后检验记录仍在',await p.locator('#main').innerText().then(t=>t.includes('虚构实验室甲')),'');await shot('labs-after');
    // 旅程：私人模式可添加预约，小路出现，可更正
    await nav('course');
    if(priv){
      // 先走疗程设置界面：总数 10、每周一三五、2031-03-03 开始，再一键排出日期。
      await nav('settings');
      await p.selectOption('#course-type','radiotherapy');await p.fill('#course-total','10');await p.check('input[name=schedule_kind][value=weekdays]');
      for(const w of ['0','2','4'])await p.check(`input[name=weekday][value="${w}"]`);
      await p.fill('#course-start','2031-03-03');await p.locator('#course-form button[type=submit]').click();
      await p.waitForFunction(()=>document.querySelector('#main').dataset.view==='course'&&document.querySelector('#generate-open'));
      add('private-course-generate-offer','保存设置后旅程页提示按设置排出 10 次',await p.locator('#generate-box').innerText().then(t=>t.includes('10')),'');
      await p.locator('#generate-open').click();await p.waitForFunction(()=>document.querySelectorAll('.journey-dots .stone:not(.blank)').length===10);
      add('private-course-generated','排出后恰为 10 格、4 周（3+3+3+1）',await p.locator('.journey-dots .stone:not(.blank)').count()===10&&await p.locator('.journey-dots .week').count()===4,await p.locator('.journey-dots .week').allInnerTexts());
      await p.fill('#private-session-form [name=date]','2031-03-04');await p.locator('#private-session-form button[type=submit]').click();await delay(900);
      add('private-course-trail','再添加一条预约后共 11 格（超出总数时以已有记录数为准）',await p.locator('.journey-dots .stone:not(.blank)').count()===11&&await p.locator('.journey-dots .stone').count()===11,'');
      await p.reload();await p.locator('.journey-dots').waitFor();
      {const folds=await p.locator('.session-fold.history-item').count(),openFolds=await p.locator('.session-fold.history-item[open]').count(),visibleComplete=await p.locator('[data-private-action=complete]:visible').count();
       add('polish-journey-folded','旅程页 11 条里只展开「今天/下一次」一条（刷新后），其余收为摘要行（操作按钮仅 1 组可见）',folds===11&&openFolds===1&&visibleComplete===1,{folds,openFolds,visibleComplete,open:await p.locator('.session-fold.history-item[open]').evaluateAll(l=>l.map(e=>e.id))});
       const summary=await p.locator('.session-fold.history-item:not([open]) > summary').first().innerText();
       add('polish-journey-summary','收起条目是一行摘要「第 N 次 · 日期 · 状态」',/^第 \d+ 次 · \d{4}-\d{2}-\d{2} · /.test(summary),summary);
       await p.locator('.session-fold.history-item:not([open]) > summary').first().click();
       add('polish-journey-expand','点开摘要后才出现该条操作',await p.locator('[data-private-action=complete]:visible').count()===2,'');
       await p.locator('.session-fold.history-item[open] > summary').nth(1).click();}
      await p.locator('[data-private-action=complete]:visible').first().click();await p.locator('button[value=confirm]').click();await delay(900);
      add('private-course-complete','确认完成后进度为 1',await p.locator('.journey-dots .stone.complete').count()===1,'');
      await p.locator('#done-group>summary').click();
      add('polish-completed-grouped','已完成的收进「已走过 1 次」一组，点开才见更正操作',await p.locator('#done-group>summary').innerText().then(t=>/已走过 1 次/.test(t)),'');
      await p.locator('[data-private-action=correct]').first().click();await p.locator('button[value=confirm]').click();await delay(900);
      add('private-course-correct','更正后回到待确认',await p.locator('.journey-dots .stone.complete').count()===0,'');
    }else{
      add('demo-course-trail','演示旅程小路恰为 6 格（化疗 6 周期，来自配置）',await p.locator('.journey-dots .stone').count()===6,'');
    }
    await shot('course-after');
    add(`${mode}-runtime`,'无脚本错误',errors.length===0,errors);
  }catch(e){add(`${mode}-error`,'执行异常',false,String(e.stack));process.exitCode=1;}
  finally{if(browser)await browser.close();if(server&&server.exitCode===null){const d=new Promise(r=>server.once('exit',r));server.kill('SIGTERM');await d;}fs.rmSync(tmp,{recursive:true,force:true});}
}
(async()=>{
  fs.mkdirSync(outDir,{recursive:true});
  await run('demo');await run('private');
  const summary={pass:checks.filter(c=>c.结果==='pass').length,total:checks.length};
  fs.writeFileSync(dest,JSON.stringify({summary,checks,cleanup:'浏览器与服务已退出，临时数据目录已删除'},null,2)+'\n');
  console.log(JSON.stringify(summary));if(summary.pass!==summary.total)process.exitCode=1;
})();
