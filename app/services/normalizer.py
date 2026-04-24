"""上传数据规范化处理。

这是项目里最能体现“异构设备统一接入、标准化存储”的模块。

职责包括：
- 校验设备类型是否合法
- 校验传感器类型是否合法
- 校验单位是否匹配
- 统一时间格式
- 将统一 JSON 中的 metrics 拆分为多条 sensor_data 记录
"""

from datetime import timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import constants, crud, schemas
from app.storage import (
    record_normalized_data,
    record_source_audit,
    record_standardized_sensor_data,
    save_raw_upload,
)


def validate_device_type(device_type: str) -> None:
    """校验设备类型是否受平台支持。"""

    if device_type not in constants.SUPPORTED_DEVICE_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的 device_type: {device_type}")


def validate_sensor_type(sensor_type: str) -> None:
    """校验传感器类型是否受平台支持。"""

    if sensor_type not in constants.SUPPORTED_SENSOR_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的 sensor_type: {sensor_type}")


def validate_metric_unit(sensor_type: str, unit: str) -> None:
    """校验单位是否与传感器类型匹配。"""

    expected_unit = constants.SENSOR_UNIT_MAP.get(sensor_type)
    if expected_unit != unit:
        raise HTTPException(
            status_code=400,
            detail=f"{sensor_type} 的单位应为 {expected_unit}，当前为 {unit}",
        )


def normalize_timestamp(payload: schemas.SensorUploadPayload) -> str:
    """把上传时间统一转成 UTC ISO 格式字符串。"""

    normalized = payload.timestamp
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    else:
        normalized = normalized.astimezone(timezone.utc)
    return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def process_upload(
    db: Session,
    payload: schemas.SensorUploadPayload,
    source_context: dict | None = None,
) -> schemas.UploadResult:
    """处理标准化上传请求。

    处理流程：
    1. 校验设备类型
    2. 统一时间格式
    3. 校验设备是否存在且类型匹配
    4. 校验 metrics 中每一项的类型和单位
    5. 自动补建 sensor 记录
    6. 批量写入 sensor_data
    """

    source_context = source_context or {}

    validate_device_type(payload.device_type)
    normalized_timestamp = normalize_timestamp(payload)
    timestamp_obj = payload.timestamp
    if timestamp_obj.tzinfo is None:
        timestamp_obj = timestamp_obj.replace(tzinfo=timezone.utc)
    else:
        timestamp_obj = timestamp_obj.astimezone(timezone.utc)
    # SQLite 里按“无时区 UTC 时间”保存，但保留毫秒精度，
    # 这样蓝牙高频上报不会在同一秒内全部挤成一个时间点。
    timestamp_obj = timestamp_obj.replace(tzinfo=None)

    device = crud.get_device(db, payload.device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device_id 不存在")
    if device.device_type != payload.device_type:
        raise HTTPException(
            status_code=400,
            detail=f"设备类型不匹配，数据库为 {device.device_type}，上传为 {payload.device_type}",
        )

    if not payload.metrics:
        raise HTTPException(status_code=400, detail="metrics 不能为空")

    # seen_sensor_types 用于防止同一次上传中重复上报同一类传感器。
    seen_sensor_types: set[str] = set()
    for metric in payload.metrics:
        validate_sensor_type(metric.sensor_type)
        validate_metric_unit(metric.sensor_type, metric.unit)
        if metric.sensor_type in seen_sensor_types:
            raise HTTPException(status_code=400, detail=f"重复的 sensor_type: {metric.sensor_type}")
        seen_sensor_types.add(metric.sensor_type)

    # 先完成全部校验，再统一组织待入库数据，保证上传过程更清晰。
    sensor_data_rows: list[dict] = []
    normalized_metrics: list[dict] = []
    for metric in payload.metrics:
        sensor = crud.get_or_create_sensor(
            db=db,
            device_id=payload.device_id,
            sensor_type=metric.sensor_type,
            unit=metric.unit,
        )
        sensor_data_rows.append(
            {
                "sensor_id": sensor.id,
                "timestamp": timestamp_obj,
                "value": metric.value,
            }
        )
        normalized_metrics.append(
            {
                "sensor_id": sensor.id,
                "sensor_type": metric.sensor_type,
                "value": metric.value,
                "unit": metric.unit,
                "timestamp": normalized_timestamp,
            }
        )

    stored_count = crud.create_sensor_data_batch(db=db, rows=sensor_data_rows)

    device_info = {
        "device_id": device.id,
        "device_name": device.name,
        "device_type": device.device_type,
    }
    raw_file_path = save_raw_upload(
        payload.model_dump(mode="json"),
        source_context=source_context,
    )
    record_normalized_data(
        device_info=device_info,
        normalized_timestamp=normalized_timestamp,
        metrics=normalized_metrics,
        source_context=source_context,
        raw_file_path=raw_file_path,
    )
    record_standardized_sensor_data(
        device_info=device_info,
        metrics=normalized_metrics,
        source_context=source_context,
        raw_file_path=raw_file_path,
    )
    record_source_audit(
        device_info=device_info,
        source_context=source_context,
        stored_count=stored_count,
        raw_file_path=raw_file_path,
    )

    return schemas.UploadResult(
        message="上传成功，数据已按标准化结构入库",
        stored_count=stored_count,
        normalized_timestamp=normalized_timestamp,
    )
