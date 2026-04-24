"""SQLAlchemy ORM 模型。

本文件定义数据库中的三张核心表：
- device：设备表
- sensor：传感器表
- sensor_data：传感器数据表

设计重点：
- 用 sensor 表描述“设备上挂了什么传感器”
- 用 sensor_data 表统一存储所有传感器采集值
- 不把 temperature、humidity 等字段直接平铺到数据表中
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Device(Base):
    """设备表。

    用于描述平台接入的设备基础信息，例如：
    - 摄像头
    - 树莓派采集节点
    """

    __tablename__ = "device"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    device_type = Column(String(50), nullable=False, index=True)
    ip_address = Column(String(50), nullable=True)
    port = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="online")
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # 一个设备可以挂载多个传感器，因此这里建立一对多关系。
    sensors = relationship(
        "Sensor",
        back_populates="device",
        cascade="all, delete-orphan",
    )


class Sensor(Base):
    """传感器表。

    用于描述某个设备下的某类传感器，例如：
    - 树莓派节点下的温度传感器
    - 树莓派节点下的湿度传感器
    """

    __tablename__ = "sensor"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("device.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    sensor_type = Column(String(50), nullable=False, index=True)
    unit = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # 传感器归属于某个设备。
    device = relationship("Device", back_populates="sensors")
    # 一个传感器可以对应多条历史采样数据。
    sensor_data = relationship(
        "SensorData",
        back_populates="sensor",
        cascade="all, delete-orphan",
    )


class SensorData(Base):
    """传感器数据表。

    采用“时间戳 + 数值”的统一结构，适合扩展更多传感器类型。
    后续即使新增气压或光照，也不需要修改这张表结构。
    """

    __tablename__ = "sensor_data"

    id = Column(Integer, primary_key=True, index=True)
    sensor_id = Column(Integer, ForeignKey("sensor.id", ondelete="CASCADE"), nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    value = Column(Float, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    sensor = relationship("Sensor", back_populates="sensor_data")
