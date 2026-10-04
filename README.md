# PHM Audit Cloud for DJI Cloud API

**PHM审计云** —— 基于DJI上云API(Cloud API)官方协议的无人机飞行安全审计层。
订阅官方MQTT遥测主题, 以物理定律实时校验每一帧OSD, 输出**三口径分离**的告警:
数据缺陷(L1) vs 物理违规(L2), 单帧候选(WARN) vs 多帧确认(ALERT)。

```
DJI Pilot 2 ──MQTT──> [PHM审计云] ──alerts──> 调度/保险/监管看板
   │官方协议              │
   └─ thing/product/{sn}/osd ──> 引擎流式校验(机型化四档) ──> L1质量/L2物理双口径
```

## 是什么

- **协议胶水(本仓库, 开源)**: 官方主题订阅 + OSD解析 + 告警发布 + REST查询/配置API + webhook推送。
  主题常量与dji-sdk/DJI-Cloud-API-Demo `TopicConst.java` 逐字对齐; OSD字段表对齐
  `OsdRcDrone.java`(RC手持机24字段)与`OsdDockDrone.java`(机场/行业机37字段)。
- **机型化五档参数表**: tello / mavic / agras / fpv / **dock(行业机)** —— 同一校验器, 分档阈值
  (公开规格+保守余量, 全部可配)。单一全局阈值在不同机型上大量误报(实测教训)。
- **多帧确认**: 速度/加速度/电池/丢星连续2帧、姿态连续3帧才升级ALERT; 电子围栏越界单帧即ALERT(硬边界)。
- **L1/L2口径分离**: 数据缺陷(重复帧/断流/坐标跳变/速度-位移背离/GPS降级)归L1质量告警;
  真实越界归L2物理告警。**脏帧不参与L2连续性计数** —— 零误报主张的前提。
- **监管合规规则(dock档)**: Remote ID广播缺失告警、大疆限飞区接近状态透传、电子围栏(per-SN运行时配置)。
- **登录握手(DJI官方语义)**: POST /manage/api/v1/login + /token/refresh, JWT HS256(stdlib), 与官方LoginController/UserServiceImpl/JwtUtil源码对齐; 盐化密码存储(官方demo明文, 此处加强)。
- **飞行数字黑匣子 v3.1**: 每个OSD帧+告警+事件append-only入**全256位**哈希链, 重启安全续链, 头部外锚(锚文件+webhook携带chain_head+REST ?anchor=验证), 篡改/全文件重写均可检。

## 不是什么

- 不控制无人机(审计云对设备只读, 不使用services/DRC下行)。
- 不是适航认证。参数表在真机标定前是"档位错配防护"。
- 引擎实现不在本仓库(见下)。

## 引擎边界（闭源说明）

校验判定逻辑位于 `engine/phm_drone_stream.py`, 为闭源模块, **不在本开源仓库分发**。
本仓库提供:
- `engine/interface.py` — 引擎接口契约(开源), 胶水只依赖此接口
- 引擎缺失时自动降级 `NullEngine`(透传不判警), 协议层可独立运行演示

## 快速开始

```bash
pip install paho-mqtt amqtt   # amqtt仅测试用(进程内broker)

python tests/test_e2e.py      # 9场景端到端回归, 产出 results/e2e_result.json
```

接真机/真云: 部署任意MQTT broker(推荐EMQX), DJI Pilot 2自定义云地址指向之,
运行 `python phm_audit_cloud.py`。告警落 `phm/audit/{sn}/alerts` 与 `phm/audit/{sn}/quality`(RETAIN)。

## 已验证行为

23/23端到端场景PASS(sha256审计链见 `results/e2e_result.json`), 13场景:
正常零告警 / 速度·加速度·姿态·电池·丢星越界多帧确认ALERT / 单帧尖峰只WARN /
重复帧·断流·坐标跳变·速度-位移背离归L1质量 / 电子围栏越界+接近预警 /
Remote ID监管告警 / 风载建议告警 / 飞行数字黑匣子(逐条哈希链+篡改检测) /
REST五端点(/stats /devices /alerts /chain/verify /config) / webhook L2推送。

**引擎延迟实测**(10万帧, 单核): p50=3.5µs, p99=8.5µs, ≈10.1万帧/秒
(1Hz OSD → 单核审计≈10万架; 10Hz → ≈1万架/核)。

完整映射与口径设计: [docs/协议映射.md](docs/协议映射.md)

## 边界与诚实声明

- 当前验证基于**模拟OSD源**(协议合规回放), 真机联调进行中(需带屏遥控器行业机)。
- OSD推送频率自适应(中位dt), 但真机GPS野值率需真机数据复核。
- 本项目与DJI无隶属关系, 上云API为DJI公开协议。
