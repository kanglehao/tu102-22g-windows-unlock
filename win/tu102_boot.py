# -*- coding: utf-8 -*-
"""TU102 算力解锁 —— 引导管理菜单。

只做三件事：看状态、改 BootOrder/BootNext、重启。
所有 NVRAM 写入前都会备份到 nvram_backup\\<时间戳>\\。

用法（双击桌面 TU102解锁管理.cmd 即可）：
  python tu102_boot.py            # 正常启动（非管理员会自动弹 UAC 提权后重开）
  python tu102_boot.py --status   # 只打印状态后退出（脚本调用用）
"""
import ctypes
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import espvar as base  # noqa: E402

CLASS_KEY = (r'HKLM\SYSTEM\CurrentControlSet\Control\Class'
             r'\{4d36e968-e325-11ce-bfc1-08002be10318}')

# PORTED 2026-10-28: Boot#### 的编号每台机器都不一样，取决于之前建过多少启动项。
# 写死 0x0085 这类数字在别的机器上必然指向错误的项，所以这里按「用途」自动识别，
# 识别规则在 espvar.classify() 里，可用环境变量 TU102_BOOT_UNLOCK / _FLR / _WIN 覆盖。
_ENT_CACHE = {}


def ent(kind):
    """按用途返回本机的 Boot#### -> (num, desc, filepath)，找不到返回 None。"""
    if kind not in _ENT_CACHE:
        c = base.classify(base.list_options())
        _ENT_CACHE.update(c)
    return _ENT_CACHE.get(kind)


def ent_num(kind):
    e = ent(kind)
    return None if e is None else e[0]


def require(kind, what):
    n = ent_num(kind)
    if n is None:
        print('找不到%s对应的启动项。请先部署解锁 EFI，或用 '
              'TU102_BOOT_%s=<编号> 手动指定。' % (what, kind.upper()))
    return n


# ---------------------------------------------------------------- 控制台
def setup_console():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    try:
        ctypes.windll.kernel32.SetConsoleTitleW('TU102 解锁管理')
    except Exception:
        pass


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate():
    """非管理员时用 runas 重开自己（会弹 UAC），当前进程退出。"""
    params = '"%s" %s' % (os.path.abspath(__file__), ' '.join(sys.argv[1:]))
    r = ctypes.windll.shell32.ShellExecuteW(None, 'runas', sys.executable, params, HERE, 1)
    if r <= 32:
        print('提权失败（ShellExecuteW=%d）。请右键 .cmd 选择“以管理员身份运行”。' % r)
        input('按回车退出...')
    return r > 32


