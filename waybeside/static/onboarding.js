'use strict';
// 首次启动流程与“可编辑”的个人设置：免责确认 → 四块问卷向导 → 主界面；完成后在设置页修改；
// 以及“导出给助手”的逐项共享开关。
// 产品原则：问卷答案留在后台，前端只放可编辑、可交互的内容——这里没有任何只读总览视图，
// 读回来的值只会出现在表单控件（输入框、选项、可删除的条目）里；唯一的文字展示是问候语里的昵称。
// 用户文字一律走 textContent / value，不参与 HTML 解析。
let settingsData = null, shareData = [], wizardError = '';
const BLOCK_ORDER = ['basic', 'treatment', 'diet', 'habit'];
const BLOCK_TITLES = Object.fromEntries(['basic', 'treatment', 'diet', 'habit'].map(b => [b, t(`wizard.title.${b}`)]));
const AGE_OPTIONS = [['under_18', '18 岁以下'], ['18_39', '18–39 岁'], ['40_64', '40–64 岁'], ['65_plus', '65 岁以上']];
const SHARE_CATEGORY_LIST = ['menu_library', 'adoption_state', 'preferences', 'recent_meal_status', 'requirement_tags', 'age_band', 'course_progress'];
const shareItems = () => SHARE_CATEGORY_LIST.map(c => [c, t(`share.${c}.title`), c === 'menu_library' ? tm('share.menu_library.copy') : t(`share.${c}.copy`)]);
const DISCLAIMER_LINE = t('disclaimer.line');

async function loadSettings() {
  settingsData = await (await api('settings')).json();
  shareData = await (await api('agent-share')).json();
}

// ---- 与其他页面的小接口 ----
function greetingText() {
  const b = settingsData?.basic;
  if (!b || !b.nickname) return '';
  return `${b.helper_mode === 'with_helper' ? '你们好' : '你好'}，${escapeText(b.nickname)} · `;
}
function helperHint() {
  return settingsData?.basic?.helper_mode === 'with_helper'
    ? '<p class="small">有人帮忙一起记也可以；请按这位使用者的实际情况填，拿不准的就留在“还没记录”。</p>' : '';
}
// 习惯默认值只以“可点击的快捷操作”出现，不做只读展示：点一下才记（用户主动，可随时更正），绝不自动记成已完成。
function habitHint(domain) {
  const m = settingsData?.habit?.meditation_usual;
  if (domain === 'meditation' && m && !m.unknown && m.minutes != null && todayRecord().meditation === 'unknown')
    return `<p><button class="secondary" data-habit-meditation="${Number(m.minutes)}">按平时习惯记一次（约 ${Number(m.minutes)} 分钟）</button></p>`;
  return '';
}
document.addEventListener('click', async event => {
  const quick = event.target.closest('[data-habit-meditation]');
  if (!quick) return;
  await saveTodayChange({meditation: 'done', minutes: String(Number(quick.dataset.habitMeditation))});
  render(); toast('已按平时习惯记下，不对可以改。');
});
function exportView() { return ''; }

// ---- 小积木 ----
function field(labelText, control, hint) {
  const wrap = element('div', undefined, 'field');
  if (control.id) { const l = element('label', labelText); l.htmlFor = control.id; wrap.append(l); }
  else wrap.append(element('span', labelText));
  wrap.append(control);
  if (hint) wrap.append(element('small', hint));
  return wrap;
}
function textInput(id, value, max, extra = {}) {
  const i = element('input'); i.id = id; i.type = extra.type || 'text'; i.value = value ?? '';
  if (max) i.maxLength = max;
  for (const [k, v] of Object.entries(extra)) if (k !== 'type') i.setAttribute(k, v);
  return i;
}
function radios(name, legend, options, current, hint) {
  const set = element('fieldset', undefined, 'field status-field'); set.append(element('legend', legend));
  const group = element('div', undefined, 'radio-group');
  for (const [value, label] of options) {
    const l = element('label'); const i = element('input'); i.type = 'radio'; i.name = name; i.value = value; i.id = `${name}-${value}`;
    if (value === current) i.checked = true;
    l.append(i, element('span', label)); group.append(l);
  }
  set.append(group); if (hint) set.append(element('small', hint));
  return set;
}
function checkLine(id, label, checked) {
  const l = element('label', undefined, 'menu-check'); const i = element('input'); i.type = 'checkbox'; i.id = id; i.checked = !!checked;
  l.append(i, element('span', label)); return l;
}
const picked = (root, name) => root.querySelector(`input[name="${name}"]:checked`)?.value;
const UNKNOWN = () => ({unknown: true});

