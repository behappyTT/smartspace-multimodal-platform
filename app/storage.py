"""运行时数据存储工具。

本模块用于统一管理程序运行过程中产生的数据文件，包括：
- SQLite 数据库文件
- 原始上传 JSON 备份
- 本地标准化传感器数据文件
- 摄像头抓拍画面与元数据
- 摄像头 MP4 录像文件与会话记录
- 规范化后的记录日志
- 数据来源审计日志

这样可以同时满足两类需求：
1. 数据库负责业务查询和前端展示
2. 文件目录负责保留原始数据和来源信息，便于追溯与答辩展示
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import cv2


ROOT_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DATA_DIR = ROOT_DIR / "runtime_data"
DB_DIR = RUNTIME_DATA_DIR / "db"
RAW_UPLOAD_DIR = RUNTIME_DATA_DIR / "raw_uploads"
STANDARDIZED_DATA_DIR = RUNTIME_DATA_DIR / "standardized_data"
CAMERA_FRAME_DIR = RUNTIME_DATA_DIR / "camera_frames"
CAMERA_VIDEO_DIR = RUNTIME_DATA_DIR / "camera_video"
NORMALIZED_DIR = RUNTIME_DATA_DIR / "normalized_records"
SOURCE_AUDIT_DIR = RUNTIME_DATA_DIR / "source_audit"
MULTIMODAL_INDEX_DIR = RUNTIME_DATA_DIR / "multimodal_index"
KNOWLEDGE_GRAPH_DIR = RUNTIME_DATA_DIR / "knowledge_graph"
EXPORT_DIR = RUNTIME_DATA_DIR / "exports"

DB_PATH = Path(os.getenv("SMARTSPACE_DB_PATH", str(DB_DIR / "smartspace.db")))
NORMALIZED_RECORD_FILE = NORMALIZED_DIR / "sensor_data_records.jsonl"
SOURCE_AUDIT_FILE = SOURCE_AUDIT_DIR / "source_audit.jsonl"
CAMERA_FRAME_RECORD_FILE = CAMERA_FRAME_DIR / "camera_frame_records.jsonl"
CAMERA_VIDEO_RECORD_FILE = CAMERA_VIDEO_DIR / "camera_video_records.jsonl"
OBJECT_INDEX_FILE = MULTIMODAL_INDEX_DIR / "object_index.jsonl"
KNOWLEDGE_GRAPH_SNAPSHOT_FILE = KNOWLEDGE_GRAPH_DIR / "knowledge_graph_snapshot.json"
CAMERA_SAVE_INTERVAL_SECONDS = int(os.getenv("SMARTSPACE_CAMERA_SAVE_INTERVAL", "5"))
CAMERA_VIDEO_FPS = float(os.getenv("SMARTSPACE_CAMERA_VIDEO_FPS", "12"))
CAMERA_VIDEO_SEGMENT_SECONDS = int(os.getenv("SMARTSPACE_CAMERA_VIDEO_SEGMENT_SECONDS", "30"))


def ensure_runtime_directories() -> None:
    """确保运行时数据目录存在。"""

    for path in (
        RUNTIME_DATA_DIR,
        DB_DIR,
        RAW_UPLOAD_DIR,
        STANDARDIZED_DATA_DIR,
        CAMERA_FRAME_DIR,
        CAMERA_VIDEO_DIR,
        NORMALIZED_DIR,
        SOURCE_AUDIT_DIR,
        MULTIMODAL_INDEX_DIR,
        KNOWLEDGE_GRAPH_DIR,
        EXPORT_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)

    # 如果通过环境变量指定了其他数据库路径，也一并确保其父目录存在。
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def utc_now_iso() -> str:
    """返回当前 UTC 时间的标准 ISO 字符串。"""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json_file(path: Path, content: dict) -> None:
    """把字典写入 JSON 文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, content: dict) -> None:
    """向 JSONL 文件追加一条记录。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(content, ensure_ascii=False) + "\n")


def record_object_index(
    object_type: str,
    modality: str,
    uri: str,
    *,
    timestamp: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    device_id: int | None = None,
    metadata: dict | None = None,
) -> None:
    """记录对象存储索引。

    SQLite 继续负责结构化查询；视频、图片和原始 JSON 这类文件对象
    通过该索引保留路径、模态和时间信息，便于后续做回放和数据集导出。
    """

    append_jsonl(
        OBJECT_INDEX_FILE,
        {
            "indexed_at": utc_now_iso(),
            "object_type": object_type,
            "modality": modality,
            "uri": uri,
            "timestamp": timestamp,
            "start_time": start_time,
            "end_time": end_time,
            "device_id": device_id,
            "metadata": metadata or {},
        },
    )


def read_object_index(limit: int | None = 100) -> list[dict]:
    """读取最近的对象索引记录。"""

    if not OBJECT_INDEX_FILE.exists():
        return []

    rows = []
    with OBJECT_INDEX_FILE.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    if limit is None:
        return rows
    return rows[-limit:]


def save_raw_upload(payload: dict, source_context: dict) -> str:
    """保存原始上传包。

    每次上传都会单独落一个 JSON 文件，保留最原始的请求内容和来源信息。
    """

    ensure_runtime_directories()
    date_dir = RAW_UPLOAD_DIR / datetime.now(timezone.utc).strftime("%Y%m%d")
    file_name = f"{datetime.now(timezone.utc).strftime('%H%M%S')}_device_{payload.get('device_id', 'unknown')}_{uuid4().hex[:8]}.json"
    file_path = date_dir / file_name

    write_json_file(
        file_path,
        {
            "received_at": utc_now_iso(),
            "source": source_context,
            "payload": payload,
        },
    )
    record_object_index(
        object_type="raw_upload",
        modality="metadata",
        uri=str(file_path),
        timestamp=utc_now_iso(),
        device_id=payload.get("device_id"),
        metadata={
            "source": source_context,
            "metric_count": len(payload.get("metrics", [])),
        },
    )
    return str(file_path)


def record_normalized_data(
    device_info: dict,
    normalized_timestamp: str,
    metrics: list[dict],
    source_context: dict,
    raw_file_path: str,
) -> None:
    """记录规范化后的数据日志。

    该日志用于说明：
    - 原始数据经过了标准化处理
    - 最终存储结构统一为标准 metrics
    - 每一条记录都可以追溯到原始上传文件
    """

    append_jsonl(
        NORMALIZED_RECORD_FILE,
        {
            "recorded_at": utc_now_iso(),
            "device": device_info,
            "normalized_timestamp": normalized_timestamp,
            "source": source_context,
            "raw_file_path": raw_file_path,
            "metrics": metrics,
        },
    )


def record_standardized_sensor_data(
    device_info: dict,
    metrics: list[dict],
    source_context: dict,
    raw_file_path: str,
) -> str:
    """把标准化后的单条传感器数据写入本地文件。

    与 normalized_records 的“按一次上传记一条”不同，
    这里会把每个 metric 拆成单条标准化记录，便于本地直接查看和后续导出。
    """

    ensure_runtime_directories()
    date_dir = STANDARDIZED_DATA_DIR / datetime.now(timezone.utc).strftime("%Y%m%d")
    file_path = date_dir / "sensor_data_standardized.jsonl"

    for metric in metrics:
        append_jsonl(
            file_path,
            {
                "recorded_at": utc_now_iso(),
                "device": device_info,
                "source": source_context,
                "raw_file_path": raw_file_path,
                "sensor_id": metric["sensor_id"],
                "sensor_type": metric["sensor_type"],
                "value": metric["value"],
                "unit": metric["unit"],
                "timestamp": metric["timestamp"],
            },
        )

    record_object_index(
        object_type="standardized_sensor_data",
        modality="time_series",
        uri=str(file_path),
        timestamp=utc_now_iso(),
        device_id=device_info.get("device_id"),
        metadata={
            "device": device_info,
            "metric_count": len(metrics),
            "raw_file_path": raw_file_path,
        },
    )

    return str(file_path)


def record_source_audit(
    device_info: dict,
    source_context: dict,
    stored_count: int,
    raw_file_path: str,
) -> None:
    """记录来源审计信息。

    这里强调“数据从哪里来”的可追溯性，例如：
    - 设备编号
    - 上传端 IP
    - 采集模式（sen0501 / dht22 / bme280）
    - 传输方式
    """

    append_jsonl(
        SOURCE_AUDIT_FILE,
        {
            "recorded_at": utc_now_iso(),
            "device": device_info,
            "source": source_context,
            "stored_count": stored_count,
            "raw_file_path": raw_file_path,
        },
    )


def save_camera_frame(frame, camera_index: int) -> str:
    """把摄像头当前帧保存到本地目录，并追加元数据记录。"""

    ensure_runtime_directories()
    date_dir = CAMERA_FRAME_DIR / datetime.now(timezone.utc).strftime("%Y%m%d")
    file_name = f"{datetime.now(timezone.utc).strftime('%H%M%S')}_camera_{camera_index}_{uuid4().hex[:8]}.jpg"
    file_path = date_dir / file_name

    date_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(file_path), frame)
    append_jsonl(
        CAMERA_FRAME_RECORD_FILE,
        {
            "recorded_at": utc_now_iso(),
            "camera_index": camera_index,
            "frame_path": str(file_path),
        },
    )
    recorded_at = utc_now_iso()
    record_object_index(
        object_type="camera_frame",
        modality="image",
        uri=str(file_path),
        timestamp=recorded_at,
        metadata={
            "camera_index": camera_index,
        },
    )
    return str(file_path)


def build_camera_video_path(camera_index: int) -> Path:
    """构造摄像头 MP4 录像文件路径。"""

    ensure_runtime_directories()
    date_dir = CAMERA_VIDEO_DIR / datetime.now(timezone.utc).strftime("%Y%m%d")
    file_name = f"{datetime.now(timezone.utc).strftime('%H%M%S')}_camera_{camera_index}_{uuid4().hex[:8]}.mp4"
    date_dir.mkdir(parents=True, exist_ok=True)
    return date_dir / file_name


def record_camera_video_session(
    camera_index: int,
    file_path: str,
    fps: float,
    frame_width: int,
    frame_height: int,
) -> None:
    """记录一次摄像头录像会话元数据。"""

    started_at = datetime.now(timezone.utc).replace(microsecond=0)
    ended_at = started_at + timedelta(seconds=CAMERA_VIDEO_SEGMENT_SECONDS)
    start_time = started_at.isoformat().replace("+00:00", "Z")
    end_time = ended_at.isoformat().replace("+00:00", "Z")
    append_jsonl(
        CAMERA_VIDEO_RECORD_FILE,
        {
            "recorded_at": start_time,
            "camera_index": camera_index,
            "file_path": file_path,
            "start_time": start_time,
            "end_time": end_time,
            "fps": fps,
            "frame_width": frame_width,
            "frame_height": frame_height,
        },
    )
    record_object_index(
        object_type="camera_video_segment",
        modality="video",
        uri=file_path,
        start_time=start_time,
        end_time=end_time,
        metadata={
            "camera_index": camera_index,
            "fps": fps,
            "frame_width": frame_width,
            "frame_height": frame_height,
        },
    )
