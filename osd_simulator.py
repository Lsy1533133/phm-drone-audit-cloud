#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OSD模拟源 —— 合成DJI上云API OSD遥测流（开源部分, 用于无真机演示/回归）。

生成thing/product/{sn}/osd标准报文, 场景:
  ── v1 (RC手持机档, OsdRcDrone schema) ──
  normal     30帧正常爬升巡航(速度0→14, 姿态±15°内)
  v_burst    连续4帧速度26m/s(mavic档21超限→ALERT)
  accel_seq  连续大加速(fpv档: 20→38→56 @200ms → ~90m/s²>70 → ALERT)
  roll_burst 连续3帧roll 55°(mavic档45超限→ALERT)
  spike      单帧速度99(下一帧恢复 → 仅L1_SPEED_SPIKE WARN, 不判ALERT)
  dup        重复帧(dt<20ms → L1_DUP)
  jump       坐标瞬移(>500m/帧 → L1_JUMP 数据缺陷)
  ── v2 (机场/行业机档, OsdDockDrone schema, 37字段) ──
  dock_normal 行业机正常巡航(rid=True, 电量80, gps=14, 风5)
  batt_drop   电量30→26→22→8→6(drop骤降ALERT + 低电量RTH WARN + 迫降线LAND WARN→ALERT)
  gps_loss    卫星12→10→3→2→12(GPS_WEAK WARN + GPS_LOSS WARN→ALERT)
  wind_high   风速14/15/16连续3帧(dock档wind_max=12 → WIND_HIGH WARN)
  rid_off     Remote ID广播关5帧(RID_OFF WARN×1, 监管合规)
  geofence    直线飞出100m圆形围栏(GEOFENCE_NEAR WARN + GEOFENCE ALERT)
