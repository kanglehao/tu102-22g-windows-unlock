# -*- coding: utf-8 -*-
r"""TU102 dump 版 EFI 装入内置 ESP + 建固件启动项（ctypes 版，替代 install-esp.ps1）。

用法:
    python _esp_install.py            # 只读预演：备份 NVRAM、解析 Boot0000、打印将写入的内容
    python _esp_install.py --apply    # 真正执行：复制 EFI 到 ESP、写 Boot0080、BootOrder 追加

设计要点（相对作者 install-esp.ps1 的收敛）:
  * 不设 BootNext —— 启动项只进 BootOrder 末尾，Windows 仍是第一项，默认启动行为不变，
    需要时开机按 F8 手动选 "TU102 Dump"。用户完全掌控时机。
  * 写前把 BootOrder 与全部 Boot#### 原始字节备份到 nvram_backup/<时间戳>/。
  * 只往 ESP 加 \EFI\TU102\ 目录和两个 1MB 占位文件，不改任何现有文件。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ARTIFACT = os.path.join(HERE, 'artifacts', 'TU102DUMP.EFI')
BACKUP_ROOT = os.path.join(HERE, 'nvram_backup')
# PORTED 2026-10-28: the ESP drive letter is configurable, so the same
# scripts work on machines that already use S: for something else.
ESP_LETTER = os.environ.get('ESP_DRIVE', 'S:')
ESP_DIR = '\\EFI\\TU102'
TARGET_NAME = 'TU102DUMP.EFI'
TARGET_PATH = '\\EFI\\TU102\\TU102DUMP.EFI'
BOOT_NUM = 0x0080
BOOT_DESC = 'TU102 Dump'

GUID_GLOBAL = '{8be4df61-93ca-11d2-aa0d-00e098032b8c}'

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)

kernel32.GetCurrentProcess.restype = wt.HANDLE
kernel32.GetCurrentProcess.argtypes = []
advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
advapi32.OpenProcessToken.restype = wt.BOOL
advapi32.LookupPrivilegeValueW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p]
advapi32.AdjustTokenPrivileges.argtypes = [wt.HANDLE, wt.BOOL, ctypes.c_void_p,
                                           wt.DWORD, ctypes.c_void_p, ctypes.c_void_p]
kernel32.GetFirmwareEnvironmentVariableExW.argtypes = [
    wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
kernel32.GetFirmwareEnvironmentVariableExW.restype = wt.DWORD
kernel32.SetFirmwareEnvironmentVariableExW.argtypes = [
    wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD, wt.DWORD]
kernel32.SetFirmwareEnvironmentVariableExW.restype = wt.BOOL


class LUID(ctypes.Structure):
    _fields_ = [('LowPart', wt.DWORD), ('HighPart', wt.LONG)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('Luid', LUID), ('Attributes', wt.DWORD)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [('PrivilegeCount', wt.DWORD), ('Privileges', LUID_AND_ATTRIBUTES * 1)]


def enable_privilege(name='SeSystemEnvironmentPrivilege'):
    h = wt.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                     0x0020 | 0x0008, ctypes.byref(h)):
        raise RuntimeError('OpenProcessToken err %d' % ctypes.get_last_error())
    luid = LUID()
    if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
        raise RuntimeError('LookupPrivilegeValue err %d' % ctypes.get_last_error())
    tp = TOKEN_PRIVILEGES()
    tp.PrivilegeCount = 1
    tp.Privileges[0].Luid = luid
    tp.Privileges[0].Attributes = 0x0002
    if not advapi32.AdjustTokenPrivileges(h, False, ctypes.byref(tp), 0, None, None):
        raise RuntimeError('AdjustTokenPrivileges err %d' % ctypes.get_last_error())


def get_var(name, size=8192):
    buf = ctypes.create_string_buffer(size)
    attr = wt.DWORD(0)
    n = kernel32.GetFirmwareEnvironmentVariableExW(
        name, GUID_GLOBAL, buf, wt.DWORD(size), ctypes.byref(attr))
    if n == 0:
        return None, 0
    return buf.raw[:n], attr.value


def set_var(name, data, attr):
    buf = ctypes.create_string_buffer(bytes(data), len(data))
    ok = kernel32.SetFirmwareEnvironmentVariableExW(
        name, GUID_GLOBAL, buf, wt.DWORD(len(data)), wt.DWORD(attr))
    if not ok:
        raise RuntimeError('SetFirmwareEnvironmentVariableExW(%s) err %d'
                           % (name, ctypes.get_last_error()))


def parse_option(b):
    """返回 (desc, device_path_list_bytes, last_media_filepath_offset)"""
    fpl_len = int.from_bytes(b[4:6], 'little')
    d = 6
    while d + 1 < len(b) and not (b[d] == 0 and b[d + 1] == 0):
        d += 2
    desc = b[6:d].decode('utf-16-le', 'ignore')
    lst = b[d + 2:d + 2 + fpl_len]
    off, last_file, fp = 0, -1, ''
    while off + 4 <= len(lst):
        t, s = lst[off], lst[off + 1]
        ln = int.from_bytes(lst[off + 2:off + 4], 'little')
        if ln < 4 or off + ln > len(lst):
            break
        if t == 4 and s == 4:
            last_file = off
            fp = lst[off + 4:off + ln - 2].decode('utf-16-le', 'ignore')
        if t == 0x7f:
            break
        off += ln
    return desc, lst, last_file, fp


def list_options(size=4096, maxn=0x100):
    """列出全部 Boot#### 选项 -> [(num, desc, filepath), ...]。"""
    out = []
    for i in range(maxn):
        d, _a = get_var('Boot%04X' % i, size)
        if not d:
            continue
        try:
            desc, _lst, _off, fp = parse_option(d)
        except Exception:
            continue
        out.append((i, desc, fp))
    return out


