'use strict';
// 界面文案抽取机制：文案在 waybeside/i18n/zh-CN.json，由服务端经 /i18n-data.js 注入 window.I18N。
// t(key, {name: value}) 取文案并替换 {name} 占位；缺 key 时返回 key 本身（测试会扫描所有 key 必须存在）。
// 静态标记用 data-i18n="key"（文本）与 data-i18n-aria-label="key"（无障碍名）。
const I18N = window.I18N || {};
function t(key, vars) {
  const text = Object.prototype.hasOwnProperty.call(I18N, key) ? I18N[key] : key;
  return vars ? text.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? String(vars[name]) : m)) : text;
}
function applyI18n(root = document) {
  root.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll('[data-i18n-aria-label]').forEach(el => { el.setAttribute('aria-label', t(el.dataset.i18nAriaLabel)); });
}
document.title = t('app.title');
applyI18n();
