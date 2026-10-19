// 产品原则：个人资料 / 问卷答案留在后台，前端不做“只读档案”视图。
// 一、源码：静态文件与路由里没有只读档案视图的痕迹，问卷答案只有一个读取口（GET /api/settings）。
// 二、DOM：真实浏览器里逐页检查——哨兵答案只出现在可编辑控件里（输入框、选项、带“移除”的条目），
//     唯一的文字展示是今天页问候语里的昵称。全部虚构，临时目录与回环服务。
const fs=require('fs'),os=require('os'),path=require('path'),cp=require('child_process'),net=require('net'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),py=require('./helpers/python.cjs')(root),passGate=require('./helpers/gate.cjs');
const staticDir=path.join(root,'waybeside/static');
const delay=ms=>new Promise(r=>setTimeout(r,ms));

// ---- 一、源码 ----
const sources=fs.readdirSync(staticDir).filter(f=>/\.(js|html)$/.test(f)).map(f=>[f,fs.readFileSync(path.join(staticDir,f),'utf8')]);
for(const [file,text] of sources){
  for(const bad of [/profile-preview/,/profileView|profile-view|profile_view/,/档案总览|我的档案|问卷档案/,/\/api\/profile/,/api\('(questionnaire|answers)/])
    assert(!bad.test(text),`${file} 含只读档案视图痕迹：${bad}`);
}
const pySources=fs.readdirSync(path.join(root,'waybeside')).filter(f=>f.endsWith('.py')).map(f=>fs.readFileSync(path.join(root,'waybeside',f),'utf8')).join('\n');
const getRoutes=[...pySources.matchAll(/@app\.get\('([^']+)'/g)].map(m=>m[1]);
assert(!getRoutes.some(r=>/profile|questionnaire|answer|archive/.test(r)),'存在可读取问卷/档案的 GET 路由：'+getRoutes);
assert(getRoutes.includes('/api/settings'),'GET /api/settings 应是唯一的可编辑读取口');
const onboarding=sources.find(([f])=>f==='onboarding.js')[1];
assert.equal((onboarding.match(/innerHTML/g)||[]).length,1,'onboarding.js 只允许用 innerHTML 渲染疗程表单标记');
console.log('源码：无只读档案视图、问卷答案只有 GET /api/settings 一个读取口：通过');

// ---- 二、DOM ----
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const SENT={nick:'SENTINEL_NICK_7f3a',allergy:'SENT_ALLERGY_7f3a',note:'SENTINEL_NOTE_7f3a',tag:'SENT_TAGLABEL_7f3a'};
(async()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'waybeside-noprofile-'));let server,browser;
  try{
    const port=await new Promise(res=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const n=s.address().port;s.close(()=>res(n));});});
    const ready=path.join(tmp,'ready.json');
    server=cp.spawn(py,[path.join(root,'run.py'),'--port',String(port),'--no-browser','--ready-file',ready],{cwd:root,env:{...process.env,WAYBESIDE_DATA_DIR:path.join(tmp,'data'),WAYBESIDE_TODAY:'2031-03-04'},stdio:'ignore'});
    for(let i=0;i<100&&!fs.existsSync(ready);i++)await delay(50);
    const url=JSON.parse(fs.readFileSync(ready)).url;
    browser=await chromium.launch({channel:'chrome',headless:true});
    const ctx=await browser.newContext({reducedMotion:'reduce',viewport:{width:1366,height:900}});
    await ctx.route('**/*',r=>{new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort();});
    const p=await ctx.newPage();const errors=[];p.on('pageerror',e=>errors.push(e.message));
    await p.goto(url);await passGate(p);
    await p.evaluate(async S=>{
      const put=async(b,answers)=>{await api('onboarding/'+b,{answers,version:settingsData.versions[b],skipped:false});await refreshState();};
      await put('basic',{nickname:S.nick,helper_mode:'self'});
      await put('diet',{custom_tags:[{id:'my_tag_1',label:S.tag}],requirement_tags:['my_tag_1'],allergy_reminders:[S.allergy],free_note:S.note});
      await put('habit',{sleep_usual:'regular',activity_usual:'light',meditation_usual:{time:'03:17',minutes:173}});
      render();
    },SENT);
    const pages=['today','record','tomorrow','plan','labs','course','history','help','settings','export'];
    const result={};
    for(const pg of pages){
      await p.evaluate(h=>{if(location.hash==='#'+h)render();else location.hash=h;},pg);
      await p.waitForFunction(h=>document.querySelector('#main').dataset.view===h,pg);await delay(150);
      result[pg]=await p.evaluate(S=>{
        const body=document.body;
        const hits=value=>[...body.querySelectorAll('*')].filter(el=>[...el.childNodes].some(n=>n.nodeType===3&&n.textContent.includes(value)));
        const editable=el=>!!(el.closest('.editable-row,.tag-choices,textarea,label')&&!!el.closest('form'))||!!el.closest('button');
        const values=[...body.querySelectorAll('input,textarea,select')].map(e=>e.value).join('|');
        return {
          archiveWords:/档案|我的资料|profile/i.test(body.innerText),
          // 非昵称的答案：任何文字节点都必须在可编辑控件里
          textOutsideEditors:[S.allergy,S.note,S.tag,'03:17'].flatMap(v=>hits(v).filter(el=>!editable(el)).map(el=>el.tagName+':'+v)),
          nickOutsideGreeting:hits(S.nick).filter(el=>!(el.closest('.eyebrow')&&el.closest('.path-right'))).map(el=>el.tagName),
          inValues:[S.nick,S.allergy,S.note].filter(v=>values.includes(v)),
          readonlyDump:[...body.querySelectorAll('dl,table')].some(el=>el.innerText.includes(S.allergy)||el.innerText.includes(S.note)),
        };
      },SENT);
    }
    for(const pg of pages){
      const r=result[pg];
      assert(!r.archiveWords,`${pg}：出现档案字样`);
      assert.deepEqual(r.textOutsideEditors,[],`${pg}：答案以只读文字出现`);
      assert(!r.readonlyDump,`${pg}：答案出现在只读列表/表格`);
      if(pg!=='today')assert.deepEqual(r.nickOutsideGreeting,[],`${pg}：昵称以只读文字出现`);
      if(pg!=='settings')assert.deepEqual(r.inValues,[],`${pg}：非设置页的控件里带着答案`);
    }
    assert.deepEqual(result.settings.inValues.sort(),[SENT.nick,SENT.note].sort(),'设置页应以控件值回显昵称与备注（提醒条目在可移除的行里）');
    assert.deepEqual(errors,[]);
    console.log('DOM：十个页面均无只读档案视图，答案只在设置页的可编辑控件里：通过');
  }finally{
    if(browser)await browser.close();
    if(server&&server.exitCode===null){const d=new Promise(r=>server.once('exit',r));server.kill('SIGTERM');await d;}
    fs.rmSync(tmp,{recursive:true,force:true});
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
