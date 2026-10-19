'use strict';
// 旅程：今天页、旅程页、疗程设置页与检验页。小路格数、每周格数、文案全部由疗程设置驱动，
// 没有任何内置次数、日期或星期；没有疗程设置时只显示引导去设置的空状态。
const dailyRecords = {};
let celebrationEnabled = true, celebrationMessage = '', questionNote = '';
const checkinLabels = {unknown:'还没记',done:'今天完成了',partial:'做了一点',not_done:'今天休息'};
// 旧三餐管线仍读写的占位，不再有界面。
const labRecords = [];
const labKey = r=>r?JSON.stringify([r.metric,r.unit,r.lab]):"";
let selectedLab = "";
let labEditIndex = null;
const addedLabRows = [];
let labRhythm = {blood:null,chemistry:null};
function recordForDate(date){return {...(dailyRecords[date] || {date,time:'20:30',session:'',mood:'unknown',energy:'unknown',diet:'unknown',sleep:'unknown',symptoms:{},exercise:'unknown',meditation:'unknown',minutes:'',note:''})};}
function todayRecord(){return recordForDate(TODAY);}
async function saveTodayChange(patch,title){await persistRecord({...todayRecord(),...patch});}

// ---- 疗程文案与派生量（全部来自 course / sessions）----
const TREATMENT_NAMES = {radiotherapy:'放疗', chemotherapy:'化疗', other:'其他治疗'};
const WEEKDAY_NAMES = ['周一','周二','周三','周四','周五','周六','周日'];   // 0=周一，与服务端一致
const SESSION_STATUS = {completed:'已完成',postponed:'已改期',cancelled:'已取消',planned:'待确认'};
const unitWord = () => course?.count_unit === 'cycle' ? '个周期' : '次';
const nth = n => `第 ${n} ${unitWord()}`;
const courseName = () => course ? (course.label || TREATMENT_NAMES[course.treatment_type]) : '';
const monthDay = date => `${Number(date.slice(5,7))} 月 ${Number(date.slice(8,10))} 日`;
const shortDay = date => date.slice(5).replace('-','.');
const cellTotal = () => Math.max(course?.total_count ?? 0, sessions.length);
const recordedCount = () => sessions.filter(s=>s.status!=='cancelled').length;
const blankCols = () => course?.schedule_kind === 'weekdays' && course.weekdays.length ? course.weekdays.length : 5;
function scheduleSummary(){
  if(!course)return '';
  const start = course.start_date ? `从 ${monthDay(course.start_date)} 开始` : '开始日还没定';
  if(course.schedule_kind === 'weekdays')return `每周${course.weekdays.map(w=>WEEKDAY_NAMES[w].slice(1)).join('、')}治疗 · ${start}`;
  return `逐次添加预约 · ${start}`;
}
function progressText(){
  if(!course)return '';
  return course.total_count ? `已走过 <b>${count()}</b> / ${course.total_count} ${unitWord()}` : `已记录 <b>${count()}</b> ${unitWord()}完成`;
}
function progressBar(){
  const total = course?.total_count;
  if(!total)return '';
  return `<div class="progress-track" role="progressbar" aria-valuenow="${count()}" aria-valuemin="0" aria-valuemax="${total}" aria-label="已确认完成的${course.count_unit==='cycle'?'周期数':'次数'}"><div class="progress-fill" style="width:${Math.min(100,count()/total*100)}%"></div></div>`;
}
function courseGuide(){
  return `<section class="empty course-guide" id="course-guide"><p class="eyebrow">还没有疗程设置</p><h2>先告诉我这段路怎么走。</h2><p>填上治疗的类型、总数或周期、治疗星期和开始日（不确定的可以先留空），小路就会按你的设置排出来。</p><a class="button primary" id="goto-settings" href="#settings">去设置疗程</a></section>`;
}
function syncNotice(){
  if(!scheduleOutOfSync)return '';
  return `<div class="state-banner warning" id="sync-banner" role="status"><h2>设置已改，已排日期还是旧的，要不要重排？</h2><p class="small">只会调整“还没发生、也没手动改过期”的预约；已完成、已改期、已取消的都不动。</p><button class="secondary" id="resync-open" data-course-resync>确认重排</button></div>`;
}
function generateNotice(){
  if(!(course?.schedule_kind === 'weekdays' && course.total_count && course.start_date))return '';
  const have = recordedCount(), need = course.total_count - have;
  if(need <= 0)return '';
  const lead = have ? `已有 ${have} 条，总数 ${course.total_count}，要补排吗？` : `总数 ${course.total_count}，要现在按设置把日期排出来吗？`;
  return `<div class="note neutral" id="generate-box"><strong>${lead}</strong><p class="small">按治疗星期从开始日往后排；已排和已完成的日期不会改动。</p><button class="secondary" id="generate-open" data-course-generate>排出剩下的 ${need} ${unitWord()}</button></div>`;
}

