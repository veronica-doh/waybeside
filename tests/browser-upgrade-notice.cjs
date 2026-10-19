// 老库升级后的一次性提示（全部虚构）：被设为「我自己记」的老库，下次打开网页提示一句，看过就不再提示；新库不提示。
const fs=require('fs'),os=require('os'),path=require('path'),cp=require('child_process'),net=require('net');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),py=require('./helpers/python.cjs')(root),passGate=require('./helpers/gate.cjs');
const checks=[];const add=(id,描述,ok,证据)=>{checks.push({id,描述,结果:ok?'pass':'fail',证据});if(!ok){console.log('FAIL',id,JSON.stringify(证据));process.exitCode=1;}};
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const NOTICE='助手的使用方式现在是「我自己记，助手只帮挑菜单」，可以在设置里改。';
async function scenario(name,width,old,body){
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'waybeside-upg-'));let server,browser;const errors=[];
  const data=path.join(tmp,'data');fs.mkdirSync(data,{recursive:true});
  if(old)cp.execFileSync(py,['-c',"import sys;sys.path.insert(0,'tests');sys.path.insert(0,'.');from pathlib import Path;from test_operator_migration import build_schema6;build_schema6(Path(sys.argv[1])/'waybeside.sqlite',True)",data],{cwd:root});
  const env={...process.env,WAYBESIDE_DATA_DIR:data,WAYBESIDE_TODAY:'2031-03-04'};
  const start=async()=>{const port=await new Promise(res=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const x=s.address().port;s.close(()=>res(x));});});const ready=path.join(tmp,'ready-'+Date.now()+'.json');server=cp.spawn(py,[path.join(root,'run.py'),'--port',String(port),'--no-browser','--ready-file',ready],{cwd:root,env,stdio:'ignore'});for(let i=0;i<100&&!fs.existsSync(ready);i++)await delay(50);return JSON.parse(fs.readFileSync(ready)).url;};
  const stop=async()=>{if(server&&server.exitCode===null){const d=new Promise(r=>server.once('exit',r));server.kill('SIGTERM');await d;}};
  try{
    browser=await chromium.launch({channel:'chrome',headless:true});
    const ctx=await browser.newContext({reducedMotion:'reduce',viewport:{width,height:width<500?812:900}});
    await ctx.route('**/*',r=>{new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort();});
    const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));
    await body({p,start,stop,tag:`${name}-${width}`});
    add(`${name}-${width}-runtime`,'无脚本错误',errors.length===0,errors);
  }catch(e){add(`${name}-${width}-error`,'执行异常',false,String(e.stack));}
  finally{if(browser)await browser.close();await stop();fs.rmSync(tmp,{recursive:true,force:true});}
}
const visible=p=>p.locator('#upgrade-notice').isVisible();
(async()=>{
  for(const width of [1366,375]){
    await scenario('old-library',width,true,async({p,start,stop,tag})=>{
      await p.goto(await start());await passGate(p);await p.locator('#main[data-view]').waitFor();
      add(tag+'-shown','老库升级后首次打开：提示「可以在设置里改」',await visible(p)&&(await p.locator('#upgrade-notice').innerText()).includes(NOTICE),await p.locator('#upgrade-notice').innerText());
      add(tag+'-fits','提示没有横向溢出',await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'');
      await p.reload();await p.locator('#main[data-view]').waitFor();
      add(tag+'-still-until-seen','没点过之前刷新仍在',await visible(p),'');
      await p.click('#upgrade-dismiss');await p.locator('#upgrade-notice').waitFor({state:'hidden'});
      add(tag+'-dismissed','点「知道了」后消失',!(await visible(p)),'');
      await p.reload();await p.locator('#main[data-view]').waitFor();await delay(300);
      add(tag+'-once','刷新后不再提示',!(await visible(p)),'');
      await stop();await p.goto(await start());await p.locator('#main[data-view]').waitFor();await delay(300);
      add(tag+'-once-after-restart','重启服务后也不再提示',!(await visible(p)),'');
    });
    await scenario('old-library-settings',width,true,async({p,start,tag})=>{
      await p.goto(await start());await passGate(p);await p.locator('#main[data-view]').waitFor();
      await p.click('#upgrade-open');await p.waitForFunction(()=>document.querySelector('#main').dataset.view==='settings');
      add(tag+'-open-settings','点「去设置看看」进入设置页，提示消失',!(await visible(p))&&await p.locator('#assistant-section').isVisible(),'');
      await p.reload();await p.locator('#main[data-view]').waitFor();
      add(tag+'-no-repeat','之后不再提示',!(await visible(p)),'');
    });
    await scenario('old-library-change',width,true,async({p,start,tag})=>{
      await p.goto(await start());await passGate(p);await p.locator('#main[data-view]').waitFor();
      await p.evaluate(()=>{location.hash='#settings';});await p.locator('#assistant-change').waitFor();
      await p.click('#assistant-change');await p.check('#mode-full');await p.click('#mode-continue');await p.waitForFunction(()=>document.querySelector('#gate').hidden);
      add(tag+'-changed-hides','在设置页改过方式：提示消失（且换成第一种后不会出现）',!(await visible(p)),'');
      await p.reload();await p.locator('#main[data-view]').waitFor();
      add(tag+'-changed-stays-hidden','刷新后不提示',!(await visible(p)),'');
    });
    await scenario('fresh-library',width,false,async({p,start,tag})=>{
      await p.goto(await start());await passGate(p);await p.locator('#main[data-view]').waitFor();await delay(300);
      add(tag+'-none','新库不提示',!(await visible(p)),'');
    });
  }
  console.log(JSON.stringify({pass:checks.filter(c=>c.结果==='pass').length,total:checks.length}));
})();
