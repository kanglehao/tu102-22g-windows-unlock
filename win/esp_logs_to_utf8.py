# -*- coding: utf-8 -*-
"""把 ESP 上的 50hx*.txt 原样转存为 utf-8（源文件本身是 8-bit ASCII）。"""
import os
import shutil
import subprocess
import espvar  # PORTED 2026-10-28: configurable ESP drive letter

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'artifacts', 'dump')
DST = os.path.join(SRC, 'utf8')


def main():
    os.makedirs(DST, exist_ok=True)
    subprocess.run(['mountvol', espvar.ESP_LETTER, '/S'], capture_output=True)
    try:
        for n in os.listdir((espvar.ESP_LETTER + '\\')):
            if not (n.lower().startswith('50hx') and n.lower().endswith('.txt')):
                continue
            p = os.path.join((espvar.ESP_LETTER + '\\'), n)
            shutil.copyfile(p, os.path.join(SRC, n))
            raw = open(p, 'rb').read()
            txt = raw.decode('utf-8', 'replace')
            out = os.path.join(DST, n.replace('.txt', '.utf8.txt'))
            open(out, 'w', encoding='utf-8', newline='\n').write(txt)
            print('%s  %d bytes  %d chars  nul=%d' % (n, len(raw), len(txt), raw.count(b'\x00')))
    finally:
        subprocess.run(['mountvol', espvar.ESP_LETTER, '/D'], capture_output=True)


if __name__ == '__main__':
    main()