def classify(options):
    """按用途给 Boot#### 分类，供不同机器自动选择。

    Boot#### 的编号取决于这台机器之前建过多少启动项，写死数字在其他机器上必然错，
    所以这里靠「描述 + 文件名」识别，并允许用环境变量覆盖：
        TU102_BOOT_UNLOCK / TU102_BOOT_FLR / TU102_BOOT_WIN   (十六进制或十进制均可)

    返回 dict: {'unlock': (num, desc, fp), 'flr': ..., 'win': ..., 'all': options}
    """
    import os

    def override(name):
        v = os.environ.get(name)
        if not v:
            return None
        n = int(v, 0)
        for opt in options:
            if opt[0] == n:
                return opt
        return None

    win = override('TU102_BOOT_WIN')
    if win is None:
        cands = [o for o in options
                 if o[1].lower().startswith('windows') or o[0] == 0]
        win = cands[0] if cands else None

    # FLR 版恢复工具：文件名或描述里带 flr
    flr = override('TU102_BOOT_FLR')
    if flr is None:
        cand = [o for o in options if 'flr' in (o[1] + ' ' + o[2]).lower()]
        flr = cand[-1] if cand else None

    # 解锁版：TU102 且不叫 FLR；同机器可能有多个历史版本，取编号最大的（最近建的）
    unlock = override('TU102_BOOT_UNLOCK')
    if unlock is None:
        cand = [o for o in options
                if 'tu102' in (o[1] + ' ' + o[2]).lower()
                and 'flr' not in (o[1] + ' ' + o[2]).lower()
                and 'dump' not in (o[1] + ' ' + o[2]).lower()
                and 'safe' not in (o[1] + ' ' + o[2]).lower()]
        unlock = cand[-1] if cand else None

    return {'unlock': unlock, 'flr': flr, 'win': win, 'all': options}


def describe_entry(e):
    return ('未找到' if e is None else 'Boot%04X  %s' % (e[0], e[1] or e[2]))


def build_option(desc, prefix, file_path):
    p = file_path.encode('utf-16-le')
    fp_len = 4 + len(p) + 2
    lst = bytearray(prefix)
    lst += bytes([4, 4]) + fp_len.to_bytes(2, 'little') + p + b'\x00\x00'
    lst += bytes([0x7F, 0xFF]) + (4).to_bytes(2, 'little')
    d = desc.encode('utf-16-le') + b'\x00\x00'
    return (1).to_bytes(4, 'little') + len(lst).to_bytes(2, 'little') + d + bytes(lst)


def mount_esp():
    p = subprocess.run(['mountvol', ESP_LETTER, '/S'], capture_output=True,
                       text=True, encoding='mbcs', errors='replace')
    if p.returncode != 0 or not os.path.isdir(ESP_LETTER + '\\'):
        raise RuntimeError('ESP 挂载失败 rc=%d %s' % (p.returncode, p.stderr))


def unmount_esp():
    subprocess.run(['mountvol', ESP_LETTER, '/D'], capture_output=True,
                   text=True, encoding='mbcs', errors='replace')