# ---------------------------------------------------------------- NVRAM
def read_order():
    d, _ = base.get_var('BootOrder', 4096)
    if not d:
        return []
    return [int.from_bytes(d[i * 2:i * 2 + 2], 'little') for i in range(len(d) // 2)]


def read_bootnext():
    d, _ = base.get_var('BootNext')
    return int.from_bytes(d, 'little') if d else None


def backup_vars(tag):
    d = os.path.join(HERE, 'nvram_backup', time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(d, exist_ok=True)
    o = read_order()
    with open(os.path.join(d, 'BootOrder.%s.bin' % tag), 'wb') as f:
        f.write(b''.join(e.to_bytes(2, 'little') for e in o))
    with open(os.path.join(d, 'README.txt'), 'w', encoding='utf-8') as f:
        f.write('%s\nBootOrder: %s\nBootNext: %s\n' % (
            time.strftime('%Y-%m-%d %H:%M:%S'), ' '.join('%04X' % e for e in o),
            read_bootnext()))
    return d


def write_order(order):
    base.set_var('BootOrder', b''.join(e.to_bytes(2, 'little') for e in order), 0x7)


def clear_bootnext():
    subprocess.run(['bcdedit', '/deletevalue', '{fwbootmgr}', 'bootsequence'],
                   capture_output=True, text=True, encoding='mbcs', errors='replace')


def write_bootnext(n):
    base.set_var('BootNext', n.to_bytes(2, 'little'), 0x7)


# ---------------------------------------------------------------- 只读探测
def gsp_switch():
    """返回 [(子键, DriverDesc, EnableGpuFirmware)]。"""
    out = []
    try:
        import winreg
        h = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CLASS_KEY[len('HKLM\\'):])
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(h, i)
            except OSError:
                break
            i += 1
            if not (len(sub) == 4 and sub.isdigit()):
                continue
            try:
                sh = winreg.OpenKey(h, sub)
                desc = winreg.QueryValueEx(sh, 'DriverDesc')[0]
                if 'NVIDIA' not in str(desc):
                    continue
                try:
                    v = winreg.QueryValueEx(sh, 'EnableGpuFirmware')[0]
                except OSError:
                    v = None
                out.append((sub, desc, v))
                winreg.CloseKey(sh)
            except OSError:
                pass
        winreg.CloseKey(h)
    except Exception as e:
        out.append(('?', '读取失败: %s' % e, None))
    return out


def nvidia_brief():
    try:
        r = subprocess.run(
            [r'C:\Windows\System32\nvidia-smi.exe',
             '--query-gpu=index,name,pci.bus_id,gsp.mode.current',
             '--format=csv,noheader'],
            capture_output=True, text=True, timeout=20)
        return (r.stdout or r.stderr).strip()
    except Exception as e:
        return 'nvidia-smi 调用失败: %s' % e


def show_status():
    print('=' * 66)
    print(' 引导顺序')
    o = read_order()
    if not o:
        print('   (读不到 BootOrder)')
    known = {}
    for kind in ('win', 'unlock', 'flr'):
        e2 = ent(kind)
        if e2:
            known[e2[0]] = {'win': 'Windows 正常启动',
                            'unlock': 'TU102 解锁（双卡，推荐）',
                            'flr': 'TU102 FLR 恢复版'}[kind]
    for idx, e in enumerate(o):
        mark = ' ← 默认' if idx == 0 else ''
        print('   %d. Boot%04X  %s%s' % (idx + 1, e, known.get(e, ''), mark))
    bn = read_bootnext()
    print(' 一次性引导 BootNext : %s' % ('0x%04X' % bn if bn is not None else '未设置'))

    u = ent_num('unlock')
    auto = bool(o) and u is not None and o[0] == u
    print(' 开机自动解锁        : %s' % ('已开启' if auto else '未开启'))

    print('=' * 66)
    print(' GSP 开关（EnableGpuFirmware，必须为 1，否则驱动报 Code 43）')
    for sub, desc, v in gsp_switch():
        st = '未设置 ✗' if v is None else ('= %s ✓' % v if v == 1 else '= %s ✗' % v)
        print('   %s  %-34s %s' % (sub, desc, st))
    print('=' * 66)
    print(' 显卡')
    for ln in nvidia_brief().splitlines():
        print('   ' + ln)
    print('=' * 66)


def bench():
    exe = os.path.join(HERE, 'mma_peak.exe')
    if not os.path.exists(exe):
        print('找不到 mma_peak.exe: %s' % exe)
        return
    for dev in (0, 1):
        print('--- GPU %d ---' % dev)
        try:
            r = subprocess.run([exe, str(dev)], capture_output=True, text=True,
                               timeout=180, encoding='utf-8', errors='replace')
            txt = (r.stdout or '') + (r.stderr or '')
            for ln in txt.strip().splitlines():
                print('   ' + ln)
        except Exception as e:
            print('   失败: %s' % e)


# ---------------------------------------------------------------- 动作
def reboot_now(why):
    print('\n%s' % why)
    for i in range(8, 0, -1):
        sys.stdout.write('\r  %d 秒后重启…  (Ctrl+C 取消) ' % i)
        sys.stdout.flush()
        time.sleep(1)
    print()
    subprocess.run(['shutdown', '/r', '/t', '0'], check=False)


def act_enable_auto():
    o = read_order()
    if not o:
        print('BootOrder 读不到，终止。')
        return
    u = require('unlock', '解锁程序')
    w = require('win', 'Windows 启动项')
    if u is None or w is None:
        return
    f = ent_num('flr')
    head = [u, w] + ([f] if f is not None else [])
    target = head + [e for e in o if e not in head]
    if target == o:
        print('已经是「开机自动解锁」状态，无需修改。')
        return
    print('\n新顺序: %s' % ' '.join('%04X' % e for e in target))
    if input('确认写入？(y/N) ').strip().lower() != 'y':
        print('已取消。')
        return
    print('备份: %s' % backup_vars('before_enable'))
    write_order(target)
    clear_bootnext()
    got = read_order()
    print('回读: %s  %s' % (' '.join('%04X' % e for e in got),
                           'OK' if got == target else '*** 不符 ***'))
    print('\n以后每次开机会先跑解锁程序（约多等 30~60 秒），再进 Windows。')
    reboot_now('重启后本次即为自动解锁模式（本次重启不需要 BootNext）。')


def act_disable_auto():
    o = read_order()
    if not o:
        print('BootOrder 读不到，终止。')
        return
    w = require('win', 'Windows 启动项')
    if w is None:
        return
    target = [w] + [e for e in o if e != w]
    if target == o:
        print('已经是「正常启动」状态，无需修改。')
        return
    print('\n新顺序: %s' % ' '.join('%04X' % e for e in target))
    if input('确认写入？(y/N) ').strip().lower() != 'y':
        print('已取消。')
        return
    print('备份: %s' % backup_vars('before_disable'))
    write_order(target)
    clear_bootnext()
    got = read_order()
    print('回读: %s  %s' % (' '.join('%04X' % e for e in got),
                           'OK' if got == target else '*** 不符 ***'))
    print('\n以后开机会直接进 Windows（显卡为原厂砍半状态）。')


def act_once(entry, label):
    print('备份: %s' % backup_vars('before_once'))
    write_bootnext(entry)
    got = read_bootnext()
    ok = (got == entry)
    print('BootNext 写入 0x%04X，回读 %s  %s' % (
        entry, '0x%04X' % got if got is not None else 'None',
        'OK' if ok else '*** 不符 ***'))
    if not ok:
        return
    reboot_now('%s —— 仅本次生效，之后回到默认顺序。' % label)


def act_recovery():
    print('\n这一项用于：解锁后进 Windows 驱动报 Code 43 / 设备管理器黄色感叹号时的兜底。')
    print('原理：走 FLR 版，把显卡 PCI function 整体复位，解锁值一并清掉，')
    print('      驱动拿到干净的原厂卡即可正常工作（算力回落砍半）。')
    print('注意：做完这一次后可以再选 [5] 重新解锁，不需要断电。')
    if input('\n确认走 FLR 恢复并重启？(y/N) ').strip().lower() != 'y':
        print('已取消。')
        return
    n = require('flr', 'FLR 恢复版')
    if n is None:
        return
    act_once(n, 'FLR 恢复')


def show_gsp_fix():
    print('\nGSP 开关的作用：让 Windows 驱动走 GSP-RM 路径，接受 pre-OS 阶段写好的')
    print('解锁状态。缺这个值 -> 驱动报 Code 43，或解锁不生效。\n')
    rows = gsp_switch()
    bad = [r for r in rows if r[2] != 1]
    for sub, desc, v in rows:
        print('   %s  %-34s %s' % (sub, desc, '未设置' if v is None else '= %s' % v))
    if not bad:
        print('\n两卡都是 1，正常。')
        return
    if input('\n检测到有缺失，是否补写 EnableGpuFirmware=1？(y/N) ').strip().lower() != 'y':
        return
    import winreg
    for sub, desc, _v in bad:
        p = CLASS_KEY[len('HKLM\\'):] + '\\' + sub
        h = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, p, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(h, 'EnableGpuFirmware', 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(h)
        print('   已写入 %s' % sub)
    for sub, desc, v in gsp_switch():
        print('   回读 %s = %s' % (sub, v))


MENU_ITEMS = [
    ('1', '状态总览（引导顺序 / GSP 开关 / 显卡）'),
    ('2', '测算力（mma_peak，两卡，约 10 秒）'),
    ('', ''),
    ('3', '开启「开机自动解锁」'),
    ('4', '关闭「开机自动解锁」（回到原厂砍半）'),
    ('5', '仅本次解锁并重启（不动默认顺序）'),
    ('6', '仅本次正常启动（清掉一次性引导，不重启）'),
    ('7', '驱动报 Code 43 时的恢复（走 FLR 版，清掉解锁态）'),
    ('8', '校验 / 补写 GSP 开关（EnableGpuFirmware）'),
    ('0', '退出'),
]


def _disp_width(s):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in s)


def build_menu(width=64):
    def row(text):
        return '║' + text + ' ' * max(0, width - _disp_width(text)) + '║'

    lines = ['╔' + '═' * width + '╗',
             row('          TU102 / 2080Ti 算力解锁 · 引导管理'),
             '╠' + '═' * width + '╣']
    for key, text in MENU_ITEMS:
        lines.append(row(('  %s  %s' % (key, text)) if key else ''))
    lines.append('╚' + '═' * width + '╝')
    return '\n'.join(lines)



def main():
    setup_console()
    status_only = '--status' in sys.argv

    if not status_only and not is_admin():
        print('需要管理员权限（写 UEFI 引导变量）。正在请求提权…')
        if elevate():
            return
        return

    base.enable_privilege()

    if status_only:
        show_status()
        return

    while True:
        print(build_menu())
        try:
            c = input(' 请选择: ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if c == '1':
            show_status()
        elif c == '2':
            bench()
        elif c == '3':
            act_enable_auto()
        elif c == '4':
            act_disable_auto()
        elif c == '5':
            n = require('unlock', '解锁程序')
            if n is not None:
                act_once(n, '仅本次解锁')
        elif c == '6':
            clear_bootnext()
            print('BootNext = %s' % (read_bootnext() or '未设置 (正常)'))
        elif c == '7':
            act_recovery()
        elif c == '8':
            show_gsp_fix()
        elif c in ('0', 'q', 'Q'):
            return
        else:
            print('无效选择。')
        try:
            input('\n按回车返回菜单…')
        except (EOFError, KeyboardInterrupt):
            return
        os.system('cls')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            input('\n出错了，按回车关闭…')
        except Exception:
            pass
