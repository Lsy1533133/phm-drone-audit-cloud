#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""协议层端到端回归(NullEngine降级模式 —— 开源包开箱可跑, 不依赖闭源引擎)。

验证: MQTT官方主题接入 / OSD解析与帧计数 / 设备在线跟踪 / REST API五端点 /
      NullEngine透传语义(零告警零webhook —— 规则判定属闭源引擎, 本包只证协议层)。
产出: protocol_e2e_result.json + sha256审计链。
"""
import asyncio
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))  # 平面布局: 测试位于包根
sys.path.insert(0, BASE)

HTTP_PORT = 18082
WEBHOOK_PORT = 18098
import tempfile
os.environ['PHM_HTTP_PORT'] = str(HTTP_PORT)
os.environ['PHM_WEBHOOK_URL'] = f'http://127.0.0.1:{WEBHOOK_PORT}/hook'
os.environ['PHM_CHAIN_DIR'] = tempfile.mkdtemp(prefix='phm_proto_chain_')  # 链文件写临时目录, 测试hermetic

from osd_simulator import gen_scenario  # noqa: E402
import phm_audit_cloud as cloud  # noqa: E402
import paho.mqtt.client as mqtt  # noqa: E402

BROKER_PORT = 18831
WEBHOOK_INBOX = []


def _start_broker():
    from amqtt.broker import Broker

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _make():
            global BROKER_REF
            BROKER_REF = Broker({'listeners': {'default': {'type': 'tcp', 'bind': f'127.0.0.1:{BROKER_PORT}'}},
                                 'auth': {'allow-anonymous': True}, 'sys_interval': 10})
            await BROKER_REF.start()
        loop.run_until_complete(_make())
        loop.run_forever()
    threading.Thread(target=_run, daemon=True).start()

    import socket
    for _ in range(50):
        try:
            s = socket.create_connection(('127.0.0.1', BROKER_PORT), timeout=0.4)
            s.close()
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError('amqtt broker not listening')


def _start_webhook():
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get('Content-Length') or 0)
            WEBHOOK_INBOX.append(json.loads(self.rfile.read(n) or b'{}'))
            self.send_response(200)
            self.end_headers()

    srv = HTTPServer(('127.0.0.1', WEBHOOK_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def main():
    checks = []

    def ck(name, cond, detail=''):
        checks.append({'name': name, 'pass': bool(cond), 'detail': str(detail)[:200]})
        print(('PASS ' if cond else 'FAIL ') + name + ' :: ' + str(detail)[:120], flush=True)

    _start_broker()
    _start_webhook()
    cloud.start_http(HTTP_PORT)
    engine_cls = type(cloud.ENGINE).__name__
    ck('NullEngine降级生效(引擎闭源包缺失)', engine_cls == 'NullEngine', engine_cls)

    cloudc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='protocol-cloud')
    cloudc.on_connect = cloud.on_connect
    cloudc.on_message = cloud.on_message
    cloudc.connect('127.0.0.1', BROKER_PORT, keepalive=60)
    cloudc.loop_start()
    time.sleep(0.8)

    simc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='protocol-sim')
    simc.connect('127.0.0.1', BROKER_PORT, keepalive=60)
    simc.loop_start()
    time.sleep(0.3)

    n_before = cloud.STATE['n_osd']
    simc.publish('sys/product/SN_PROTO_001/status', json.dumps({'data': {'status': 'ok'}}), qos=1)
    seq = gen_scenario('normal', 'SN_PROTO_001')
    for dt, m in seq:
        simc.publish('thing/product/SN_PROTO_001/osd', json.dumps(m), qos=1)
        time.sleep(min(dt, 0.02))
    simc.publish('thing/product/SN_PROTO_001/events',
                 json.dumps({'method': 'test_event', 'timestamp': int(time.time() * 1000), 'data': {}}), qos=1)
    time.sleep(1.2)

    def get(p):
        return json.load(urllib.request.urlopen(f'http://127.0.0.1:{HTTP_PORT}{p}', timeout=5))

    st = get('/stats')
    ck('OSD帧计数与模拟源一致', st['state']['n_osd'] - n_before == len(seq),
       f"delta={st['state']['n_osd'] - n_before} expect={len(seq)}")
    ck('NullEngine零告警透传', st['state']['n_alerts'] == 0, f"n_alerts={st['state']['n_alerts']}")
    ck('设备事件计数', st['state'].get('n_events', 0) >= 1, st['state'].get('n_events'))
    dv = get('/devices')
    ck('设备在线跟踪', 'SN_PROTO_001' in dv['online'], str(dv['online']))
    cfg = json.load(urllib.request.urlopen(urllib.request.Request(
        f'http://127.0.0.1:{HTTP_PORT}/config/SN_PROTO_001',
        data=json.dumps({'geofence': {'type': 'circle', 'lat': 22.817,
                                      'lon': 108.366, 'radius_m': 100}}).encode(),
        headers={'Content-Type': 'application/json'}), timeout=5))
    ck('REST /config降级模式可用', cfg.get('ok') is True, str(cfg))
    # 事件入链是设计语义(EVT记录也进黑匣子链): NullEngine下无告警链, 但事件链应存在且可验证
    cv = get('/chain/SN_PROTO_001/verify')
    ck('事件审计链生成且重算验证通过', cv.get('ok') is True and cv.get('records', 0) >= 1, str(cv))
    # v3.1: 全256位哈希 + 帧级入链 + 头部外锚
    import glob as _g
    _chain_p = os.path.join(cloud.CHAIN_DIR, 'flight_audit_SN_PROTO_001.jsonl')
    _laste = json.loads(open(_chain_p, encoding='utf-8').readlines()[-1])
    ck('哈希链_全256位', len(_laste['hash']) == 64, f"hash_len={len(_laste['hash'])}")
    ck('帧级入链_链记录数≥帧数+事件', _laste['n'] >= len(seq) + 1, f"chain_n={_laste['n']}")
    _anchor = json.load(open(os.path.join(cloud.CHAIN_DIR, 'chain_anchor_SN_PROTO_001.json')))
    ck('头部外锚_锚文件与链尾一致', _anchor['head'] == _laste['hash'], f"head={_anchor['head'][:16]}...")
    import shutil as _sh, hashlib as _hl
    _rw = _chain_p + '.rewritten'
    _rls = open(_chain_p, encoding='utf-8').readlines()
    _prev = 'GENESIS'; _newlines = []
    for _ln in _rls:
        _e = json.loads(_ln)
        if isinstance(_e['rec'].get('frame'), dict):
            _e['rec']['frame']['horizontalSpeed'] = 0.0   # 攻击者清洗遥测数据
        _blob = json.dumps(_e['rec'], sort_keys=True, ensure_ascii=False, default=str)
        _h = _hl.sha256((_prev + _blob).encode()).hexdigest()[:len(_e['hash'])]
        _e['prev'] = _prev; _e['hash'] = _h; _prev = _h
        _newlines.append(json.dumps(_e, ensure_ascii=False) + '\n')
    open(_rw, 'w', encoding='utf-8').writelines(_newlines)
    _ok_rw, _, _fin_rw = cloud.verify_chain(_rw, expect_head=_anchor['head'])
    ck('全文件清洗重写_外部锚点戳穿', _ok_rw is False, f'caught={_ok_rw is False}')
    os.remove(_rw)
    # 篡改检测: 改动任一记录任一字节 → 全链验证必须失败
    chain_path = os.path.join(cloud.CHAIN_DIR, 'flight_audit_SN_PROTO_001.jsonl')
    tam = chain_path + '.tampered'
    _lines = open(chain_path, encoding='utf-8').readlines()
    _e = json.loads(_lines[0]); _e['rec']['method'] = 'tampered_event'
    _lines[0] = json.dumps(_e, ensure_ascii=False) + '\n'
    open(tam, 'w', encoding='utf-8').writelines(_lines)
    _ok_tampered, _, _ = cloud.verify_chain(tam)
    os.remove(tam)
    ck('哈希链篡改检测生效', _ok_tampered is False, f'tampered_ok={_ok_tampered}')
    al = get('/alerts')
    ck('告警查询为空(NullEngine)', al['n'] == 0, f"n={al['n']}")
    time.sleep(1.5)
    ck('零webhook(无ALERT即无推送)', len(WEBHOOK_INBOX) == 0, f"hits={len(WEBHOOK_INBOX)}")

    n_pass = sum(1 for c in checks if c['pass'])
    blob = json.dumps({'checks': checks}, sort_keys=True, ensure_ascii=False)
    out = {'verdict': 'PASS' if n_pass == len(checks) else 'FAIL',
           'n_pass': n_pass, 'n_total': len(checks), 'engine_class': engine_cls,
           'mode': 'NullEngine passthrough (open package)',
           'checks': checks, 'ts': time.strftime('%F %T')}
    out['audit_sha256'] = hashlib.sha256(blob.encode()).hexdigest()[:16]
    path = os.path.join(BASE, 'protocol_e2e_result.json')
    json.dump(out, open(path, 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({k: out[k] for k in ('verdict', 'n_pass', 'n_total', 'audit_sha256')}), flush=True)
    cloudc.loop_stop()
    simc.loop_stop()
    return 0 if out['verdict'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
