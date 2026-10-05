# 真机遥测实测数据源与独立复核

## 数据源（公开可下载）

| 文件 | 来源URL | 规格 |
|---|---|---|
| DJI Neo 室内飞行 | https://raw.githubusercontent.com/Butterbananenbrot/FlightViewer/master/DJIFlightRecord_2025-06-27_%5B23-13-13%5D.csv | 220s / 2,198帧 / 191列 |
| DJI Neo 室外飞行 | https://raw.githubusercontent.com/Butterbananenbrot/FlightViewer/master/demo%20flight%20one.csv | 344s / 3,436帧 / 191列 |

说明: 上述CSV为真实用户导出的DJI飞行记录(Airdata式格式, 第一行为`sep=,`, 第二行为表头)。
本仓库与该用户无关联, 仅引用其公开数据。

## 独立复核（无需本仓库引擎）

```bash
python3 verify_peaks.py 室内CSV路径 室外CSV路径
```

输出两段飞行的包线峰值(水平/垂直速度, 滚转/俯仰角)与电池返航线穿越事件——
全部结论可仅用公开数据+本脚本复核, 不依赖闭源引擎。

## 引擎实测结果

`real_device_result.json`: PHM审计引擎(mavic档)对上述数据的流式判定结果,
含逐条告警(触发时刻/实测值/阈值/严重级)与SHA-256审计链:
- 室内 `678e00778bc5…` / 室外 `c83e955d3d56…`
- 口径: 5,634帧全部喂入引擎; 室内2,198帧无GPS坐标键(坐标类规则对其不适用);
  mavic档阈值高于Neo规格上限——检验正常飞行不误报, 不检验边界告警灵敏度。
