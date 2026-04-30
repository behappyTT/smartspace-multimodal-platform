"""IMU 活动识别服务。

基于腰部佩戴 WT901 上报的加速度和角速度时间窗口，优先尝试 1D-CNN
+ GRU/LSTM 深度模型推理；当模型文件或 PyTorch 不可用时，自动回退到
可解释规则模型，保证毕业设计演示稳定运行。
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, pstdev

from sqlalchemy.orm import Session

from app import constants, crud, models
from app.services import imu_deep_model


WINDOW_SECONDS = 1.2


def _empty_result(reason: str) -> dict:
    """返回空态识别结果。"""

    return {
        "activity": "暂无数据",
        "level": "unknown",
        "confidence": 0.0,
        "model_source": "rule_fallback",
        "summary": reason,
        "window_seconds": WINDOW_SECONDS,
        "sample_count": 0,
        "features": {
            "acc_magnitude_mean": None,
            "acc_dynamic_std": None,
            "acc_dynamic_peak": None,
            "gyro_rms": None,
            "gyro_peak": None,
        },
        "deep_model": imu_deep_model.predict_activity([]),
        "suggestions": ["等待 WT901 蓝牙节点上传连续 IMU 数据"],
        "generated_at": crud.format_utc_timestamp(datetime.utcnow()),
    }


def _sensor_map(db: Session, device_id: int) -> dict[str, models.Sensor]:
    """获取 IMU 分析需要的传感器对象。"""

    return {
        sensor_type: sensor
        for sensor_type in constants.MOTION_SENSOR_TYPES
        if (sensor := crud.get_sensor_by_device_and_type(db, device_id, sensor_type)) is not None
    }


def _collect_window_samples(db: Session, device_id: int, target_time: datetime) -> list[dict]:
    """查询目标时刻前一小段窗口内的 IMU 数据并按时间戳聚合。"""

    sensors = _sensor_map(db, device_id)
    required = {
        constants.SensorType.ACCELERATION_X,
        constants.SensorType.ACCELERATION_Y,
        constants.SensorType.ACCELERATION_Z,
        constants.SensorType.ANGULAR_VELOCITY_X,
        constants.SensorType.ANGULAR_VELOCITY_Y,
        constants.SensorType.ANGULAR_VELOCITY_Z,
    }
    if not required.issubset(sensors):
        return []

    start_time = target_time - timedelta(seconds=WINDOW_SECONDS)
    sensor_id_to_type = {sensor.id: sensor_type for sensor_type, sensor in sensors.items()}
    rows = (
        db.query(models.SensorData)
        .filter(
            models.SensorData.sensor_id.in_(sensor_id_to_type.keys()),
            models.SensorData.timestamp >= start_time,
            models.SensorData.timestamp <= target_time,
        )
        .order_by(models.SensorData.timestamp.asc(), models.SensorData.id.asc())
        .all()
    )

    grouped: dict[datetime, dict[str, float]] = defaultdict(dict)
    for row in rows:
        grouped[row.timestamp][sensor_id_to_type[row.sensor_id]] = row.value

    samples = []
    for timestamp, values in grouped.items():
        if required.issubset(values):
            samples.append({"timestamp": timestamp, **values})
    return samples


def _classify(acc_dynamic_std: float, acc_dynamic_peak: float, gyro_rms: float, gyro_peak: float) -> tuple[str, str, float, str, list[str]]:
    """根据窗口特征分类活动状态。"""

    if acc_dynamic_peak >= 1.2 or gyro_peak >= 320:
        return (
            "冲击或剧烈晃动",
            "impact",
            0.92,
            "检测到较大的瞬时加速度或角速度峰值，可能存在撞击、快速甩动或剧烈姿态变化。",
            ["建议结合摄像头回放确认当时空间活动情况"],
        )
    if acc_dynamic_std >= 0.22 or acc_dynamic_peak >= 0.55 or gyro_rms >= 90:
        return (
            "剧烈运动",
            "high",
            0.86,
            "IMU 波动明显，当前处于较强运动或快速摆动状态。",
            ["适合标记为高强度活动片段，后续可用于活动数据集构建"],
        )
    if acc_dynamic_std >= 0.08 or acc_dynamic_peak >= 0.22 or gyro_rms >= 25:
        return (
            "持续活动",
            "medium",
            0.78,
            "加速度和角速度存在连续波动，当前设备处于移动或人体活动状态。",
            ["可继续观察活动持续时间和峰值变化"],
        )
    if acc_dynamic_std >= 0.025 or acc_dynamic_peak >= 0.08 or gyro_rms >= 6:
        return (
            "轻微运动",
            "low",
            0.72,
            "IMU 存在小幅变化，可能是轻微晃动、拿起放下或姿态微调。",
            ["当前运动强度较低，可作为静止和活动之间的过渡状态"],
        )
    return (
        "静止",
        "still",
        0.82,
        "加速度合量和角速度整体稳定，当前设备基本处于静止状态。",
        ["可作为静止状态基线，用于和后续运动片段对比"],
    )


def _level_from_deep_activity(activity: str) -> str:
    """将深度模型类别映射到前端使用的运动强度等级。"""

    if activity in {"静止", "站立", "坐姿", "躺卧"}:
        return "still"
    if activity in {"轻微运动", "转身", "弯腰"}:
        return "low"
    if activity in {"行走", "上楼/下楼", "坐下/起立", "坐下起立"}:
        return "medium"
    if activity in {"跑步", "剧烈晃动"}:
        return "high"
    return "medium"


def _merge_deep_prediction(
    rule_activity: str,
    rule_level: str,
    rule_confidence: float,
    rule_summary: str,
    rule_suggestions: list[str],
    deep_prediction: dict,
    *,
    stationary_guard: bool = False,
) -> tuple[str, str, float, str, list[str], str]:
    """深度模型可用时作为主预测结果，否则保留规则兜底结果。"""

    if stationary_guard:
        model_hint = ""
        if deep_prediction.get("available"):
            model_hint = f"；深度模型原始预测为“{deep_prediction.get('activity')}”，已由静止门控校正"
        return (
            "静止",
            "still",
            max(rule_confidence, 0.9),
            f"加速度和角速度窗口波动很小，系统判定当前处于静止状态{model_hint}。",
            ["当前窗口更符合静止特征，可继续保持 WT901 腰部固定以减少误判"],
            "stationary_guard",
        )

    if deep_prediction.get("available") and deep_prediction.get("confidence", 0.0) >= 0.65:
        deep_activity = deep_prediction["activity"]
        model_type = deep_prediction.get("model_type", "1D-CNN-GRU")
        deep_confidence = deep_prediction.get("confidence", 0.0)
        confidence_note = "置信度较高" if deep_confidence >= 0.55 else "置信度一般，建议结合规则参考"
        return (
            deep_activity,
            _level_from_deep_activity(deep_activity),
            deep_confidence,
            f"{model_type} 模型预测当前活动为“{deep_activity}”，{confidence_note}；规则模型参考结果为“{rule_activity}”。",
            ["建议固定 WT901 在腰部同一位置，保证训练数据和实际推理姿态一致"],
            "deep_model",
        )

    if deep_prediction.get("available"):
        rule_summary = (
            f"{rule_summary} 深度模型短窗口预测为“{deep_prediction.get('activity')}”，"
            f"但置信度较低，因此采用规则结果。"
        )

    return (
        rule_activity,
        rule_level,
        rule_confidence,
        rule_summary,
        rule_suggestions,
        "rule_fallback",
    )


def build_imu_activity_analysis(db: Session, target_time: datetime | None = None) -> dict:
    """构建 IMU 活动识别结果。"""

    device = crud.get_first_device_by_type(db, constants.DeviceType.BLUETOOTH_NODE)
    if not device:
        return _empty_result("暂无蓝牙 IMU 设备")

    target_time = target_time or datetime.utcnow()
    samples = _collect_window_samples(db, device.id, target_time)
    if len(samples) < 3:
        return _empty_result("最近窗口内 IMU 样本不足，暂时无法识别活动状态")

    acc_magnitudes = []
    gyro_magnitudes = []
    for sample in samples:
        ax = sample[constants.SensorType.ACCELERATION_X]
        ay = sample[constants.SensorType.ACCELERATION_Y]
        az = sample[constants.SensorType.ACCELERATION_Z]
        gx = sample[constants.SensorType.ANGULAR_VELOCITY_X]
        gy = sample[constants.SensorType.ANGULAR_VELOCITY_Y]
        gz = sample[constants.SensorType.ANGULAR_VELOCITY_Z]
        acc_magnitudes.append(math.sqrt(ax * ax + ay * ay + az * az))
        gyro_magnitudes.append(math.sqrt(gx * gx + gy * gy + gz * gz))

    dynamic_acc = [abs(value - 1.0) for value in acc_magnitudes]
    acc_dynamic_std = pstdev(dynamic_acc) if len(dynamic_acc) > 1 else 0.0
    acc_dynamic_peak = max(dynamic_acc)
    gyro_rms = math.sqrt(mean([value * value for value in gyro_magnitudes]))
    gyro_peak = max(gyro_magnitudes)
    rule_activity, rule_level, rule_confidence, rule_summary, rule_suggestions = _classify(
        acc_dynamic_std,
        acc_dynamic_peak,
        gyro_rms,
        gyro_peak,
    )
    deep_prediction = imu_deep_model.predict_activity(samples)
    stationary_guard = (
        rule_level == "still"
        and acc_dynamic_std < 0.04
        and acc_dynamic_peak < 0.12
        and gyro_rms < 8
    )
    activity, level, confidence, summary, suggestions, model_source = _merge_deep_prediction(
        rule_activity,
        rule_level,
        rule_confidence,
        rule_summary,
        rule_suggestions,
        deep_prediction,
        stationary_guard=stationary_guard,
    )

    return {
        "device_id": device.id,
        "device_name": device.name,
        "activity": activity,
        "level": level,
        "confidence": confidence,
        "model_source": model_source,
        "summary": summary,
        "window_seconds": WINDOW_SECONDS,
        "sample_count": len(samples),
        "features": {
            "acc_magnitude_mean": round(mean(acc_magnitudes), 4),
            "acc_dynamic_std": round(acc_dynamic_std, 4),
            "acc_dynamic_peak": round(acc_dynamic_peak, 4),
            "gyro_rms": round(gyro_rms, 3),
            "gyro_peak": round(gyro_peak, 3),
        },
        "rule_reference": {
            "activity": rule_activity,
            "level": rule_level,
            "confidence": rule_confidence,
        },
        "deep_model": deep_prediction,
        "suggestions": suggestions,
        "generated_at": crud.format_utc_timestamp(target_time),
    }
