# -*- coding: utf-8 -*-
"""PHM-Drone 引擎接口契约（开源部分）。

DJI上云API审计云的协议胶水(phm_audit_cloud.py)只依赖本接口,
不依赖任何具体校验器实现。真实引擎(phm_drone_stream.py)为闭源模块,
不在GitHub开源包内分发; 缺失时胶水自动降级为 NullEngine(透传+落盘,不判警)。
"""
from abc import ABC, abstractmethod


class PHMEngineInterface(ABC):
    """流式无人机物理校验引擎接口。

    实现方保证: on_osd() 幂等可重入、无阻塞IO、单次调用 <5ms。
    """

    @abstractmethod
    def profile_for(self, sn: str, osd: dict) -> str:
        """返回机型档位名(tello/mavic/agras/fpv)。"""

    @abstractmethod
    def on_osd(self, sn: str, ts_ms: int, osd: dict) -> list:
        """喂一帧OSD遥测, 返回告警记录列表(可为空)。

        告警记录 schema(所有实现必须遵守):
        {
          "rule": str,             # V_SPEED / V_SPEED_V / ACCEL / ATT_ROLL / ATT_PITCH / L1_DUP / L1_GAP / L1_JUMP
          "level": "WARN"|"ALERT", # WARN=单帧候选; ALERT=多帧确认(物理判定)
          "caliber": "L1质量"|"L2物理",
          "measured": float, "limit": float,
          "frames_confirmed": int, "ts": int
        }
        """

    def on_status(self, sn: str, online: bool) -> None:
        """设备上下线通知。默认无操作。"""
        return None


class NullEngine(PHMEngineInterface):
    """降级实现: 引擎闭源包缺失时使用。只透传不判警, 保证协议胶水可独立演示。"""

    def profile_for(self, sn, osd):
        return "unknown"

    def on_osd(self, sn, ts_ms, osd):
        return []

    def configure(self, sn, geofence=None, profile=None):
        return None  # NullEngine无运行时配置; REST /config在降级模式下同样返回ok
