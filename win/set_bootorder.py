# -*- coding: utf-8 -*-
"""把解锁 EFI 排到 BootOrder 首位 —— 之后每次开机自动解锁再进 Windows。

用法:
  python set_bootorder.py            # 预演：只打印当前与目标顺序
  python set_bootorder.py --apply    # 写入 NVRAM（先备份到 nvram_backup\\<时间戳>\\）

设计:
  - 目标顺序 = <解锁项> -> <Windows> -> <FLR 恢复项> -> 其余保持原样
    Windows 放第二位：解锁程序若「返回错误」而不是挂死，固件会继续尝试下一项。
  - **Boot#### 编号每台机器都不一样**，靠 espvar.classify() 按用途自动识别；
    识别不准时用环境变量覆盖：TU102_BOOT_UNLOCK / TU102_BOOT_FLR / TU102_BOOT_WIN
  - 只写 BootOrder 一个变量，不删任何启动项、不碰 ESP
  - BootNext 若存在会被清掉（它优先于 BootOrder，留着会干扰）

风险:
  解锁程序如果「挂死」（不是返回错误），固件不会跳到下一项，机器会停在开机画面。
  这时用主板的启动菜单（MSI 为 F11）直接选 Windows 即可，再改回 Windows 优先，
  或删掉那个启动项。原生改动只有 NVRAM 里的顺序，不碰 VBIOS、不动熔丝。
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import espvar as base  # noqa: E402

LABEL = {'win': 'Windows 正常启动',
         'unlock': 'TU102 解锁（双卡解锁，不含 FLR）',
         'flr': 'TU102 FLR 恢复版'}


def read_order():
    d, _attr = base.get_var('BootOrder', 4096)
    if not d:
        return []
    return [int.from_bytes(d[i * 2:i * 2 + 2], 'little') for i in range(len(d) // 2)]


def dump(order, title, entries):
    print('%s (%d 项):' % (title, len(order)))
    for e in order:
        tag = ''
        for kind, ent in entries.items():
            if ent and ent[0] == e:
                tag = '  [%s] %s' % (kind, LABEL.get(kind, ''))
                break
        print('   Boot%04X%s' % (e, tag))
    print('   -> ' + ' '.join('%04X' % e for e in order))


def main():
    apply_ = '--apply' in sys.argv
    base.enable_privilege()

    entries = base.classify(base.list_options())
    print('=== 自动识别结果 ===')
    for kind in ('unlock', 'win', 'flr'):
        print('   %-7s %s' % (kind, base.describe_entry(entries.get(kind))))
    print('   （不准的话用 TU102_BOOT_UNLOCK / _FLR / _WIN 环境变量指定编号）')

    cur = read_order()
    if not cur:
        raise SystemExit('BootOrder 读不到，终止。')
    print()
    dump(cur, '当前 BootOrder', entries)

    bn, _ = base.get_var('BootNext')
    cur_next = int.from_bytes(bn, 'little') if bn else None
    print('\n当前 BootNext = %s' % ('0x%04X' % cur_next if cur_next is not None else '未设置'))

    unlock = entries.get('unlock')
    win = entries.get('win')
    if unlock is None or win is None:
        raise SystemExit('\n找不到解锁项或 Windows 启动项，拒绝修改 BootOrder。')

    head = [unlock[0], win[0]]
    flr = entries.get('flr')
    if flr and flr[0] not in head:
        head.append(flr[0])
    target = head + [e for e in cur if e not in head]

    if target == cur:
        print('\n顺序已经是目标值，无需写入。')
        return

    print()
    dump(target, '目标 BootOrder', entries)

    if not apply_:
        print('\n[预演] 未写入。加 --apply 执行。')
        return

    stamp = time.strftime('%Y%m%d-%H%M%S')
    bdir = os.path.join(HERE, 'nvram_backup', stamp)
    os.makedirs(bdir, exist_ok=True)
    with open(os.path.join(bdir, 'BootOrder.before.bin'), 'wb') as f:
        f.write(b''.join(e.to_bytes(2, 'little') for e in cur))
    with open(os.path.join(bdir, 'README.txt'), 'w', encoding='utf-8') as f:
        f.write('修改前的 BootOrder: %s\n'
                % ' '.join('%04X' % e for e in cur))
    print('\n备份: %s' % bdir)

    base.set_var('BootOrder', b''.join(e.to_bytes(2, 'little') for e in target), 0x7)
    if cur_next is not None:
        subprocess.run(['bcdedit', '/deletevalue', '{fwbootmgr}', 'bootsequence'],
                       capture_output=True, text=True, encoding='mbcs', errors='replace')

    got = read_order()
    print('\n回读 BootOrder: %s' % ' '.join('%04X' % e for e in got))
    print('结果: %s' % ('OK 与目标一致' if got == target else '*** 不符 ***'))
    bn2, _ = base.get_var('BootNext')
    print('回读 BootNext: %s' % ('0x%04X' % int.from_bytes(bn2, 'little') if bn2 else '未设置 (正常)'))
    print('\n以后每次开机会先跑解锁程序（多等 30~60 秒），再进 Windows。')
    print('若开机卡住：F11 调出启动菜单选 Windows，再决定要不要改回来。')


if __name__ == '__main__':
    main()
