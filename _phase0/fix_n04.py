# -*- coding: utf-8 -*-
"""修 N04 prompt 里被 \n 转义坑破坏的字符串。"""
import pathlib
import re

p = pathlib.Path(r'F:\WorkBuddy AI\Novel Platform\novel-workbench\scripts\pipeline_spec.py')
t = p.read_text(encoding='utf-8')

# 定位破坏区：从 "'  {\"verdict\"" 开始，到 "才标 B" 结束（含其后残缺引号）
start = t.find("'  {\"verdict\":\"pass|block\"")
assert start > 0, '未找到破坏区起点'
end_marker = '才标 B'
e = t.find(end_marker, start)
assert e > 0, '未找到破坏区终点'
e = e + len(end_marker)
# 吞掉后面残留的引号/换行，直到下一个正常行
tail = t[e:]
m = re.match(r'[^\n]*\n\s*\n', tail)
# 找到下一行的 "quote 必须是报告" 起点
q = tail.find('"quote ')
if q >= 0:
    e = e + q
else:
    # 兜底：吞到 ), 之前
    q2 = tail.find('            "quote ')
    e = e + q2 if q2 >= 0 else e

fixed = (
    "'  {\"verdict\":\"pass|block\",\"summary\":\"...\",\"items\":[{\"criterion\":\"...\","
    "\"quote\":\"报告原文连续12-30字\",\"source\":\"大纲/选题报告.md\","
    "\"verdict\":\"pass|fail\",\"level\":\"A|B\"}]}\\n"
    "\\n"
    "**level 决定是否阻断（重要）**：\\n"
    "  \\u00b7 level \\\"B\\\"（默认）= 硬伤，有它就打回重写\\n"
    "  \\u00b7 level \\\"A\\\" = 风险提示/待观察，只记录不阻断\\n"
    "  你列的风险类发现（如热度无实证、留存未知）**必须标 A**，\\n"
    "  否则会被当成硬伤强制打回——只有真正的硬伤才标 B。\\n"
    "'\n"
    "            "
)
t = t[:start] + fixed + t[e:]
p.write_text(t, encoding='utf-8')
print('N04 字符串已修复')
PY
