# -*- coding: utf-8 -*-
"""P2 卷结构验证：整条游标链每个节点都必须可解析。"""
import sys
from pathlib import Path
sys.path.insert(0, r'F:\WorkBuddy AI\Novel Platform\novel-workbench\scripts')
import pipeline_spec as ps

BOOK = Path(r'X:\book')


def walk(st, cap=6000):
    """模拟推进，检查每个 cursor 都能解析出节点。"""
    chain, missing = [], []
    for _ in range(cap):
        cur = st['cursor']
        # 解析
        if cur.startswith('C'):
            n = st['current_chapter'] + 1
            spec = ps.chapter_nodes(st, BOOK, n).get(cur)
        elif cur.startswith('V'):
            v = int(cur.split('.', 1)[0][1:])
            spec = ps.volume_nodes(st, BOOK, v).get(cur)
        elif cur == '__done__':
            spec = {'id': '__done__'}
        else:
            spec = ps.GLOBAL_NODES.get(cur)
        if spec is None:
            missing.append(cur)
        chain.append(cur)
        if cur in ('__done__',):
            break
        st['cursor'] = ps.next_cursor(st)
        if st['cursor'] == '__done__':
            chain.append('__done__')
            break
    return chain, missing, st


new = {'cursor': 'N06', 'max_chapters': 130, 'target_words': 250000,
       'current_chapter': 0, 'chapters_committed': 0, 'words_total': 0,
       'plan': {'vol_size': 22}, 'platform': 'zongheng', 'genre': 'dushi', 'nodes': {}}
ch, miss, st = walk(new)
print(f'新书: {len(ch)} 步 | 终止 {ch[-1]} | 封板 {st["chapters_committed"]} 章')
print(f'  未解析节点: {len(miss)} 个 {miss[:5]}')
print(f'  卷前节点: {[x for x in ch if x.endswith((".N07", ".N08", ".N09d"))][:6]}')
print(f'  卷末节点: {[x for x in ch if x.startswith("V1.")][-4:]}')
print(f'  有 N17g: {"N17g" in ch} | 有 N20g: {"N20g" in ch}')

old = {'cursor': 'N06', 'max_chapters': 10, 'target_words': 20000,
       'current_chapter': 0, 'chapters_committed': 0, 'words_total': 0, 'nodes': {}}
ch2, miss2, st2 = walk(old)
print(f'老书: {len(ch2)} 步 | 终止 {ch2[-1]} | 封板 {st2["chapters_committed"]} 章')
print(f'  未解析节点: {len(miss2)} 个 {miss2[:5]}')
print(f'  有无卷级节点: {any(x.startswith("V") for x in ch2)}')
