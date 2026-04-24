"""树莓派统一数据上传脚本。

当前同时支持 MQTT 和 HTTP 两种传输协议：
- MQTT：用于树莓派真实节点接入，建议用于 SKU:SEN0501
- HTTP：用于本地接口调试和演示

无论使用哪种协议，上传的标准化 JSON 主体结构保持不变。
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
# 把项目根目录加入模块搜索路径，保证脚本可直接运行。
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import constants
from raspberry_pi.collector import read_environment_metrics


API_URL = os.getenv("SMARTSPACE_API_URL", "http://127.0.0.1:8000/sensor-data/upload")
DEVICE_ID = int(os.getenv("SMARTSPACE_DEVICE_ID", "1"))
SOURCE_NAME = os.getenv("SMARTSPACE_SOURCE_NAME", "raspberry_pi_env_node")
TRANSPORT_PROTOCOL = os.getenv("SMARTSPACE_TRANSPORT", "mqtt").lower()
MQTT_HOST = os.getenv("SMARTSPACE_MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("SMARTSPACE_MQTT_PORT", "1883"))
MQTT_TOPIC_PREFIX = os.getenv("SMARTSPACE_MQTT_TOPIC_PREFIX", "smartspace/sensor/upload")


def build_payload() -> dict:
    """构造统一标准化上传 JSON。

    无论底层采集来源是 SEN0501、DHT22 还是 BME280，
    上传格式都保持一致。
    """

    return {
        "device_id": DEVICE_ID,
        "device_type": constants.DeviceType.SENSOR_NODE,
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "metrics": read_environment_metrics(),
    }


def build_headers() -> dict[str, str]:
    """构造来源信息请求头。

    这些信息用于后端记录数据来源审计日志，不影响统一 JSON 主体结构。
    """

    return {
        "X-Source-Name": SOURCE_NAME,
        "X-Collector-Mode": os.getenv("SENSOR_SOURCE", "sen0501").lower(),
    }


def build_mqtt_topic() -> str:
    """构造 MQTT 主题。

    主题中带上 source_name 和 collector_mode，便于后端记录来源信息。
    """

    collector_mode = os.getenv("SENSOR_SOURCE", "sen0501").lower()
    return f"{MQTT_TOPIC_PREFIX}/{SOURCE_NAME}/{collector_mode}"


def upload_once_http(payload: dict) -> None:
    """通过 HTTP 上传一次数据。"""

    response = requests.post(API_URL, json=payload, headers=build_headers(), timeout=10)
    response.raise_for_status()
    print(f"HTTP 上传成功: {response.json()}")


def upload_once_mqtt(payload: dict) -> None:
    """通过 MQTT 发布一次数据。"""

    client = mqtt.Client(client_id=f"{SOURCE_NAME}_publisher")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    result = client.publish(build_mqtt_topic(), json.dumps(payload, ensure_ascii=False), qos=1)
    result.wait_for_publish()
    client.disconnect()
    print(f"MQTT 发布成功: topic={build_mqtt_topic()}")


def upload_once() -> None:
    """执行一次上传请求。

    默认走 MQTT，更符合树莓派真实节点接入场景。
    """

    payload = build_payload()
    if TRANSPORT_PROTOCOL == "http":
        upload_once_http(payload)
    else:
        upload_once_mqtt(payload)


def main() -> None:
    """循环上传数据，默认每 5 秒一次。"""

    print(f"开始上传树莓派环境数据，当前协议为 {TRANSPORT_PROTOCOL}，默认每 5 秒一次。按 Ctrl+C 结束。")
    while True:
        try:
            upload_once()
        except Exception as exc:
            print(f"上传失败: {exc}")
        time.sleep(5)


if __name__ == "__main__":
    main()
