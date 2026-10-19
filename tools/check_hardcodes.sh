#!/usr/bin/env bash
# 结构性反向检查：疗程总数、日期、星期、检查节奏只能来自配置或用户输入，
# 不得以字面量写进 waybeside/ 的源码。命中即缺陷。
# 允许清单：每行一个 "文件:正则"，仅在确有必要时添加（当前为空）。
set -u
cd "$(dirname "$0")/.."
patterns=(
  # 任意 ISO 日期字面量
  -e '[0-9]{4}-[0-9]{2}-[0-9]{2}'
  # 用数字字面量比较星期
  -e '(weekday|getDay)\(\)[[:space:]]*(>=|<=|===|!==|==|!=|<|>)[[:space:]]*[0-9]'
  # 疗程总数 / 长度 / 次数以两位及以上数字字面量赋值
  -e '(target|total|length|count)[A-Za-z_]*[[:space:]]*[:=][[:space:]]*[0-9]{2,}'
  # 固定次数的循环或格数
  -e 'repeat\([[:space:]]*[0-9]' -e 'range\([[:space:]]*[0-9]+[[:space:]]*,[[:space:]]*[0-9]{2,}'
  # 内联的纯数字数组（如固定检查节奏）
  -e '\[[[:space:]]*[0-9]+[[:space:]]*,[[:space:]]*[0-9]+[[:space:]]*[],]'
  # 界面文字中写死的数量
  -e '/ [0-9]+ 次' -e '共 [0-9]+ 格' -e '[0-9]+ 次(放|化|治)'
)
if command -v rg >/dev/null 2>&1; then
  out=$(rg -n "${patterns[@]}" waybeside/ -g '*.py' -g '*.js' -g '*.html' 2>/dev/null)
else
  out=$(grep -rnE --include='*.py' --include='*.js' --include='*.html' "${patterns[@]}" waybeside/ 2>/dev/null)
fi
if [ -n "$out" ]; then
  printf '%s\n' "$out"
  echo "check_hardcodes: FAIL ($(printf '%s\n' "$out" | wc -l | tr -d ' ') 命中)" >&2
  exit 1
fi
echo "check_hardcodes: OK (0 命中)"
