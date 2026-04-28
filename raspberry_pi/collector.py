"""树莓派数据采集层。

本文件只保留真实传感器采集接口，不再提供模拟温湿度数据。
这样可以确保前端展示和本地存储的数据都来自真实设备，而不是演示假数据。
上传层统一复用标准化 JSON，不需要修改后端和数据库结构。
"""

from __future__ import annotations

from typing import Any
import os

from app import constants

def read_from_dht22() -> dict[str, float]:
    """预留真实 DHT22 采集接口。

    后续可在此处接入 Adafruit_DHT 等库读取真实温湿度。
    函数返回结构必须保持不变，上传层无需修改。
    """
    raise NotImplementedError("请在此处接入真实 DHT22 读取逻辑")


def read_from_sen0501() -> dict[str, float]:
    """读取 SKU:SEN0501 多功能环境传感器。

    SEN0501 集成温湿度、气压、海拔估算、紫外线和环境光。
    这里优先使用 `dfrobot-environmental-sensor` 库，让官方驱动负责
    寄存器读取与单位换算，采集层只保留统一字段命名。
    """

    try:
        from dfrobot_environmental_sensor import EnvironmentalSensor, Units, UVSensor
    except ImportError as exc:
        raise RuntimeError("请先在树莓派上执行: pip install dfrobot-environmental-sensor") from exc

    uv_variant_name = os.getenv("SEN0501_UV_VARIANT", "LTR390UV").upper()
    uv_variant = getattr(UVSensor, uv_variant_name, UVSensor.LTR390UV)
    sensor = EnvironmentalSensor.i2c(
        bus=int(os.getenv("SEN0501_I2C_BUS", "1")),
        address=int(os.getenv("SEN0501_I2C_ADDRESS", "0x22"), 0),
        uv_sensor=uv_variant,
    )
    if not sensor.is_present():
        raise RuntimeError("SEN0501 未响应，请检查 I2C 开关、接线、电源和地址 0x22")

    return {
        constants.SensorType.TEMPERATURE: round(float(sensor.read_temperature(Units.C)), 2),
        constants.SensorType.HUMIDITY: round(float(sensor.read_humidity()), 2),
        constants.SensorType.PRESSURE: round(float(sensor.read_pressure(Units.HPA)), 2),
        constants.SensorType.ALTITUDE: round(float(sensor.estimate_altitude()), 2),
        constants.SensorType.ULTRAVIOLET: round(float(sensor.read_uv_irradiance()), 4),
        constants.SensorType.ILLUMINANCE: round(float(sensor.read_illuminance()), 2),
    }


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
            "sensor_type": sensor_type,
            "value": raw_data[sensor_type],
            "unit": constants.SENSOR_UNIT_MAP[sensor_type],
        }
        for sensor_type in constants.ENV_SENSOR_TYPES
        if sensor_type in raw_data
    ]
