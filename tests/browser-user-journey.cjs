// 用户旅程模拟里发现的问题的回归测试（全部虚构数据）：真实库不写“虚构”、求助页人话、登录失效提示、
// 过敏提醒不丢、开始日填好后的提示、导入提示、被替换的建议说明、助手记录标注、375 宽表格提示。
const fs=require('fs'),os=require('os'),path=require('path'),cp=require('child_process'),net=require('net');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),py=require('./helpers/python.cjs')(root),passGate=require('./helpers/gate.cjs');
const checks=[];const add=(id,描述,ok,证据)=>{checks.push({id,描述,结果:ok?'pass':'fail',证据});if(!ok){console.log('FAIL',id,JSON.stringify(证据));process.exitCode=1;}};
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const ALLERGY='SENT_ALLERGY_6e1d';
async function scenario(name,width,body){
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'waybeside-uj-'));let server,browser;const errors=[];
  const data=path.join(tmp,'data');fs.mkdirSync(data,{recursive:true});
  const env={...process.env,WAYBESIDE_DATA_DIR:data,WAYBESIDE_TODAY:'2031-03-04'};
  let n=0;
  const cli=(...a)=>{const r=cp.spawnSync(py,['-m','waybeside.agent_door',...a],{cwd:root,env,encoding:'utf8'});return {code:r.status,out:r.stdout.trim()?JSON.parse(r.stdout):null,raw:r.stdout};};
  const agent=(m,u,b)=>{const a=['api',m,u];if(b!==undefined){const f=path.join(tmp,`b${++n}.json`);fs.writeFileSync(f,JSON.stringify(b));a.push('--json',f);}const r=cli(...a);if(r.code!==0)throw new Error(u+' '+r.raw);return r.out.body;};
  try{
    const port=await new Promise(res=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const x=s.address().port;s.close(()=>res(x));});});
    const ready=path.join(tmp,'ready.json');
    server=cp.spawn(py,[path.join(root,'run.py'),'--port',String(port),'--no-browser','--ready-file',ready],{cwd:root,env,stdio:'ignore'});
    for(let i=0;i<100&&!fs.existsSync(ready);i++)await delay(50);
    const url=JSON.parse(fs.readFileSync(ready)).url;
    browser=await chromium.launch({channel:'chrome',headless:true});
    const ctx=await browser.newContext({reducedMotion:'reduce',viewport:{width,height:width<500?812:900}});
    await ctx.route('**/*',r=>{new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort();});
    const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));
    const nav=async h=>{await p.evaluate(h=>{if(location.hash==='#'+h)render();else location.hash=h;},h);await p.waitForFunction(h=>document.querySelector('#main').dataset.view===h,h,{timeout:15000});await delay(300);};
    await body({p,url,cli,agent,nav,tmp,tag:`${name}-${width}`,ctx,port,data,library:()=>JSON.parse(fs.readFileSync(path.join(root,'examples/menu/demo-menu-fictional.json'),'utf8'))});
    add(`${name}-${width}-runtime`,'无脚本错误',errors.length===0,errors);
  }catch(e){add(`${name}-${width}-error`,'执行异常',false,String(e.stack));}
  finally{if(browser)await browser.close();if(server&&server.exitCode===null){const d=new Promise(r=>server.once('exit',r));server.kill('SIGTERM');await d;}fs.rmSync(tmp,{recursive:true,force:true});}
}
(async()=>{
  for(const width of [1366,375]){
    await scenario('real-library',width,async({p,url,nav,tag})=>{
      await p.goto(url);await passGate(p);await p.locator('#main[data-view]').waitFor();
      const banner=async()=>(await p.locator('.prototype span').innerText());
      const texts=[];
      for(const h of ['today','plan','tomorrow','course','labs','settings','help']){await nav(h);texts.push(h+':'+await banner());}
      add(tag+'-no-fictional-banner','真实库每个页面的顶部横幅都不写「虚构数据」，也没有「演示」横幅',texts.every(t=>!t.includes('虚构'))&&await p.locator('#demo-banner').isHidden(),texts);
      await nav('help');
      const help=await p.locator('#main').innerText();
      add(tag+'-help-plain','求助页没有原型、生成、核对状态、尚未配置这类用语，并告诉人联系医生护士',!/原型|生成|核对状态|尚未配置|提示来源|模型/.test(help)&&help.includes('联系一直照顾你的医生、护士')&&help.includes('抄在纸上'),help.slice(0,300));
      add(tag+'-help-fits','求助页不横向溢出',await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'');
    });
    await scenario('session-lost',width,async({p,url,ctx,tag})=>{
      await p.goto(url);await passGate(p);await p.locator('#main[data-view]').waitFor();
      await ctx.clearCookies();
      const fresh=await ctx.newPage();await fresh.goto(url.split('#')[0]);
      await fresh.waitForFunction(()=>document.querySelector('#main').textContent.length>20&&!document.querySelector('#main').textContent.includes('正在读取'));
      const text=await fresh.locator('#main').innerText();
      add(tag+'-lost-message','登录失效时：不说「服务已重启」，也不要求「运行启动命令」，告诉人让助手再运行 open-url',text.includes('open-url')&&!/服务已重启|启动命令/.test(text),text);
      add(tag+'-lost-chrome','没登录时不显示导航和退出',!(await fresh.locator('.nav').isVisible())&&!(await fresh.locator('#reset').isVisible()),'');
    });
    await scenario('allergy-and-trail',width,async({p,url,nav,tag,agent})=>{
      await p.goto(url);await p.waitForSelector('#accept-disclaimer');await p.click('#accept-disclaimer');
      await p.waitForSelector('#mode-menu_only');await p.check('#mode-menu_only');await p.click('#mode-continue');await p.waitForSelector('#wizard-next');
      await p.click('#wizard-skip');await p.waitForFunction(()=>document.querySelector('#gate').dataset.block==='treatment');
      await p.selectOption('#course-type','radiotherapy');await p.fill('#course-total','5');await p.check('input[name=schedule_kind][value=weekdays]');
      for(const w of [0,1,2,3,4,5,6])await p.setChecked(`input[name=weekday][value="${w}"]`,[0,2,4].includes(w));
      await p.fill('#course-start','2031-03-05');
      await p.click('#wizard-next');await p.waitForFunction(()=>document.querySelector('#gate').dataset.block==='diet');
      await p.fill('#q-newallergy',ALLERGY);await p.fill('#q-newtag','自加标签甲');
      await p.click('#wizard-next');await p.waitForFunction(()=>document.querySelector('#gate').dataset.block==='habit');
      await p.click('#wizard-skip');await p.waitForFunction(()=>document.querySelector('#gate').hidden&&document.querySelector('#main').dataset.view);
      const s=await p.evaluate(async()=>(await (await api('settings')).json()).diet);
      add(tag+'-allergy-kept','过敏提醒写在框里没点「加一条」就点下一步：不丢',JSON.stringify(s.allergy_reminders)===JSON.stringify([ALLERGY])&&s.custom_tags.length===1&&s.custom_tags[0].label==='自加标签甲',s);
      const hint=await p.locator('#trail-generate-hint').innerText();
      add(tag+'-trail-hint','开始日、星期、总数都填好后，今天页提示去「这段旅程」排出日期，不再叫人填日期',hint.includes('还没有排出来')&&hint.includes('这段旅程')&&!/填写第一次治疗日期/.test(await p.locator('#main').innerText()),hint);
      await p.click('#trail-generate-hint a');await p.waitForFunction(()=>document.querySelector('#main').dataset.view==='course');
      add(tag+'-trail-link','提示里的链接把人带到「这段旅程」',await p.locator('#generate-open').isVisible(),'');
      await nav('settings');
      const aiCount=async()=>(await p.locator('#main h2').allInnerTexts()).filter(t=>t.trim()==='AI 助手').length;
      add(tag+'-ai-section-once','设置页「AI 助手」小标题只出现一次',await aiCount()===1,await p.locator('#main h2').allInnerTexts());
      await p.click('#assistant-change');await p.check('#mode-full');await p.click('#mode-continue');await p.waitForFunction(()=>document.querySelector('#gate').hidden);await delay(400);
      add(tag+'-ai-section-once-after-switch','换方式之后「AI 助手」小标题仍只出现一次',await aiCount()===1&&await p.locator('#assistant-section').count()===1,await p.locator('#main h2').allInnerTexts());
    });
    await scenario('replaced-candidate',width,async({p,url,nav,tag,agent,cli,tmp,library})=>{
      await p.goto(url);await passGate(p);await p.locator('#main[data-view]').waitFor();
      // 人导入菜单、打开共享（走页面同一套接口）
      const lib=library();
      await p.evaluate(async lib=>{const pv=await (await api('menu/import-preview',lib)).json();await api('menu/import-confirm',{menu:lib,digest:pv.digest,expected:pv.expected,confirmed:true});for(const r of await (await api('agent-share')).json())if(r.category==='menu_library')await api('agent-share',{category:r.category,enabled:true,version:r.version});await refreshState();await fetchMenu();render();},lib);
      const pkg=path.join(tmp,'p1.json');const r1=cli('export','--date','2031-03-05','--output',pkg);
      const cand=path.join(tmp,'c1.json');const pk=JSON.parse(fs.readFileSync(pkg));fs.writeFileSync(cand,JSON.stringify({...pk.candidate_template,group_id:pk.snapshot.groups[1].group_id,reason_code:'VARIETY'}));
      const sub=cli('submit',cand);
      await nav('tomorrow');
      add(tag+'-ready','助手提交后，页面显示这份建议，没有「被替换」说明',sub.out.status==='READY_TO_REVIEW'&&await p.locator('#menu-cand-adopt').count()===1&&await p.locator('#menu-candidate-replaced').count()===0,sub.out);
      cli('export','--date','2031-03-05','--output',path.join(tmp,'p2.json'));
      await p.evaluate(async()=>{await fetchMenu(menuDate);render();});await delay(300);
      const note=await p.locator('#menu-candidate-replaced').innerText().catch(()=> '');
      add(tag+'-replaced-note','助手又 export 之后，页面说明之前的建议已作废，不再悄悄消失',note.includes('已作废')&&await p.locator('#menu-cand-adopt').count()===0,note);
      add(tag+'-import-toast-plain','导入菜单的提示里没有「个人档案仍待迁移」',!(await p.evaluate(()=>document.body.innerText)).includes('档案仍待迁移'),'');
    });
    await scenario('agent-marks-and-table',width,async({p,url,nav,tag,agent})=>{
      await p.goto(url);await p.waitForSelector('#accept-disclaimer');await p.click('#accept-disclaimer');
      await p.waitForSelector('#mode-full');await p.check('#mode-full');await p.click('#mode-continue');await p.click('#handover-assistant');
      await p.waitForFunction(()=>document.querySelector('#gate').hidden&&document.querySelector('#main').dataset.view);
      agent('POST','/api/observations',{date:'2031-03-04',time:'08:00',exercise:'done',meditation:'partial'});
      agent('POST','/api/aux',{labs:[{date:'2031-03-01',metric:'示例指标',unit:'示例单位',lab:'虚构实验室',value:5,low:1,high:9}],question:'',rhythm:{blood:null,chemistry:null},celebration:true,version:0});
      await p.reload();await p.locator('#main[data-view]').waitFor();await nav('today');
      add(tag+'-checkin-mark','助手记的活动、冥想在今天页的两张打卡卡片上都标「助手记录」',await p.locator('.checkin-card .agent-mark').count()===2,await p.locator('.checkin-card').first().innerHTML());
      await nav('labs');
      const hintVisible=await p.locator('.table-hint').isVisible();
      add(tag+'-table-hint','窄屏的检验表格有「可以左右滑动」提示，宽屏没有',hintVisible===(width<500),hintVisible);
      if(width<500)add(tag+'-table-scroll','窄屏表格可横向滚动，不撑破页面',await p.evaluate(()=>{const s=document.querySelector('.table-scroll');return s.scrollWidth>s.clientWidth&&document.documentElement.scrollWidth<=innerWidth;}),'');
    });
  }
  console.log(JSON.stringify({pass:checks.filter(c=>c.结果==='pass').length,total:checks.length}));
})();