// ---- 四块表单：build 返回 {node, read}，read() 返回 answers，输入有问题抛 Error ----
function buildBasic(d = {}) {
  const node = element('div');
  const nick = textInput('q-nickname', d.nickname, 20);
  const age = element('select'); age.id = 'q-age';
  for (const [v, l] of [['', '不填'], ['unknown', '不知道 / 不想说'], ...AGE_OPTIONS]) { const o = element('option', l); o.value = v; age.append(o); }
  age.value = d.age_band?.unknown ? 'unknown' : (d.age_band || '');
  node.append(
    field('怎么称呼你？（可不填；不用写真实姓名）', nick),
    radios('q-helper', '是你自己在用，还是有人帮忙一起记？', [['self', '我自己用'], ['with_helper', '有他人帮我一起记']], d.helper_mode || 'self'),
    field(tmx('wizard.age_hint'), age));
  return {node, read() {
    const a = {helper_mode: picked(node, 'q-helper'), locale: 'zh-CN'};
    if (!a.helper_mode) throw new Error('请选择是谁在用。');
    if (nick.value.trim()) a.nickname = nick.value.trim();
    if (age.value === 'unknown') a.age_band = UNKNOWN(); else if (age.value) a.age_band = age.value;
    return a;
  }};
}

// 可增删的文字条目（自加标签、过敏提醒）：条目本身就是可编辑控件。
function editableList(items, max, onChange) {
  const box = element('div', undefined, 'editable-list');
  const draw = () => {
    box.replaceChildren();
    items.forEach((item, index) => {
      const row = element('div', undefined, 'editable-row');
      row.append(element('span', item.label ?? item));
      const del = element('button', '移除', 'secondary'); del.type = 'button';
      del.setAttribute('aria-label', '移除 ' + (item.label ?? item));
      del.onclick = () => { items.splice(index, 1); draw(); onChange?.(); };
      row.append(del); box.append(row);
    });
  };
  draw();
  return {box, redraw: draw};
}
function buildDiet(d = {}, builtin = {}) {
  const node = element('div');
  const custom = (d.custom_tags || []).map(t => ({...t}));
  const chosen = new Set(Array.isArray(d.requirement_tags) ? d.requirement_tags : []);
  const allergies = Array.isArray(d.allergy_reminders) ? [...d.allergy_reminders] : [];
  const tagsUnknown = checkLine('q-tags-unknown', '我还不确定要选哪些（先不选）', d.requirement_tags?.unknown);
  const tagBox = element('div', undefined, 'tag-choices');
  const drawTags = () => {
    tagBox.replaceChildren();
    const keep = new Set([...tagBox.querySelectorAll('input:checked')].map(i => i.dataset.tag));
    for (const [id, label] of [...Object.entries(builtin), ...custom.map(t => [t.id, t.label])]) {
      const line = checkLine('q-tag-' + id, label, chosen.has(id)); line.querySelector('input').dataset.tag = id;
      line.querySelector('input').addEventListener('change', e => e.target.checked ? chosen.add(id) : chosen.delete(id));
      if (!builtin[id]) {
        const del = element('button', '移除', 'secondary'); del.type = 'button'; del.setAttribute('aria-label', '移除标签 ' + label);
        del.onclick = () => { custom.splice(custom.findIndex(t => t.id === id), 1); chosen.delete(id); drawTags(); };
        line.append(del);
      }
      tagBox.append(line);
    }
  };
  drawTags();
  const newTag = textInput('q-newtag', '', 20);
  const addTag = element('button', '加一个标签', 'secondary'); addTag.type = 'button'; addTag.id = 'q-addtag';
  const addPendingTag = () => {
    const label = newTag.value.trim(); if (!label) return;
    if (custom.some(t => t.label === label)) { newTag.value = ''; return; }
    let n = custom.length + 1; while (custom.some(t => t.id === 'my_tag_' + n)) n++;
    const id = 'my_tag_' + n; custom.push({id, label}); chosen.add(id); newTag.value = ''; drawTags();
  };
  addTag.onclick = addPendingTag;
  const allergyUnknown = checkLine('q-allergy-unknown', '我不知道 / 先不填', d.allergy_reminders?.unknown);
  const aList = editableList(allergies, 30);
  const newAllergy = textInput('q-newallergy', '', 20);
  const addAllergy = element('button', '加一条', 'secondary'); addAllergy.type = 'button'; addAllergy.id = 'q-addallergy';
  const addPendingAllergy = () => { const v = newAllergy.value.trim(); if (v && !allergies.includes(v)) allergies.push(v); newAllergy.value = ''; aList.redraw(); };
  addAllergy.onclick = addPendingAllergy;
  const note = element('textarea'); note.id = 'q-freenote'; note.maxLength = 500; note.value = d.free_note || '';
  const s1 = element('fieldset', undefined, 'field'); s1.append(element('legend', '你想要的饮食要求（自己勾；这些只是筛选用的标签，伴程不会替你解释它们的医学含义）'), tagBox, tagsUnknown,
    field('自己加一个标签', newTag), addTag);
  const s2 = element('fieldset', undefined, 'field'); s2.append(element('legend', tmx('wizard.allergy_legend')), aList.box, allergyUnknown,
    field('写一条', newAllergy), addAllergy);
  node.append(s1, s2, field(tmx('wizard.note_label'), note));
  return {node, read() {
    // 写在框里、但没点“加一条 / 加一个标签”的内容，保存时一并算上，不静默丢掉。
    addPendingTag(); addPendingAllergy();
    const a = {custom_tags: custom.map(t => ({...t}))};
    a.requirement_tags = tagsUnknown.querySelector('input').checked ? UNKNOWN() : [...chosen];
    a.allergy_reminders = allergyUnknown.querySelector('input').checked ? UNKNOWN() : [...allergies];
    a.free_note = note.value;
    return a;
  }};
}
function buildHabit(d = {}) {
  const node = element('div');
  const med = d.meditation_usual || {};
  const time = textInput('q-medtime', med.time || '', 5, {type: 'time'});
  const minutes = textInput('q-medmin', med.minutes ?? '', 3, {type: 'number', min: 0, max: 180});
  const medUnknown = checkLine('q-med-unknown', '我不知道 / 没有固定习惯', med.unknown);
  node.append(
    radios('q-sleep', '平时睡眠', [['regular', '比较规律'], ['irregular', '不太规律'], ['unknown', '不知道 / 说不清']], d.sleep_usual?.unknown ? 'unknown' : d.sleep_usual),
    radios('q-activity', '平时活动', [['none', '几乎不动'], ['light', '轻度（比如散步）'], ['regular', '规律运动'], ['unknown', '不知道 / 说不清']], d.activity_usual?.unknown ? 'unknown' : d.activity_usual),
    field('平时冥想的时间（可不填）', time), field('平时每次冥想多少分钟（可不填，0–180）', minutes), medUnknown);
  return {node, read() {
    const a = {};
    const sleep = picked(node, 'q-sleep'), activity = picked(node, 'q-activity');
    if (sleep) a.sleep_usual = sleep;
    if (activity) a.activity_usual = activity === 'unknown' ? UNKNOWN() : activity;
    if (medUnknown.querySelector('input').checked) a.meditation_usual = UNKNOWN();
    else if (time.value || minutes.value !== '') {
      const m = minutes.value === '' ? null : Number(minutes.value);
      if (m !== null && (!Number.isInteger(m) || m < 0 || m > 180)) throw new Error('冥想分钟数请填 0 到 180 的整数。');
      a.meditation_usual = {time: time.value || null, minutes: m};
    }
    return a;
  }};
}
function buildForm(block) {
  if (block === 'basic') return buildBasic(settingsData.basic);
  if (block === 'diet') return buildDiet(settingsData.diet, settingsData.builtin_tags);
  return buildHabit(settingsData.habit);
}

