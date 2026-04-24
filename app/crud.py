"""数据库操作函数。

这里统一封装数据库的常用增删改查逻辑，目的是：
- 保持路由函数简洁
- 让数据库操作职责更清晰
- 便于后续扩展和复用
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import desc
from sqlalchemy.orm import Session, joinedload

from app import constants, models, schemas


def create_device(db: Session, device_in: schemas.DeviceCreate) -> models.Device:
    """创建一个设备记录。"""

    device = models.Device(**device_in.model_dump())
    db.add(device)
    db.commit()
    db.refresh(device)
    return device


def list_devices(db: Session) -> list[models.Device]:
    """按 ID 升序返回全部设备。"""

    return db.query(models.Device).order_by(models.Device.id.asc()).all()


def get_device(db: Session, device_id: int) -> models.Device | None:
    """根据设备 ID 查询单个设备。"""

    return db.query(models.Device).filter(models.Device.id == device_id).first()


def update_device(db: Session, device: models.Device, device_in: schemas.DeviceUpdate) -> models.Device:
    """更新设备字段。

    只更新请求里显式传入的字段。
    """

    for field, value in device_in.model_dump(exclude_unset=True).items():
        setattr(device, field, value)
    db.commit()
    db.refresh(device)
    return device


def delete_device(db: Session, device: models.Device) -> None:
    """删除设备。

    由于模型关系设置了级联删除，关联传感器和传感器数据也会一并删除。
    """

    db.delete(device)
    db.commit()


def create_sensor(db: Session, sensor_in: schemas.SensorCreate) -> models.Sensor:
    """手动创建一个传感器记录。"""

    sensor = models.Sensor(**sensor_in.model_dump())
    db.add(sensor)
    db.commit()
    db.refresh(sensor)
    return sensor


def list_sensors(db: Session) -> list[models.Sensor]:
    """返回全部传感器。

    使用 joinedload 预加载 device，避免后续访问设备信息时出现额外查询。
    """

    return (
        db.query(models.Sensor)
        .options(joinedload(models.Sensor.device))
        .order_by(models.Sensor.id.asc())
        .all()
    )


def get_sensor_by_device_and_type(
    db: Session,
    device_id: int,
    sensor_type: str,
) -> models.Sensor | None:
    """根据“设备 ID + 传感器类型”查询传感器。"""

    return (
        db.query(models.Sensor)
        .filter(
            models.Sensor.device_id == device_id,
            models.Sensor.sensor_type == sensor_type,
        )
        .first()
    )


def get_or_create_sensor(
    db: Session,
    device_id: int,
    sensor_type: str,
    unit: str,
) -> models.Sensor:
    """获取已有传感器；若不存在则自动创建。

    这样上传接口可以自动适配首次接入的标准化传感器数据。
    """

    sensor = get_sensor_by_device_and_type(db, device_id, sensor_type)
    if sensor:
        return sensor

    sensor = models.Sensor(
        device_id=device_id,
        name=constants.DEFAULT_SENSOR_NAMES.get(sensor_type, sensor_type),
        sensor_type=sensor_type,
        unit=unit,
    )
    db.add(sensor)
    db.commit()
    db.refresh(sensor)
    return sensor


def create_sensor_data(
    db: Session,
    sensor_id: int,
    timestamp: datetime,
    value: float,
) -> models.SensorData:
    """创建单条传感器数据。

    当前主要用于预留，批量写入场景优先使用 create_sensor_data_batch。
    """

    data = models.SensorData(sensor_id=sensor_id, timestamp=timestamp, value=value)
    db.add(data)
    db.commit()
    db.refresh(data)
    return data


def create_sensor_data_batch(
    db: Session,
    rows: list[dict],
) -> int:
    """批量写入多条传感器数据。

    一次上传中的多个 metric 会在这里统一提交，避免逐条 commit。
    """

    for row in rows:
        db.add(models.SensorData(**row))
    db.commit()
    return len(rows)


def get_first_device_by_type(db: Session, device_type: str) -> models.Device | None:
    """按类型获取第一台设备。

    仪表盘默认选择第一台传感器节点设备做展示。
    """

    return (
        db.query(models.Device)
        .filter(models.Device.device_type == device_type)
        .order_by(models.Device.id.asc())
        .first()
    )


def format_utc_timestamp(timestamp: datetime | None) -> str | None:
    """把数据库中的 datetime 统一格式化为 UTC ISO 字符串。"""

    if timestamp is None:
        return None
    return timestamp.replace(tzinfo=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def get_latest_metrics(
    db: Session,
    device_id: int,
    sensor_types: list[str] | None = None,
) -> list[dict]:
    """获取指定设备的最新指标数据。

    即使某个传感器暂时没有数据，也会返回空位结构，方便前端统一渲染。
    默认返回环境节点使用的温湿度指标；蓝牙节点可传入运动传感器列表。
    """

    metrics: list[dict] = []
    target_sensor_types = sensor_types or constants.ENV_SENSOR_TYPES
    for sensor_type in target_sensor_types:
        sensor = get_sensor_by_device_and_type(db, device_id, sensor_type)
        unit = constants.SENSOR_UNIT_MAP[sensor_type]
        if not sensor:
            metrics.append(
                {
                    "sensor_type": sensor_type,
                    "value": None,
                    "unit": unit,
                    "timestamp": None,
                }
            )
            continue

        # 对每类传感器只取时间最新的一条记录。
        latest_data = (
            db.query(models.SensorData)
            .filter(models.SensorData.sensor_id == sensor.id)
            .order_by(desc(models.SensorData.timestamp), desc(models.SensorData.id))
            .first()
        )
        metrics.append(
            {
                "sensor_type": sensor_type,
                "value": latest_data.value if latest_data else None,
                "unit": sensor.unit if sensor.unit else unit,
                "timestamp": format_utc_timestamp(latest_data.timestamp) if latest_data else None,
            }
        )
    return metrics


def get_history(
    db: Session,
    device_id: int,
    hours: int,
    sensor_types: list[str] | None = None,
) -> list[dict]:
    """获取指定设备最近 N 小时的历史数据。"""

    start_time = datetime.utcnow() - timedelta(hours=hours)
    series: list[dict] = []
    target_sensor_types = sensor_types or constants.ENV_SENSOR_TYPES

    for sensor_type in target_sensor_types:
        sensor = get_sensor_by_device_and_type(db, device_id, sensor_type)
        if not sensor:
            series.append(
                {
                    "sensor_type": sensor_type,
                    "unit": constants.SENSOR_UNIT_MAP[sensor_type],
                    "points": [],
                }
            )
            continue

        # 只查询时间窗口内的数据，并按时间正序返回给前端画折线图。
        data_rows = (
            db.query(models.SensorData)
            .filter(
                models.SensorData.sensor_id == sensor.id,
                models.SensorData.timestamp >= start_time,
            )
            .order_by(models.SensorData.timestamp.asc())
            .all()
        )
        series.append(
            {
                "sensor_type": sensor_type,
                "unit": sensor.unit,
                "points": [
                    {
                        "timestamp": format_utc_timestamp(row.timestamp),
                        "value": row.value,
                    }
                    for row in data_rows
                ],
            }
        )
    return series