// ---- 小路：按 ISO 周分组；格数 = max(总数, 已有记录数) ----
const dayAt = date => new Date(date+'T12:00:00');
function mondayOf(date){const d=dayAt(date);d.setDate(d.getDate()-((d.getDay()+6)%7));return localDate(d);}
const weeksBetween = (a,b) => Math.round((dayAt(b)-dayAt(a))/604800000);
function trailRows(){
  const real = sessions.slice().sort((a,b)=>a.planned_date.localeCompare(b.planned_date)||a.ordinal-b.ordinal);
  const rows = [];
  for(const s of real){
    const key = mondayOf(s.planned_date);
    let row = rows.at(-1);
    if(!row || row.key !== key){row = {key,cells:[]};rows.push(row);}
    row.cells.push(s);
  }
  rows.forEach(r=>{r.week = weeksBetween(rows[0].key,r.key)+1;});
  const blank = Math.max(0,cellTotal()-real.length), step = blankCols();
  let ordinal = Math.max(0,...sessions.map(s=>s.ordinal));
  for(let i=0;i<blank;i+=step){
    const cells = [];
    for(let j=i;j<Math.min(i+step,blank);j++)cells.push({blank:true,ordinal:++ordinal});
    rows.push({blank:true,cells});
  }
  return rows;
}
function pathTrail(interactive=false){
  const rows = trailRows();
  const cols = Math.max(blankCols(),...rows.map(r=>r.cells.length));
  const canGenerate = course?.schedule_kind === 'weekdays' && course.total_count && course.start_date;
  const hint = course && !sessions.length ? (canGenerate ? `<p class="trail-hint" id="trail-generate-hint">${t('trail.generate_hint')} <a href="#course">${t('trail.generate_link')}</a></p>` : `<p class="trail-hint">${t('trail.manual_hint')}</p>`) : '';
  if(!rows.length)return hint;
  const heading = row => {
    if(row.blank)return '日期还没填';
    const first = row.cells[0].planned_date, last = row.cells.at(-1).planned_date;
    return `第 ${row.week} 周 · ${shortDay(first)}${last===first?'':' — '+shortDay(last)}`;
  };
  const stone = s => {
    if(s.blank)return `<span class="stone blank" aria-label="${nth(s.ordinal)} · 日期还没填">${s.ordinal}</span>`;
    const label = `${nth(s.ordinal)} · ${s.planned_date} · ${SESSION_STATUS[s.status]}`;
    const cls = `stone ${s.status==='completed'?'complete':''} ${s.planned_date===TODAY?'today':''}`;
    return interactive
      ? `<button type="button" class="${cls}" data-private-jump="${s.id}" aria-label="${label}">${s.ordinal}<span class="sr-only">${label}</span></button>`
      : `<span class="${cls}" title="${label}" aria-label="${label}">${s.ordinal}</span>`;
  };
  const total = course?.total_count ? `，共 ${cellTotal()} 格` : '';
  return `<div class="trail journey-dots" style="--cols:${cols};--cells:${cellTotal()}" aria-label="已填写 ${sessions.length} ${unitWord()}，已完成 ${count()} ${unitWord()}${total}">${rows.map(row=>`<div class="week"><p class="small">${heading(row)}</p><div class="stones">${row.cells.map(stone).join('')}</div></div>`).join('')}</div>${hint}`;
}

