"""集中定义平台支持的设备类型、传感器类型和单位映射。

把这些值统一放在常量文件里有两个好处：
1. 避免在多个文件里散落魔法字符串，提升可维护性。
2. 后续扩展新设备或新传感器时，只需要集中修改这一处。
"""

from typing import Final


class DeviceType:
    """设备类型枚举。

    当前平台支持摄像头、树莓派环境采集节点和蓝牙运动采集节点。
    """

    CAMERA = "camera"
    SENSOR_NODE = "sensor_node"
    BLUETOOTH_NODE = "bluetooth_node"


class SensorType:
    """传感器类型枚举。

    当前演示包含 SEN0501 环境指标与 WT901 的加速度、角速度。
    """

    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    PRESSURE = "pressure"
    ALTITUDE = "altitude"
    ULTRAVIOLET = "ultraviolet"
    ILLUMINANCE = "illuminance"
    ACCELERATION_X = "acceleration_x"
    ACCELERATION_Y = "acceleration_y"
    ACCELERATION_Z = "acceleration_z"
    ANGULAR_VELOCITY_X = "angular_velocity_x"
    ANGULAR_VELOCITY_Y = "angular_velocity_y"
    ANGULAR_VELOCITY_Z = "angular_velocity_z"


SUPPORTED_DEVICE_TYPES: Final[set[str]] = {
    DeviceType.CAMERA,
    DeviceType.SENSOR_NODE,
    DeviceType.BLUETOOTH_NODE,
}

ENV_SENSOR_TYPES: Final[list[str]] = [
    SensorType.TEMPERATURE,
    SensorType.HUMIDITY,
    SensorType.PRESSURE,
    SensorType.ALTITUDE,
    SensorType.ULTRAVIOLET,
    SensorType.ILLUMINANCE,
]

MOTION_SENSOR_TYPES: Final[list[str]] = [
    SensorType.ACCELERATION_X,
    SensorType.ACCELERATION_Y,
    SensorType.ACCELERATION_Z,
    SensorType.ANGULAR_VELOCITY_X,
    SensorType.ANGULAR_VELOCITY_Y,
    SensorType.ANGULAR_VELOCITY_Z,
]

SUPPORTED_SENSOR_TYPES: Final[set[str]] = {
    *ENV_SENSOR_TYPES,
    *MOTION_SENSOR_TYPES,
}

SENSOR_UNIT_MAP: Final[dict[str, str]] = {
    SensorType.TEMPERATURE: "C",
    SensorType.HUMIDITY: "%",
    SensorType.PRESSURE: "hPa",
    SensorType.ALTITUDE: "m",
    SensorType.ULTRAVIOLET: "mW/cm2",
    SensorType.ILLUMINANCE: "lx",
    SensorType.ACCELERATION_X: "g",
    SensorType.ACCELERATION_Y: "g",
    SensorType.ACCELERATION_Z: "g",
    SensorType.ANGULAR_VELOCITY_X: "deg/s",
    SensorType.ANGULAR_VELOCITY_Y: "deg/s",
    SensorType.ANGULAR_VELOCITY_Z: "deg/s",
}

DEFAULT_SENSOR_NAMES: Final[dict[str, str]] = {
    SensorType.TEMPERATURE: "温度传感器",
    SensorType.HUMIDITY: "湿度传感器",
    SensorType.PRESSURE: "大气压强传感器",
    SensorType.ALTITUDE: "海拔高度估算",
    SensorType.ULTRAVIOLET: "紫外线强度传感器",
    SensorType.ILLUMINANCE: "环境光强度传感器",
    SensorType.ACCELERATION_X: "加速度 X 轴",
    SensorType.ACCELERATION_Y: "加速度 Y 轴",
    SensorType.ACCELERATION_Z: "加速度 Z 轴",
    SensorType.ANGULAR_VELOCITY_X: "角速度 X 轴",
    SensorType.ANGULAR_VELOCITY_Y: "角速度 Y 轴",
    SensorType.ANGULAR_VELOCITY_Z: "角速度 Z 轴",
}

DEFAULT_DEVICE_STATUS: Final[str] = "online"
