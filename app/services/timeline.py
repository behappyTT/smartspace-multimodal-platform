"""历史回溯时间轴服务。"""

from __future__ import annotations

import json
import time
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app import constants, crud, models
from app.storage import (
    CAMERA_FRAME_RECORD_FILE,
    CAMERA_VIDEO_RECORD_FILE,
    CAMERA_VIDEO_SEGMENT_SECONDS,
    RUNTIME_DATA_DIR,
    read_object_index,
)


# 回溯时允许匹配目标时间前后 5 分钟内的数据。
# 这样可以覆盖传感器上报间隔不完全一致的情况，同时避免误拿很久以前的数据当作当前状态。
MAX_TIMELINE_DELTA_SECONDS = 300


def parse_timeline_time(value: datetime) -> datetime:
    """把接口时间统一转换为 SQLite 使用的 UTC naive datetime。"""

    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_index_time(value: str | None) -> datetime | None:
    """解析对象索引或抓拍记录中的时间字段。"""

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _metric_at_or_near_time(db: Session, sensor: models.Sensor, target_time: datetime) -> models.SensorData | None:
    """查找目标时间附近最近的一条传感器数据。

    优先取目标时刻之前的最新值，因为仪表盘状态通常表示“该时刻已经发生的状态”；
    如果前序数据不存在，再向目标时刻之后补找一条最近值，提升演示时的容错性。
    """

    before = (
        db.query(models.SensorData)
        .filter(
            models.SensorData.sensor_id == sensor.id,
            models.SensorData.timestamp <= target_time,
        )
        .order_by(desc(models.SensorData.timestamp), desc(models.SensorData.id))
        .first()
    )
    if before and abs((before.timestamp - target_time).total_seconds()) <= MAX_TIMELINE_DELTA_SECONDS:
        return before

    after = (
        db.query(models.SensorData)
        .filter(
            models.SensorData.sensor_id == sensor.id,
            models.SensorData.timestamp >= target_time,
        )
        .order_by(models.SensorData.timestamp.asc(), models.SensorData.id.asc())
        .first()
    )
    if after and abs((after.timestamp - target_time).total_seconds()) <= MAX_TIMELINE_DELTA_SECONDS:
        return after
    return None


def build_metrics_at_time(
    db: Session,
    device_type: str,
    sensor_types: list[str],
    target_time: datetime,
) -> dict:
    """查询某类设备在指定时刻附近的传感器状态。"""

    target_device = crud.get_first_device_by_type(db, device_type)
    if not target_device:
        return {
            "device_id": None,
            "device_name": None,
            "metrics": [],
        }

    metrics = []
    for sensor_type in sensor_types:
        sensor = crud.get_sensor_by_device_and_type(db, target_device.id, sensor_type)
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

        row = _metric_at_or_near_time(db, sensor, target_time)
        metrics.append(
            {
                "sensor_type": sensor_type,
                "value": row.value if row else None,
                "unit": sensor.unit or unit,
                "timestamp": crud.format_utc_timestamp(row.timestamp) if row else None,
            }
        )

    return {
        "device_id": target_device.id,
        "device_name": target_device.name,
        "metrics": metrics,
    }


def _iter_camera_frame_records() -> list[dict]:
    """汇总摄像头抓拍索引。

    新版本优先从 multimodal_index 读取对象索引，同时兼容旧版本的
    camera_frame_records.jsonl，避免已有运行数据因为升级而不可回放。
    """

    records = []
    for item in read_object_index(limit=None):
        if item.get("object_type") == "camera_frame":
            records.append(
                {
                    "timestamp": item.get("timestamp") or item.get("indexed_at"),
                    "frame_path": item.get("uri"),
                    "source": "object_index",
                }
            )

    if CAMERA_FRAME_RECORD_FILE.exists():
        with CAMERA_FRAME_RECORD_FILE.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                records.append(
                    {
                        "timestamp": item.get("recorded_at"),
                        "frame_path": item.get("frame_path"),
                        "source": "camera_frame_records",
                    }
                )
    return records


def find_nearest_camera_frame(target_time: datetime) -> dict | None:
    """查找最接近指定时间的摄像头抓拍帧。"""

    best: dict | None = None
    best_delta: float | None = None
    runtime_root = RUNTIME_DATA_DIR.resolve()

    for record in _iter_camera_frame_records():
        frame_time = _parse_index_time(record.get("timestamp"))
        frame_path = record.get("frame_path")
        if frame_time is None or not frame_path:
            continue

        path = Path(frame_path)
        if not path.exists():
            continue

        resolved = path.resolve()
        if runtime_root not in resolved.parents:
            continue

        delta = abs((frame_time - target_time).total_seconds())
        if delta > MAX_TIMELINE_DELTA_SECONDS:
            continue
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best = {
                "available": True,
                "timestamp": crud.format_utc_timestamp(frame_time),
                "frame_path": str(resolved),
                "source": record.get("source"),
                "delta_seconds": delta,
            }

    return best


def _iter_camera_video_records() -> list[dict]:
    """汇总摄像头 MP4 分段录像索引。

    新记录会写入 multimodal_index，旧记录则可能只存在 camera_video_records.jsonl。
    这里统一整理成 start_time / end_time / video_path，方便时间轴回放查询。
    """

    records = []
    for item in read_object_index(limit=None):
        if item.get("object_type") == "camera_video_segment":
            metadata = item.get("metadata") or {}
            records.append(
                {
                    "start_time": item.get("start_time") or item.get("timestamp") or item.get("indexed_at"),
                    "end_time": item.get("end_time"),
                    "video_path": item.get("uri"),
                    "fps": metadata.get("fps"),
                    "source": "object_index",
                }
            )

    if CAMERA_VIDEO_RECORD_FILE.exists():
        with CAMERA_VIDEO_RECORD_FILE.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                records.append(
                    {
                        "start_time": item.get("start_time") or item.get("recorded_at"),
                        "end_time": item.get("end_time"),
                        "video_path": item.get("file_path"),
                        "fps": item.get("fps"),
                        "source": "camera_video_records",
                    }
                )
    return records


