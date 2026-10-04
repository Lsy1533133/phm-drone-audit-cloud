#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DJI上云API 登录握手(Pilot 2 → 第三方云) — 与dji-sdk/DJI-Cloud-API-Demo官方实现对齐。

官方语义(源码逐字核对: LoginController/UserServiceImpl/JwtUtil/HttpResultResponse):
  POST /manage/api/v1/login          body={username,password,flag}
      → 校验顺序: 用户存在 → flag==user_type → 密码 → workspace
      → {code:0, message:"success", data:{access_token, username, user_id, user_type,
                                          workspace_id, mqtt_addr}}
  POST /manage/api/v1/token/refresh  头 x-auth-token → 续签(官方: 过期token也可续, 本实现同语义)
JWT: HMAC-SHA256(stdlib零依赖), claims={user_id,username,user_type,workspace_id},
     iss="DJI", sub="CloudApiSample"(官方默认), age=86400s。
差异声明: 官方demo密码明文存储, 本实现盐化sha256(接口语义不变, 安全加强)。
MQTT凭证: Pilot 2侧由App Key/Secret/License三件套端侧推导后直连MQTT broker;
          云端需在生产EMQX配置对应认证(本仓库测试broker为allow-anonymous, 如实标注)。
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

DATA_DIR = os.environ.get('PHM_DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
JWT_SECRET = os.environ.get('PHM_JWT_SECRET', 'CloudApiSample')   # 官方默认值; 生产必须改
JWT_ISS = 'DJI'
JWT_SUB = 'CloudApiSample'
JWT_AGE = int(os.environ.get('PHM_JWT_AGE', '86400'))
USERS_PATH = os.path.join(DATA_DIR, 'users.json')


# ---- JWT (HS256, stdlib) ----
def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def jwt_create(claims: dict, secret: str = None, age: int = None) -> str:
    now = int(time.time())
    header = {'alg': 'HS256', 'typ': 'JWT'}
    payload = {**claims, 'iss': JWT_ISS, 'sub': JWT_SUB,
               'iat': now, 'exp': now + (age or JWT_AGE), 'nbf': now}
    h = _b64u(json.dumps(header, separators=(',', ':')).encode())
    p = _b64u(json.dumps(payload, separators=(',', ':')).encode())
    sig = _b64u(hmac.new((secret or JWT_SECRET).encode(), f'{h}.{p}'.encode(), hashlib.sha256).digest())
    return f'{h}.{p}.{sig}'


def jwt_verify(token: str, secret: str = None):
    """返回claims dict; 无效/过期→None(过期token仍可decode供refresh, 与官方语义一致)。"""
    try:
        h, p, s = token.split('.')
        exp_sig = _b64u(hmac.new((secret or JWT_SECRET).encode(), f'{h}.{p}'.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(s, exp_sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(p + '=='))
        payload['_expired'] = payload.get('exp', 0) < time.time()
        return payload
    except Exception:
        return None


# ---- 用户存储 (盐化sha256) ----
def _load_users() -> dict:
    if not os.path.exists(USERS_PATH):
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({}, open(USERS_PATH, 'w'))
    try:
        return json.load(open(USERS_PATH))
    except Exception:
        return {}


def create_user(username: str, password: str, user_id: int, user_type: int,
                workspace_id: str = 'phm-workspace'):
    """创建/覆盖用户。user_type须与登录flag一致(官方语义)。"""
    users = _load_users()
    salt = secrets.token_hex(8)
    users[username] = {'salt': salt,
                       'pwd': hashlib.sha256((salt + password).encode()).hexdigest(),
                       'user_id': user_id, 'user_type': user_type,
                       'workspace_id': workspace_id}
    json.dump(users, open(USERS_PATH, 'w'), ensure_ascii=False, indent=1)
    return users[username]


# ---- 登录/续签 (官方校验顺序逐字对齐) ----
def login(username: str, password: str, flag: int, mqtt_addr: str = None) -> dict:
    users = _load_users()
    u = users.get(username)
    if u is None:
        return {'code': 401, 'message': 'invalid username', 'data': None}
    if int(flag) != int(u['user_type']):
        return {'code': -1, 'message': 'The account type does not match.', 'data': None}
    if hashlib.sha256((u['salt'] + password).encode()).hexdigest() != u['pwd']:
        return {'code': 401, 'message': 'invalid password', 'data': None}
    claims = {'user_id': u['user_id'], 'username': username,
              'user_type': u['user_type'], 'workspace_id': u['workspace_id']}
    token = jwt_create(claims)
    return {'code': 0, 'message': 'success',
            'data': {'access_token': token, 'username': username,
                     'user_id': u['user_id'], 'user_type': u['user_type'],
                     'workspace_id': u['workspace_id'],
                     'mqtt_addr': mqtt_addr or os.environ.get(
                         'PHM_MQTT_ADDR', 'tcp://127.0.0.1:1883')}}


def refresh(token: str) -> dict:
    claims = jwt_verify(token)
    if claims is None:
        return {'code': 401, 'message': 'token invalid', 'data': None}
    claims.pop('_expired', None)
    claims.pop('iss', None); claims.pop('sub', None)
    claims.pop('iat', None); claims.pop('exp', None); claims.pop('nbf', None)
    new_token = jwt_create({k: v for k, v in claims.items()
                            if k in ('user_id', 'username', 'user_type', 'workspace_id')})
    return {'code': 0, 'message': 'success',
            'data': {'access_token': new_token, 'username': claims.get('username'),
                     'user_id': claims.get('user_id'), 'user_type': claims.get('user_type'),
                     'workspace_id': claims.get('workspace_id'),
                     'mqtt_addr': os.environ.get('PHM_MQTT_ADDR', 'tcp://127.0.0.1:1883')}}