async function saveBlock(block, answers, skipped = false) {
  await api('onboarding/' + block, {answers, version: settingsData.versions[block], skipped});
  await refreshState();
}

// ---- 首次启动门 ----
function nextBlock() { return BLOCK_ORDER.find(b => !settingsData.completed[b]); }
// 首次启动门：免责 -> 选使用方式 -> （选了“全部打理”时先问问卷谁来填）-> 问卷向导。设置页的“换一种”也走同一个选择页。
let changingMode = false;
const HANDOVER_KEY = 'waybeside.questionnaire_by';
const handoverChoice = () => { try { return sessionStorage.getItem(HANDOVER_KEY); } catch (e) { return null; } };
function gateStage() {
  if (!gateInfo.disclaimer) return 'disclaimer';
  if (assistantMode === 'unset' || changingMode) return 'mode';
  if (!gateInfo.onboarded) {
    if (assistantMode === 'full' && !handoverChoice()) return 'handover';
    if (assistantMode === 'full' && handoverChoice() === 'assistant') return null;
    return 'wizard';
  }
  return null;
}
function renderGate() {
  const gate = document.querySelector('#gate');
  if (!settingsData && gateInfo.disclaimer && assistantMode !== 'unset') {
    // 设置数据还没读到：向导和主页都画不了，先不出门层，只显示加载态，读到后会重绘。
    document.body.classList.remove('gating'); gate.hidden = true; gate.replaceChildren();
    main.textContent = '正在读取本机记录…'; return true;
  }
  const stage = gateStage();
  document.body.classList.toggle('gating', !!stage);
  gate.hidden = !stage;
  if (!stage) { gate.replaceChildren(); return false; }
  if (stage === 'disclaimer') drawDisclaimer(gate);
  else if (stage === 'mode') drawMode(gate);
  else if (stage === 'handover') drawHandover(gate);
  else drawWizard(gate);
  return true;
}
// 选择页与设置页共用的“三问”。
function modeLines(mode) {
  const box = element('div', undefined, 'mode-lines');
  for (const q of ['sees', 'does', 'you']) {
    const line = element('p', undefined, 'mode-line');
    line.append(element('strong', t('mode.q.' + q)), t(`mode.${mode}.${q}`));
    box.append(line);
  }
  return box;
}
function drawMode(gate) {
  gate.dataset.stage = 'mode'; delete gate.dataset.block;
  const box = element('section', undefined, 'gate-box mode-box');
  box.append(element('h1', t('mode.title')), element('p', t('mode.intro'), 'subtitle'));
  const set = element('fieldset', undefined, 'mode-choices'); set.append(element('legend', t('mode.title'), 'sr-only'));
  const next = element('button', t('mode.continue'), 'primary'); next.type = 'button'; next.id = 'mode-continue'; next.disabled = true;
  for (const mode of ['full', 'menu_only']) {
    const option = element('label', undefined, 'mode-option'); option.htmlFor = 'mode-' + mode; option.id = 'mode-option-' + mode;
    const radio = element('input'); radio.type = 'radio'; radio.name = 'assistant-mode'; radio.value = mode; radio.id = 'mode-' + mode;
    radio.onchange = () => { next.disabled = false; };
    const body = element('div', undefined, 'mode-body');
    body.append(element('strong', t(`mode.${mode}.name`), 'mode-name'), modeLines(mode));
    option.append(radio, body); set.append(option);
  }
  next.onclick = async () => {
    const picked = box.querySelector('input[name=assistant-mode]:checked')?.value; if (!picked) return;
    next.disabled = true;
    try {
      await api('assistant-mode', {mode: picked, version: assistantModeVersion});
      const wasChange = changingMode; changingMode = false;
      await refreshState(); if (wasChange) toast(t('mode.saved')); render();
    } catch (e) { next.disabled = false; toast(e.message); if (e.status === 409) { await refreshState(); render(); } }
  };
  const actions = element('div', undefined, 'actions'); actions.append(next);
  if (changingMode) { const cancel = element('button', t('mode.cancel'), 'secondary'); cancel.type = 'button'; cancel.id = 'mode-cancel'; cancel.onclick = () => { changingMode = false; render(); }; actions.append(cancel); }
  box.append(set, element('p', t('mode.tip'), 'note neutral mode-tip'), actions);
  gate.replaceChildren(box);
  box.querySelector('input')?.focus({preventScroll: true});
}
function drawHandover(gate) {
  gate.dataset.stage = 'handover'; delete gate.dataset.block;
  const box = element('section', undefined, 'gate-box');
  box.append(element('h1', t('mode.handover.title')), element('p', t('mode.handover.copy'), 'subtitle'));
  const choose = value => () => { try { sessionStorage.setItem(HANDOVER_KEY, value); } catch (e) { /* 不能保存就只对本次有效 */ } if (value === 'assistant') navigate('today'); render(); };
  const actions = element('div', undefined, 'actions');
  const self = element('button', t('mode.handover.self'), 'primary'); self.type = 'button'; self.id = 'handover-self'; self.onclick = choose('self');
  const later = element('button', t('mode.handover.assistant'), 'secondary'); later.type = 'button'; later.id = 'handover-assistant'; later.onclick = choose('assistant');
  actions.append(self, later); box.append(actions); gate.replaceChildren(box);
}
function drawDisclaimer(gate) {
  gate.dataset.stage = 'disclaimer';
  const box = element('section', undefined, 'gate-box');
  box.append(
    element('p', t('disclaimer.eyebrow'), 'eyebrow'),
    element('h1', t('disclaimer.title')),
    element('p', t('disclaimer.p1')),
    element('p', t('disclaimer.p2')),
    element('p', t('disclaimer.p3')),
    element('p', t('disclaimer.p4')));
  const ok = element('button', t('disclaimer.accept'), 'primary'); ok.id = 'accept-disclaimer'; ok.type = 'button';
  ok.onclick = async () => {
    ok.disabled = true;
    try { await api('disclaimer', {accepted: true}); await refreshState(); render(); }
    catch (e) { ok.disabled = false; toast(e.message); }
  };
  box.append(ok); gate.replaceChildren(box);
}
function drawWizard(gate) {
  const block = nextBlock() || 'habit', index = BLOCK_ORDER.indexOf(block);
  gate.dataset.stage = 'wizard'; gate.dataset.block = block;
  const box = element('section', undefined, 'gate-box');
  box.append(element('p', t('wizard.step', {index: index + 1, total: BLOCK_ORDER.length, title: BLOCK_TITLES[block]}), 'eyebrow'));
  const intro = [t(`wizard.${block}.h`), t(`wizard.${block}.p`)];
  box.append(element('h1', intro[0]), element('p', intro[1], 'subtitle'));
  let form, reader;
  if (block === 'treatment') {
    // 复用设置页的疗程表单（同一份标记、同一个读取函数），只是提交走问卷向导的入口。
    const holder = element('div'); holder.innerHTML = courseFormMarkup(course, '');
    hydrateText(holder);
    form = holder.querySelector('form'); form.dataset.wizard = '1';
    form.querySelector('.actions')?.remove();
    reader = () => { const r = readCourseForm(form); if (r.error) throw new Error(r.error); return r.payload; };
  } else {
    form = element('form'); form.id = 'wizard-form';
    const built = buildForm(block); form.append(built.node); reader = built.read;
  }
  form.querySelector('#course-error')?.remove();
  const error = element('p', wizardError, 'inline-error'); error.id = 'wizard-error'; error.setAttribute('role', 'alert');
  const actions = element('div', undefined, 'actions');
  const next = element('button', index === BLOCK_ORDER.length - 1 ? '完成，进入伴程' : '下一步', 'primary'); next.type = 'submit'; next.id = 'wizard-next';
  const skip = element('button', '先跳过这一步', 'secondary'); skip.type = 'button'; skip.id = 'wizard-skip';
  actions.append(next, skip);
  form.append(error, actions);
  const run = async (skipped) => {
    wizardError = ''; error.textContent = '';
    next.disabled = skip.disabled = true;
    try {
      await saveBlock(block, skipped ? {} : reader(), skipped);
      if (!gateInfo.onboarded) { render(); return; }
      navigate('today'); render();
    } catch (e) {
      error.textContent = e.message; next.disabled = skip.disabled = false;
      if (e.status === 409) { await refreshState(); render(); }
    }
  };
  form.onsubmit = e => { e.preventDefault(); run(false); };
  skip.onclick = () => run(true);
  box.append(form); gate.replaceChildren(box);
  box.querySelector('input,select,textarea,button')?.focus({preventScroll: true});
}

