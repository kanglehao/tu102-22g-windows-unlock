# -*- coding: utf-8 -*-
"""strata prefill 对照测速。

为什么要固定 prompt：
  strata 日志里的 "read" 速度依赖「本次真正要读的 token 数」和内容路由分布，
  只有同一批 prompt 在解锁态 / 锁定态各跑一遍，数字才可比。

用法：
  python run_bench.py --gen        # 只生成 prompt 文件（幂等，已存在则跳过）
  python run_bench.py              # 跑全部 3 条并解析日志
  python run_bench.py --tag locked # 结果存到 results-<tag>.json，便于两轮对比
"""
import io
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.environ.get('STRATA_LOG', r'')
# PORTED 2026-10-28: point STRATA_LOG at your engine log, e.g.
#   set STRATA_LOG=\path\to\strata-<cfg>.log
URL = os.environ.get('STRATA_URL', 'http://127.0.0.1:8080/v1/chat/completions')
MODEL = os.environ.get('STRATA_MODEL', 'qwen3.8-flash-next-iq3_xxs')



BS2 = chr(92)   # backslash, keeps this string literal clean

PAT = re.compile(r'strata serve: prompt (\d+) tokens = (\d+) reused \+ (\d+) read '
                 r'in (\d+) ms \(([\d.]+) tok/s\), (\d+) generated in (\d+) ms '
                 r'\(([\d.]+) tok/s\)')


# ------------------------------------------------------------ prompt 生成
TERMS = [
    '张量核心', '显存带宽', '专家路由', '预填充阶段', '增量解码', '键值缓存',
    '层间切分', '流水线窗口', '投机采样', '接受率', '批处理尺寸', '量化位宽',
    '内存带宽瓶颈', '算子融合', '注意力窗口', '状态空间层', '混合专家结构',
]
NUM_WORDS = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十']


def gen_prompt(seed, paragraphs=500):
    """生成确定性长文本（约 8 万汉字 ≈ 5 万 token）。"""
    out = []
    out.append('下面是一份关于本地大模型推理引擎的内部技术记录，请通读后提炼要点。\n')
    for i in range(paragraphs):
        t1 = TERMS[(i * 7 + seed) % len(TERMS)]
        t2 = TERMS[(i * 13 + seed * 3) % len(TERMS)]
        t3 = TERMS[(i * 29 + seed * 5) % len(TERMS)]
        out.append(
            '第%d节。这一节记录第%d次实验的观察。工程师把%s与%s的表现放在一起比对，'
            '发现当%s的取值落在第%d档时，整体吞吐出现了约%d%%的偏差。'
            '样本编号%d-%d，采集时间戳偏移%d毫秒，温度%d摄氏度，功耗上限%d瓦。'
            '结论是该现象与%s无关，主要来自%s的抖动，需要再取%d组数据确认。'
            % (i + 1, i * 3 + seed, t1, t2, t3, (i % 9) + 1, (i * 7) % 37,
               i + 1, seed, (i * 11) % 997, (i % 20) + 48, 200 + (i % 60),
               t2, t1, (i % 5) + 3)
        )
    return '\n'.join(out)


def ensure_prompts():
    os.makedirs(HERE, exist_ok=True)
    made = []
    for k in (1, 2, 3):
        p = os.path.join(HERE, 'prompt%d.txt' % k)
        if not os.path.exists(p):
            txt = gen_prompt(k)
            with io.open(p, 'w', encoding='utf-8') as f:
                f.write(txt)
            made.append((p, len(txt)))
        else:
            with io.open(p, encoding='utf-8') as f:
                made.append((p, len(f.read())))
    for p, n in made:
        print('%s  %d 字符' % (os.path.basename(p), n))
    return [os.path.join(HERE, 'prompt%d.txt' % k) for k in (1, 2, 3)]


# ------------------------------------------------------------ 请求
def log_tail_lines(since_bytes):
    with open(LOG, 'rb') as f:
        f.seek(since_bytes)
        return f.read().decode('utf-8', 'replace')


def check_log():
    if not LOG or not os.path.exists(LOG):
        raise SystemExit(
            'set STRATA_LOG to your engine log first, e.g.'
            '  set STRATA_LOG=' + BS2 + 'path' + BS2 + 'to' + BS2 + 'strata-xx.log')


def run_one(path, max_tokens=24):
    with io.open(path, encoding='utf-8') as f:
        prompt = f.read()
    body = json.dumps({
        'model': MODEL,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': max_tokens,
        'temperature': 0.0,
        'top_k': 1,
        'stream': False,
    }).encode('utf-8')
    req = urllib.request.Request(URL, data=body,
                                 headers={'Content-Type': 'application/json'})
    start_size = os.path.getsize(LOG)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        raw = r.read().decode('utf-8', 'replace')
    wall = time.time() - t0
    new = log_tail_lines(start_size)
    m = PAT.findall(new)
    return wall, m, raw


def main():
    tag = 'unlocked'
    for i, a in enumerate(sys.argv):
        if a == '--tag' and i + 1 < len(sys.argv):
            tag = sys.argv[i + 1]
    check_log()
    paths = ensure_prompts()
    if '--gen' in sys.argv:
        return

    results = []
    for k, p in enumerate(paths, 1):
        print('\n--- prompt%d ---' % k)
        try:
            wall, m, raw = run_one(p)
        except Exception as e:
            print('请求失败: %r' % e)
            continue
        if not m:
            print('日志未捕获到统计行，服务端返回: %s' % raw[:200])
            continue
        prompt_tok, reused, read, ms, ts, gtok, gms, gs = m[-1]
        row = {
            'prompt': k, 'wall_s': round(wall, 1),
            'tokens': int(prompt_tok), 'reused': int(reused), 'read': int(read),
            'prefill_ms': int(ms), 'prefill_tps': float(ts),
            'gen_tokens': int(gtok), 'decode_tps': float(gs),
        }
        results.append(row)
        print('  prompt=%s tok (reused %s, cold read %s) | prefill %s ms = %.1f tok/s | decode %.1f tok/s'
              % (row['tokens'], row['reused'], row['read'], row['prefill_ms'],
                 row['prefill_tps'], row['decode_tps']))

    if results:
        cold = [r for r in results if r['read'] > 1000]
        if cold:
            avg = sum(r['prefill_tps'] for r in cold) / len(cold)
            print('\n冷 prefill 平均: %.1f tok/s  (%d 条, cold read %d~%d tok)'
                  % (avg, len(cold), min(r['read'] for r in cold),
                     max(r['read'] for r in cold)))
            results.append({'summary': 'cold_prefill_avg', 'tag': tag, 'value': round(avg, 1),
                            'n': len(cold)})
        out = os.path.join(HERE, 'results-%s.json' % tag)
        with io.open(out, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'tag': tag, 'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
                                'rows': results}, ensure_ascii=False, indent=2))
        print('结果已存: %s' % out)


if __name__ == '__main__':
    main()