function checkinCard(domain,title){const status=todayRecord()[domain];return `<section class="checkin-card"><div class="card-top"><h3>${title}</h3>${tag(checkinLabels[status],status==='unknown'?'muted':'')}${status!=='unknown'&&todayRecord().recorded_by==='agent'?' '+agentMark():''}</div><div class="checkin-options" role="group" aria-label="${title}今天的记录">${[['done','完成了'],['partial','做了一点'],['not_done','今天休息']].map(([value,label])=>`<button class="${status===value?'selected':''}" data-checkin="${domain}" data-value="${value}" aria-pressed="${status===value}">${label}</button>`).join('')}</div>${status!=='unknown'?`<button class="text-button small" data-clear-checkin="${domain}">误点了可以改</button>`:'<p class="small">按实际情况选，也可以暂时不记。</p>'}${typeof habitHint==='function'?habitHint(domain):''}</section>`;}
function pastPages(){const dates=[...new Set([...sessions.filter(s=>s.status==='completed').map(s=>s.actual_date||s.planned_date),...Object.keys(dailyRecords)])].sort().reverse();return `<section class="past"><h2>已经记下的日子</h2>${dates.length?dates.map((date,i)=>{const r=dailyRecords[date];const treatment=sessions.filter(s=>(s.actual_date||s.planned_date)===date&&s.status==='completed').map(s=>nth(s.ordinal)+' · 已完成');const fields=r?Object.entries({diet:'饮食',exercise:'活动',meditation:'冥想',mood:'心情',energy:'精力',sleep:'睡眠'}).filter(([k])=>r[k]&&r[k]!=='unknown').map(([k,v])=>v+'：'+(labels[r[k]]||r[k])).concat(Object.entries(r.symptoms||{}).filter(([,v])=>v!=='unknown').map(([k,v])=>k+'：'+(labels[v]||v))):[];const byAgent=r?.recorded_by==='agent'||sessions.some(s=>(s.actual_date||s.planned_date)===date&&s.status==='completed'&&s.confirmation_source==='agent');return `<article data-date="${date}" style="opacity:${(1-Math.min(i,24)*.01).toFixed(2)}"><h3>${date}${byAgent?' '+agentMark():''}</h3><p>${[...treatment,...fields].map(escapeText).join('；')||'这一天，留下一条记录。'}</p>${r?.note?`<p>${escapeText(r.note)}</p>`:''}</article>`;}).join(''):'<p>还没有留下记录。慢慢来。</p>'}</section>`;}

// ---- 今天 ----
function pathAside(){
  if(!course)return `<aside class="path-aside"><p class="eyebrow">我的疗程 · 一条小路</p><h1>先把疗程设置好，<br>小路才排得出来。</h1><p class="small">还没有疗程设置。告诉我治疗的类型、总数和治疗星期，这里就会铺出你的小路。</p><a class="button primary" id="goto-settings" href="#settings">去设置疗程</a></aside>`;
  return `<aside class="path-aside"><p class="eyebrow">我的疗程 · 一条小路</p><h1>走到这里，<br>每一步都在。</h1><p class="path-count">${progressText()}</p>${progressBar()}<p class="small">${escapeText(scheduleSummary())}${course.total_count?'':' · 总数还没填'}</p>${pathTrail()}${scheduleOutOfSync?'<p class="small" id="sync-hint"><a href="#course">设置已改，已排日期还是旧的，去看看 →</a></p>':''}<a href="#course">填写预约与治疗记录 →</a></aside>`;
}
function todayView(){return `<div class="path-layout">${pathAside()}<div class="path-right"><p class="eyebrow">${typeof greetingText==='function'?greetingText():''}${dateCaption(TODAY,true)}</p><h1>今天，一件一件来。</h1><section class="today-menu-entry"><h3>今天的饮食</h3><p id="today-menu-copy">菜单库还没有导入，导入后这里会显示今天的安排。</p><button id="today-menu-open" class="secondary">看今天五餐 →</button></section>${checkinCard('exercise','今天的活动，怎么样？')}${checkinCard('meditation','今天的冥想，怎么样？')}<a class="button secondary" href="#record">记一下身体感受</a><a class="button secondary" href="#tomorrow">准备明天 →</a></div></div>`;}