// ---- 设置页：问卷三块的可编辑表单（疗程表单仍是 journey.js 的 settingsView）----
function settingsSections() {
  const holder = element('div', undefined, 'settings-sections');
  for (const block of ['basic', 'diet', 'habit']) {
    const card = element('form', undefined, 'card'); card.id = `settings-${block}`;
    const heading = element('h2', BLOCK_TITLES[block]);
    if (activityData?.by_agent?.questionnaire?.[block]) { heading.append(' ', element('span', t('mark.recent'), 'tag agent-mark')); }
    card.append(heading);
    const built = buildForm(block); card.append(built.node);
    const error = element('p', '', 'inline-error'); error.setAttribute('role', 'alert');
    const save = element('button', '保存', 'primary'); save.type = 'submit'; save.id = `save-${block}`;
    card.append(error, element('div', undefined, 'actions')); card.lastChild.append(save);
    card.onsubmit = async e => {
      e.preventDefault(); error.textContent = '';
      try { await saveBlock(block, built.read()); toast('已保存。'); render(); }
      catch (failure) { error.textContent = failure.message; if (failure.status === 409) { await refreshState(); render(); } }
    };
    holder.append(card);
  }
  return holder;
}

// 老库升级后被设为“我自己记”：提示一次“可以在设置里改”。人点过任一按钮就不再提示（服务端记一次）。
function renderUpgradeNotice() {
  const box = document.querySelector('#upgrade-notice'); if (!box) return;
  box.hidden = !(upgradeNotice && assistantMode === 'menu_only');
  if (box.hidden) { box.replaceChildren(); return; }
  if (box.firstChild) return;
  const done = async go => { try { await api('upgrade-notice', {}); upgradeNotice = false; renderUpgradeNotice(); if (go) navigate('settings'); } catch (e) { toast(e.message); } };
  const open = element('button', t('mode.upgrade_open'), 'secondary'); open.type = 'button'; open.id = 'upgrade-open'; open.onclick = () => done(true);
  const ok = element('button', t('mode.upgrade_dismiss'), 'secondary'); ok.type = 'button'; ok.id = 'upgrade-dismiss'; ok.onclick = () => done(false);
  const actions = element('div', undefined, 'actions'); actions.append(open, ok);
  box.append(element('p', t('mode.upgrade_notice')), actions);
}

