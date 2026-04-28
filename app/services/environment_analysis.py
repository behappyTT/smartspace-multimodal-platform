"""空间环境状态分析服务。

该模块基于树莓派环境节点上报的多项指标，生成可解释的环境分析结论。
分析逻辑刻意采用规则模型，方便在毕业设计答辩中说明每个结论的来源。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import constants, crud


def _metric_map(metrics: list[dict]) -> dict[str, dict]:
    """把 metrics 列表转为按 sensor_type 索引的字典。"""

    return {item["sensor_type"]: item for item in metrics}


def _value(metrics: dict[str, dict], sensor_type: str) -> float | None:
    """安全读取某个指标值。"""

    value = metrics.get(sensor_type, {}).get("value")
    return float(value) if value is not None else None


def _grade(score: float) -> tuple[str, str]:
    """根据综合分数给出状态等级。"""

    if score >= 85:
        return "良好", "当前空间环境整体较舒适，适合正常学习、展示或轻量活动。"
    if score >= 70:
        return "基本适宜", "当前空间环境可接受，但存在少量需要关注的指标。"
    if score >= 55:
        return "需要关注", "当前空间环境存在明显不适因素，建议适当调整后再进行长时间活动。"
    return "不适宜", "当前空间环境不太适合持续停留或运动，建议优先改善环境条件。"


def _bounded_penalty(value: float, ideal: float, tolerance: float, weight: float, max_penalty: float) -> float:
    """根据偏离理想值的幅度计算连续扣分。

    tolerance 表示“不明显扣分”的缓冲范围；超过缓冲范围后，偏离越大扣分越多。
    这样温湿度小幅变化也会让综合评分有细微变化，而不是一直停在同一个档位。
    """

    deviation = max(abs(value - ideal) - tolerance, 0)
    return min(deviation * weight, max_penalty)


def analyze_environment_metrics(metrics: list[dict]) -> dict:
    """根据环境指标生成空间状态分析。

    输入直接复用仪表盘 latest/timeline 返回的 metrics 结构，因此实时模式和历史回溯模式
    都可以使用同一套分析规则。
    """

    by_type = _metric_map(metrics)
    temperature = _value(by_type, constants.SensorType.TEMPERATURE)
    humidity = _value(by_type, constants.SensorType.HUMIDITY)
    pressure = _value(by_type, constants.SensorType.PRESSURE)
    altitude = _value(by_type, constants.SensorType.ALTITUDE)
    ultraviolet = _value(by_type, constants.SensorType.ULTRAVIOLET)
    illuminance = _value(by_type, constants.SensorType.ILLUMINANCE)

    score = 100.0
    findings: list[str] = []
    suggestions: list[str] = []

    if temperature is None:
        score -= 18
        findings.append("温度数据暂未上报")
    else:
        score -= _bounded_penalty(temperature, ideal=23, tolerance=1.0, weight=2.4, max_penalty=24)
        if temperature < 16:
            findings.append("温度偏低")
            suggestions.append("可适当提高室内温度，避免长时间静坐受凉")
        elif temperature > 30:
            findings.append("温度偏高")
            suggestions.append("建议通风或降温，不宜进行高强度运动")
        elif temperature > 26:
            findings.append("温度略高")
            suggestions.append("如需运动，建议控制强度并注意补水")
        else:
            findings.append("温度处于较舒适范围")

    if humidity is None:
        score -= 16
        findings.append("湿度数据暂未上报")
    else:
        score -= _bounded_penalty(humidity, ideal=50, tolerance=5.0, weight=0.65, max_penalty=18)
        if humidity < 35:
            findings.append("空气偏干")
            suggestions.append("可适当补水或增加空气湿度")
        elif humidity > 75:
            findings.append("湿度偏高")
            suggestions.append("建议加强通风，避免闷热感")
        elif 40 <= humidity <= 65:
            findings.append("湿度处于较舒适范围")
        else:
            findings.append("湿度基本可接受")

    if pressure is None:
        score -= 4
        findings.append("气压数据暂未上报")
    else:
        score -= _bounded_penalty(pressure, ideal=1013.25, tolerance=8.0, weight=0.18, max_penalty=8)
        if not 950 <= pressure <= 1050:
            findings.append("气压偏离常见室内范围")
        else:
            findings.append("气压处于常见室内范围")

    if illuminance is None:
        score -= 4
        findings.append("环境光数据暂未上报")
    else:
        if illuminance < 300:
            score -= min((300 - illuminance) / 25, 10)
            findings.append("环境光偏暗")
            suggestions.append("建议增加照明，提升展示或阅读可见性")
        elif illuminance > 1200:
            score -= min((illuminance - 1200) / 300, 8)
            findings.append("环境光较强")
            suggestions.append("如画面过曝，可调整摄像头朝向或降低强光直射")
        else:
            score -= min(abs(illuminance - 500) / 500, 3)
            findings.append("环境光强度适中")

    if ultraviolet is None:
        score -= 4
        findings.append("紫外线数据暂未上报")
    else:
        score -= min(max(ultraviolet, 0) * 18, 12)
        if ultraviolet > 0.4:
            findings.append("紫外线强度偏高")
            suggestions.append("建议减少强光直射，必要时采取遮挡或防护措施")
        else:
            findings.append("紫外线强度较低")

    if altitude is not None:
        findings.append(f"海拔估算约 {altitude:.2f} m")

    if not suggestions:
        suggestions.append("当前无需明显干预，可继续观察环境变化趋势")

    score = round(max(0, min(100, score)), 1)
    level, summary = _grade(score)

    exercise_suitable = (
        temperature is not None
        and humidity is not None
        and 16 <= temperature <= 28
        and 30 <= humidity <= 70
        and (ultraviolet is None or ultraviolet <= 0.4)
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "score": score,
        "level": level,
        "summary": summary,
        "exercise_suitable": exercise_suitable,
        "findings": findings[:6],
        "suggestions": suggestions[:4],
    }


def build_latest_environment_analysis(db: Session) -> dict:
    """基于当前最新环境数据生成空间环境分析。"""

    target_device = crud.get_first_device_by_type(db, constants.DeviceType.SENSOR_NODE)
    if not target_device:
        return {
            "device_id": None,
            "device_name": None,
            "analysis": analyze_environment_metrics([]),
        }

    metrics = crud.get_latest_metrics(db, target_device.id, sensor_types=constants.ENV_SENSOR_TYPES)
    return {
        "device_id": target_device.id,
        "device_name": target_device.name,
        "metrics": metrics,
        "analysis": analyze_environment_metrics(metrics),
    }