"""
import json
import math
import random
import time


def osd_msg(sn, ts_ms, lat, lon, h, vh, vv, head, pitch, roll, mode=2, **extra):
    data = {'latitude': lat, 'longitude': lon, 'height': h, 'elevation': 50.0,
            'horizontalSpeed': vh, 'verticalSpeed': vv,
            'attitudeHead': head, 'attitudePitch': pitch, 'attitudeRoll': roll,
            'modeCode': mode}
    data.update(extra)  # battery/positionState/windSpeed/ridState等(dock档)
    return {'tid': f'{sn}-{ts_ms}', 'bid': f'{sn}-b', 'timestamp': ts_ms, 'data': data}


def gen_scenario(name, sn='SIM7C0DE'):
    """生成(间隔秒, osd_msg)序列。"""
    msgs = []
    lat, lon = 22.8170, 108.3660  # 南宁基准点(示例)
    t0 = int(time.time() * 1000)
    random.seed(42)
    DEG = 1.0 / 111320.0  # 1米对应纬度度数 → 位置移动与上报速度物理一致

    def step(i, dt_ms, vh, vv=0.0, pitch=0.0, roll=0.0, h=100.0, lat_d=0.0, lon_d=0.0,
             mode=2, **extra):
        nonlocal lat, lon
        lat += lat_d
        lon += lon_d
        msgs.append((dt_ms / 1000.0,
                     osd_msg(sn, t0 + sum(int(m[0] * 1000) for m in msgs) + dt_ms,
                             round(lat, 7), round(lon, 7), h, vh, vv,
                             90.0, pitch, roll, mode, **extra)))

    if name == 'normal':
        for i in range(30):
            vh = min(14.0, i * 0.8)
            step(i, 1000, round(vh, 1), 0.0, random.uniform(-12, 12), random.uniform(-12, 12),
                 100 + i * 0.5, lat_d=vh * DEG)
    elif name == 'v_burst':
        for i in range(30):
            vh = 14.0 if i < 20 else (26.0 if i < 24 else 14.0)
            step(i, 1000, vh, lat_d=vh * DEG)
    elif name == 'accel_seq':
        speeds = [20, 38, 56, 40, 20, 5]
        for i, vh in enumerate(speeds * 3):
            step(i, 200, vh, lat_d=vh * 0.2 * DEG)
    elif name == 'roll_burst':
        for i in range(30):
            r = 55.0 if 20 <= i < 23 else random.uniform(-12, 12)
            step(i, 1000, 10.0, roll=r, lat_d=10 * DEG)
    elif name == 'spike':
        for i in range(30):
            vh = 99.0 if i == 15 else 12.0
            step(i, 1000, vh, lat_d=12 * DEG)
    elif name == 'dup':
        for i in range(30):
            step(i, 1000, 8.0, lat_d=8 * DEG)
            if i == 10:  # 插入重复帧
                m = msgs[-1][1]
                msgs.append((0.01, dict(m, tid=m['tid'] + '-dup')))
    elif name == 'jump':
        for i in range(30):
            dl = 0.006 if i == 18 else 8 * DEG  # 单帧瞬移~668m
            step(i, 1000, 8.0, lon_d=dl)
    # ---- v2 dock/行业机场景 ----
    elif name == 'dock_normal':
        for i in range(30):
            step(i, 1000, 10.0, roll=random.uniform(-10, 10),
                 lat_d=10 * DEG, windSpeed=5.0, ridState=True,
                 battery={'capacityPercent': 80, 'landingPower': 10, 'returnHomePower': 20},
                 positionState={'gpsNumber': 14, 'quality': 5})
    elif name == 'batt_drop':
        caps = [80, 30, 22, 16, 8, 6, 6, 6]
        for i, cap in enumerate(caps):
            step(i, 1000, 10.0, lat_d=10 * DEG, windSpeed=4.0, ridState=True,
                 battery={'capacityPercent': cap, 'landingPower': 10, 'returnHomePower': 20},
                 positionState={'gpsNumber': 14, 'quality': 5})
    elif name == 'gps_loss':
        gps = [14, 10, 4, 3, 2, 12, 13]
        for i, gn in enumerate(gps):
            step(i, 1000, 10.0, lat_d=10 * DEG, windSpeed=4.0, ridState=True,
                 battery={'capacityPercent': 70, 'landingPower': 10, 'returnHomePower': 20},
                 positionState={'gpsNumber': gn, 'quality': 5 if gn > 5 else 2})
    elif name == 'wind_high':
        for i in range(30):
            w = [14.0, 15.0, 16.0][i - 10] if 10 <= i < 13 else 5.0
            step(i, 1000, 10.0, lat_d=10 * DEG, windSpeed=w, ridState=True,
                 battery={'capacityPercent': 70, 'landingPower': 10, 'returnHomePower': 20},
                 positionState={'gpsNumber': 14, 'quality': 5})
    elif name == 'rid_off':
        for i in range(30):
            step(i, 1000, 10.0, lat_d=10 * DEG, windSpeed=4.0,
                 ridState=(i < 10 or i >= 15),  # 第10-14帧广播关闭
                 battery={'capacityPercent': 70, 'landingPower': 10, 'returnHomePower': 20},
                 positionState={'gpsNumber': 14, 'quality': 5})
    elif name == 'geofence':
        for i in range(30):  # 从围栏圆心直线飞出, 12m/帧, 第9帧出100m半径
            step(i, 1000, 12.0, lat_d=12 * DEG, windSpeed=4.0, ridState=True,
                 battery={'capacityPercent': 70, 'landingPower': 10, 'returnHomePower': 20},
                 positionState={'gpsNumber': 14, 'quality': 5})
    else:
        raise ValueError(name)
    return msgs


def main():
    import argparse
    import paho.mqtt.client as mqtt
    ap = argparse.ArgumentParser()
    ap.add_argument('--scenario', default='normal')
    ap.add_argument('--sn', default='SIM7C0DE')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=18830)
    args = ap.parse_args()

    clc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'sim-{args.sn}')
    clc.connect(args.host, args.port, keepalive=60)
    clc.loop_start()
    time.sleep(0.3)
    # 上线状态
    clc.publish(f'sys/product/{args.sn}/status',
                json.dumps({'data': {'status': 'ok'}}), qos=1)
    seq = gen_scenario(args.scenario, args.sn)
    for dt, m in seq:
        clc.publish(f'thing/product/{args.sn}/osd', json.dumps(m), qos=1)
        time.sleep(dt)
    time.sleep(0.8)
    clc.loop_stop()
    print(f'[sim] scenario={args.scenario} frames={len(seq)} done', flush=True)


if __name__ == '__main__':
    main()