// 设置页「AI 助手」一节：用选择页同样的三问写出当前方式；换一种必须经过同一个选择页。
const activityLabel = kind => { const key = 'activity.' + kind; return Object.hasOwn(I18N, key) ? t(key) : (Object.hasOwn(I18N, 'activity.' + kind.split('.')[0]) ? t('activity.' + kind.split('.')[0]) : t('activity.other')); };
function assistantSection() {
  const box = element('section', undefined, 'card settings-entry assistant-section'); box.id = 'assistant-section';
  box.append(element('h2', t('settings.assistant.title')), element('h3', t(`mode.${assistantMode}.name`), 'mode-name'), modeLines(assistantMode));
  const actions = element('div', undefined, 'actions'); const change = element('button', t('settings.assistant.change'), 'secondary'); change.type = 'button'; change.id = 'assistant-change';
  change.onclick = () => { changingMode = true; render(); };
  actions.append(change); box.append(actions, element('p', t('settings.assistant.switch_note'), 'small'));
  if (assistantMode === 'full' || (activityData?.recent || []).length) {
    const recent = element('div'); recent.id = 'assistant-recent';
    recent.append(element('h3', t('settings.assistant.recent_title')), element('p', t('settings.assistant.recent_note'), 'small'));
    const rows = activityData?.recent || [];
    if (!rows.length) recent.append(element('p', t('settings.assistant.recent_empty')));
    else { const list = element('ul', undefined, 'assistant-recent-list'); for (const row of rows) { const item = element('li', `${activityLabel(row.kind)} · `); item.append(element('span', localDate(new Date(row.at)), 'nowrap')); list.append(item); } recent.append(list); }
    box.append(recent);
  }
  return box;
}