// ---- 旅程 ----
// 旅程页只展开“今天/下一次”那一条；其余收成一行摘要，点开才出现操作；已完成的归入“已走过 N 次”。
function focusSession(){
  const open = sessions.filter(s=>['planned','postponed'].includes(s.status)).sort((a,b)=>a.planned_date.localeCompare(b.planned_date)||a.ordinal-b.ordinal);
  return open.find(s=>s.planned_date===TODAY) || open[0] || null;   // 今天的优先，否则最早一条待确认（含已过期未确认的）
}
function sessionCard(s,isFocus=false){
  const actions = s.status==='completed'
    ? `<button class="secondary" data-private-id="${s.id}" data-private-action="correct">更正误记完成</button>`
    : s.status==='cancelled'
      ? `<button class="secondary" data-private-id="${s.id}" data-private-action="restore">恢复预约</button>`
      : `<label>实际日期或改期日期<input type="date" id="private-date-${s.id}" value="${s.planned_date}"></label><button class="secondary" data-private-id="${s.id}" data-private-action="complete">确认完成</button><button class="secondary" data-private-id="${s.id}" data-private-action="reschedule">改期</button><button class="text-button" data-private-id="${s.id}" data-private-action="cancel">取消预约</button>`;
  const todayTag = s.planned_date===TODAY && s.status!=='completed' ? ' <span class="tag today-tag">今天</span>' : '';
  return `<details class="history-item session-fold ${isFocus?'focus':''}" id="private-session-${s.id}" ${isFocus?'open':''}><summary>${nth(s.ordinal)} · ${s.planned_date} · ${SESSION_STATUS[s.status]}${todayTag}${s.confirmation_source==='agent'?' '+agentMark():''}</summary><div class="session-body">${offScheduleNote(s)}${s.actual_date?`<p>实际完成日 ${s.actual_date}</p>`:''}<div class="actions">${actions}</div></div></details>`;
}
function offScheduleNote(s){const kind=activityData?.off_schedule?.[String(s.id)];return kind?`<span class="small agent-note">${t('mark.off_schedule.'+kind)}</span>`:'';}
function doneRow(s){return `<div class="done-row" id="private-session-${s.id}" tabindex="-1"><span>${nth(s.ordinal)} · ${s.planned_date} · 已完成${s.actual_date?'（实际 '+s.actual_date+'）':''}${s.confirmation_source==='agent'?' '+agentMark():''}${offScheduleNote(s)}</span><button class="secondary" data-private-id="${s.id}" data-private-action="correct">更正误记完成</button></div>`;}
function sessionList(){
  const sorted = sessions.slice().sort((a,b)=>a.planned_date.localeCompare(b.planned_date)||a.ordinal-b.ordinal);
  const focus = focusSession();
  const done = sorted.filter(s=>s.status==='completed'), rest = sorted.filter(s=>s.status!=='completed');
  const group = done.length ? `<details class="card done-group session-fold" id="done-group"><summary>已走过 ${done.length} ${unitWord()}</summary><div class="session-body">${done.map(doneRow).join('')}</div></details>` : '';
  return group + rest.map(s=>sessionCard(s,focus&&s.id===focus.id)).join('');
}
function courseView(){
  if(!course)return `<p class="eyebrow">这段旅程</p><h1>一步一步，留下记录。</h1>${courseGuide()}${pathTrail(true)}${pastPages()}`;
  const pending = sessions.filter(s=>['planned','postponed'].includes(s.status)).length;
  return `<div class="page-heading"><div><p class="eyebrow">${escapeText(courseName())}${course.total_count?` · 共 ${course.total_count} ${unitWord()}`:''} ${recentAgentMark(activityData?.by_agent?.course)}</p><h1>一步一步，留下记录。</h1><p class="subtitle">${escapeText(scheduleSummary())}。已完成 ${count()} ${unitWord()} · ${pending} 条待确认预约。没有记录的日期不推算。</p></div><div><p class="path-count">${progressText()}</p>${progressBar()}</div></div>${syncNotice()}${generateNotice()}${pathTrail(true)}<form id="private-session-form" class="card"><h2>记下一次治疗</h2><div class="record-grid"><label class="field">记录内容<select name="action"><option value="schedule">添加预约</option><option value="record">补记实际完成</option></select></label><label class="field">日期<input type="date" name="date" required value="${TODAY}"></label></div><button type="submit" class="primary">保存日期</button></form><div class="section-title"><h2>预约与完成</h2></div>${sessions.length?sessionList():'<div class="empty">还没有本人填写的治疗记录。已提供的背景资料不会自动变成完成次数。</div>'}<p><a id="open-settings" href="#settings">修改疗程设置 →</a> · <a href="#history">查看修改记录 →</a></p>${pastPages()}`;
}

