"""WT901SDCL-BT50 蓝牙采集层。

本模块负责：
- 搜索并连接 WT901 蓝牙节点
- 订阅 BLE 通知
- 解析加速度和角速度数据帧

该模块只做“数据采集”，不直接处理上传逻辑。
上传层会把采集结果转换成统一的标准化 JSON 结构后再发送到平台后端。
"""

from __future__ import annotations

import asyncio
import os
import struct
from datetime import datetime, timezone
from typing import Callable

from bleak import BleakClient, BleakScanner


# 这里默认对齐你当前 WT901 设备的名称和 MAC。
# 如果后续设备地址变化，也可以通过环境变量覆盖。
TARGET_NAME = os.getenv("SMARTSPACE_WT901_NAME", "WTSDCL")
TARGET_MAC = os.getenv("SMARTSPACE_WT901_MAC", "E5:64:B0:59:D2:42") or None

SERVICE_UUID = "0000ffe5-0000-1000-8000-00805f9a34fb"
NOTIFY_UUID = "0000ffe4-0000-1000-8000-00805f9a34fb"


class WT901AccGyrReceiver:
    """WT901 通知数据接收器。"""

    def __init__(self, on_motion_data: Callable[[tuple[float, float, float], tuple[float, float, float], str], None]):
        self.buffer = bytearray()
        self.on_motion_data = on_motion_data

    def feed(self, data: bytes) -> None:
        """把收到的 BLE 原始字节流送入解析缓冲区。"""

        self.buffer.extend(data)
        self._parse()

    def _parse(self) -> None:
        """解析 0x55 0x61 数据帧。"""

        while True:
            idx = self.buffer.find(b"\x55\x61")
            if idx < 0:
                if len(self.buffer) > 1:
                    self.buffer = self.buffer[-1:]
                break

            if idx > 0:
                del self.buffer[:idx]

            if len(self.buffer) < 20:
                break

            frame = bytes(self.buffer[:20])
            del self.buffer[:20]

            try:
                ax, ay, az, wx, wy, wz, _, _, _ = struct.unpack("<hhhhhhhhh", frame[2:20])
            except struct.error:
                continue

            acc_scale = 16.0 / 32768.0
            gyr_scale = 2000.0 / 32768.0
            acc = (ax * acc_scale, ay * acc_scale, az * acc_scale)
            gyr = (wx * gyr_scale, wy * gyr_scale, wz * gyr_scale)
            # 蓝牙节点原始上报频率较高，这里保留毫秒精度，
            # 避免同一秒内多帧数据共用同一个时间戳，导致上传层只能看到“每秒一条”。
            timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            self.on_motion_data(acc, gyr, timestamp)


async def find_target(target_name: str = TARGET_NAME, target_mac: str | None = TARGET_MAC):
    """按 MAC 或名称搜索目标蓝牙设备。"""

    if target_mac:
        device = await BleakScanner.find_device_by_address(target_mac, timeout=10.0)
        if device:
            return device

    devices = await BleakScanner.discover(timeout=8.0)
    for device in devices:
        if device.name and target_name.upper() in device.name.upper():
            return device
    print("本轮扫描未发现目标 WT901 设备。")
    for device in devices:
        print(f"扫描到设备: {device.name} ({device.address})")
    return None


async def stream_wt901_motion_data(
    on_motion_data: Callable[[tuple[float, float, float], tuple[float, float, float], str], None],
    target_name: str = TARGET_NAME,
    target_mac: str | None = TARGET_MAC,
) -> None:
    """持续接收 WT901 的加速度和角速度数据。"""

    device = await find_target(target_name=target_name, target_mac=target_mac)
    if not device:
        print("未找到 WT901 BLE 设备")
        return

    receiver = WT901AccGyrReceiver(on_motion_data=on_motion_data)

    def notification_handler(_sender, data: bytearray):
        receiver.feed(bytes(data))

    async with BleakClient(device, timeout=15.0) as client:
        print(f"已连接 WT901: {device.name} ({device.address})")
        services = await client.get_services()
        service_ok = any(service.uuid.lower() == SERVICE_UUID for service in services)
        if not service_ok:
            print("未找到 WT901 的 FFE5 服务")
            return

        await client.start_notify(NOTIFY_UUID, notification_handler)
        print("开始接收 WT901 加速度 / 角速度，按 Ctrl+C 退出")

        try:
            while True:
                await asyncio.sleep(1)
        finally:
            await client.stop_notify(NOTIFY_UUID)
