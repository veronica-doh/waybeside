'use strict';
// 所有导入文字均经 textContent；整日菜单与实际记录保持分离。
const MENU_PAGE_SIZE=6;
let menuData=null, menuDate=null, menuBusy=false;
let menuBrowseLimit=MENU_PAGE_SIZE, menuFavoritesOnly=false;
const menuShoppingDates=new Set();
const oldDecorate=decorateLocal;
decorateLocal=function(current){if((current!=='tomorrow'||!menuData?.enabled)&&!['plan','baseline'].includes(current))oldDecorate(current);document.querySelectorAll('.nav a').forEach(link=>link.hidden=false);if(menuData?.enabled&&['today','tomorrow','plan','baseline'].includes(current))renderMenu(current==='baseline'?'plan':current);if(menuData&&!menuData.enabled&&['tomorrow','plan','baseline'].includes(current))renderNoMenuLibrary(current);};
function renderNoMenuLibrary(current){const box=element('section',undefined,'menu-section');box.id='menu-empty';box.append(element('h1',current==='tomorrow'?'准备明天':'我的方案'),element('p','尚未导入菜单库。导入一份菜单文件后，这里才会出现每天的推荐；没有导入前不会生成任何菜单。','menu-state'),importPanel());main.replaceChildren(box);}
let menuReplaced=false, menuNotice='', menuCandidate=null, candidateSignature='', menuSettings=null;
// ---- 规则筛选：规则文字、提醒文字一律走 textContent。过敏提醒只作提示，不参与任何筛选。----
async function loadMenuSettings(){try{menuSettings=await(await api('settings')).json();}catch(error){menuSettings=null;}}
function ruleLabel(id){return menuSettings?.builtin_tags?.[id]||menuSettings?.diet?.custom_tags?.find(t=>t.id===id)?.label||id;}
function hiddenIds(){return new Set((menuData?.rules?.hidden||[]).map(h=>h.group_id));}
function allergyText(){const list=menuSettings?.diet?.allergy_reminders;return Array.isArray(list)&&list.length?`你记录了：${list.join('、')}。本应用不能作为过敏原规避工具；未标注的食材不等于不含该成分。`:'';}
function rulesTop(){
  const r=menuData.rules,box=element('div');box.id='menu-rules-top';if(!r)return box;
  if(r.fictional)box.append(element('p','演示规则，虚构，非医学建议。','menu-state'));
  const tags=menuSettings?.diet?.requirement_tags;
  if(r.tags_without_rules&&Array.isArray(tags)&&tags.length){const bad=r.files.filter(f=>!f.ok).length;box.append(element('p',`你勾选了 ${tags.map(ruleLabel).join('、')}，但还没有规则文件，菜单没有按这些要求筛选。${bad?`（有 ${bad} 个规则文件没能加载，原因见“我的方案”里的规则文件。）`:''}`,'menu-state'));}
  const allergy=allergyText();if(allergy)box.append(element('p',allergy,'small'));
  return box;
}
function groupRuleNotes(group){
  const r=menuData.rules||{},box=element('div',undefined,'menu-rule-notes');
  for(const w of r.warnings?.[group.group_id]||[]){const names=w.scope==='group'?'整组':[...new Set(w.where.map(x=>w.scope==='food'?x.food:x.meal))].join('、');box.append(element('p',`提醒（${names}）：${w.message}`,'small'));}
  const n=r.unlabeled?.[group.group_id];if(n)box.append(element('p',`这一组有 ${n} 种食材未标注，规则可能没覆盖。`,'small'));
  const allergy=allergyText();if(allergy)box.append(element('p',allergy,'small'));
  return box;
}
function hiddenPanel(){
  const r=menuData.rules;if(!r?.hidden?.length)return null;
  const box=element('details');box.id='menu-hidden';box.append(element('summary',`已隐藏的组 · ${r.hidden.length}`),element('p','这些组按你的规则暂不推荐。本应用不是医嘱，你仍可以确认后查看。','small'));
  for(const h of r.hidden){const card=element('article',undefined,'menu-group');card.append(element('h3',groupName(h.group_id)));const list=element('ul',undefined,'menu-group-names');for(const reason of h.reasons)list.append(element('li',reason));card.append(list);
    if(menuData.state.target_date>=TODAY)card.append(button('仍想看这组',()=>confirmAction('仍要查看被隐藏的组？',`${groupName(h.group_id)}被你的规则隐藏。预览不会改变已有安排。`,()=>menuAction('select',{group_id:h.group_id,rule_confirmed:true})),true,'menu-hidden-show-'+h.group_id));box.append(card);}
  return box;
}
function ruleFilesPanel(){
  const r=menuData.rules,box=element('details');box.id='menu-rule-files';box.append(element('summary','规则文件'),element('p','规则文件放在数据目录的 rules 文件夹里，由你或你的营养师编写；本应用只按标签匹配，不含任何内置规则。','small'));
  if(!r.files.length)box.append(element('p','还没有规则文件。','small'));
  for(const f of r.files)box.append(element('p',f.ok?`${f.name} · 已加载 · ${f.rules} 条规则${f.fictional?'（虚构演示）':''}`:`${f.name} · 没有加载：${f.error}`,'small'));
  return box;
}
function groupName(id){return id==='自选'?'自选组合':`第 ${id} 组`;}
function dayLabel(date){const d=new Date(date+'T12:00:00');const rel=date===TODAY?'今天':date===TOMORROW?'明天':'';return `${rel?rel+' ':''}${d.getMonth()+1}月${d.getDate()}日`;}
function element(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
function button(text,fn,secondary=true,id=''){const b=element('button',text,secondary?'secondary':'primary');b.type='button';if(id)b.id=id;b.onclick=async()=>{b.focus({preventScroll:true});try{await fn();}catch(error){toast(error.message);}};return b;}
function menuFocus(id,block='nearest'){const target=document.getElementById(id);if(target){target.focus({preventScroll:true});target.scrollIntoView({block});}}
function announceMenu(message){const live=document.getElementById('agent-status');if(live)live.textContent=message;}
const nutrientLabels={energy_kcal:'能量（千卡）',protein_g:'蛋白质（克）',fat_g:'脂肪（克）',carbohydrate_g:'碳水（克）',fibre_g:'纤维（克）'};
function nutritionText(values){return Object.entries(nutrientLabels).map(([key,label])=>`${label}：${values[key]===null?'未知':values[key].toFixed(1)}`).join(' · ');}
async function loadMenuCandidate(){try{const reply=await(await api('menu/candidate?target_date='+encodeURIComponent(menuData.state.target_date))).json();menuCandidate=reply?.candidate||null;menuReplaced=reply?.replaced===true;}catch(error){menuCandidate=null;}candidateSignature=menuCandidate?menuCandidate.id+menuCandidate.status+(menuReplaced?'r':''):'';}
async function fetchMenu(target){const result=await(await api('menu'+(target?'?date='+encodeURIComponent(target):''))).json();menuData=result;if(result.enabled){await loadMenuSettings();TODAY=result.today;TOMORROW=result.tomorrow;menuDate=result.state.target_date;await loadMenuCandidate();await loadActivity();}return result;}
function weightBasisLabel(value){return {cooked_edible:'熟制后可食部分重量',dry:'干重',raw_edible:'生的可食部分重量',ready_to_cook:'待烹调食材重量',ready_to_eat:'即食状态重量'}[value]||'重量口径待核对（保留原份量）';}
async function nextMenuGroup(){
  const state=menuData.state,current=state.draft?.group_id||state.adopted?.group_id||state.recommendation?.group_id;
  const groups=menuData.groups,start=groups.findIndex(g=>g.group_id===current),hidden=hiddenIds();
  for(let step=1;step<=groups.length;step++){
    const candidate=groups[(start+step+groups.length)%groups.length];
    if(candidate.group_id!==current&&!hidden.has(candidate.group_id)&&!menuData.preferences.some(p=>p.group_id===candidate.group_id&&p.avoided))return menuAction('select',{group_id:candidate.group_id});
  }
  toast('暂时没有其他可推荐的组，可以点“自己挑选”查看全部方案。');
}
function foodSourceView(food,index,scope){
  const details=element('details');details.id='menu-source-'+scope+'-'+index;details.append(element('summary',food.name+' · 营养数据来源'));
  const source=food.source;
  if(typeof source==='string'){details.append(element('p',source||'来源待补充','small'));return details;}
  if(!source||typeof source!=='object'||Array.isArray(source)){details.append(element('p','来源待补充','small'));return details;}
  let shown=false;for(const [key,label] of Object.entries({publisher:'发布者',dataset:'资料名称',food_code:'食物条目',accessed:'查阅日期'})){if(typeof source[key]==='string'||typeof source[key]==='number'){details.append(element('p',label+'：'+source[key],'small'));shown=true;}}
  if(typeof source.url==='string'){try{const url=new URL(source.url);if(['https:','http:'].includes(url.protocol)&&!url.username&&!url.password){const link=element('a','查看原始营养资料');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';link.referrerPolicy='no-referrer';details.append(link);shown=true;}}catch(error){/* 不可用链接仅作为缺失来源处理。 */}}
  if(!shown)details.append(element('p','来源待补充','small'));return details;
}
async function menuAction(action,extra={}){
  if(menuBusy)return;menuBusy=true;
  const s=menuData.state, focusId=document.activeElement?.id;
  if(['select','adopt'].includes(action)&&hiddenIds().has(extra.group_id))extra={...extra,rule_confirmed:true};   // 入口已由“仍想看”或采用确认框确认
  try{await api('menu/action',{action,target_date:s.target_date,revision:s.revision,library_hash:s.library_hash,...extra});menuNotice=action==='select'?`${dayLabel(s.target_date)}已换成${groupName(extra.group_id)}（预览，尚未采用）。`:'';await fetchMenu(menuDate);render();
    if(action==='select'){const label=dayLabel(s.target_date);const browse=document.getElementById('menu-browse');if(browse)browse.open=false;menuFocus('menu-preview-title','start');announceMenu(`${label}已换成${groupName(extra.group_id)}，尚未采用。原安排没有改变。`);}
    if(action==='cancel_draft'){menuFocus('menu-change');announceMenu('已取消预览，原安排与实际记录保持不变。');}
    if(action==='preference'&&focusId&&!document.getElementById(focusId))menuFocus('menu-favorite-filter');}
  catch(error){await fetchMenu(menuDate);render();toast(error.message);}
  finally{menuBusy=false;}
}
function renderMeal(meal,allowRecords=false,scope='preview'){
  const key=scope+'-'+meal.meal;
  const card=element('article',undefined,'menu-meal');card.append(element('h3',meal.meal+' · '+meal.title));
  const list=element('ul');for(const item of meal.ingredients)list.append(element('li',`${item.food.name} ${item.g} 克（${weightBasisLabel(item.food.weight_basis)}）`));
  card.append(element('p',`共 ${meal.ingredients.reduce((total,item)=>total+item.g,0).toFixed(1)} 克 · 各食材按注明的状态称重`,'small'));
  const details=element('details');details.id='menu-meal-'+key;details.append(element('summary','食材、做法与营养'),list);
  const steps=Array.isArray(meal.steps)?meal.steps.join('\n'):meal.steps;details.append(element('p',steps));
  meal.ingredients.forEach((item,index)=>details.append(foodSourceView(item.food,index,key)));card.append(details);
  details.append(element('p',nutritionText(meal.nutrition),'small'));
  if(!allowRecords)return card;
  const s=menuData.state, locked=!!s.locks[meal.meal];
  if(s.adopted||locked)card.append(button(locked?'解锁此餐以更正':'锁定此餐',()=>menuAction('lock',{meal:meal.meal,locked:!locked}),true,'menu-lock-'+key));
  if(s.target_date<=TODAY){
    const actual=s.actual[meal.meal], form=element('div',undefined,'field');
    const select=element('select');select.id='menu-actual-'+key;select.setAttribute('aria-label',meal.meal+'实际情况');
    for(const [v,label] of Object.entries({unknown:'还没记录',done:'按安排吃了',partial:'吃了一部分',other:'吃了其他',not_done:'没有吃'})){const option=element('option',label);option.value=v;select.append(option);}select.value=actual?.status||'unknown';
    const note=element('input');note.id='menu-note-'+key;note.placeholder='实际情况备注（不估算营养）';note.maxLength=500;note.value=actual?.note||'';note.setAttribute('aria-label',meal.meal+'实际备注');
    const save=button('保存实际记录',()=>menuAction('actual',{meal:meal.meal,status:select.value,note:note.value}),true,'menu-save-'+key);save.disabled=locked;select.disabled=locked;note.disabled=locked;
    form.append(select,note,save,element('p',locked?'已锁定；更正前先显式解锁。':'保存后自动锁餐。未填克数，不推算实际摄入。','small'));card.append(form);
  }
  return card;
}
function menuGroupView(group,role,allowRecords){
  const view=element('section',undefined,'menu-group-view');view.setAttribute('data-menu-role',role);view.setAttribute('data-group-id',group.group_id);
  const title=element('h2',role==='adopted'&&menuData.state.draft?`仍在使用 · ${groupName(group.group_id)}`:'五餐，一餐一餐来。');
  title.id=role==='adopted'?'menu-adopted-title':'menu-preview-title';title.tabIndex=-1;view.append(title);
  if(role==='adopted'&&menuData.state.draft)view.append(element('p','下面只记录已采用菜单的实际情况，上面的预览还没有生效。','small'));
  view.append(element('p',nutritionText(group.nutrition),'menu-nutrition'),groupRuleNotes(group));
  const meals=element('div',undefined,'menu-meals');for(const meal of group.meals)meals.append(renderMeal(meal,allowRecords,role+'-'+group.group_id));view.append(meals);
  return view;
}
function menuPreferences(group,scope){
  const pref=menuData.preferences.find(p=>p.group_id===group.group_id)||{}, actions=element('div',undefined,'menu-actions menu-secondary-actions');
  actions.append(button(pref.favorite?'取消收藏':'收藏',()=>menuAction('preference',{group_id:group.group_id,favorite:!pref.favorite,avoided:!!pref.avoided}),true,'menu-favorite-'+scope+'-'+group.group_id),button(pref.avoided?'恢复推荐':'暂不想吃',()=>menuAction('preference',{group_id:group.group_id,favorite:!!pref.favorite,avoided:!pref.avoided}),true,'menu-avoid-'+scope+'-'+group.group_id));return actions;
}
function menuBrowse(shown){
  const browse=element('details');browse.id='menu-browse';const hiddenSet=hiddenIds(),visible=menuData.groups.filter(g=>!hiddenSet.has(g.group_id)).length;const summary=element('summary',shown?`换一组 · 其他${visible-(hiddenSet.has(shown.group_id)?0:1)}组`:`查看全部${visible}组`);summary.id='menu-browse-summary';browse.append(summary);
  const filter=element('label',undefined,'menu-check');const checkbox=element('input');checkbox.type='checkbox';checkbox.id='menu-favorite-filter';checkbox.checked=menuFavoritesOnly;checkbox.onchange=()=>{menuFavoritesOnly=checkbox.checked;menuBrowseLimit=MENU_PAGE_SIZE;render();};filter.append(checkbox,element('span','只看收藏'));browse.append(filter);
  const groups=menuData.groups.filter(g=>g.group_id!==shown?.group_id&&!hiddenSet.has(g.group_id)&&(!menuFavoritesOnly||menuData.preferences.some(p=>p.group_id===g.group_id&&p.favorite)));
  const list=element('div',undefined,'menu-group-list');
  for(const group of groups.slice(0,menuBrowseLimit)){
    const card=element('article',undefined,'menu-group');const title=element('h3',`${groupName(group.group_id)}`);title.id='menu-group-title-'+group.group_id;title.tabIndex=-1;card.append(title);
    const names=element('ul',undefined,'menu-group-names');for(const meal of group.meals)names.append(element('li',`${meal.meal} · ${meal.title}`));card.append(names);
    card.append(groupRuleNotes(group));
    const nutrition=element('details');nutrition.id='menu-group-nutrition-'+group.group_id;nutrition.append(element('summary','看看整日营养'),element('p',nutritionText(group.nutrition),'small'));card.append(nutrition);
    if(menuData.state.target_date>=TODAY)card.append(button('预览并手选这组',()=>menuAction('select',{group_id:group.group_id}),true,'menu-select-'+group.group_id));
    card.append(menuPreferences(group,'list'));list.append(card);
  }
  browse.append(list,element('p',groups.length?`已显示 ${Math.min(menuBrowseLimit,groups.length)} / ${groups.length} 组`:'还没有收藏其他菜单。取消筛选可以看看全部。','small'));
  if(menuBrowseLimit<groups.length)browse.append(button(`再看${MENU_PAGE_SIZE}组`,()=>{const first=groups[menuBrowseLimit].group_id;menuBrowseLimit+=MENU_PAGE_SIZE;render();menuFocus('menu-group-title-'+first);},true,'menu-show-more'));
  const rules=element('details');rules.id='menu-recommendation-rules';rules.append(element('summary','推荐是怎么选的？'),element('p','未采用优先，再按最久采用目标日；同层收藏优先，最后按组号。偏好只影响未来的新推荐，不替换已有推荐或草稿。','small'));browse.append(rules);return browse;
}
function renderMenu(current){
  const expected=current==='today'?TODAY:current==='tomorrow'?TOMORROW:menuDate;
  if(expected!==menuDate&&current==='today'){fetchMenu(expected).then(()=>render());return;}
  if(expected!==menuDate){main.replaceChildren(element('p','读取 '+expected+' 的菜单…'));fetchMenu(expected).then(()=>render());return;}
  const s=menuData.state;
  if(current==='today'){const copy=document.getElementById('today-menu-copy');if(copy)copy.textContent=s.adopted?`${dayLabel(TODAY)} · 已安排${groupName(s.adopted.group_id)}。`:`${dayLabel(TODAY)} · 菜单还没有确定。`;const open=document.getElementById('today-menu-open');if(open)open.onclick=async()=>{menuNotice='';await fetchMenu(TODAY);navigate('plan');};return;}
  const selected=s.draft||s.adopted||menuData.groups.find(g=>g.group_id===s.recommendation?.group_id), shown=selected;
  const section=element('section',undefined,'menu-section');section.id='menu-section';
  const dayBadge=element('p',dayLabel(s.target_date),'menu-day'+(s.target_date===TODAY?' is-today':''));dayBadge.id='menu-day-label';
  section.append(dayBadge,element('h1',current==='tomorrow'?'明天，吃得安心。':'我的方案'));
  if(menuData.synthetic){const note=element('p',t('menu.synthetic_note'),'menu-state');note.id='menu-synthetic-note';section.append(note);}   // 示例菜单只在菜单区域说明，顶部横幅只看是不是演示库
  if(current==='tomorrow'&&menuCandidate?.status==='READY_TO_REVIEW'){const hint=button('电脑上的助手给明天提了一份建议 · 去看看 ↓',()=>{const target=document.getElementById('menu-candidate');target.scrollIntoView({block:'start',behavior:'auto'});target.focus({preventScroll:true});},true,'menu-cand-hint');hint.className='menu-cand-hint';section.append(hint);}
  if(current==='plan'){section.append(element('h2','饮食 · 菜单'));const sw=element('div',undefined,'menu-day-switch');sw.setAttribute('role','group');sw.setAttribute('aria-label','选择日期');
    for(const day of [TODAY,TOMORROW]){const b=button('看'+dayLabel(day)+'的菜单',async()=>{menuNotice='';await fetchMenu(day);render();},true,'menu-day-'+(day===TODAY?'today':'tomorrow'));b.setAttribute('aria-pressed',String(s.target_date===day));sw.append(b);}
    section.append(sw);}
  if(menuNotice){const fb=element('p',menuNotice,'menu-feedback');fb.setAttribute('role','status');section.append(fb);}
  const status=s.draft?`正在预览${groupName(s.draft.group_id)}，尚未采用。${s.adopted?'原安排仍为'+groupName(s.adopted.group_id)+'。':'这一天的安排还没有确定。'}`:s.adopted?`已安排${groupName(s.adopted.group_id)}。按实际情况记录就好。`:selected?`为你准备了${groupName(selected.group_id)}，确认采用后才会成为当天安排。`:'暂时没有推荐。可以查看全部菜单，选一组喜欢的。';
  section.append(element('p',status,'menu-state'));const agentNote=agentAdoptNote(s);if(agentNote)section.append(agentNote);section.append(rulesTop());
  const actions=element('div',undefined,'menu-actions');
  const alreadyAdopted=selected&&s.adopted?.group_id===selected.group_id&&s.adopted?.library_hash===selected.library_hash;
  if(selected&&s.target_date>=TODAY&&!alreadyAdopted)actions.append(button('采用这一整组',()=>confirmAction('采用整日五餐？',`${s.target_date} ${groupName(selected.group_id)}；任何不一致锁餐都会拒绝整组。`,()=>menuAction('adopt',{group_id:selected.group_id})),false,'menu-adopt'));
  actions.append(button('换一组',()=>nextMenuGroup(),true,'menu-change'));
  actions.append(button('自己挑选 · 查看全部方案',()=>{document.getElementById('menu-browse').open=true;menuFocus('menu-browse-summary');},true,'menu-pick'));
  if(s.draft)actions.append(button(s.adopted?'取消预览，返回已采用菜单':'取消手选，返回推荐',()=>menuAction('cancel_draft'),true,'menu-cancel'));
  section.append(actions);
  if(selected&&s.target_date>=TODAY&&(selected.library_hash!==s.library_hash||(!s.draft&&!s.adopted&&s.recommendation?.library_hash!==s.library_hash)))section.append(button('重新预览当前组的新库版本',()=>menuAction('select',{group_id:selected.group_id}),true,'menu-repreview'));
  if(shown)section.append(menuGroupView(shown,s.draft?'draft':s.adopted?'adopted':'recommendation',!s.draft&&!!s.adopted));
  if(s.draft&&s.adopted)section.append(menuGroupView(s.adopted,'adopted',true));
  const more=element('details');more.id='menu-more';more.append(element('summary','更多操作'));
  if(shown)more.append(menuPreferences(shown,'current'));
  const undo=s.previous.length?button('撤销上次采用',()=>confirmAction('撤销上次采用？','保留全部实际记录；若与锁餐冲突则拒绝。',()=>menuAction('undo')),true,'menu-undo'):null;
  if(undo&&current!=='plan')more.append(undo);
  section.append(more);
  if(current==='plan')section.append(menuVersions(s,undo));
  section.append(menuBrowse(shown));
  const hiddenBox=hiddenPanel();if(hiddenBox)section.append(hiddenBox);
  if(current==='plan'&&menuData.rules)section.append(ruleFilesPanel());
  if(current==='tomorrow')section.append(candidatePanel(s,selected));
  if(current==='plan'){
    section.append(menuHistory(),importPanel());
    const backupButton=button('备份菜单与全部本机记录',async()=>{const response=await api('backup',{});const url=URL.createObjectURL(await response.blob());const a=element('a');a.href=url;a.download='waybeside-backup.sqlite';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);},true,'menu-backup');section.append(backupButton);
  }else{const management=element('a','菜单资料、历史采购与备份 →','menu-management');management.href='#plan';management.id='menu-management-link';section.append(management);}
  main.replaceChildren(section);
}
function agentAdoptNote(s){
  if(!s.adopted||!activityData?.by_agent?.menu_adopt?.[s.target_date])return null;
  const note=element('p',undefined,'small agent-note');note.id='menu-agent-adopt';note.append(element('span',t('mark.recent'),'tag agent-mark'));
  if((activityData.rule_overrides||[]).some(o=>o.target_date===s.target_date&&String(o.group_id)===String(s.adopted.group_id)))note.append(' '+t('mark.override'));
  return note;
}
function menuVersions(s,undo){
  const box=element('details');box.id='menu-versions';box.append(element('summary','版本记录 · '+dayLabel(s.target_date)));
  const rows=[...s.previous].reverse();
  box.append(element('p',s.adopted?'现在用的：'+groupName(s.adopted.group_id)+(s.adopted.composed_from?'（逐餐自选）':''):'这一天还没有采用任何菜单。'));
  if(rows.length){const list=element('ul');rows.forEach((g,i)=>list.append(element('li',(i===0?'上一版：':'更早：')+(g?groupName(g.group_id):'尚未采用'))));box.append(list);}
  else box.append(element('p','还没有更早的版本。','small'));
  if(undo)box.append(undo);return box;
}
const reasonText={USER_PREFERENCE:'更贴合本人偏好',VARIETY:'换换花样',KEEP_CURRENT:'保持现状'};
function candidatePanel(s,selected){
  const panel=element('section',undefined,'menu-candidate');panel.id='menu-candidate';panel.tabIndex=-1;
  panel.append(element('h2','电脑上的助手给的建议'));const c=menuCandidate;
  if(c&&menuReplaced){const replaced=element('p',t('menu.candidate_replaced'),'menu-feedback');replaced.id='menu-candidate-replaced';replaced.setAttribute('role','status');panel.append(replaced);}
  if(!c||c.status==='WAITING_EXTERNAL'){panel.append(element('p',c?'已把资料交给电脑上的助手，等它提交建议。':tm('menu.candidate_empty'),'small'));return panel;}
  if(c.status!=='READY_TO_REVIEW'){panel.append(element('p','菜单或偏好有变化，之前的建议已经失效，请让助手重新取资料再提交。','small'));return panel;}
  panel.append(element('p','由电脑上的助手提交 · '+dayLabel(c.target_date)+' · '+(reasonText[c.reason_code]||'')+'。'+tm('menu.candidate_tail'),'small'));
  const base=s.adopted||selected,rows=element('div',undefined,'menu-diff');
  for(const meal of c.group.meals){const before=base?.meals.find(m=>m.meal===meal.meal);const same=before&&before.title===meal.title;
    const row=element('div',undefined,'menu-diff-row'+(same?'':' changed'));row.append(element('strong',meal.meal),element('span',(s.adopted?'现在：':'目前准备的：')+(before?before.title:'未定')),element('span','建议：'+meal.title),element('span',same?'相同':'更换'));rows.append(row);}
  panel.append(rows);
  const label=element('label',undefined,'menu-check'),box=element('input');box.type='checkbox';box.id='menu-cand-reviewed';label.append(box,element('span','我看过上面的对照，愿意采用这份建议。'));panel.append(label);
  const actions=element('div',undefined,'menu-actions');
  const adopt=button('采用这份建议',()=>confirmAction('采用助手的建议？',dayLabel(c.target_date)+'：'+groupName(c.group.group_id)+'。采用后可在“我的方案”撤销。',async()=>{await api('menu/candidate/'+c.id+'/adopt',{confirmed:true});menuNotice=dayLabel(c.target_date)+'已采用助手的建议。';await fetchMenu(menuDate);render();announceMenu(dayLabel(c.target_date)+'已采用助手的建议。');}),false,'menu-cand-adopt');adopt.disabled=true;box.onchange=()=>{adopt.disabled=!box.checked;};
  const decline=button('保留原安排',async()=>{await api('menu/candidate/'+c.id+'/decline',{});menuNotice='已保留原安排。';await fetchMenu(menuDate);render();announceMenu('已保留原安排，没有采用助手的建议。');},true,'menu-cand-decline');
  actions.append(adopt,decline);panel.append(actions);return panel;
}
async function pollMenuCandidate(){if(!menuData?.enabled||page()!=='tomorrow'||menuBusy||document.hidden)return;const before=candidateSignature;try{await loadMenuCandidate();if(candidateSignature!==before){await loadActivity();render();}}catch(error){/* 下次再试 */}}
function menuHistory(){
  const histories=element('details');histories.id='menu-history';histories.append(element('summary','往日菜单与采购清单'));
  const rows=menuData.history.filter(r=>r.adopted), valid=new Set(rows.map(r=>r.target_date));
  for(const day of menuShoppingDates)if(!valid.has(day))menuShoppingDates.delete(day);
  const count=element('p','', 'small');count.id='menu-shopping-count';count.setAttribute('aria-live','polite');
  const shopping=element('div');shopping.id='menu-shopping-result';
  const collect=button('汇总所选日期的采购',async()=>{const dates=[...menuShoppingDates];if(!dates.length)return;const data=await(await api('menu/shopping?'+dates.map(d=>'date='+d).join('&'))).json();shopping.replaceChildren(element('p','采购日期：'+data.dates.join('、')+'；仅采购，不计实际摄入。'));for(const item of data.items)shopping.append(element('p',`${item.name} ${item.g.toFixed(1)} ${item.unit==='g'?'克':'单位待核对'}（${weightBasisLabel(item.weight_basis)}）`));},true,'menu-shopping-collect');
  const update=()=>{count.textContent=`已选择 ${menuShoppingDates.size} 天`;collect.disabled=!menuShoppingDates.size;};
  for(const row of rows){const line=element('div',undefined,'menu-history-row'),label=element('label',undefined,'menu-check'),check=element('input');check.type='checkbox';check.id='menu-shopping-'+row.target_date;check.checked=menuShoppingDates.has(row.target_date);check.onchange=()=>{if(check.checked)menuShoppingDates.add(row.target_date);else menuShoppingDates.delete(row.target_date);update();};
    label.append(check,element('span',`${row.target_date} · 加入采购`));line.append(label,button(`查看这天菜单 · ${groupName(row.adopted.group_id)}`,async()=>{await fetchMenu(row.target_date);navigate('plan');render();},true,'menu-history-view-'+row.target_date));histories.append(line);}
  if(!rows.length)histories.append(element('p','采用过的菜单会按日期保存在这里。','small'));
  update();histories.append(count,collect,shopping);return histories;
}
function importPanel(){
  const panel=element('details');panel.id='menu-import';panel.append(element('summary','导入菜单库'));
  panel.append(element('p',tm('menu.import_copy'),'small'));
  const file=element('input');file.id='menu-import-file';file.type='file';file.accept='.json,application/json';file.setAttribute('aria-label','选择我的菜单文件');const output=element('div');output.setAttribute('aria-live','polite');
  file.onchange=async()=>{output.replaceChildren();try{if(!file.files[0]||file.files[0].size>1500000)throw new Error('请选择小于1.5MB的menu.json');const menu=JSON.parse(await file.files[0].text());const preview=await(await api('menu/import-preview',menu)).json();output.append(element('p',`版本 ${preview.version} · ${preview.groups} 组 · 每组 ${preview.meals} 餐 · ${preview.identity}`),element('p',preview.message),button('确认导入这份私人菜单',async()=>{await api('menu/import-confirm',{menu,digest:preview.digest,expected:preview.expected,confirmed:true});await fetchMenu();render();toast('菜单已导入。');},false));}catch(error){output.textContent=error instanceof SyntaxError?'菜单文件格式不正确，请选择完整的 menu.json 文件。':error.message;}};
  panel.append(file,output);return panel;
}
async function syncMenuDate(){try{const previous=TODAY;const fresh=await(await api('menu'+(menuDate?'?date='+menuDate:''))).json();if(fresh.enabled&&previous!==fresh.today){TODAY=fresh.today;TOMORROW=fresh.tomorrow;await fetchMenu(page()==='today'?TODAY:TOMORROW);await refreshState();render();}}catch(error){toast(error.message);}}
window.addEventListener('focus',syncMenuDate);
setInterval(syncMenuDate,60000);
setInterval(pollMenuCandidate,2000);
(async()=>{await stateReady;if(csrf){await fetchMenu(page()==='today'?TODAY:undefined);render();}})();