// ---- 疗程设置（最小界面）----
function courseFormMarkup(c,actions){
  const type = c?.treatment_type || '', unit = c?.count_unit || 'session', kind = c?.schedule_kind || '';
  const option = (value,label,current) => `<option value="${value}" ${current===value?'selected':''}>${label}</option>`;
  return `<form id="course-form" class="card"><div class="record-grid"><div class="field"><label for="course-type">治疗类型</label><select id="course-type" name="treatment_type" required><option value="" ${type?'':'selected'}>请选择</option>${option('radiotherapy','放疗',type)}${option('chemotherapy','化疗',type)}${option('other','其他治疗',type)}</select></div><div class="field"><label for="course-label">名称（可不填）</label><input id="course-label" name="label" maxlength="60" value="${escapeText(c?.label||'')}"></div><div class="field"><label for="course-unit">怎么计数</label><select id="course-unit" name="count_unit">${option('session','按次数',unit)}${option('cycle','按周期（每隔一段时间一轮）',unit)}</select></div><div class="field"><label for="course-total">总数（不知道就留空）</label><input id="course-total" name="total_count" type="number" min="1" max="200" value="${c?.total_count??''}"></div><fieldset class="field status-field"><legend>怎么排日期</legend><div class="radio-group"><label><input type="radio" name="schedule_kind" value="weekdays" ${kind==='weekdays'?'checked':''} required><span>按每周固定几天</span></label><label><input type="radio" name="schedule_kind" value="manual" ${kind==='manual'?'checked':''}><span>逐次自己添加预约</span></label></div></fieldset><fieldset class="field status-field" id="weekday-field" ${kind==='weekdays'?'':'hidden'}><legend>每周哪几天治疗</legend><div class="radio-group">${WEEKDAY_NAMES.map((name,i)=>`<label><input type="checkbox" name="weekday" value="${i}" ${c?.weekdays?.includes(i)?'checked':''}><span>${name}</span></label>`).join('')}</div></fieldset><div class="field"><label for="course-start">开始日（不知道就留空）</label><input id="course-start" name="start_date" type="date" value="${c?.start_date||''}"></div></div><p id="course-error" class="inline-error" role="alert"></p><div class="actions">${actions}</div></form>`;
}
function settingsView(){
  const c = course;
  return `<p class="eyebrow">设置 ${recentAgentMark(activityData?.by_agent?.course)}</p><h1>告诉我这段路怎么走。</h1><p class="subtitle">这些内容只保存在本机，之后随时可以改。不确定的就先留空，系统不会替你猜；改了设置不会悄悄改动已经排好的日期。</p>${courseFormMarkup(c,`<button type="submit" class="primary">保存设置</button><a class="button secondary" href="${c?'#course':'#today'}">先不改</a>`)}`;
}

