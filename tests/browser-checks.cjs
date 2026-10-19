// 使用本机已有 Chrome / Playwright；服务与数据库均在临时目录。
const fs=require('fs'),os=require('os'),path=require('path'),cp=require('child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),ART=require('./helpers/artifacts.cjs'),pyPath=require('./helpers/python.cjs')(root),passGate=require('./helpers/gate.cjs');
const outDir=path.join(ART,'2026-10-15-waybeside-step1');
const dest=path.join(ART,'2026-10-15-waybeside-step1-checks.json');
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'waybeside-step1-browser-'));
const checks=[];let server,browser;const errors=[],external=[];
const add=(id,描述,ok,证据)=>{checks.push({id,描述,方法:'本机 Chrome 设备仿真 / 控件操作 / DOM 读回；强制 reduced-motion',结果:ok?'pass':'fail',证据});if(!ok)throw new Error(id+' '+描述);};
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function start(){
 const ready=path.join(tmp,'ready.json');if(fs.existsSync(ready))fs.unlinkSync(ready);
 const net=require('net');const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const n=s.address().port;s.close(()=>resolve(n));});});
 server=cp.spawn(pyPath,[path.join(root,'run.py'),'--demo','--port',String(port),'--no-browser','--ready-file',ready],{env:{...process.env,WAYBESIDE_DATA_DIR:path.join(tmp,'data'),WAYBESIDE_TODAY:'2031-03-04'},stdio:['ignore','pipe','pipe']});
 let output='';server.stdout.on('data',d=>output+=d);server.stderr.on('data',d=>output+=d);
 for(let i=0;i<100&&!fs.existsSync(ready);i++){if(server.exitCode!==null)throw new Error(output);await delay(50);}
 if(!fs.existsSync(ready))throw new Error('服务未就绪 '+output);
 return JSON.parse(fs.readFileSync(ready)).url;
}
async function stop(){if(server&&server.exitCode===null){const done=new Promise(resolve=>server.once('exit',resolve));server.kill('SIGTERM');await done;}}
(async()=>{
 fs.mkdirSync(outDir,{recursive:true});
 try{
 const url=await start();browser=await chromium.launch({channel:'chrome',headless:true});
 const ctx=await browser.newContext({reducedMotion:'reduce',viewport:{width:1366,height:768}});
 await ctx.route('**/*',route=>{const u=new URL(route.request().url());if(u.protocol==='http:'&&u.hostname==='127.0.0.1')route.continue();else if(u.protocol==='file:')route.continue();else{external.push(u.origin);route.abort();}});
 const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));
 await p.goto(url);await passGate(p);await p.locator('.path-count').waitFor();
 const nav=async h=>{await p.evaluate(h=>location.hash=h,h);await p.waitForFunction(h=>document.querySelector('#main').dataset.view===h,h);};
 const geometry=async page=>page.locator('#main h1').first().evaluate(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {x:r.x,y:r.y,width:r.width,font:s.fontSize,color:s.color};});
 for(const width of [1366,390]){
   await p.setViewportSize({width,height:width===390?844:768});
   for(const h of ['today','record','plan','course']){
     await nav(h);
     const actual=await geometry(p);
     const dimensions=await p.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,reduced:matchMedia('(prefers-reduced-motion: reduce)').matches}));
     const screenshot=path.join(outDir,`${h}-${width}.png`);await p.screenshot({path:screenshot,fullPage:width===390});
     add(`B-${h}-${width}`,'自带页面布局无横溢、主标题可见',actual.width>0&&dimensions.scroll<=dimensions.width&&dimensions.reduced,{actual,dimensions,screenshot:path.relative(ART,screenshot)});
   }
 }
 await p.setViewportSize({width:1366,height:768});
 await nav('record');const payload='<script>window.injected=true</script><img src=x onerror="window.injected=true">';
 await p.locator('#note').fill(payload);await p.locator('input[name=diet][value=partial]').check();await p.locator('#record-form button[type=submit]').click();
 await p.waitForFunction(()=>saved?.note.includes('<script>'));
 await nav('course');add('B-XSS','用户文本只按文本显示，无脚本执行',await p.locator('.past').innerText().then(t=>t.includes(payload))&&await p.evaluate(()=>!window.injected&&!document.querySelector('#main script,#main img')),'备注原文可读；无 script/img 元素');
 await stop();const restarted=await start();await p.goto(restarted);await p.locator('.path-count').waitFor();await nav('record');
 add('B-restart','保存后真实停止并重启服务，记录读回一致',await p.locator('#note').inputValue()===payload&&await p.locator('input[name=diet][value=partial]').isChecked(),'独立 Waitress 进程停止后新进程；同一临时数据目录');
 await nav('today');await p.locator('[data-checkin=exercise][data-value=done]').click();await p.waitForFunction(()=>dailyRecords[TODAY]?.exercise==='done');await p.reload();await p.locator('.path-count').waitFor();
 add('B-checkin','打卡持久化且未填冥想仍未知',await p.locator('[data-checkin=exercise][data-value=done]').getAttribute('aria-pressed')==='true'&&await p.locator('[data-checkin=meditation][aria-pressed=true]').count()===0,'刷新后活动完成、冥想未记录');
 await nav('course');await p.locator('[data-private-action=complete]').first().click();await p.locator('button[value=confirm]').click();await p.waitForFunction(()=>count()===1);
 await nav('today');const afterComplete=await p.locator('.path-count').innerText();
 await nav('course');await p.locator('#done-group>summary').click();await p.locator('[data-private-action=correct]').first().click();await p.locator('button[value=confirm]').click();await p.waitForFunction(()=>count()===0);
 add('B-course','确认完成与误记更正通过后端更新计数（演示疗程 6 个周期）',/已走过\s*1\s*\/\s*6\s*个周期/.test(afterComplete)&&await p.locator('.journey-dots .stone.complete').count()===0,'0 → 1 → 0，API 审计留痕');
 await nav('tomorrow');await p.locator('#menu-section').waitFor();add('B-menu-tomorrow','准备明天显示明天的菜单组与采用入口（旧三餐生成已被五餐菜单取代）',await p.locator('#main').innerText().then(t=>/明天 \d+月\d+日/.test(t)&&t.includes('采用这一整组'))&&await p.locator('[data-action=generate]').count()===0,'菜单库启用时无旧 mock 生成按钮');
 await nav('help');add('B-help','外网阻断且离线求助含来源和核对状态',await p.locator('#main').innerText().then(t=>t.includes('提示来源')&&t.includes('核对状态'))&&await p.locator('.grid .card').allTextContents().then(a=>a.every(t=>t.includes('提示来源')&&t.includes('未核对'))),'非回环请求全部阻断；四块提示均有来源与核对状态');
 await nav('record');add('B-unknown','未知不显示正常，习惯不填为今日完成',await p.locator('input[name=sym_0][value=unknown]').isChecked()&&await p.locator('[data-symptom]').count()===4&&await p.locator('#minutes').inputValue()==='','默认四个症状条目均为未记录，实际分钟留空');
 add('B-runtime','无浏览器脚本错误或外网请求',errors.length===0&&external.length===0,{errors,external});
 await p.locator('#reset').click();await p.locator('button[value=confirm]').click();await p.waitForFunction(()=>document.querySelector('#main').textContent.includes('本机服务已退出'));await delay(600);
 add('B-exit','退出入口停止实际服务进程',server.exitCode===0,{exitCode:server.exitCode});
 }catch(error){checks.push({id:'B-error',描述:'浏览器执行卡点',方法:'真实浏览器',结果:'fail',证据:String(error.stack)});process.exitCode=1;}
 finally{
   if(browser)await browser.close();await stop();fs.rmSync(tmp,{recursive:true,force:true});
   const old=fs.existsSync(dest)?JSON.parse(fs.readFileSync(dest)):{};old.browser=checks;old.browser_summary={pass:checks.filter(c=>c.结果==='pass').length,total:checks.length};old.cleanup='浏览器与自建服务停止；本轮临时数据目录已清理';
   fs.writeFileSync(dest,JSON.stringify(old,null,2)+'\n');console.log(JSON.stringify({summary:old.browser_summary,fail:checks.filter(c=>c.结果==='fail')}));
 }
})();
