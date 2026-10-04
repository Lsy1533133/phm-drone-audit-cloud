#!/usr/bin/env bash
# PHM审计云一键演示 —— 协议层回归 + DJI登录握手回归 (NullEngine模式, 无需闭源引擎)
# 用法: bash run_demo.sh   (依赖: pip install paho-mqtt amqtt)
set -e
cd "$(dirname "$0")"
python3 -c "import paho.mqtt" 2>/dev/null || pip3 install -q paho-mqtt
python3 -c "import amqtt" 2>/dev/null || pip3 install -q amqtt
echo "== 1/2 协议层端到端回归 (MQTT接入/OSD解析/REST/事件链/篡改检测) =="
python3 test_protocol_e2e.py
echo
echo "== 2/2 DJI登录握手回归 (官方语义对齐) =="
python3 test_dji_auth.py
echo
echo "== 完成: 根目录 *_result.json 查看带SHA审计链的完整结果 =="
