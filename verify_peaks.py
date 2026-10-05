#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真机遥测独立复核脚本(开源, 不依赖闭源引擎)
用法: python3 verify_peaks.py <室内CSV> <室外CSV>
只读公开CSV, 输出包线峰值与电池返航线穿越事件——任何人可复算本仓库宣称的真机数据结论。
换算口径: MPH→m/s(×0.44704); 电池空单元格=缺测(不造0%伪事件)。
"""
import csv, sys

MPH = 0.44704

def load(path):
    rows = list(csv.reader(open(path, newline='', encoding='utf-8', errors='replace')))
    idx = {n: i for i, n in enumerate(rows[1])}
    def col(r, name):
        return r[idx[name]].strip() if idx.get(name, -1) < len(r) else ''
    return rows[2:], idx, col

def verify(path, label):
    rows, idx, col = load(path)
    n = 0
    vh = vv = roll = pit = 0.0
    batt_events = []
    prev_cap = None
    for r in rows:
        if len(r) < 40:
            continue
        try:
            n += 1
            vh = max(vh, abs(float(col(r, 'OSD.hSpeed [MPH]') or 0)) * MPH)
            vv = max(vv, abs(float(col(r, 'OSD.zSpeed [MPH]') or 0)) * MPH)
            roll = max(roll, abs(float(col(r, 'OSD.roll') or 0)))
            pit = max(pit, abs(float(col(r, 'OSD.pitch') or 0)))
            cap = col(r, 'BATTERY.chargeLevel')
            gh = col(r, 'BATTERY.goHomeBattery')
            ts = col(r, 'OSD.flyTime [s]') or '0'
            if cap and gh and prev_cap is not None:
                c, g = float(cap), float(gh)
                # 首次跌破返航线 = 真实返航事件(与官方低电量逻辑一致)
                if prev_cap > g >= c and (not batt_events or batt_events[-1][0] != 'RTH'):
                    batt_events.append(('RTH', ts, c, g))
            prev_cap = float(cap) if cap else prev_cap
        except ValueError:
            continue
    print(f'[{label}] 帧数={n}')
    print(f'  包线峰值: h={vh:.2f}m/s v={vv:.2f}m/s roll={roll:.1f}° pitch={pit:.1f}°')
    for e in batt_events:
        print(f'  电池返航线穿越: t={e[1]}s 电量={e[2]}% 触线={e[3]}%')

if __name__ == '__main__':
    verify(sys.argv[1], '室内')
    verify(sys.argv[2], '室外')
