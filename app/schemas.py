"""Pydantic 数据模型。

本文件用于定义：
- 接口请求体
- 接口响应体
- 数据上传的标准化 JSON 结构

这样可以让 FastAPI 自动完成参数校验和接口文档生成。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DeviceBase(BaseModel):
    """设备基础字段。"""

    name: str = Field(..., max_length=100)
    device_type: str = Field(..., max_length=50)
    ip_address: str | None = Field(default=None, max_length=50)
    port: int | None = None
    status: str = Field(default="online", max_length=20)
    description: str | None = None


class DeviceCreate(DeviceBase):
    """创建设备请求模型。"""

    pass


class DeviceUpdate(BaseModel):
    """更新设备请求模型。

    所有字段都可选，便于实现局部更新。
    """

    name: str | None = Field(default=None, max_length=100)
    device_type: str | None = Field(default=None, max_length=50)
    ip_address: str | None = Field(default=None, max_length=50)
    port: int | None = None
    status: str | None = Field(default=None, max_length=20)
    description: str | None = None


class DeviceRead(DeviceBase):
    """设备响应模型。"""

    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SensorBase(BaseModel):
    """传感器基础字段。"""

    device_id: int
    name: str = Field(..., max_length=100)
    sensor_type: str = Field(..., max_length=50)
    unit: str = Field(..., max_length=20)


class SensorCreate(SensorBase):
    """创建传感器请求模型。"""

    pass


class SensorRead(SensorBase):
    """传感器响应模型。"""

    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MetricItem(BaseModel):
    """统一 metrics 数组中的单项结构。

    不同传感器数据统一用该结构表达，便于后续扩展。
    """

    sensor_type: str = Field(..., max_length=50)
    value: float
    unit: str = Field(..., max_length=20)


class SensorUploadPayload(BaseModel):
    """传感器节点上传请求模型。

    这是平台统一的标准化上报格式。
    """

    device_id: int
    device_type: str = Field(..., max_length=50)
    timestamp: datetime
    metrics: list[MetricItem]


class UploadResult(BaseModel):
    """上传接口响应模型。"""

    message: str
    stored_count: int
    normalized_timestamp: str


class LatestMetric(BaseModel):
    """仪表盘最新单项指标响应结构。"""

    sensor_type: str
    value: float | None
    unit: str
    timestamp: str | None


class DashboardLatestResponse(BaseModel):
    """仪表盘最新数据响应模型。"""

    device_id: int | None
    device_name: str | None
    metrics: list[LatestMetric]


class HistoryPoint(BaseModel):
    """历史曲线中的单个时间点。"""

    timestamp: str
    value: float


class HistorySeries(BaseModel):
    """某一类传感器的历史序列。"""

    sensor_type: str
    unit: str
    points: list[HistoryPoint]


class HistoryResponse(BaseModel):
    """历史查询接口响应模型。"""

    device_id: int | None
    hours: int
    series: list[HistorySeries]


class SemanticTriple(BaseModel):
    """知识图谱三元组。"""

    subject: str
    predicate: str
    object: str
    source: str | None = None


class KnowledgeGraphResponse(BaseModel):
    """轻量知识图谱响应模型。"""

    generated_at: str
    description: str
    model_layers: list[dict[str, Any]]
    triple_count: int
    triples: list[SemanticTriple]
    snapshot_path: str | None = None


class ObjectIndexResponse(BaseModel):
    """对象索引响应模型。"""

    count: int
    items: list[dict[str, Any]]


class DatasetQueryResponse(BaseModel):
    """数据集查询摘要响应模型。"""

    start_time: str
    end_time: str
    device_type: str
    sensor_data_count: int
    object_index_count: int
    sensor_data_preview: list[dict[str, Any]]
    object_index_preview: list[dict[str, Any]]


class TimelineCameraFrame(BaseModel):
    """时间轴回放对应的摄像头帧。"""

    available: bool
    timestamp: str | None
    frame_path: str | None
    source: str | None
    delta_seconds: float | None


class TimelineCameraVideo(BaseModel):
    """时间轴回放对应的摄像头视频片段。"""

    available: bool
    start_time: str | None
    end_time: str | None
    video_path: str | None
    source: str | None
    delta_seconds: float | None
    offset_seconds: float | None
    segment_key: str | None


class TimelineStateResponse(BaseModel):
    """指定时刻附近的多模态状态。"""

    target_time: str
    environment: DashboardLatestResponse
    motion: DashboardLatestResponse
    camera_frame: TimelineCameraFrame
    camera_video: TimelineCameraVideo
