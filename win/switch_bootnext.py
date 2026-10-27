# -*- coding: utf-8 -*-
"""只切换一次性引导目标（BootNext），不碰 ESP、不碰 BootOrder。

用法:
  python _switch_bootnext.py            # 列出全部 TU102 启动项 + 当前 BootNext
  python _switch_bootnext.py 84         # 设 BootNext=0x0084（十进制或 0x 前缀均可）
  python _switch_bootnext.py clear      # 清除 BootNext（走默认 bootmgr）
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import espvar as base  # noqa: E402


def list_options():
    print('=== TU102 相关启动项 ===')
    found = []
    for i in range(0x100):
        nm = 'Boot%04X' % i
        d, _a = base.get_var(nm, 4096)
        if not d:
            continue
        try:
            desc, _l, _lf, fp = base.parse_option(d)
        except Exception:
            continue
        if 'TU102' in desc.upper() or 'TU102' in fp.upper():
            found.append((i, desc, fp))
            print('  Boot%04X  %-14s  %s' % (i, desc, fp))
    bn, _ = base.get_var('BootNext')
    cur = int.from_bytes(bn, 'little') if bn else None
    print('  BootNext = %s' % ('0x%04X' % cur if cur is not None else '未设置'))
    return found, cur


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    base.enable_privilege()
    found, cur = list_options()

    if arg is None:
        return

    if arg.lower() == 'clear':
        if cur is None:
            print('\n[跳过] BootNext 本来就没设置。')
            return
        r = subprocess.run(['bcdedit', '/deletevalue', '{fwbootmgr}', 'bootsequence'],
                           capture_output=True, text=True, encoding='mbcs', errors='replace')
        print('\nbcdedit rc=%d %s' % (r.returncode, (r.stdout or r.stderr or '').strip()[:120]))
        bn, _ = base.get_var('BootNext')
        print('回读 BootNext = %s'
              % ('0x%04X' % int.from_bytes(bn, 'little') if bn else '不存在（正常）'))
        return

    # PORTED 2026-10-28: Boot#### 编号每台机器不同，除了数字也接受用途关键字
    kind = None
    if arg.lower() in ('unlock', 'flr', 'win', 'windows'):
        kind = 'win' if arg.lower() == 'windows' else arg.lower()
        e = base.classify(base.list_options()).get(kind)
        if e is None:
            raise SystemExit('找不到 %s 对应的启动项。可先用不带参数的命令列一遍。' % kind)
        num = e[0]
        print('按用途 %s 选中 Boot%04X (%s)' % (kind, num, e[1]))
    else:
        try:
            num = int(arg, 0)
        except ValueError:
            raise SystemExit('无法识别的参数 %r：给 Boot#### 编号，'
                             '或 unlock / flr / win 中的一个。' % arg)
    # Windows 启动项不在「TU102」过滤器里，所以走关键字选出来时跳过该检查
    if kind != 'win' and num not in [f[0] for f in found]:
        raise SystemExit('Boot%04X 不是 TU102 启动项，拒绝设置。可用: %s'
                         % (num, ['%04X' % f[0] for f in found]))
    base.set_var('BootNext', num.to_bytes(2, 'little'), 0x7)
    bn, _ = base.get_var('BootNext')
    got = int.from_bytes(bn, 'little') if bn else None
    print('\nBootNext 写入 0x%04X，回读 = %s  %s'
          % (num, '0x%04X' % got if got is not None else 'None',
             'OK' if got == num else '*** 不符 ***'))
    print('下次开机将一次性进入该程序，执行完 chainload 回 Windows；'
          'BootNext 被消费后自动失效，再下次开机仍走默认。')


if __name__ == '__main__':
    main()