def main():
    apply = '--apply' in sys.argv
    enable_privilege()

    print('=== 1. 备份固件变量 ===')
    ts = time.strftime('%Y%m%d-%H%M%S')
    bakdir = os.path.join(BACKUP_ROOT, ts)
    os.makedirs(bakdir, exist_ok=True)

    order, order_attr = get_var('BootOrder')
    if not order:
        raise SystemExit('读不到 BootOrder，中止')
    with open(os.path.join(bakdir, 'BootOrder.bin'), 'wb') as f:
        f.write(order)

    items = {}
    for i in range(0x100):
        nm = 'Boot%04X' % i
        d, a = get_var(nm, 4096)
        if d:
            items[nm] = (d, a)
            with open(os.path.join(bakdir, nm + '.bin'), 'wb') as f:
                f.write(d)
    with open(os.path.join(bakdir, '_index.json'), 'w', encoding='utf-8') as f:
        json.dump({'time': ts, 'bootorder': order.hex(),
                   'order_attr': order_attr,
                   'vars': {k: v[0].hex() for k, v in items.items()}}, f, indent=1)
    print('  备份目录: %s' % bakdir)
    print('  共备份 %d 个 Boot#### + BootOrder' % len(items))

    print('=== 2. 解析 Boot0000（Windows Boot Manager） ===')
    wbm = None
    for nm, (d, a) in items.items():
        desc, lst, last_file, fp = parse_option(d)
        if 'BOOTMGFW' in fp.upper():
            wbm = (nm, desc, lst, last_file, fp)
            print('  %s  desc=%r' % (nm, desc))
            print('  设备路径: %s' % ' '.join('%02x' % x for x in lst[:24]))
            print('  loader : %s' % fp)
            break
    if not wbm:
        raise SystemExit('找不到 bootmgfw 的启动项，中止')
    nm, desc, lst, last_file, fp = wbm
    if last_file < 0:
        raise SystemExit('设备路径里没有 FilePath 节点，中止')

    new_opt = build_option(BOOT_DESC, lst[:last_file], TARGET_PATH)
    print('  新启动项将写入 Boot%04X，%d 字节：' % (BOOT_NUM, len(new_opt)))
    print('    Attr=1  FilePathListLen=%d  Desc=%r'
          % (int.from_bytes(new_opt[4:6], 'little'), BOOT_DESC))
    print('    路径尾部: %s' % TARGET_PATH)

    order_ids = [int.from_bytes(order[i:i + 2], 'little') for i in range(0, len(order), 2)]
    print('=== 3. BootOrder 现状 ===')
    print('  %s' % ['%04X' % x for x in order_ids])
    already = BOOT_NUM in order_ids
    if already:
        print('  Boot%04X 已在 BootOrder 中，不重复追加' % BOOT_NUM)
        new_order = order
    else:
        new_order = order + BOOT_NUM.to_bytes(2, 'little')
        print('  将追加 %04X 到末尾 -> %s'
              % (BOOT_NUM, ['%04X' % x for x in
                            [int.from_bytes(new_order[i:i + 2], 'little')
                             for i in range(0, len(new_order), 2)]]))
    print('  Windows 仍是第一项，默认启动行为不变')

    if not apply:
        print('\n[预演] 未做任何写入。确认无误后加 --apply 执行。')
        return

    print('=== 4. 写入 ESP ===')
    if not os.path.isfile(ARTIFACT):
        raise SystemExit('缺少 %s' % ARTIFACT)
    mount_esp()
    try:
        dst_dir = ESP_LETTER + ESP_DIR
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, TARGET_NAME)
        shutil.copyfile(ARTIFACT, dst)
        import hashlib
        h = hashlib.sha256(open(dst, 'rb').read()).hexdigest()
        print('  写入 %s (%d 字节)' % (dst, os.path.getsize(dst)))
        print('  sha256 = %s' % h)
        # raw BlockIo 兜底用的预建占位文件（1MB 全零）
        for nm2 in ('tu102p30.bin', 'tu102p11.bin'):
            p2 = os.path.join(ESP_LETTER + '\\', nm2)
            if not os.path.exists(p2):
                with open(p2, 'wb') as f:
                    f.write(b'\x00' * 0x100000)
                print('  预建占位 %s (1 MiB)' % p2)
            else:
                print('  占位已存在 %s（保留原内容）' % p2)
        import shutil as _sh
        t, u, fr = _sh.disk_usage(ESP_LETTER + '\\')
        print('  ESP 剩余 %.0f MB' % (fr / 1048576))
    finally:
        unmount_esp()
        print('  ESP 盘符已卸下（恢复隐藏）')

    print('=== 5. 写固件变量 ===')
    set_var('Boot%04X' % BOOT_NUM, new_opt, 0x7)
    print('  Boot%04X 已写入 (attr=0x7)' % BOOT_NUM)
    if not already:
        set_var('BootOrder', new_order, order_attr)
        print('  BootOrder 已更新 (attr=0x%x)' % order_attr)

    print('=== 6. 回读验证 ===')
    d, a = get_var('Boot%04X' % BOOT_NUM, 4096)
    print('  Boot%04X 读回 %d 字节, attr=0x%x' % (BOOT_NUM, len(d) if d else 0, a))
    o2, _ = get_var('BootOrder')
    ids2 = [int.from_bytes(o2[i:i + 2], 'little') for i in range(0, len(o2), 2)]
    print('  BootOrder 读回 %s' % ['%04X' % x for x in ids2])
    print('\n完成。开机按 F8 -> 选 "%s" 即进入 dump（前提：Secure Boot 已关闭）' % BOOT_DESC)


if __name__ == '__main__':
    main()
