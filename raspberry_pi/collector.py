"""树莓派数据采集层。

本文件只保留真实传感器采集接口，不再提供模拟温湿度数据。
这样可以确保前端展示和本地存储的数据都来自真实设备，而不是演示假数据。
上传层统一复用标准化 JSON，不需要修改后端和数据库结构。
"""

from __future__ import annotations

import os
from typing import Any

from app import constants

def read_from_dht22() -> dict[str, float]:
    """预留真实 DHT22 采集接口。

    后续可在此处接入 Adafruit_DHT 等库读取真实温湿度。
    函数返回结构必须保持不变，上传层无需修改。
    """
    raise NotImplementedError("请在此处接入真实 DHT22 读取逻辑")


def read_from_sen0501() -> dict[str, float]:
    """预留 SKU:SEN0501 真实采集接口。

    当树莓派接入 SKU:SEN0501 芯片时，建议搭配 MQTT 上传协议使用。
    你只需要在这里补充真实读取逻辑，返回值仍保持统一结构：
    {
        "temperature": 数值,
        "humidity": 数值
    }
    """

    raise NotImplementedError("请在此处接入 SKU:SEN0501 读取逻辑")


def read_from_bme280() -> dict[str, float]:
    """预留真实 BME280 采集接口。

    后续可在此处接入 smbus2/bme280 等库读取真实传感器。
    函数返回结构必须保持不变，上传层无需修改。
    """
    raise NotImplementedError("请在此处接入真实 BME280 读取逻辑")


def read_environment_metrics() -> list[dict[str, Any]]:
    """采集环境数据并转换为统一 metrics 列表。

    这里体现“采集层”和“上传层”解耦：
    - 无论是真实 SEN0501、DHT22 还是 BME280
    - 最终都转成统一的 metrics 数组
    - 因此上传层和后端都不需要感知采集来源变化
    """

    mode = os.getenv("SENSOR_SOURCE", "sen0501").lower()

    # 根据环境变量选择真实采集模式，默认优先使用 sen0501。
    if mode == "sen0501":
        raw_data = read_from_sen0501()
    elif mode == "dht22":
        raw_data = read_from_dht22()
    elif mode == "bme280":
        raw_data = read_from_bme280()
    else:
        raise ValueError(f"不支持的真实传感器模式: {mode}")

    # 把底层采集结果标准化为统一 JSON metrics 结构。
    return [
        {
            "sensor_type": constants.SensorType.TEMPERATURE,
            "value": raw_data[constants.SensorType.TEMPERATURE],
            "unit": constants.SENSOR_UNIT_MAP[constants.SensorType.TEMPERATURE],
        },
        {
            "sensor_type": constants.SensorType.HUMIDITY,
            "value": raw_data[constants.SensorType.HUMIDITY],
            "unit": constants.SENSOR_UNIT_MAP[constants.SensorType.HUMIDITY],
        },
    ]
