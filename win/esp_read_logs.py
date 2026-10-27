# -*- coding: utf-8 -*-
"""挂载 ESP，列出根目录，取回全部 50hx*.txt 并打印。"""
import os
import shutil
import subprocess
import espvar  # PORTED 2026-10-28: configurable ESP drive letter

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, 'artifacts', 'dump')


def dec(raw):
    for enc in ('utf-16-le', 'utf-8', 'mbcs'):
        try:
            txt = raw.decode(enc)
            if txt.count('\x00') > len(txt) * 0.3:
                continue
            return enc, txt
        except Exception:
            continue
    return None, raw.decode('latin-1', 'replace')


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    r = subprocess.run(['mountvol', espvar.ESP_LETTER, '/S'], capture_output=True, text=True,
                       encoding='mbcs', errors='replace')
    print('[mountvol rc=%d] %s' % (r.returncode, (r.stdout or '').strip()))
    try:
        if not os.path.isdir((espvar.ESP_LETTER + '\\')):
            print('!! S: 挂载失败')
            return
        print('=== S:\\ 根目录 ===')
        for n in sorted(os.listdir((espvar.ESP_LETTER + '\\'))):
            p = os.path.join((espvar.ESP_LETTER + '\\'), n)
            tag = 'DIR ' if os.path.isdir(p) else 'FILE'
            sz = 0 if os.path.isdir(p) else os.path.getsize(p)
            print('  %s %-28s %d' % (tag, n, sz))
        targets = [n for n in os.listdir((espvar.ESP_LETTER + '\\'))
                   if n.lower().startswith('50hx') or n.lower().endswith('.txt')]
        for name in sorted(targets):
            src = os.path.join((espvar.ESP_LETTER + '\\'), name)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(OUTDIR, name)
            shutil.copyfile(src, dst)
            raw = open(dst, 'rb').read()
            enc, txt = dec(raw)
            print('\n===== %s (%d bytes, %s) =====' % (name, len(raw), enc))
            print(txt)
    finally:
        subprocess.run(['mountvol', espvar.ESP_LETTER, '/D'], capture_output=True, text=True)


if __name__ == '__main__':
    main()
