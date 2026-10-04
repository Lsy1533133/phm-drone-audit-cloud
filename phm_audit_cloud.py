#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PHM审计云 —— DJI上云API协议胶水（开源部分）。

订阅官方主题(常量与dji-sdk/DJI-Cloud-API-Demo TopicConst.java逐字对齐):
  sys/product/{sn}/status        设备上下线
  thing/product/{sn}/osd         OSD遥测推送(horizontalSpeed/verticalSpeed/attitudeRoll/...)
  thing/product/{sn}/events      设备事件

告警发布(本云自有业务主题, 属上云API允许的扩展区, 文档见docs/协议映射.md):
  phm/audit/{sn}/alerts          PHM告警(RETAIN)
  phm/audit/{sn}/quality         L1数据质量告警(RETAIN)

引擎边界: 校验判定全部走 engine.interface.PHMEngineInterface,
闭源引擎(engine.phm_drone_stream)缺失时自动降级NullEngine(只透传不判警)。
"""
import json
import os
import sys
import time

import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine_interface import PHMEngineInterface, NullEngine  # noqa: E402
import dji_auth  # noqa: E402

TOPIC_STATUS = 'sys/product/{sn}/status'
TOPIC_OSD = 'thing/product/{sn}/osd'
TOPIC_EVENTS = 'thing/product/{sn}/events'
TOPIC_ALERTS = 'phm/audit/{sn}/alerts'
TOPIC_QUALITY = 'phm/audit/{sn}/quality'

STATE = {'online': set(), 'n_osd': 0, 'n_alerts': 0}


def load_engine():
    """闭源引擎存在则加载, 否则降级NullEngine。"""
    try:
        from engine.phm_drone_stream import PHMDroneStreamEngine
        eng = PHMDroneStreamEngine()
        print('[cloud] engine=PHMDroneStreamEngine (closed, loaded)', flush=True)
        return eng
    except ImportError:
        print('[cloud] engine package absent -> NullEngine (passthrough)', flush=True)
        return NullEngine()


ENGINE: PHMEngineInterface = load_engine()


def _sn_from_topic(topic, suffix):
    # thing/product/<sn>/osd
    parts = topic.split('/')
    return parts[2] if len(parts) >= 4 and topic.endswith(suffix) else None


def publish_alert(clc, sn, rec):
    topic = TOPIC_ALERTS.format(sn=sn) if rec['caliber'] == 'L2物理' else TOPIC_QUALITY.format(sn=sn)
    payload = json.dumps({'sn': sn, 'engine_ver': getattr(ENGINE, '__version__', 'null'),
                          **rec}, ensure_ascii=False)
    clc.publish(topic, payload, qos=1, retain=True)
    chain_append(sn, rec)
    ALERT_LOG.append({'sn': sn, 'wall_ts': int(time.time() * 1000), **rec})
    STATE['n_alerts'] += 1
    if rec['caliber'] == 'L2物理' and rec['level'] == 'ALERT':
        _ch = FLIGHT_CHAINS.get(sn) or {}
        _fire_webhooks({'type': 'ALERT', **{k: rec[k] for k in
                        ('rule', 'level', 'caliber', 'measured', 'limit', 'ts')}, 'sn': sn,
                        'chain_head': _ch.get('prev'), 'chain_n': _ch.get('n')})
    print(f'[ALERT] {topic} :: {rec["rule"]} {rec["level"]} measured={rec["measured"]} '
          f'limit={rec["limit"]} frames={rec["frames_confirmed"]}', flush=True)


# ---- 第三层: 数字黑匣子 v3.1 —— 告警/事件/遥测帧全量入链, 全256位哈希, 重启安全续链, 头部外锚 ----
import hashlib

FLIGHT_CHAINS = {}
CHAIN_DIR = os.environ.get('PHM_CHAIN_DIR',
                           os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results'))


def _chain_state(sn):
    """链状态(内存缓存+文件续链): 进程重启后从链文件尾恢复prev/n, 保证重启不断链。"""
    ch = FLIGHT_CHAINS.get(sn)
    if ch is None:
        ch = {'prev': 'GENESIS', 'n': 0}
        p = os.path.join(CHAIN_DIR, f'flight_audit_{sn}.jsonl')
        if os.path.exists(p):
            try:
                with open(p, 'rb') as f:
                    f.seek(0, os.SEEK_END)
                    f.seek(max(0, f.tell() - 8192))
                    tail = f.read().decode('utf-8', errors='replace').strip().splitlines()
                if tail:
                    last = json.loads(tail[-1])
                    ch['prev'], ch['n'] = last['hash'], int(last.get('n', 0))
            except Exception:
                pass  # 链文件损坏时从GENESIS重起(verify_chain会暴露断裂), 不静默伪造
        FLIGHT_CHAINS[sn] = ch
    return ch


def chain_append(sn, rec):
    """prev_hash + record → sha256全256位逐条链接; 落盘 results/flight_audit_{sn}.jsonl;
    同时原子更新外锚文件 chain_anchor_{sn}.json(生产环境应将head哈希推送至客户侧独立留存)。"""
    ch = _chain_state(sn)
    blob = json.dumps(rec, sort_keys=True, ensure_ascii=False, default=str)
    h = hashlib.sha256((ch['prev'] + blob).encode()).hexdigest()      # v3.1: 全256位(旧64位截断链仍可由verify_chain兼容验证)
    os.makedirs(CHAIN_DIR, exist_ok=True)
    with open(os.path.join(CHAIN_DIR, f'flight_audit_{sn}.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps({'n': ch['n'] + 1, 'prev': ch['prev'], 'hash': h,
                            'ts': rec.get('ts'), 'rec': rec}, ensure_ascii=False) + '\n')
    ch['prev'], ch['n'] = h, ch['n'] + 1
    _ap = os.path.join(CHAIN_DIR, f'chain_anchor_{sn}.json')
    _tmp = _ap + '.tmp'
    with open(_tmp, 'w', encoding='utf-8') as f:
        json.dump({'sn': sn, 'n': ch['n'], 'head': ch['prev'],
                   'ts': int(time.time() * 1000)}, f)
    os.replace(_tmp, _ap)
    return h


def verify_chain(path, expect_head=None):
    """重算全链: 返回(ok, n, final_hash)。任一记录被篡改→False。
    兼容旧64位截断链(hash长度16)与新全256位链; expect_head提供外部锚定时, 尾哈希必须匹配。"""
    prev = 'GENESIS'
    n = 0
    for line in open(path, encoding='utf-8'):
        e = json.loads(line)
        blob = json.dumps(e['rec'], sort_keys=True, ensure_ascii=False, default=str)
        h = hashlib.sha256((prev + blob).encode()).hexdigest()
        if e['hash'] != (h if len(e['hash']) == 64 else h[:16]) or e['prev'] != prev:
            return False, n, prev
        prev = e['hash']          # 链内沿用记录存储哈希(新旧长度混合链均可验证)
        n += 1
    if expect_head is not None and prev != expect_head:
        return False, n, prev
    return True, n, prev


# ---- v2: webhook通知(L2 ALERT触达外部系统) + events入审计链 ----
import threading
import urllib.request
from collections import deque

WEBHOOK_URLS = [u for u in (os.environ.get('PHM_WEBHOOK_URL'),) if u]
ALERT_LOG = deque(maxlen=2000)
STATE.setdefault('n_events', 0)
STATE.setdefault('n_webhooks', 0)


def _fire_webhooks(body):
    for u in WEBHOOK_URLS:
        def _post(url=u):
            try:
                req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                             headers={'Content-Type': 'application/json'})
                urllib.request.urlopen(req, timeout=5)
                STATE['n_webhooks'] += 1
            except Exception as e:
                print(f'[webhook] {url} err: {str(e)[:60]}', flush=True)
        threading.Thread(target=_post, daemon=True).start()


# ---- v2: REST查询/配置API (stdlib实现, 零新依赖) ----
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


class _RestHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path == '/stats':
                s = {k: (len(v) if isinstance(v, set) else v) for k, v in STATE.items()}
                return self._json(200, {'state': s,
                                        'engine': getattr(ENGINE, '__version__', 'null'),
                                        'engine_class': type(ENGINE).__name__})
            if u.path == '/devices':
                return self._json(200, {'online': sorted(STATE['online']),
                                        'chains': sorted(FLIGHT_CHAINS.keys())})
            if u.path == '/alerts':
                sn = parse_qs(u.query).get('sn', [None])[0]
                rows = [r for r in ALERT_LOG if sn is None or r['sn'] == sn]
                return self._json(200, {'n': len(rows), 'rows': rows[-200:]})
            if u.path.startswith('/chain/') and u.path.endswith('/verify'):
                sn = u.path.split('/')[2]
                p = os.path.join(CHAIN_DIR, f'flight_audit_{sn}.jsonl')
                if not os.path.exists(p):
                    return self._json(404, {'error': 'no chain for sn', 'sn': sn})
                _anchor = parse_qs(u.query).get('anchor', [None])[0]
                _anch = None
                _ap = os.path.join(CHAIN_DIR, f'chain_anchor_{sn}.json')
                if os.path.exists(_ap):
                    try: _anch = json.load(open(_ap)).get('head')
                    except Exception: _anch = None
                if _anchor is not None:
                    _anch = _anchor          # 调用方自带的外部锚定值优先(客户侧独立留存)
                ok, n, fin = verify_chain(p, expect_head=_anch)
                return self._json(200, {'sn': sn, 'ok': ok, 'records': n, 'final_hash': fin,
                                        'anchor_checked': _anch is not None,
                                        'anchor_match': (fin == _anch) if _anch else None})
            return self._json(404, {'error': 'not found'})
        except Exception as e:
            return self._json(500, {'error': str(e)[:120]})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == '/manage/api/v1/login':
            # DJI上云API官方登录语义(Pilot 2接入第一跳): {username,password,flag}
            try:
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length) or b'{}')
                return self._json(200, dji_auth.login(str(body.get('username', '')),
                                                      str(body.get('password', '')),
                                                      body.get('flag', 0)))
            except Exception as e:
                return self._json(500, {'error': str(e)[:120]})
        if u.path == '/manage/api/v1/token/refresh':
            try:
                tok = self.headers.get('x-auth-token', '')
                return self._json(200, dji_auth.refresh(tok))
            except Exception as e:
                return self._json(500, {'error': str(e)[:120]})
        if u.path.startswith('/config/'):
            sn = u.path.split('/')[2]
            try:
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length) or b'{}')
                ENGINE.configure(sn, geofence=body.get('geofence'),
                                 profile=body.get('profile'))
                return self._json(200, {'ok': True, 'sn': sn,
                                        'geofence': body.get('geofence'),
                                        'profile': body.get('profile')})
            except AssertionError as e:
                return self._json(400, {'error': str(e)})
            except Exception as e:
                return self._json(500, {'error': str(e)[:120]})
        return self._json(404, {'error': 'not found'})


def start_http(port=None):
    port = int(port or os.environ.get('PHM_HTTP_PORT', '18081'))
    srv = ThreadingHTTPServer(('0.0.0.0', port), _RestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f'[cloud] REST API on :{port} '
          '(/stats /devices /alerts?sn= /chain/{sn}/verify POST /config/{sn})', flush=True)
    return srv


def on_message(clc, userdata, msg):
    try:
        if msg.topic.endswith('/osd'):
            sn = _sn_from_topic(msg.topic, '/osd')
            if not sn:
                return
            payload = json.loads(msg.payload.decode('utf-8', errors='replace'))
            data = payload.get('data', payload)  # 协议OSD包在data字段, 容错裸字段
            ts = int(payload.get('timestamp') or payload.get('bid') or time.time() * 1000)
            STATE['n_osd'] += 1
            chain_append(sn, {'rule': 'OSD_FRAME', 'level': 'FRAME', 'ts': ts, 'frame': data})
            for rec in ENGINE.on_osd(sn, ts, data):
                publish_alert(clc, sn, rec)
        elif msg.topic.endswith('/status'):
            sn = _sn_from_topic(msg.topic, '/status')
            payload = json.loads(msg.payload.decode('utf-8', errors='replace'))
            st = payload.get('data', payload)
            online = str(st.get('status', '')).lower() != 'offline'
            (STATE['online'].add if online else STATE['online'].discard)(sn)
            ENGINE.on_status(sn, online)
            print(f'[status] {sn} online={online}', flush=True)
        elif msg.topic.endswith('/events'):
            payload = json.loads(msg.payload.decode('utf-8', errors='replace'))
            STATE['n_events'] += 1
            sn = _sn_from_topic(msg.topic, '/events')
            if sn:
                chain_append(sn, {'rule': 'EVT', 'level': 'EVENT',
                                  'method': str(payload.get('method', ''))[:60],
                                  'ts': int(payload.get('timestamp') or time.time() * 1000)})
    except Exception as e:  # 单条消息解析失败不影响整体
        print(f'[err] {msg.topic}: {e}', flush=True)


def on_connect(clc, userdata, flags, rc, props=None):
    subs = ['sys/product/+/status', 'thing/product/+/osd', 'thing/product/+/events']
    for t in subs:
        clc.subscribe(t, qos=1)
    print(f'[cloud] connected rc={rc}, subscribed {subs}', flush=True)


def main(host=None, port=None):
    host = host or os.environ.get('PHM_MQTT_HOST', '127.0.0.1')
    port = int(port or os.environ.get('PHM_MQTT_PORT', '18830'))
    start_http()
    clc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='phm-audit-cloud')
    clc.on_connect = on_connect
    clc.on_message = on_message
    clc.reconnect_delay_set(min_delay=1, max_delay=30)
    clc.connect(host, port, keepalive=60)
    print(f'[cloud] PHM audit cloud up on {host}:{port} '
          f'(engine={"real" if not isinstance(ENGINE, NullEngine) else "null"})', flush=True)
    clc.loop_forever()


if __name__ == '__main__':
    main()
