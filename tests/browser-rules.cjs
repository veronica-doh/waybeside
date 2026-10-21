// 规则筛选的浏览器验收：只用虚构菜单与虚构规则；脚本字符串必须只显示为文字。
const fs=require('fs'),os=require('os'),path=require('path'),cp=require('child_process'),net=require('net');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),ART=require('./helpers/artifacts.cjs'),py=require('./helpers/python.cjs')(root),passGate=require('./helpers/gate.cjs');
const outDir=path.join(ART,'waybeside-rules'),dest=path.join(ART,'waybeside-rules-checks.json');
const checks=[];const add=(id,描述,ok,证据)=>{checks.push({id,描述,结果:ok?'pass':'fail',证据});if(!ok)console.log('FAIL',id,证据);};
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const library=JSON.parse(cp.spawnSync(py,['-c',"import sys,json;sys.path.insert(0,'tests');from test_rules import library;print(json.dumps(library(),ensure_ascii=False))"],{cwd:root,encoding:'utf8'}).stdout);
const SCRIPT='<script>window.__xss=1</script>演示隐藏原因';
async function run(mode){
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'waybeside-rules-'));let server,browser;const errors=[];
  const data=path.join(tmp,'data');fs.mkdirSync(path.join(data,'rules'),{recursive:true});
  if(mode==='rules'){const r=JSON.parse(fs.readFileSync(path.join(root,'examples/rules/demo-fictional-rules.json'),'utf8'));r.rules[0].message=SCRIPT;fs.writeFileSync(path.join(data,'rules/demo.json'),JSON.stringify(r));}
  const env={...process.env,WAYBESIDE_DATA_DIR:data,WAYBESIDE_TODAY:'2031-12-31'};
  try{
    const port=await new Promise(res=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const n=s.address().port;s.close(()=>res(n));});});
    const ready=path.join(tmp,'ready.json');
    server=cp.spawn(py,[path.join(root,'run.py'),'--port',String(port),'--no-browser','--ready-file',ready],{env,stdio:'ignore'});
    for(let i=0;i<100&&!fs.existsSync(ready);i++)await delay(50);
    const url=JSON.parse(fs.readFileSync(ready)).url;
    browser=await chromium.launch({headless:true});
    const ctx=await browser.newContext({reducedMotion:'reduce',viewport:{width:1366,height:900}});
    await ctx.route('**/*',r=>{new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort();});
    const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));
    await p.goto(url);await passGate(p);await delay(300);
    await p.evaluate(async library=>{
      const pv=await (await api('menu/import-preview',library)).json();await api('menu/import-confirm',{menu:library,digest:pv.digest,expected:pv.expected,confirmed:true});
      await api('onboarding/diet',{answers:{requirement_tags:['soft_texture'],allergy_reminders:['花生','虾']},version:settingsData.versions.diet});
      await refreshState();await loadSettings();await fetchMenu();render();
    },library);
    const nav=async h=>{await p.evaluate(h=>{if(location.hash==='#'+h)render();else location.hash=h;},h);await p.waitForFunction(h=>document.querySelector('#main').dataset.view===h,h);await delay(600);};
    const shot=async n=>{await p.locator('#notice').evaluate(e=>e.classList.remove('show'));await p.screenshot({path:path.join(outDir,mode+'-'+n+'.png'),fullPage:true});};
    await nav('tomorrow');
    const text=await p.locator('#menu-section').innerText();
    add(mode+'-allergy','准备明天显示过敏提醒原话（只作提示）',text.includes('你记录了：花生、虾')&&text.includes('不能作为过敏原规避工具')&&text.includes('未标注的食材不等于不含该成分'),'');
    const rec=await p.evaluate(()=>menuData.state.recommendation.group_id);
    if(mode==='rules'){
      add(mode+'-banner','虚构规则常驻横幅',text.includes('演示规则，虚构，非医学建议'),'');
      add(mode+'-recommend','推荐不落在被隐藏的组',rec==='01',rec);
      add(mode+'-hidden-summary','“已隐藏的组”折叠区显示数量',await p.locator('#menu-hidden summary').innerText().then(t=>t.includes('已隐藏的组 · 2')),'');
      await p.locator('#menu-hidden summary').click();
      const hiddenText=await p.locator('#menu-hidden').innerText();
      add(mode+'-script-as-text','脚本字符串只显示为文字，不执行、不生成脚本节点',hiddenText.includes('<script>window.__xss=1</script>')&&await p.evaluate(()=>window.__xss===undefined&&document.querySelectorAll('#main script').length===0),'');
      await shot('tomorrow-hidden');
      const seen=new Set();for(let i=0;i<6;i++){await p.locator('#menu-change').click();await delay(700);seen.add(await p.evaluate(()=>menuData.state.draft?.group_id));}
      add(mode+'-change-skips-hidden','“换一组”轮换不会进入被隐藏的组',![...seen].some(g=>['02','04'].includes(g)),[...seen]);
      await p.locator('#menu-cancel').click().catch(()=>{});await delay(500);
      await p.evaluate(()=>{document.getElementById('menu-hidden').open=true;});
      await p.locator('#menu-hidden-show-02').click();await p.locator('button[value=confirm]').click();await delay(900);
      add(mode+'-still-want','“仍想看”确认后预览被隐藏的组（尚未采用）',await p.evaluate(()=>menuData.state.draft?.group_id==='02'&&menuData.state.adopted===null),'');
      await p.locator('#menu-cancel').click().catch(()=>{});await delay(500);
      await p.locator('#menu-browse-summary').click();await p.locator('#menu-select-03').click();await delay(900);
      add(mode+'-unlabeled','未标注提示：这一组有 1 种食材未标注',await p.locator('.menu-group-view').innerText().then(t=>t.includes('这一组有 1 种食材未标注，规则可能没覆盖')),'');
      await nav('plan');await p.locator('#menu-rule-files summary').click();
      add(mode+'-files','规则文件状态：已加载 1 条规则',await p.locator('#menu-rule-files').innerText().then(t=>t.includes('demo.json · 已加载 · 2 条规则（虚构演示）')),'');
      add(mode+'-footer','我的方案页脚免责常驻',await p.locator('#page-disclaimer').isVisible(),'');
      await shot('plan');
    }else{
      add(mode+'-no-file-notice','有标签但没有规则文件：如实提示未筛选',text.includes('你勾选了 软烂质地，但还没有规则文件，菜单没有按这些要求筛选'),text.slice(0,200));
      add(mode+'-no-hidden','没有规则文件时没有“已隐藏的组”',await p.locator('#menu-hidden').count()===0,'');
      await shot('tomorrow-no-rules');
    }
    for(const [w,h] of [[390,844],[1366,900]]){await p.setViewportSize({width:w,height:h});await nav('tomorrow');add(mode+'-fit-'+w,'宽度 '+w+' 不横向溢出',await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'');}
    add(mode+'-runtime','无脚本错误',errors.length===0,errors);
  }catch(e){add(mode+'-error','执行异常',false,String(e.stack));process.exitCode=1;}
  finally{if(browser)await browser.close();if(server&&server.exitCode===null){const d=new Promise(r=>server.once('exit',r));server.kill('SIGTERM');await d;}
    fs.rmSync(tmp,{recursive:true,force:true});}
}
(async()=>{fs.mkdirSync(outDir,{recursive:true});await run('rules');await run('norules');
  const summary={pass:checks.filter(c=>c.结果==='pass').length,total:checks.length};
  fs.writeFileSync(dest,JSON.stringify({summary,checks,cleanup:'浏览器与服务已退出，临时数据目录已删除'},null,2)+'\n');console.log(JSON.stringify(summary));if(summary.pass!==summary.total)process.exitCode=1;})();
