"""Export labeled waist-mounted WT901 samples from the SQLite database.

The script turns manually annotated collection intervals into a CSV that can be
used by scripts/train_imu_cnn_gru.py.

Example:
python scripts/export_imu_activity_dataset.py
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import constants, crud, models
from app.database import SessionLocal
from app.storage import RUNTIME_DATA_DIR, ensure_runtime_directories


LOCAL_TZ = timezone(timedelta(hours=8))
DEFAULT_OUTPUT = RUNTIME_DATA_DIR / "labels" / "waist_imu_20260428_20260429.csv"

# These intervals are annotated from the user's waist/belly WT901 collection.
# They are written in China local time because that is how the experiment was
# recorded during collection.
DEFAULT_INTERVALS = [
    ("静止", "2026-04-28 19:43:00", "2026-04-28 19:43:50"),
    ("行走", "2026-04-28 19:44:00", "2026-04-28 19:45:00"),
    ("跑步", "2026-04-28 19:45:00", "2026-04-28 19:46:00"),
    ("坐下起立", "2026-04-28 19:46:05", "2026-04-28 19:47:00"),
    ("静止", "2026-04-29 19:07:00", "2026-04-29 19:07:50"),
    ("行走", "2026-04-29 19:08:00", "2026-04-29 19:09:50"),
    ("跑步", "2026-04-29 19:10:00", "2026-04-29 19:11:30"),
    ("坐下起立", "2026-04-29 19:12:00", "2026-04-29 19:14:00"),
]

CSV_COLUMNS = [
    "timestamp",
    "label",
    *constants.MOTION_SENSOR_TYPES,
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export labeled WT901 IMU activity dataset.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output CSV path.",
    )
    return parser.parse_args()


def parse_local_time(value: str) -> datetime:
    """Parse a local China-time timestamp and convert it to naive UTC.

    The database stores datetimes as naive UTC values, so this keeps querying
    consistent with the rest of the platform.
    """

    local_dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
    return local_dt.astimezone(timezone.utc).replace(tzinfo=None)


def format_local_time(value: datetime) -> str:
    """Format a database UTC datetime back to local time for human-readable CSV."""

    return value.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).isoformat(timespec="milliseconds")


def get_motion_sensor_map(db: Session) -> dict[int, str]:
    """Return sensor_id -> sensor_type for the first bluetooth node."""

    device = crud.get_first_device_by_type(db, constants.DeviceType.BLUETOOTH_NODE)
    if not device:
        raise RuntimeError("数据库中没有找到 bluetooth_node 设备，请先确认 WT901 数据已经上传。")

    sensor_map: dict[int, str] = {}
    missing_types: list[str] = []
    for sensor_type in constants.MOTION_SENSOR_TYPES:
        sensor = crud.get_sensor_by_device_and_type(db, device.id, sensor_type)
        if not sensor:
            missing_types.append(sensor_type)
            continue
        sensor_map[sensor.id] = sensor_type

    if missing_types:
        raise RuntimeError(f"蓝牙节点缺少以下运动传感器：{', '.join(missing_types)}")
    return sensor_map


def query_interval_rows(
    db: Session,
    sensor_map: dict[int, str],
    start_utc: datetime,
    end_utc: datetime,
) -> list[dict[str, str | float]]:
    """Query one labeled interval and align six-axis samples by timestamp."""

    rows = (
        db.query(models.SensorData)
        .filter(
            models.SensorData.sensor_id.in_(sensor_map.keys()),
            models.SensorData.timestamp >= start_utc,
            models.SensorData.timestamp <= end_utc,
        )
        .order_by(models.SensorData.timestamp.asc(), models.SensorData.id.asc())
        .all()
    )

    grouped: dict[datetime, dict[str, float]] = defaultdict(dict)
    for row in rows:
        grouped[row.timestamp][sensor_map[row.sensor_id]] = row.value

    aligned_rows: list[dict[str, str | float]] = []
    required_types = set(constants.MOTION_SENSOR_TYPES)
    for timestamp, values in sorted(grouped.items()):
        if set(values) != required_types:
            continue
        aligned_rows.append(
            {
                "timestamp": format_local_time(timestamp),
                **{sensor_type: values[sensor_type] for sensor_type in constants.MOTION_SENSOR_TYPES},
            }
        )
    return aligned_rows


def export_dataset(output_path: Path) -> dict[str, int]:
    ensure_runtime_directories()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        sensor_map = get_motion_sensor_map(db)
        counts: dict[str, int] = {}
        with output_path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
            writer.writeheader()

            for label, start_text, end_text in DEFAULT_INTERVALS:
                start_utc = parse_local_time(start_text)
                end_utc = parse_local_time(end_text)
                aligned_rows = query_interval_rows(db, sensor_map, start_utc, end_utc)
                counts[label] = len(aligned_rows)
                for row in aligned_rows:
                    writer.writerow({"label": label, **row})
                print(
                    f"{label}: {start_text} - {end_text} "
                    f"(UTC {start_utc.isoformat()} - {end_utc.isoformat()}), "
                    f"samples={len(aligned_rows)}"
                )
        return counts
    finally:
        db.close()


def main() -> None:
    args = parse_args()
    output_path = Path(args.output)
    counts = export_dataset(output_path)
    print(f"output={output_path}")
    print(f"total_samples={sum(counts.values())}")


if __name__ == "__main__":
    main()
