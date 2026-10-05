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

## 真实DJI真机遥测实测(2026-10-05)

验证不止于模拟OSD源——已用**公开渠道真实DJI真机飞行数据**完成流式实测:

- 数据源: GitHub公开仓库 `Butterbananenbrot/FlightViewer` 中真实用户导出的 **DJI Neo** 飞行记录CSV
  (191列OSD/电池全字段, Airdata式导出), 两段飞行:
  - 室内 220秒 / 2,198帧 (VPS光流定位, GPS全程未用, 坐标键缺省)
  - 室外 344秒 / 3,436帧 (GPS定位, 北纬52.99°)
- 结果: **5,634帧全部喂入引擎(mavic档), L2物理违规=0; 告警6条记录(4个规则事件)全部为
  L1质量/环境层**(室内GPS丢星 gpsNum=1<3; 真实电池返航线穿越 87.6s电量30%触线30 /
  310.1s电量25%触线25), 含WARN与ALERT两级, **未触发任何姿态/速度类L2告警**
  (室外包线峰值 h=8.5m/s/阈值21, roll=22.8°/阈值45)。
- 逐条告警含 触发时刻/实测值/阈值, 审计链:
  室内 `678e00778bc5…` / 室外 `c83e955d3d56…`
- 口径: 判定层为流式引擎对真机数据的离线回放; mavic档阈值高于Neo规格上限——本实测检验
  **正常飞行不误报**, 不检验边界告警灵敏度; 经Pilot 2与行业实体机在线端到端闭环属下一步。
- PX4公开坠机日志库(47.2万条)回放另见 `phm/audit` 引擎报告: 姿态失守告警比不可恢复倒扣
  **提前1.3秒**(20Hz姿态流)。

## API 使用指南(集成商5步)

```text
1. developer.dji.com 注册应用 → 获得App ID / App Key / App License三件套
2. git clone本仓库 && bash run_demo.sh   # 审计云起在本地(协议层, 无需闭源引擎)
3. 遥控器DJI Pilot 2 → 设置 → 云服务器 → 填云端地址+三件套
4. 起飞: Pilot 2自动把OSD遥测推上MQTT → 引擎实时判定
5. 拉结果(REST) / 收推送(webhook)
```

MQTT主题(与官方TopicConst逐字对齐):

| 方向 | 主题 | 内容 |
|---|---|---|
| 订阅 | `sys/product/{sn}/status` | 设备上下线 |
| 订阅 | `thing/product/{sn}/osd` | OSD遥测逐帧(horizontalSpeed/attitudeRoll/battery/...) |
| 订阅 | `thing/product/{sn}/events` | 设备事件 |
| 发布 | `phm/audit/{sn}/alerts` | PHM告警(RETAIN, 含rule/level/caliber/measured/limit) |

REST(登录按DJI官方语义):

| 端点 | 用途 |
|---|---|
| `POST /manage/api/v1/login` | 登录握手 → `code=0` + JWT + `mqtt_addr`(Pilot 2接入所需) |
| `POST /manage/api/v1/token/refresh` | JWT续签 |
| `GET /alerts?sn=xxx` | 该机历史告警(含L1/L2口径与实测值vs阈值) |
| `GET /chain/{sn}/verify` | 飞行数字黑匣子验链(任一字节篡改即暴露) |
| `POST /config/{sn}` | 电子围栏(circle)/机型档位 运行时配置 |
| `GET /stats` · `GET /devices` | 全局状态/在线设备 |

## 边界与诚实声明

- 模拟OSD源(23/23)+公开渠道真实DJI真机遥测(5,634帧)均已实测; 经DJI Pilot 2与行业
  实体机的**在线端到端闭环**进行中(需带屏遥控器行业机)。
- 本实测检验正常飞行不误报(阈值高于Neo规格), 不宣称全机型零误报。
- 本项目与DJI无隶属关系, 上云API为DJI公开协议。
