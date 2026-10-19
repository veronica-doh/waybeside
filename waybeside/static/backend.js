'use strict';
// 用户文字只作为 textContent / value / 属性值写入 DOM，从不参与 HTML 解析。
let courseRevision=0, demoMode=false, stateLoaded=false;
let gateInfo={disclaimer:false,onboarded:false};   // 首次启动门：免责确认 → 选使用方式 → 问卷向导 → 主界面
let assistantMode='unset', assistantModeVersion=0, activityData=null, upgradeNotice=false;
// 按使用方式显示的文案：键名 prefix.menu_only / prefix.full（文案都在 i18n 里）。
const tm=prefix=>t(prefix+'.'+(assistantMode==='full'?'full':'menu_only'));
const agentMark=()=>tag(t('mark.agent'),'agent-mark');
const recentAgentMark=flag=>flag?tag(t('mark.recent'),'agent-mark'):'';
let resolveStateReady;const stateReady=new Promise(resolve=>{resolveStateReady=resolve;});
let textSlots=[], csrf='', auxVersion=0, busy=false;
function safeText(value){const index=textSlots.push(String(value))-1;return `__FS_TEXT_${index}__`;}
function hydrateText(root){
  const replace=value=>value.replace(/__FS_TEXT_(\d+)__/g,(_,i)=>textSlots[Number(i)]??'');
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let node;
  while(node=walker.nextNode())node.textContent=replace(node.textContent);
  for(const el of root.querySelectorAll('*'))for(const attr of [...el.attributes])if(attr.value.includes('__FS_TEXT_'))el.setAttribute(attr.name,replace(attr.value));
  textSlots=[];
}
async function api(path,data){
  const response=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
  if(!response.ok){const detail=await response.json().catch(()=>({}));const failure=new Error(response.status===409?(detail.code==='CONFIRM_REQUIRED'?detail.error:'记录已变化或已锁定，请刷新、重新预览或显式解锁后再保存。'):response.status===401?'服务已重启，请从启动时打开的窗口进入。':detail.error||'未保存，请检查输入、预约日期或本机服务。');failure.status=response.status;failure.code=detail.code;throw failure;}
  return response;
}
function readableAudit(event){
  const titles={treatment_session:'治疗记录更新',observation:'日常记录更新',plan:'安排已采用',plan_undo:'安排已撤销',menu_adopt:'整日菜单已采用',menu_undo:'菜单采用已撤销',menu_select:'菜单草稿已选择',menu_cancel_draft:'菜单预览已取消',menu_actual:'用餐实际记录更新',menu_lock:'餐次锁定状态更新',menu_preference:'菜单偏好更新',menu_library:'菜单库已接入',seed:'演示资料已准备',treatment_course:'疗程设置已更新'};
  const states={completed:'已完成',planned:'待确认',postponed:'已延期',cancelled:'已取消',unknown:'未记录',done:'已完成',partial:'完成一部分',not_done:'未完成',other:'其他情况'};
  function brief(value){
    if(!value||typeof value!=='object')return '暂无记录';
    let content=value;if(value.content){try{content=typeof value.content==='string'?JSON.parse(value.content):value.content;}catch(error){return '记录已保存';}}
    if(!content||typeof content!=='object')return '记录已保存';
    const parts=[];
    if(Object.hasOwn(content,'adopted'))parts.push(content.adopted?'已安排'+groupName(content.adopted.group_id)+'':'尚未采用');
    if(content.draft)parts.push('预览'+groupName(content.draft.group_id)+'');
    for(const [key,label] of Object.entries({date:'日期',planned_date:'预约日',actual_date:'实际日'}))if(typeof content[key]==='string')parts.push(label+' '+content[key]);
    for(const [key,label] of Object.entries({status:'状态',diet:'饮食',exercise:'活动',meditation:'冥想'}))if(states[content[key]])parts.push(label+'：'+states[content[key]]);
    if(content.actual&&typeof content.actual==='object')for(const [meal,record] of Object.entries(content.actual))if(states[record?.status])parts.push(meal+'：'+states[record.status]);
    if(content.locks&&typeof content.locks==='object'){const locked=Object.entries(content.locks).filter(([,on])=>on).map(([meal])=>meal);parts.push(locked.length?'已锁定：'+locked.join('、'):'没有锁定餐次');}
    return parts.join('；')||'记录已保存';
  }
  const title=titles[event.entity]||'本机记录已更新';
  return {title,time:event.occurred_at,text:titles[event.entity]?brief(event.old_value)+' → '+brief(event.new_value):'本机记录已更新'};
}
function loadState(state){
  demoMode=state.demo===true; courseRevision=state.course_revision||0;
  gateInfo={disclaimer:state.disclaimer_accepted===true,onboarded:state.onboarded===true};
  assistantMode=['menu_only','full'].includes(state.assistant_mode)?state.assistant_mode:'unset'; assistantModeVersion=state.assistant_mode_version||0; upgradeNotice=state.upgrade_notice===true;
  if(state.today)TODAY=state.today;
  labRecords.length=0;selectedLab="";auxVersion=0;questionNote="";labRhythm={blood:null,chemistry:null};
  course=state.treatment_course[0]||null; settingsVersion=state.settings_version||0; scheduleOutOfSync=state.schedule_out_of_sync===true;
  sessions.splice(0,sessions.length,...state.treatment_session);
  Object.keys(dailyRecords).forEach(k=>delete dailyRecords[k]);
  saved=null;
  for(const row of state.observation){
    if(row.kind==='daily'){
      const r={...row.content,id:row.id,version:row.version,recorded_by:row.recorded_by};
      // 同日多条全部保留在数据库及历史；表单加载最后发生的一条。
      if(!dailyRecords[r.date]||dailyRecords[r.date].time<=r.time)dailyRecords[r.date]=r;
      saved=r;
    }
    if(row.kind==='aux'){
      auxVersion=row.version;questionNote=row.content.question;labRhythm=row.content.rhythm;celebrationEnabled=row.content.celebration;
      labRecords.splice(0,labRecords.length,...row.content.labs);
      if(!labRecords.some(r=>labKey(r)===selectedLab))selectedLab=labRecords.length?labKey(labRecords[0]):"";
    }
  }
  history=state.audit.slice().reverse().map(readableAudit);
  stateLoaded=true;resolveStateReady();
}
async function loadActivity(){try{activityData=await (await api('assistant-activity')).json();}catch(error){activityData=null;}}
async function refreshState(){loadState(await (await api('state')).json());await Promise.all([typeof loadSettings==='function'?loadSettings():null,loadActivity()]);}
async function persistRecord(next){
  if(busy)throw new Error('正在保存，请稍等。');busy=true;
  try{const payload=Object.fromEntries(Object.entries(next).filter(([k])=>!k.startsWith('sym_')&&k!=='recorded_by'));const result=await (await api('observations',payload)).json();await refreshState();record={...result};changeState();}
  finally{busy=false;}
}
async function persistSession(s,action,date,confirmOff=false){
  try{await api('sessions/'+s.id,{action,date,version:s.version,...(confirmOff?{confirm_off_schedule:true}:{})});}
  catch(error){
    // 这天不在设定的治疗日：温和确认一次，确认后带确认参数重发；不是拒绝。
    if(error.code==='CONFIRM_REQUIRED'&&!confirmOff){confirmAction('再确认一下这一天','这天不是你设定的治疗日，仍要记下吗？',()=>persistSession(s,action,date,true));return;}
    throw error;
  }
  await refreshState();changeState();render();
}
async function persistAux(){
  try{const result=await (await api('aux',{labs:labRecords.map(({recorded_by,...lab})=>lab),question:questionNote,rhythm:labRhythm,celebration:celebrationEnabled,version:auxVersion})).json();auxVersion=result.version;}
  catch(error){await refreshState();render();throw error;}
}
function decorateLocal(current){
  if(current==='help')for(const card of main.querySelectorAll('.grid .card')){
    const source=document.createElement('p');source.className='small';source.textContent='提示来源：本项目内置的通用说明；核对状态：未核对（未经专业人员审阅），不含医学阈值。';card.append(source);
  }
  if(current==='course'||current==='history'){
    const actions=document.createElement('div');actions.className='actions';
    const link=document.createElement('a');link.href='/api/summary';link.className='button secondary';link.textContent='导出记录（JSON，含健康记录）';actions.append(link);const warn=document.createElement('p');warn.className='small';warn.textContent='这个文件含你的健康记录，请勿随意转发。';actions.append(warn);
    const button=document.createElement('button');button.className='secondary';button.textContent='备份本机记录';button.onclick=async()=>{try{const response=await api('backup',{});const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download='waybeside-backup.sqlite';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){toast(e.message);}};actions.append(button);main.append(actions);
  }
}
window.addEventListener('unhandledrejection',event=>{event.preventDefault();toast(event.reason?.message||'未保存，请重试。');});
document.querySelector('#reset').onclick=()=>confirmAction('退出本机服务？','记录已保存在本机。关闭后需要重新启动才能继续使用。',async()=>{await api('exit',{});main.replaceChildren();const message=document.createElement('p');message.textContent='本机服务已退出，记录保留。可以关闭窗口。';main.append(message);});
(async()=>{
  try{
    const match=location.hash.match(/^#login=(.+)$/);
    if(match){const token=decodeURIComponent(match[1]);window.history.replaceState(null,'',location.pathname);csrf=(await (await api('login',{token})).json()).csrf;}
    else csrf=(await (await api('session')).json()).csrf;
    await refreshState();record=recordForDate(TODAY);render();
  }catch(error){main.textContent=error.message+' 请重新运行启动命令打开窗口。';}
})();