// ---- 检验 ----
function fmt(value){return value===null?'待核对':Number(value.toFixed(1)).toString();}
function chart(rows){const points=rows.filter(r=>r.value!==null);if(!points.length)return `<div class="empty">${labRecords.length?'这一组还没有可绘制的值。缺失值不会画成 0。':'还没有检验记录。点上面的“录入一项结果”开始，之后这里会画出曲线。'}</div>`;const dates=rows.map(r=>Date.parse(r.date+'T00:00:00Z'));const minX=Math.min(...dates),maxX=Math.max(...dates);const values=points.map(r=>r.value);const low=Math.min(...values),high=Math.max(...values);const margin=Math.max((high-low)*0.25,Math.abs(high)*0.05,0.1);const minY=low-margin,maxY=high+margin;const x=r=>70+(maxX===minX?270:(Date.parse(r.date+'T00:00:00Z')-minX)/(maxX-minX)*540);const y=r=>190-(r.value-minY)/(maxY-minY)*140;let lines='';for(let i=1;i<rows.length;i++)if(rows[i-1].value!==null&&rows[i].value!==null)lines+=`<line x1="${x(rows[i-1])}" y1="${y(rows[i-1])}" x2="${x(rows[i])}" y2="${y(rows[i])}" stroke="#176450" stroke-width="2"/>`;return `<svg viewBox="0 0 660 260" role="img" aria-label="${escapeText(rows[0].metric)}，${escapeText(rows[0].unit)}，${escapeText(rows[0].lab)}，采样日期趋势；详细值见下方表格"><line x1="70" y1="210" x2="620" y2="210" stroke="#ccd8d0"/><text x="12" y="54">${fmt(maxY)}</text><text x="12" y="193">${fmt(minY)}</text>${lines}${points.map(r=>`<circle cx="${x(r)}" cy="${y(r)}" r="5" fill="#176450"><title>${escapeText(r.date)}：${r.value} ${escapeText(r.unit)}</title></circle><text x="${x(r)}" y="${y(r)-13}" text-anchor="middle">${r.value}</text>`).join('')}${[...new Set(rows.map(r=>r.date))].map(date=>`<text x="${x({date})}" y="236" text-anchor="middle">${date.slice(5)}</text>`).join('')}</svg>`;}
function labsView(){
  const groups=[...new Set(labRecords.map(labKey))];
  const rows=labRecords.filter(r=>labKey(r)===selectedLab).sort((a,b)=>a.date.localeCompare(b.date));
  const empty=!labRecords.length;
  const seriesSelect=empty?'<select id="lab-series" disabled><option>还没有检验记录</option></select>':`<select id="lab-series">${groups.map(key=>`<option value="${escapeText(key)}" ${key===selectedLab?'selected':''}>${JSON.parse(key).map(escapeText).join(' · ')}</option>`).join('')}</select>`;
  const body=empty?'<tr><td colspan="6">还没有检验记录，点“录入一项结果”开始。</td></tr>':rows.map(r=>`<tr><td>${r.date}${r.recorded_by==='agent'?' '+agentMark():''}</td><td>${r.value===null?'缺失':r.value}</td><td>${escapeText(r.unit)}</td><td>${r.low===null||r.high===null?'未记录':r.low+'—'+r.high}</td><td>${escapeText(r.lab)}</td><td><button class="text-button" data-edit-lab="${labRecords.indexOf(r)}">编辑</button>${addedLabRows.includes(r)?`<button class="text-button" data-undo-lab="${labRecords.indexOf(r)}">撤销新增</button>`:''}</td></tr>`).join('');
  const rhythm=(id,name)=>`<label>${name}间隔（天） <input type="number" id="${id}" min="1" max="365" inputmode="numeric" placeholder="尚未设置" value="${labRhythm[id==='rhythm-blood'?'blood':'chemistry']??''}"></label>`;
  return `<p class="eyebrow">检查与复诊 · 把想问的先记下来</p><h1>看见变化，也留住问题。</h1><p class="subtitle">按报告记录，带着问题复诊。曲线只整理数据，不判断病情。</p><section class="check-rhythm"><div><h2>检查节奏</h2><p class="small">按医院要求设置，尚未设置时不推算检查日期。</p></div>${rhythm('rhythm-blood','血常规')}${rhythm('rhythm-chemistry','生化')}</section><section class="lab-trend"><div class="section-title"><h2>我的检查记录</h2><button class="${empty?'primary':'secondary'}" data-open-lab>录入一项结果</button></div><label class="field">选择同一指标、单位和实验室${seriesSelect}</label><p class="small">横轴按实际采样日期间隔。缺失值处断线；不同实验室或单位分别展示，参考区间逐条保留。</p>${chart(rows)}<p class="small table-hint">${t('labs.swipe_hint')}</p><div class="table-scroll"><table><caption>原始记录 · 当前所选系列</caption><thead><tr><th>采样日期</th><th>结果</th><th>单位</th><th>报告参考区间</th><th>实验室</th><th>更正</th></tr></thead><tbody>${body}</tbody></table></div></section><form id="lab-form" class="lab-form" hidden><h2>照着报告，录入一项</h2><div class="record-grid"><label class="field">采样日期<input type="date" name="date" required max="${TODAY}" value="${TODAY}"></label><label class="field">指标名称（按报告填写）<input name="metric" required maxlength="40" value=""></label><label class="field">报告上的单位<input name="unit" required maxlength="30" value=""></label><label class="field">实验室<input name="lab" required maxlength="60" value=""></label><label class="field">结果（缺失可留空）<input type="number" step="any" min="0" name="value"></label><div class="field"><span>报告参考区间（可留空）</span><div class="range-fields"><input type="number" step="any" name="low" aria-label="参考区间下限" placeholder="下限"><input type="number" step="any" name="high" aria-label="参考区间上限" placeholder="上限"></div></div></div><p id="lab-error" class="inline-error" role="alert"></p><button class="primary" type="submit">保存这项结果</button> <button type="button" class="secondary" data-close-lab>先不填</button></form><section class="visit-note"><p class="eyebrow">下次见面想问什么</p><h2>不用现在想清楚，先写下来。</h2><form id="question-form"><label class="field"><span class="small">问题便签，只保存在本机，不发送</span><textarea id="question-note" name="question" maxlength="1200" placeholder="例如：这次报告上哪一项需要再确认？">${escapeText(questionNote)}</textarea></label><button class="primary" type="submit">保存问题便签</button></form></section>`;
}

