"""数据查询与对齐导出服务。"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy.orm import Session

from app import models
from app.crud import format_utc_timestamp
from app.storage import (
    CAMERA_VIDEO_RECORD_FILE,
    CAMERA_VIDEO_SEGMENT_SECONDS,
    EXPORT_DIR,
    LEGACY_CAMERA_VIDEO_RECORD_FILE,
    RUNTIME_DATA_DIR,
    read_object_index,
    utc_now_iso,
)


def _iter_jsonl(path: Path) -> list[dict]:
    """安全读取 JSONL，避免单行编码或格式问题拖垮整个导出接口。"""

    if not path.exists():
        return []

    rows = []
    with path.open("r", encoding="utf-8-sig") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def parse_query_time(value: datetime) -> datetime:
    """把接口收到的时间统一转换为 SQLite 中使用的 UTC naive datetime。"""

    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_index_time(value: str | None) -> datetime | None:
    """解析对象索引中的时间字段。

    对象索引来源较多，可能来自抓拍、视频分段或原始上传备份。
    这里统一兼容带 Z 的 UTC 字符串和普通 ISO 字符串，便于后续按时间窗筛选。
    """

    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def query_sensor_rows(
    db: Session,
    start_time: datetime,
    end_time: datetime,
    device_type: str | None = None,
) -> list[dict]:
    """查询指定时间范围内的传感器观测数据。"""

    query = (
        db.query(models.SensorData, models.Sensor, models.Device)
        .join(models.Sensor, models.SensorData.sensor_id == models.Sensor.id)
        .join(models.Device, models.Sensor.device_id == models.Device.id)
        .filter(
            models.SensorData.timestamp >= start_time,
            models.SensorData.timestamp <= end_time,
        )
    )
    if device_type:
        # device_type 只筛选关系型数据库中的设备类型，例如 sensor_node / bluetooth_node。
        query = query.filter(models.Device.device_type == device_type)

    rows = query.order_by(models.SensorData.timestamp.asc(), models.SensorData.id.asc()).all()
    return [
        {
            "timestamp": format_utc_timestamp(sensor_data.timestamp),
            "device_id": device.id,
            "device_name": device.name,
            "device_type": device.device_type,
            "sensor_id": sensor.id,
            "sensor_name": sensor.name,
            "sensor_type": sensor.sensor_type,
            "value": sensor_data.value,
            "unit": sensor.unit,
            "sensor_data_id": sensor_data.id,
        }
        for sensor_data, sensor, device in rows
    ]


def _object_overlaps_window(item: dict, start_time: datetime, end_time: datetime) -> bool:
    """判断文件对象是否与导出时间窗口有交集。"""

    start_time = parse_query_time(start_time)
    end_time = parse_query_time(end_time)
    object_start = _parse_index_time(item.get("timestamp") or item.get("start_time") or item.get("indexed_at"))
    object_end = _parse_index_time(item.get("end_time"))
    if object_start is None:
        return False
    if object_end is None and item.get("object_type") == "camera_video_segment":
        # 兼容旧版本摄像头视频索引：没有 end_time 时按默认分段长度估算覆盖范围。
        object_end = object_start + timedelta(seconds=CAMERA_VIDEO_SEGMENT_SECONDS)
    if object_end is None:
        return start_time <= object_start <= end_time
    return object_start <= end_time and object_end >= start_time


def _iter_object_rows() -> list[dict]:
    """读取对象索引，并兼容旧版摄像头视频记录文件。"""

    rows = list(read_object_index(limit=None))
    indexed_video_paths = {
        str(item.get("uri"))
        for item in rows
        if item.get("object_type") == "camera_video_segment" and item.get("uri")
    }
    video_record_files = [CAMERA_VIDEO_RECORD_FILE]
    if LEGACY_CAMERA_VIDEO_RECORD_FILE != CAMERA_VIDEO_RECORD_FILE:
        video_record_files.append(LEGACY_CAMERA_VIDEO_RECORD_FILE)

    for record_file in video_record_files:
        for item in _iter_jsonl(record_file):
            file_path = item.get("file_path")
            if not file_path or str(file_path) in indexed_video_paths:
                continue
            rows.append(
                {
                    "indexed_at": item.get("recorded_at"),
                    "object_type": "camera_video_segment",
                    "modality": "video",
                    "uri": file_path,
                    "timestamp": None,
                    "start_time": item.get("start_time") or item.get("recorded_at"),
                    "end_time": item.get("end_time"),
                    "device_id": None,
                    "metadata": {
                        "camera_index": item.get("camera_index"),
                        "fps": item.get("fps"),
                        "frame_width": item.get("frame_width"),
                        "frame_height": item.get("frame_height"),
                        "finalized": item.get("finalized"),
                        "partial": item.get("partial"),
                        "source": "camera_video_records",
                    },
                }
            )
    return rows


def query_object_rows(start_time: datetime, end_time: datetime, device_type: str | None = None) -> list[dict]:
    """按时间窗口查询对象索引。

    对象索引中部分媒体对象不直接记录 device_type，因此 device_type 过滤
    只作用于带有该元数据的对象；未标注设备类型的媒体对象仍保留，便于对齐查看。
    """

    objects = []
    for item in _iter_object_rows():
        if not _object_overlaps_window(item, start_time, end_time):
            continue

        metadata_device_type = item.get("metadata", {}).get("device", {}).get("device_type")
        if device_type and metadata_device_type and metadata_device_type != device_type:
            continue
        objects.append(item)
    return objects


def _resolve_exportable_object_path(item: dict) -> Path | None:
    """解析对象索引中的本地文件路径，并限制只能导出 runtime_data 内的文件。"""

    uri = item.get("uri")
    if not uri:
        return None
    path = Path(uri)
    if not path.exists() or not path.is_file():
        return None

    runtime_root = RUNTIME_DATA_DIR.resolve()
    resolved = path.resolve()
    if resolved != runtime_root and runtime_root not in resolved.parents:
        return None
    return resolved


def build_dataset_summary(
    db: Session,
    start_time: datetime,
    end_time: datetime,
    device_type: str | None = None,
) -> dict:
    """返回数据集查询摘要。

    该函数只统计数量和少量预览，不生成文件，适合前端先展示“可导出内容概览”。
    """

    start_time = parse_query_time(start_time)
    end_time = parse_query_time(end_time)
    sensor_rows = query_sensor_rows(db, start_time, end_time, device_type)
    object_rows = query_object_rows(start_time, end_time, device_type)
    return {
        "start_time": format_utc_timestamp(start_time),
        "end_time": format_utc_timestamp(end_time),
        "device_type": device_type or "all",
        "sensor_data_count": len(sensor_rows),
        "object_index_count": len(object_rows),
        "sensor_data_preview": sensor_rows[:20],
        "object_index_preview": object_rows[:20],
    }


def create_aligned_dataset_zip(
    db: Session,
    start_time: datetime,
    end_time: datetime,
    device_type: str | None = None,
) -> Path:
    """创建对齐数据集 ZIP 包。

    导出采用“时间窗口对齐”的轻量策略：只要传感器数据或文件对象落在
    start_time 到 end_time 之间，就会被放入同一个数据包，供后续实验整理使用。
    """

    start_time = parse_query_time(start_time)
    end_time = parse_query_time(end_time)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    sensor_rows = query_sensor_rows(db, start_time, end_time, device_type)
    object_rows = query_object_rows(start_time, end_time, device_type)

    created_at = utc_now_iso()
    export_path = EXPORT_DIR / f"aligned_dataset_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.zip"
    manifest = {
        "created_at": created_at,
        "start_time": format_utc_timestamp(start_time),
        "end_time": format_utc_timestamp(end_time),
        "device_type": device_type or "all",
        "sensor_data_count": len(sensor_rows),
        "object_index_count": len(object_rows),
        "alignment_rule": "按 UTC timestamp / start_time / indexed_at 落入同一时间窗口进行对齐",
        "files": [
            "sensor_data.csv",
            "object_index.json",
            "manifest.json",
        ],
    }

    with ZipFile(export_path, "w", compression=ZIP_DEFLATED) as archive:
        # 传感器观测数据使用 CSV，便于 Excel、Python pandas 和论文实验表格直接读取。
        csv_buffer = StringIO()
        fieldnames = [
            "timestamp",
            "device_id",
            "device_name",
            "device_type",
            "sensor_id",
            "sensor_name",
            "sensor_type",
            "value",
            "unit",
            "sensor_data_id",
        ]
        writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sensor_rows)
        archive.writestr("sensor_data.csv", "\ufeff" + csv_buffer.getvalue())

        # 文件对象不仅保留索引，也尽量把 runtime_data 内的文件实体一并打包。
        enriched_object_rows = []
        exported_path_map: dict[str, str] = {}
        exported_files = []
        for index, item in enumerate(object_rows, start=1):
            enriched_item = dict(item)
            object_path = _resolve_exportable_object_path(item)
            if object_path:
                path_key = str(object_path)
                if path_key not in exported_path_map:
                    object_type = item.get("object_type") or "object"
                    archive_name = f"objects/{object_type}/{index:04d}_{object_path.name}"
                    archive.write(object_path, archive_name)
                    exported_path_map[path_key] = archive_name
                    exported_files.append(archive_name)
                enriched_item["export_file"] = exported_path_map[path_key]
            enriched_object_rows.append(enriched_item)

        manifest["exported_object_file_count"] = len(exported_files)
        manifest["files"].extend(exported_files)

        # 文件对象索引保留 JSON 格式，避免丢失嵌套 metadata 信息。
        archive.writestr("object_index.json", json.dumps(enriched_object_rows, ensure_ascii=False, indent=2))
        # manifest 用于说明导出范围、数量和对齐规则，方便之后复现实验。
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    return export_path
