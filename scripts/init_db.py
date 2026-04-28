"""初始化数据库和基础设备档案。

该脚本会创建：
- 树莓派环境采集节点
- 蓝牙 WT901 采集节点
- 本地摄像头设备

并补齐对应传感器定义，但不再写入任何演示历史数据。
这样在真实设备尚未运行时，前端会保持空态，而不是显示旧的演示值。
"""
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
# 把项目根目录加入模块搜索路径，保证脚本可直接单独运行。
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import constants, models
from app.database import SessionLocal, engine


def seed_devices_and_sensors():
    """初始化数据库、设备和传感器档案。"""

    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        # 清空旧的温湿度历史数据，确保前端从真实空态开始展示。
        db.query(models.SensorData).delete()
        db.commit()

        # 先创建树莓派环境采集节点，便于全新数据库时它优先获得设备编号。
        sensor_node = db.query(models.Device).filter(models.Device.name == "树莓派环境采集节点").first()
        if not sensor_node:
            sensor_node = models.Device(
                name="树莓派环境采集节点",
                device_type=constants.DeviceType.SENSOR_NODE,
                ip_address="127.0.0.1",
                port=1883,
                status="online",
                description="真实树莓派 SEN0501 环境采集节点，建议通过 MQTT 上报温度、湿度、气压、海拔、紫外线和环境光数据",
            )
            db.add(sensor_node)
            db.flush()

        # 初始化蓝牙 WT901 采集节点。
        bluetooth_node = db.query(models.Device).filter(models.Device.name == "WT901蓝牙采集节点").first()
        if not bluetooth_node:
            bluetooth_node = models.Device(
                name="WT901蓝牙采集节点",
                device_type=constants.DeviceType.BLUETOOTH_NODE,
                ip_address="127.0.0.1",
                port=0,
                status="online",
                description="用于接入 WT901SDCL-BT50 蓝牙节点，采集加速度与角速度并按统一结构上传",
            )
            db.add(bluetooth_node)

        # 初始化摄像头设备。
        camera = db.query(models.Device).filter(models.Device.name == "教室USB摄像头").first()
        if not camera:
            camera = models.Device(
                name="教室USB摄像头",
                device_type=constants.DeviceType.CAMERA,
                ip_address="127.0.0.1",
                port=0,
                status="online",
                description="用于本地演示的视频设备，运行时会持续保存 MP4 并定时抓拍画面到本地目录",
            )
            db.add(camera)

        db.flush()

        # 为树莓派节点补齐 SEN0501 支持的六类环境传感器档案。
        for sensor_type in constants.ENV_SENSOR_TYPES:
            unit = constants.SENSOR_UNIT_MAP[sensor_type]
            existing = (
                db.query(models.Sensor)
                .filter(
                    models.Sensor.device_id == sensor_node.id,
                    models.Sensor.sensor_type == sensor_type,
                )
                .first()
            )
            if not existing:
                db.add(
                    models.Sensor(
                        device_id=sensor_node.id,
                        name=constants.DEFAULT_SENSOR_NAMES[sensor_type],
                        sensor_type=sensor_type,
                        unit=unit,
                    )
                )

        # 为蓝牙节点补齐加速度和角速度六个传感器档案。
        for sensor_type in constants.MOTION_SENSOR_TYPES:
            unit = constants.SENSOR_UNIT_MAP[sensor_type]
            existing = (
                db.query(models.Sensor)
                .filter(
                    models.Sensor.device_id == bluetooth_node.id,
                    models.Sensor.sensor_type == sensor_type,
                )
                .first()
            )
            if not existing:
                db.add(
                    models.Sensor(
                        device_id=bluetooth_node.id,
                        name=constants.DEFAULT_SENSOR_NAMES[sensor_type],
                        sensor_type=sensor_type,
                        unit=unit,
                    )
                )

        db.commit()
        print("数据库初始化完成，已写入环境节点、蓝牙节点、摄像头及传感器档案，未写入任何演示历史数据。")
    finally:
        db.close()


if __name__ == "__main__":
    seed_devices_and_sensors()
