"""WT901 蓝牙节点上传脚本。

运行该脚本后，程序会：
1. 通过 BLE 连接 WT901SDCL-BT50
2. 持续读取加速度和角速度
3. 把数据组织为统一的 metrics 结构
4. 通过 HTTP 上传到平台后端

这样蓝牙节点就和树莓派节点一样，复用同一套规范化上传与存储流程。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import constants
from bluetooth_node.collector import TARGET_MAC, TARGET_NAME, stream_wt901_motion_data


API_URL = os.getenv("SMARTSPACE_API_URL", "http://127.0.0.1:8000/sensor-data/upload")
BASE_API_URL = API_URL.removesuffix("/sensor-data/upload")
DEVICE_ID = int(os.getenv("SMARTSPACE_BLUETOOTH_DEVICE_ID", "0"))
DEVICE_NAME = os.getenv("SMARTSPACE_BLUETOOTH_DEVICE_NAME", "WT901蓝牙采集节点")
SOURCE_NAME = os.getenv("SMARTSPACE_BLUETOOTH_SOURCE_NAME", "wt901_bluetooth_node")
# 当前采集代码只订阅 WT901 的 BLE 通知，不主动写入 RATE 配置。
# 因此这里的 0.1 秒表示“上传层检查最新帧的间隔”，用于配合 WT901 默认约 10Hz 的回传节奏。
UPLOAD_INTERVAL_SECONDS = float(os.getenv("SMARTSPACE_BLUETOOTH_UPLOAD_INTERVAL", "0.1"))
WT901_NAME = os.getenv("SMARTSPACE_WT901_NAME", TARGET_NAME)
WT901_MAC = os.getenv("SMARTSPACE_WT901_MAC", TARGET_MAC or "") or None


class MotionState:
    """保存最新一帧蓝牙运动数据。"""

    def __init__(self, device_id: int) -> None:
        self.device_id = device_id
        self.latest_payload: dict | None = None
        self.last_uploaded_timestamp: str | None = None

    def update(self, acc: tuple[float, float, float], gyr: tuple[float, float, float], timestamp: str) -> None:
        """把采集结果转换成统一标准化 JSON 结构。"""

        self.latest_payload = {
            "device_id": self.device_id,
            "device_type": constants.DeviceType.BLUETOOTH_NODE,
            "timestamp": timestamp,
            "metrics": [
                {"sensor_type": constants.SensorType.ACCELERATION_X, "value": acc[0], "unit": "g"},
                {"sensor_type": constants.SensorType.ACCELERATION_Y, "value": acc[1], "unit": "g"},
                {"sensor_type": constants.SensorType.ACCELERATION_Z, "value": acc[2], "unit": "g"},
                {"sensor_type": constants.SensorType.ANGULAR_VELOCITY_X, "value": gyr[0], "unit": "deg/s"},
                {"sensor_type": constants.SensorType.ANGULAR_VELOCITY_Y, "value": gyr[1], "unit": "deg/s"},
                {"sensor_type": constants.SensorType.ANGULAR_VELOCITY_Z, "value": gyr[2], "unit": "deg/s"},
            ],
        }

    def pop_payload_if_ready(self) -> dict | None:
        """取出尚未上传过的最新数据。"""

        if not self.latest_payload:
            return None

        timestamp = self.latest_payload["timestamp"]
        if timestamp == self.last_uploaded_timestamp:
            # WT901 通知帧没有更新时不重复上传旧数据，避免数据库中出现同一时刻的重复记录。
            return None

        self.last_uploaded_timestamp = timestamp
        return self.latest_payload


def build_headers() -> dict[str, str]:
    """构造上传来源信息。"""

    return {
        "X-Source-Name": SOURCE_NAME,
        "X-Collector-Mode": "wt901_ble",
    }


def upload_payload(payload: dict) -> None:
    """通过 HTTP 上传一帧蓝牙运动数据。"""

    response = requests.post(API_URL, json=payload, headers=build_headers(), timeout=10)
    if not response.ok:
        raise requests.HTTPError(f"{response.status_code} {response.text}", response=response)
    print(f"WT901 上传成功: {response.json()}")


def resolve_bluetooth_device_id() -> int:
    """自动解析或创建 WT901 蓝牙设备档案。

    这样即使用户忘记重新初始化数据库，上传脚本也能自动补齐设备信息，
    避免因为 device_id 不匹配持续报 400。
    """

    if DEVICE_ID > 0:
        return DEVICE_ID

    response = requests.get(f"{BASE_API_URL}/devices", timeout=10)
    response.raise_for_status()
    devices = response.json()

    for device in devices:
        if device.get("device_type") == constants.DeviceType.BLUETOOTH_NODE:
            return int(device["id"])

    create_response = requests.post(
        f"{BASE_API_URL}/devices",
        json={
            "name": DEVICE_NAME,
            "device_type": constants.DeviceType.BLUETOOTH_NODE,
            "ip_address": "127.0.0.1",
            "port": 0,
            "status": "online",
            "description": "用于接入 WT901SDCL-BT50 蓝牙节点，采集加速度与角速度并按统一结构上传",
        },
        timeout=10,
    )
    create_response.raise_for_status()
    created_device = create_response.json()
    print(f"已自动创建蓝牙设备档案: id={created_device['id']} name={created_device['name']}")
    return int(created_device["id"])


async def wait_for_backend_ready() -> int:
    """等待平台后端启动完成，并解析蓝牙设备编号。

    start.ps1/start.bat 会同时启动后端和蓝牙上传脚本，
    这里增加重试可以避免因为后端还没完全启动就直接报错退出。
    """

    while True:
        try:
            return resolve_bluetooth_device_id()
        except requests.RequestException as exc:
            print(f"等待后端启动中，暂时无法连接 {BASE_API_URL}: {exc}")
            await asyncio.sleep(2)


async def upload_loop(state: MotionState) -> None:
    """按固定间隔上传最近一帧蓝牙运动数据。"""

    try:
        while True:
            payload = state.pop_payload_if_ready()
            if payload:
                try:
                    upload_payload(payload)
                except Exception as exc:
                    print(f"WT901 上传失败: {exc}")
            await asyncio.sleep(UPLOAD_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        # 退出时由上层统一做收尾提示，这里安静结束即可。
        return


async def run_uploader() -> None:
    """蓝牙采集与上传主流程。"""

    print("启动 WT901 蓝牙采集节点上传脚本")
    print(f"目标设备名称: {WT901_NAME}，目标 MAC: {WT901_MAC or '未固定'}")
    print(f"上传地址: {API_URL}，默认上传间隔: {UPLOAD_INTERVAL_SECONDS} 秒")

    device_id = await wait_for_backend_ready()
    print(f"WT901 上传将使用设备编号: {device_id}")
    state = MotionState(device_id=device_id)
    collector_task = asyncio.create_task(
        stream_wt901_motion_data(
            on_motion_data=state.update,
            target_name=WT901_NAME,
            target_mac=WT901_MAC,
        )
    )
    uploader_task = asyncio.create_task(upload_loop(state))

    try:
        await asyncio.gather(collector_task, uploader_task)
    finally:
        for task in (collector_task, uploader_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(collector_task, uploader_task, return_exceptions=True)


def main() -> None:
    """脚本入口。

    单独捕获 Ctrl+C，避免把 asyncio 的取消异常直接打印到终端。
    """

    try:
        asyncio.run(run_uploader())
    except KeyboardInterrupt:
        print("程序已停止")


if __name__ == "__main__":
    main()