// 设置页里的“导出给助手”入口区块（主导航不再放它）。
function exportEntry() {
  const undecided = shareData.filter(r => r.version === 1).length;
  const box = element('section', undefined, 'card settings-entry'); box.id = 'export-entry';
  box.append(element('h2', t('settings.export.title')),
    element('p', tm('settings.export.copy')));
  if (undecided && assistantMode !== 'full') box.append(element('p', t('settings.export.undecided', {count: undecided}), 'small'));
  const actions = element('div', undefined, 'actions'); const link = element('a', t('settings.export.open'), 'button secondary');
  link.href = '#export'; link.id = 'open-export'; actions.append(link); box.append(actions);
  return box;
}

// ---- 导出给助手：七类逐项开关，默认全关；第一次进来逐项决定 ----
function exportBack() { const a = element('a', t('export.back'), 'text-button'); a.href = '#settings'; a.id = 'export-back'; return a; }
function exportPage() {
  const box = element('section', undefined, 'menu-section'); box.id = 'export-page';
  const undecided = shareData.filter(r => r.version === 1);
  box.append(element('p', tm('export.eyebrow'), 'eyebrow'), element('h1', t('export.title')), exportBack(),
    element('p', tm('export.intro'), 'menu-state'),
    element('p', tmx('export.never'), 'small'));
  if (undecided.length && assistantMode !== 'full') {
    const guide = element('div', undefined, 'note neutral'); guide.id = 'share-guide'; guide.setAttribute('role', 'status');
    guide.append(element('strong', `第一次来这里？下面还有 ${undecided.length} 项没决定，请一项一项来。`),
      element('p', '不想给的点“不给”就行，它会保持关闭。', 'small'));
    box.append(guide);
  }
  for (const [category, title, copy] of shareItems()) {
    const row = shareData.find(r => r.category === category); if (!row) continue;
    const decided = row.version > 1;
    const item = element('article', undefined, 'history-item'); item.id = 'share-' + category; item.dataset.category = category;
    const state = element('span', !decided ? '还没决定' : row.enabled ? '已开启' : '已关闭', 'tag'); state.dataset.state = !decided ? 'undecided' : row.enabled ? 'on' : 'off';
    const head = element('h3', title + ' '); head.append(state);
    item.append(head, element('p', copy));
    const actions = element('div', undefined, 'actions');
    const set = async enabled => {
      try { await api('agent-share', {category, enabled, version: row.version}); await loadSettings(); render(); }
      catch (e) { await loadSettings(); render(); toast(e.message); }
    };
    if (!decided) {
      const keep = element('button', '不给（保持关闭）', 'secondary'); keep.type = 'button'; keep.dataset.share = 'off'; keep.onclick = () => set(false);
      const open = element('button', '给助手看', 'secondary'); open.type = 'button'; open.dataset.share = 'on'; open.onclick = () => set(true);
      actions.append(keep, open);
    } else {
      const flip = element('button', row.enabled ? '关闭' : '开启', 'secondary'); flip.type = 'button'; flip.dataset.share = row.enabled ? 'off' : 'on'; flip.onclick = () => set(!row.enabled);
      actions.append(flip);
    }
    item.append(actions); box.append(item);
  }
  return box;
}

// ---- 挂到页面 ----
const decorateBeforeOnboarding = decorateLocal;
decorateLocal = function (current) {
  decorateBeforeOnboarding(current);
  if (current === 'settings' && settingsData) { main.append(assistantSection()); main.append(settingsSections()); main.append(exportEntry()); }
  if (current === 'export' && settingsData) { main.replaceChildren(exportPage()); }
  const foot = document.querySelector('#page-disclaimer');
  if (foot) { foot.textContent = DISCLAIMER_LINE; foot.hidden = !['plan', 'baseline', 'export', 'rules'].includes(current); }
};