def find_nearest_camera_video_segment(target_time: datetime) -> dict | None:
    """查找覆盖或最接近指定时间的摄像头 MP4 分段录像。"""

    best: dict | None = None
    best_delta: float | None = None
    runtime_root = RUNTIME_DATA_DIR.resolve()

    for record in _iter_camera_video_records():
        start_time = _parse_index_time(record.get("start_time"))
        end_time = _parse_index_time(record.get("end_time"))
        video_path = record.get("video_path")
        if start_time is None or not video_path:
            continue
        if end_time is None:
            # 兼容旧版本记录：没有 end_time 时按当前配置的分段长度估算。
            end_time = start_time + timedelta(seconds=CAMERA_VIDEO_SEGMENT_SECONDS)

        path = Path(video_path)
        if not path.exists() or path.stat().st_size <= 0:
            continue

        resolved = path.resolve()
        if runtime_root not in resolved.parents:
            continue

        if start_time <= target_time <= end_time:
            delta = 0.0
        elif target_time < start_time:
            delta = (start_time - target_time).total_seconds()
        else:
            delta = (target_time - end_time).total_seconds()

        if delta > MAX_TIMELINE_DELTA_SECONDS:
            continue
        if best_delta is not None and delta >= best_delta:
            continue

        duration_seconds = max((end_time - start_time).total_seconds(), 1.0)
        offset_seconds = min(max((target_time - start_time).total_seconds(), 0.0), duration_seconds)
        best_delta = delta
        best = {
            "available": True,
            "start_time": crud.format_utc_timestamp(start_time),
            "end_time": crud.format_utc_timestamp(end_time),
            "video_path": str(resolved),
            "source": record.get("source"),
            "delta_seconds": delta,
            "offset_seconds": offset_seconds,
            "segment_key": f"{resolved.name}:{start_time.isoformat()}",
        }

    return best


def read_camera_video_frame(target_time: datetime) -> bytes | None:
    """从历史 MP4 分段中读取指定时刻对应的一帧 JPEG。"""

    video_segment = find_nearest_camera_video_segment(target_time)
    if not video_segment or not video_segment.get("video_path"):
        return None

    capture = cv2.VideoCapture(video_segment["video_path"])
    try:
        if not capture.isOpened():
            return None
        capture.set(cv2.CAP_PROP_POS_MSEC, float(video_segment["offset_seconds"] or 0) * 1000)
        ok, frame = capture.read()
        if not ok or frame is None:
            return None
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return None
        return jpeg.tobytes()
    finally:
        capture.release()


def historical_camera_mjpeg_stream(target_time: datetime) -> Generator[bytes, None, None]:
    """把历史 MP4 分段转成 MJPEG 流，供前端像实时摄像头一样播放。"""

    video_segment = find_nearest_camera_video_segment(target_time)
    if not video_segment or not video_segment.get("video_path"):
        return

    capture = cv2.VideoCapture(video_segment["video_path"])
    try:
        if not capture.isOpened():
            return

        metadata_fps = capture.get(cv2.CAP_PROP_FPS) or 12
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        start_time = _parse_index_time(video_segment.get("start_time"))
        end_time = _parse_index_time(video_segment.get("end_time"))
        duration_seconds = (
            max((end_time - start_time).total_seconds(), 1.0)
            if start_time is not None and end_time is not None
            else max(frame_count / metadata_fps, 1.0)
        )
        effective_fps = frame_count / duration_seconds if frame_count > 0 else metadata_fps
        # OpenCV 写出的 MP4 元数据帧率可能偏低；按片段实际帧数估算能避免历史流被慢放。
        frame_delay = 1 / max(min(effective_fps, 30), 1)
        capture.set(cv2.CAP_PROP_POS_MSEC, float(video_segment["offset_seconds"] or 0) * 1000)
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if not ok:
                break
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
            )
            time.sleep(frame_delay)
    finally:
        capture.release()


def build_timeline_state(db: Session, target_time: datetime) -> dict:
    """构建指定时刻附近的多模态状态。"""

    frame = find_nearest_camera_frame(target_time)
    video_segment = find_nearest_camera_video_segment(target_time)
    state = build_timeline_sensor_state(db, target_time)
    state.update(
        {
            "camera_frame": frame or {
                "available": False,
                "timestamp": None,
                "frame_path": None,
                "source": None,
                "delta_seconds": None,
            },
            "camera_video": video_segment or {
                "available": False,
                "start_time": None,
                "end_time": None,
                "video_path": None,
                "source": None,
                "delta_seconds": None,
                "offset_seconds": None,
                "segment_key": None,
            },
        }
    )
    return state


def build_timeline_sensor_state(db: Session, target_time: datetime) -> dict:
    """构建指定时刻附近的环境和运动传感器状态。"""

    return {
        "target_time": crud.format_utc_timestamp(target_time),
        "environment": build_metrics_at_time(
            db,
            constants.DeviceType.SENSOR_NODE,
            constants.ENV_SENSOR_TYPES,
            target_time,
        ),
        "motion": build_metrics_at_time(
            db,
            constants.DeviceType.BLUETOOTH_NODE,
            constants.MOTION_SENSOR_TYPES,
            target_time,
        ),
    }
