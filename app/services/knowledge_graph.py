"""轻量知识图谱扩展服务。

当前系统仍以 SQLite 关系模型为核心实现。该模块把设备、传感器、
运行时对象索引等信息整理为三元组，用于表达多模态数据之间的语义关系。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session, joinedload

from app import models
from app.storage import KNOWLEDGE_GRAPH_SNAPSHOT_FILE, read_object_index, write_json_file


SEED_GRAPH_FILE = Path(__file__).resolve().parents[1] / "knowledge_graph.json"
DEFAULT_SPACE_NAME = "智能空间演示场景"


def _utc_now_iso() -> str:
    """生成知识图谱快照使用的 UTC 时间。"""

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _triple(subject: str, predicate: str, object_: str, source: str) -> dict:
    """统一封装三元组结构，避免不同来源字段格式不一致。"""

    return {
        "subject": subject,
        "predicate": predicate,
        "object": object_,
        "source": source,
    }


def load_seed_triples() -> list[dict]:
    """读取静态语义关系种子。"""

    if not SEED_GRAPH_FILE.exists():
        return []
    return json.loads(SEED_GRAPH_FILE.read_text(encoding="utf-8"))


def build_database_triples(db: Session) -> list[dict]:
    """根据当前关系数据库内容生成实体关系三元组。"""

    triples: list[dict] = []
    devices = (
        db.query(models.Device)
        .options(joinedload(models.Device.sensors))
        .order_by(models.Device.id.asc())
        .all()
    )

    for device in devices:
        # 设备实体来自关系型数据库，用于表达“空间-设备-传感器”的层级关系。
        triples.append(_triple(DEFAULT_SPACE_NAME, "包含", device.name, "database"))
        triples.append(_triple(device.name, "设备类型", device.device_type, "database"))
        triples.append(_triple(device.name, "运行状态", device.status, "database"))

        for sensor in sorted(device.sensors, key=lambda item: item.id):
            triples.append(_triple(device.name, "挂载", sensor.name, "database"))
            triples.append(_triple(sensor.name, "传感器类型", sensor.sensor_type, "database"))
            triples.append(_triple(sensor.name, "计量单位", sensor.unit, "database"))

    return triples


def build_object_triples(limit: int = 30) -> list[dict]:
    """根据对象索引生成文件对象与模态之间的关系。"""

    triples: list[dict] = []
    for item in read_object_index(limit=limit):
        # 对象索引中的 uri 可以理解为文件对象的唯一标识。
        # 这里不读取文件内容，只把文件路径、模态和时间转为可展示的语义关系。
        uri = item.get("uri")
        object_type = item.get("object_type")
        modality = item.get("modality")
        timestamp = item.get("timestamp") or item.get("start_time")
        if not uri:
            continue

        triples.append(_triple(uri, "对象类型", str(object_type), "object_index"))
        triples.append(_triple(uri, "数据模态", str(modality), "object_index"))
        if timestamp:
            triples.append(_triple(uri, "记录时间", str(timestamp), "object_index"))

    return triples


def build_knowledge_graph(db: Session) -> dict:
    """构建当前系统的轻量知识图谱视图。"""

    # seed_triples 表示设计层面预设的语义关系；database/object_index 表示运行时生成关系。
    seed_triples = [
        {**item, "source": "seed"}
        for item in load_seed_triples()
    ]
    triples = seed_triples + build_database_triples(db) + build_object_triples()

    return {
        "generated_at": _utc_now_iso(),
        "description": "关系模型 + 对象存储索引 + 知识图谱三元组的轻量扩展视图",
        "model_layers": [
            {
                "name": "关系模型",
                "purpose": "管理设备、传感器和结构化观测数据",
                "implementation": "SQLite: device / sensor / sensor_data",
            },
            {
                "name": "时序模型",
                "purpose": "按 timestamp 管理高频传感器观测数据",
                "implementation": "sensor_data.timestamp 与 standardized_data JSONL",
            },
            {
                "name": "对象存储模型",
                "purpose": "索引视频、图片、原始上传 JSON 等非结构化文件",
                "implementation": "runtime_data/multimodal_index/object_index.jsonl",
            },
            {
                "name": "知识图谱模型",
                "purpose": "表达空间、设备、传感器、观测值和媒体文件之间的语义关系",
                "implementation": "app/knowledge_graph.json + 动态数据库三元组",
            },
        ],
        "triple_count": len(triples),
        "triples": triples,
    }


def write_knowledge_graph_snapshot(db: Session) -> dict:
    """把当前知识图谱视图写入运行时快照文件。"""

    graph = build_knowledge_graph(db)
    write_json_file(KNOWLEDGE_GRAPH_SNAPSHOT_FILE, graph)
    graph["snapshot_path"] = str(KNOWLEDGE_GRAPH_SNAPSHOT_FILE)
    return graph
