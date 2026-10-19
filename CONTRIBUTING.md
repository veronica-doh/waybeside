# 参与贡献（CONTRIBUTING）

欢迎提 issue 和 PR。动手前请读 [README.md](README.md) 与 [DISCLAIMER.md](DISCLAIMER.md)。

## 三条硬原则

1. **只用虚构数据。** 测试、示例、截图、issue 里都不要出现真实的姓名、病历、检验结果、医院或邮箱。示例文件须明确标注虚构（规则文件里 `"fictional": true`）。
2. **只放机制，不放规则内容。** 程序不内置任何医学、营养或过敏规则，也不带真实菜单或检验参考值；规则由用户或其营养师通过规则文件提供。PR 不应加入“某病该吃什么”之类的内置内容。
3. **不扩大数据面。** 新字段是否进入导出、资料包或备份，必须在 PR 里说明，并更新 [PRIVACY.md](PRIVACY.md) 与对应测试（`tests/test_no_leak_sentinel.py`）。

## 开发环境

```sh
python3.11 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt     # Windows: .venv\Scripts\pip
npm ci && npx playwright install chromium         # 仅浏览器测试需要
```

## 提交前自检

```sh
.venv/bin/python -m pytest -q
tools/check_hardcodes.sh                          # 必须输出 0 命中
node tests/browser-private-ui.cjs                 # 其余 tests/*.cjs 见 tests/README.md
```

## 界面文案

用户可见文字放在 `waybeside/i18n/zh-CN.json`，前端用 `t('key')` 或 `data-i18n="key"` 引用；`tests/test_i18n.py` 会检查 key 是否齐全。新增文案请走这条路径；历史上尚未迁移的文案欢迎逐步迁移。

## 视觉与可用性

面向不熟悉电脑的用户（包括老年人）：正文不小于 18px，按钮高度不小于 56px，不用蓝色与渐变，主色玉绿 `#176450`。改界面请附虚构数据截图。

## 提交信息与署名

项目署名为 "WayBeside contributors"。请勿在代码或文档里写入个人姓名或邮箱；提交时建议使用 GitHub 提供的 noreply 邮箱。贡献即同意以 Apache-2.0 授权。