// ---- 事件 ----
document.addEventListener('click',async event=>{const check=event.target.closest('[data-checkin]');if(check){const domain=check.dataset.checkin,value=check.dataset.value;if(todayRecord()[domain]===value){toast('已经记下了，不用重复打卡。');return;}await saveTodayChange({[domain]:value,...(domain==='meditation'&&value==='not_done'?{minutes:'0'}:{})},'记录今天的'+(domain==='exercise'?'活动':'冥想'));celebrationMessage=value==='done'?(domain==='exercise'?'今天的活动完成了，恭喜你给自己留了一份用心。':'冥想完成了，恭喜你留出这一段属于自己的时间。'):value==='partial'?'做了一点，也是一份真实的记录。今天这样就好。':'认真选择休息，也是在照顾自己。今天不用追赶。';render();toast(checkinLabels[value]+'，已同步到今天的完整记录。');}const clear=event.target.closest('[data-clear-checkin]');if(clear){await saveTodayChange({[clear.dataset.clearCheckin]:'unknown',...(clear.dataset.clearCheckin==='meditation'?{minutes:''}:{})},'更正今日打卡');celebrationMessage='';render();toast('已恢复未记录，可重新选择。');}if(event.target.closest('[data-open-lab]')){labEditIndex=null;document.querySelector('#lab-form').reset();document.querySelector('#lab-form').hidden=false;document.querySelector('#lab-form').scrollIntoView({block:'start'});}if(event.target.closest('[data-close-lab]'))document.querySelector('#lab-form').hidden=true;});
document.addEventListener('change',async event=>{const el=event.target;if(el.id==='lab-series'){selectedLab=el.value;render();}if(el.id==='rhythm-blood'||el.id==='rhythm-chemistry'){const key=el.id==='rhythm-blood'?'blood':'chemistry',value=el.value===''?null:Number(el.value);if(value!==null&&(!Number.isInteger(value)||value<1||value>365)){toast('请填 1 到 365 之间的天数，或留空。');el.value=labRhythm[key]??'';return;}labRhythm[key]=value;await persistAux();toast('检查节奏已更新，具体日期仍待确认。');}if(el.name==='schedule_kind'&&el.closest('#course-form'))document.querySelector('#weekday-field').hidden=el.value!=='weekdays';});
document.addEventListener('submit',async event=>{if(event.target.id==='question-form'){event.preventDefault();questionNote=new FormData(event.target).get('question');await persistAux();toast('问题便签已保存在本机，复诊前可再修改。');}if(event.target.id==='lab-form'){event.preventDefault();const f=Object.fromEntries(new FormData(event.target));const low=f.low===''?null:Number(f.low),high=f.high===''?null:Number(f.high);if(!f.metric.trim()||!f.unit.trim()||!f.lab.trim()||f.date>TODAY||(low===null)!==(high===null)||(low!==null&&low>high)){document.querySelector('#lab-error').textContent='请核对单位、实验室、采样日期；参考区间请成对填写，且下限不大于上限。';return;}const row={date:f.date,metric:f.metric.trim(),unit:f.unit.trim(),lab:f.lab.trim(),value:f.value===''?null:Number(f.value),low,high};if(labEditIndex!==null){const previous=labRecords[labEditIndex];labRecords[labEditIndex]=row;const addedIndex=addedLabRows.indexOf(previous);if(addedIndex!==-1)addedLabRows[addedIndex]=row;}else{labRecords.push(row);addedLabRows.push(row);}labEditIndex=null;selectedLab=labKey(row);await persistAux();addHistory('保存检查记录',`${row.date} · ${row.metric} · ${row.lab}`);render();toast('结果已记录，按对应单位和实验室单独展示。');}});
document.addEventListener('click',async event=>{const edit=event.target.closest('[data-edit-lab]');if(edit){labEditIndex=Number(edit.dataset.editLab);const row=labRecords[labEditIndex],form=document.querySelector('#lab-form');for(const key of ['date','metric','unit','lab','value','low','high'])form.elements[key].value=row[key]??'';form.hidden=false;form.scrollIntoView({block:'start'});}const undo=event.target.closest('[data-undo-lab]');if(undo){const index=Number(undo.dataset.undoLab),row=labRecords[index];confirmAction('撤销这条本轮新增记录？','只移除这一条结果，其余检查记录保留。',async()=>{labRecords.splice(index,1);addedLabRows.splice(addedLabRows.indexOf(row),1);if(!labRecords.some(r=>labKey(r)===selectedLab))selectedLab=labRecords.length?labKey(labRecords[0]):"";labEditIndex=null;await persistAux();render();toast('本次新增已撤销。');});}});

