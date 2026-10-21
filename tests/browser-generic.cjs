// 检验 / 症状 / 演示数据通用化的浏览器验收：--demo 虚构数据，回环服务，临时目录。
const fs=require('fs'),os=require('os'),path=require('path'),cp=require('child_process'),net=require('net');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),ART=require('./helpers/artifacts.cjs'),py=require('./helpers/python.cjs')(root),passGate=require('./helpers/gate.cjs');
const outDir=path.join(ART,'waybeside-generic'),dest=path.join(ART,'waybeside-generic-checks.json');
const checks=[];const add=(id,描述,ok,证据)=>{checks.push({id,描述,结果:ok?'pass':'fail',证据});if(!ok)console.log('FAIL',id,证据);};
const delay=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
  fs.mkdirSync(outDir,{recursive:true});
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'waybeside-generic-'));let server,browser;const errors=[];
  const env={...process.env,WAYBESIDE_DATA_DIR:path.join(tmp,'data'),WAYBESIDE_TODAY:'2031-03-10'};
  try{
    const port=await new Promise(res=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const n=s.address().port;s.close(()=>res(n));});});
    const ready=path.join(tmp,'ready.json');
    server=cp.spawn(py,[path.join(root,'run.py'),'--demo','--port',String(port),'--no-browser','--ready-file',ready],{env,stdio:'ignore'});
    for(let i=0;i<100&&!fs.existsSync(ready);i++)await delay(50);
    const url=JSON.parse(fs.readFileSync(ready)).url;
    browser=await chromium.launch({headless:true});
    const ctx=await browser.newContext({reducedMotion:'reduce',viewport:{width:1366,height:900}});
    await ctx.route('**/*',r=>{new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort();});
    const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));
    await p.goto(url);await passGate(p);await delay(400);
    const nav=async h=>{await p.evaluate(h=>{if(location.hash==='#'+h)render();else location.hash=h;},h);await p.waitForFunction(h=>document.querySelector('#main').dataset.view===h,h);await delay(500);};
    const shot=async n=>{await p.locator('#notice').evaluate(e=>e.classList.remove('show'));await p.screenshot({path:path.join(outDir,n+'.png'),fullPage:true});};
    await nav('labs');
    const labs=await p.locator('#main').innerText();
    add('labs-demo','演示检验：示例指标 A/B、示例单位、虚构实验室、区间 10—20',labs.includes('示例指标 A')&&labs.includes('示例单位')&&labs.includes('虚构实验室')&&labs.includes('10—20'),labs.slice(0,200));
    add('labs-no-rhythm','检查节奏默认留空：输入框为空，文案“尚未设置时不推算检查日期”，不出现推算日期',await p.locator('#rhythm-blood').inputValue()===''&&labs.includes('尚未设置时不推算检查日期'),'');
    await shot('labs');
    await p.locator('[data-open-lab]').click();
    add('labs-free-metric','指标名是自由文字输入框，不是固定下拉',await p.locator('#lab-form input[name=metric]').count()===1&&await p.locator('#lab-form select[name=metric]').count()===0,'');
    await p.locator('#lab-form input[name=metric]').fill('示例指标 C');await p.locator('#lab-form input[name=unit]').fill('示例单位');await p.locator('#lab-form input[name=lab]').fill('虚构实验室');await p.locator('#lab-form input[name=value]').fill('14');
    await p.locator('#lab-form button[type=submit]').click();await delay(800);
    add('labs-save','自填指标名可保存并出现在系列里',await p.locator('#lab-series').innerText().then(t=>t.includes('示例指标 C')),'');
    await nav('record');
    const names=await p.locator('[data-symptom] legend').allInnerTexts();
    add('symptom-defaults','默认四个通用症状条目：食欲/恶心/疲劳/疼痛',JSON.stringify(names)===JSON.stringify(['食欲','恶心','疲劳','疼痛']),names);
    await p.locator('#symptom-add').fill('口腔');await p.locator('[data-action=add-symptom]').click();await delay(300);
    add('symptom-add','可以自加一项',await p.locator('[data-symptom] legend').allInnerTexts().then(a=>a.includes('口腔')),'');
    await p.locator('input[name=sym_0][value=some]').check();await p.locator('input[name=sym_4][value=none]').check();
    await shot('record');
    await p.locator('#record-form button[type=submit]').click();await delay(800);
    await nav('record');
    add('symptom-persist','保存后重新进入，自加项仍在且状态保留',await p.evaluate(()=>{const r=todayRecord();return r.symptoms&&r.symptoms['食欲']==='some'&&r.symptoms['口腔']==='none';})&&await p.locator('[data-symptom] legend').allInnerTexts().then(a=>a.includes('口腔')),'');
    for(const w of [390,1366]){await p.setViewportSize({width:w,height:900});await nav('record');add('fit-'+w,'宽度 '+w+' 不横向溢出',await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'');}
    add('runtime','无脚本错误',errors.length===0,errors);
  }catch(e){add('error','执行异常',false,String(e.stack));process.exitCode=1;}
  finally{if(browser)await browser.close();if(server&&server.exitCode===null){const d=new Promise(r=>server.once('exit',r));server.kill('SIGTERM');await d;}fs.rmSync(tmp,{recursive:true,force:true});}
  const summary={pass:checks.filter(c=>c.结果==='pass').length,total:checks.length};
  fs.writeFileSync(dest,JSON.stringify({summary,checks,cleanup:'浏览器与服务已退出，临时数据目录已删除'},null,2)+'\n');console.log(JSON.stringify(summary));if(summary.pass!==summary.total)process.exitCode=1;
})();
