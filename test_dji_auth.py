#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DJI上云API登录握手流程实测 —— 模拟Pilot 2接入第一跳(不依赖MQTT)。

官方语义对齐(dji-sdk/DJI-Cloud-API-Demo: LoginController/UserServiceImpl/JwtUtil):
  ①错误用户名→401 invalid username  ②flag不匹配→account type does not match
  ③错误密码→401 invalid password    ④成功→code=0+JWT(access_token)+mqtt_addr+workspace_id
  ⑤JWT claims(user_id/username/user_type/workspace_id, iss=DJI, sub=CloudApiSample, age=86400s)
  ⑥token/refresh(x-auth-token)→新token  ⑦过期token仍可refresh(官方TokenExpiredException语义)
  ⑧篡改签名→拒绝
产出: dji_auth_flow_result.json + sha256审计链。
"""
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))  # 平面布局: 测试位于包根
sys.path.insert(0, BASE)

HTTP_PORT = 18083
os.environ['PHM_HTTP_PORT'] = str(HTTP_PORT)
os.environ['PHM_DATA_DIR'] = tempfile.mkdtemp(prefix='phm_auth_users_')

import phm_audit_cloud as cloud  # noqa: E402  (engine缺失→NullEngine, 本测试只验握手层)
import dji_auth  # noqa: E402


def main():
    checks = []

    def ck(name, cond, detail=''):
        checks.append({'name': name, 'pass': bool(cond), 'detail': str(detail)[:220]})
        print(('PASS ' if cond else 'FAIL ') + name + ' :: ' + str(detail)[:130], flush=True)

    cloud.start_http(HTTP_PORT)
    time.sleep(0.4)

    def post(p, body=None, token=None):
        req = urllib.request.Request(f'http://127.0.0.1:{HTTP_PORT}{p}',
                                     data=json.dumps(body or {}).encode(),
                                     headers={'Content-Type': 'application/json',
                                              **({'x-auth-token': token} if token else {})})
        return json.load(urllib.request.urlopen(req, timeout=5))

    # 用户种子(盐化存储)
    dji_auth.create_user('pilot_demo', 'Pilot@2026', user_id=1, user_type=2,
                         workspace_id='phm-workspace')

    r = post('/manage/api/v1/login', {'username': 'ghost', 'password': 'x', 'flag': 2})
    ck('①错误用户名→401 invalid username', r['code'] == 401 and r['message'] == 'invalid username', str(r))

    r = post('/manage/api/v1/login', {'username': 'pilot_demo', 'password': 'Pilot@2026', 'flag': 1})
    ck('②flag不匹配→account type', r['code'] == -1 and 'account type' in r['message'], str(r))

    r = post('/manage/api/v1/login', {'username': 'pilot_demo', 'password': 'wrong', 'flag': 2})
    ck('③错误密码→401 invalid password', r['code'] == 401 and r['message'] == 'invalid password', str(r))

    r = post('/manage/api/v1/login', {'username': 'pilot_demo', 'password': 'Pilot@2026', 'flag': 2})
    ck('④登录成功code=0', r['code'] == 0 and r['message'] == 'success', str(r)[:120])
    tok = r['data']['access_token']
    ck('④a响应含access_token+mqtt_addr+workspace_id',
       bool(tok) and 'mqtt_addr' in r['data'] and r['data']['workspace_id'] == 'phm-workspace',
       str({k: r['data'][k] for k in ('mqtt_addr', 'workspace_id')}))

    # JWT结构核验(独立解包: header.alg=HS256, claims齐全, iss/sub/age官方默认)
    h64, p64, s64 = tok.split('.')
    header = json.loads(__import__('base64').urlsafe_b64decode(h64 + '=='))
    payload = json.loads(__import__('base64').urlsafe_b64decode(p64 + '=='))
    ck('⑤JWT结构: HS256+官方claims', header['alg'] == 'HS256'
       and payload['iss'] == 'DJI' and payload['sub'] == 'CloudApiSample'
       and payload['exp'] - payload['iat'] == 86400
       and payload['username'] == 'pilot_demo' and payload['user_type'] == 2,
       str({k: payload[k] for k in ('iss', 'sub', 'user_id', 'user_type')}))
    ck('⑤a签名stdlib独立复算通过', dji_auth.jwt_verify(tok) is not None
       and dji_auth.jwt_verify(tok).get('_expired') is False)

    time.sleep(1.1)  # JWT无jti字段: 同claims+同iat会生成相同token, 间隔1.1s使iat推进以断言"换发"
    r2 = post('/manage/api/v1/token/refresh', {}, token=tok)
    tok2 = r2['data']['access_token']
    ck('⑥refresh换发新token且claims保留', r2['code'] == 0 and tok2 != tok
       and dji_auth.jwt_verify(tok2)['user_id'] == 1, str({k: r2['data'][k] for k in ('username', 'user_id')}))

    forged = tok[:-4] + ('AAAA' if tok[-4:] != 'AAAA' else 'BBBB')
    ck('⑧篡改签名→拒绝', dji_auth.jwt_verify(forged) is None)

    # ⑦过期token仍可refresh(官方TokenExpiredException语义): 造一个exp已过的合法签名token
    now = int(time.time())
    expired = dji_auth.jwt_create({'user_id': 1, 'username': 'pilot_demo', 'user_type': 2,
                                   'workspace_id': 'phm-workspace'}, age=-10)
    pl = dji_auth.jwt_verify(expired)
    ck('⑦过期token可续签(官方语义)', pl is not None and pl['_expired'] is True
       and dji_auth.refresh(expired)['code'] == 0, f"expired={pl.get('_expired')}")

    n_pass = sum(1 for c in checks if c['pass'])
    blob = json.dumps({'checks': checks}, sort_keys=True, ensure_ascii=False)
    out = {'verdict': 'PASS' if n_pass == len(checks) else 'FAIL',
           'n_pass': n_pass, 'n_total': len(checks),
           'mode': 'DJI Cloud API login handshake (stdlib JWT HS256)',
           'align_source': 'dji-sdk/DJI-Cloud-API-Demo LoginController/UserServiceImpl/JwtUtil/HttpResultResponse',
           'checks': checks, 'ts': time.strftime('%F %T')}
    out['audit_sha256'] = hashlib.sha256(blob.encode()).hexdigest()
    path = os.path.join(BASE, 'dji_auth_flow_result.json')
    json.dump(out, open(path, 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({k: out[k] for k in ('verdict', 'n_pass', 'n_total', 'audit_sha256')}), flush=True)
    return 0 if out['verdict'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