// 疗程设置：保存后回到旅程页；已排日期与新设置不一致时那里会出现“确认重排”。
// 读取疗程表单：设置页与问卷向导的“治疗方案”一块共用；返回 {payload} 或 {error}。
function readCourseForm(form){
  const data=new FormData(form);
  const kind=data.get('schedule_kind'),weekdays=kind==='weekdays'?data.getAll('weekday').map(Number).sort((a,b)=>a-b):[];
  if(!data.get('treatment_type'))return {error:'请选择治疗类型。'};
  if(!kind)return {error:'请选择怎么排日期。'};
  if(kind==='weekdays'&&!weekdays.length)return {error:'请至少选一个治疗星期，或改成“逐次自己添加预约”。'};
  const totalText=String(data.get('total_count')||'').trim(),total=totalText===''?null:Number(totalText);
  if(total!==null&&(!Number.isInteger(total)||total<1||total>200))return {error:'总数请填 1 到 200 之间的整数，或留空。'};
  return {payload:{treatment_type:data.get('treatment_type'),label:String(data.get('label')||'').trim()||null,count_unit:data.get('count_unit'),total_count:total,schedule_kind:kind,weekdays,start_date:data.get('start_date')||null,settings_version:settingsVersion}};
}
document.addEventListener('submit',async event=>{
  if(event.target.id!=='course-form'||event.target.dataset.wizard)return;
  event.preventDefault();
  const error=document.querySelector('#course-error');error.textContent='';
  const read=readCourseForm(event.target);
  if(read.error){error.textContent=read.error;return;}
  try{await api('course',read.payload);await refreshState();toast('疗程设置已保存。');navigate('course');}
  catch(failure){error.textContent=failure.message;if(failure.status===409)await refreshState();}
});
// 旅程页：按设置排出日期、确认重排、行内操作、新增预约。
document.addEventListener('click',async event=>{
  if(event.target.closest('[data-course-generate]')){
    try{await api('course/generate',{expected_revision:courseRevision});await refreshState();render();toast('已按设置排出日期。');}
    catch(failure){if(failure.status===409){await refreshState();render();}toast(failure.message);}
    return;
  }
  if(event.target.closest('[data-course-resync]')){
    confirmAction('重排还没发生的预约？','只调整“还没发生、也没手动改过期”的预约，按新的治疗星期和开始日顺延，不早于今天；已完成、已改期、已取消的都不动。',async()=>{
      const {treatment_type,label,count_unit,total_count,schedule_kind,weekdays,start_date}=course;
      await api('course',{treatment_type,label,count_unit,total_count,schedule_kind,weekdays,start_date,settings_version:settingsVersion,reschedule:true,expected_revision:courseRevision});
      await refreshState();render();toast('未发生的预约已按新设置重排。');
    });
    return;
  }
  const control=event.target.closest('[data-private-action]');
  if(control){
    const s=sessions.find(s=>s.id===Number(control.dataset.privateId));if(!s)return;
    const action=control.dataset.privateAction,date=document.getElementById('private-date-'+s.id)?.value||s.planned_date;
    confirmAction('确认更新这次记录？',`${s.planned_date} · ${({complete:'确认实际完成',correct:'更正误记完成',reschedule:'调整预约日期',cancel:'取消预约',restore:'恢复预约'})[action]}${['complete','reschedule'].includes(action)?'：'+date:''}`,()=>persistSession(s,action,date));
    return;
  }
  const jump=event.target.closest('[data-private-jump]');
  if(jump){const target=document.getElementById('private-session-'+jump.dataset.privateJump);if(target){for(let el=target;el;el=el.parentElement?.closest('details'))el.open=true;target.scrollIntoView({block:'start'});(target.querySelector('summary')||target).focus({preventScroll:true});}}
});
document.addEventListener('submit',async event=>{
  if(event.target.id!=='private-session-form')return;
  event.preventDefault();
  const form=event.target,data=Object.fromEntries(new FormData(form));
  const save=async()=>{await api('sessions',{...data,expected_revision:courseRevision});await refreshState();render();toast('日期已保存。');};
  try{if(data.action==='record')confirmAction('补记实际完成？',`仅记录你确认的 ${data.date} 这一次。`,save);else await save();}catch(error){toast(error.message);}
});
