"""FastAPI 应用入口。

本文件负责：
- 创建 FastAPI 应用
- 初始化数据库表
- 挂载静态资源和模板
- 定义全部接口
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import constants, crud, models, schemas
from app.database import engine, get_db
from app.services.dataset_export import build_dataset_summary, create_aligned_dataset_zip, parse_query_time
from app.services.environment_analysis import build_latest_environment_analysis
from app.services.knowledge_graph import build_knowledge_graph, write_knowledge_graph_snapshot
from app.services.mqtt_listener import mqtt_ingestion_service
from app.services.normalizer import process_upload, validate_device_type, validate_sensor_type
from app.services.timeline import (
    build_timeline_sensor_state,
    build_timeline_state,
    find_nearest_camera_frame,
    find_nearest_camera_video_segment,
    historical_camera_mjpeg_stream,
    parse_timeline_time,
    read_camera_video_frame,
)
from app.services.video import camera, mjpeg_stream
from app.storage import ensure_runtime_directories, read_object_index


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用生命周期钩子。

    启动时自动创建数据库表并打开摄像头，关闭时释放摄像头资源。
    """

    ensure_runtime_directories()
    models.Base.metadata.create_all(bind=engine)
    camera.open()
    mqtt_ingestion_service.start()
    yield
    mqtt_ingestion_service.stop()
    camera.release()


def build_source_context(request: Request) -> dict[str, str]:
    """从请求中提取来源信息。

    来源信息不会写入业务三表，而是写入运行时审计文件，
    用于说明数据来自哪个节点、以什么方式上传、何时进入平台。
    """

    return {
        "client_ip": request.client.host if request.client else "unknown",
        "transport": "http",
        "source_name": request.headers.get("X-Source-Name", "unknown_source"),
        "collector_mode": request.headers.get("X-Collector-Mode", "unspecified"),
        "user_agent": request.headers.get("User-Agent", "unknown"),
        "received_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


app = FastAPI(
    title="面向智能空间的多模态感知数据采集系统和分析平台",
    version="0.1.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request):
    """返回仪表盘页面。"""

    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/devices", response_model=schemas.DeviceRead)
def create_device(device_in: schemas.DeviceCreate, db: Session = Depends(get_db)):
    """创建设备。"""

    validate_device_type(device_in.device_type)
    return crud.create_device(db, device_in)


@app.get("/devices", response_model=list[schemas.DeviceRead])
def get_devices(db: Session = Depends(get_db)):
    """查询全部设备。"""

    return crud.list_devices(db)


@app.put("/devices/{device_id}", response_model=schemas.DeviceRead)
def update_device(
    device_id: int,
    device_in: schemas.DeviceUpdate,
    db: Session = Depends(get_db),
):
    """更新指定设备。"""

    device = crud.get_device(db, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")

    if device_in.device_type is not None:
        validate_device_type(device_in.device_type)

    return crud.update_device(db, device, device_in)


@app.delete("/devices/{device_id}")
def delete_device(device_id: int, db: Session = Depends(get_db)):
    """删除指定设备。"""

    device = crud.get_device(db, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    crud.delete_device(db, device)
    return {"message": "设备删除成功"}


@app.post("/sensors", response_model=schemas.SensorRead)
def create_sensor(sensor_in: schemas.SensorCreate, db: Session = Depends(get_db)):
    """创建传感器记录。"""

    device = crud.get_device(db, sensor_in.device_id)
    if not device:
        raise HTTPException(status_code=404, detail="关联设备不存在")

    validate_sensor_type(sensor_in.sensor_type)
    expected_unit = constants.SENSOR_UNIT_MAP[sensor_in.sensor_type]
    if sensor_in.unit != expected_unit:
        raise HTTPException(status_code=400, detail=f"单位应为 {expected_unit}")
    return crud.create_sensor(db, sensor_in)


@app.get("/sensors", response_model=list[schemas.SensorRead])
def get_sensors(db: Session = Depends(get_db)):
    """查询全部传感器。"""

    return crud.list_sensors(db)


@app.post("/sensor-data/upload", response_model=schemas.UploadResult)
def upload_sensor_data(
    payload: schemas.SensorUploadPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    """接收标准化传感器上传数据。"""

    return process_upload(db, payload, source_context=build_source_context(request))


@app.get("/dashboard/latest", response_model=schemas.DashboardLatestResponse)
def dashboard_latest(
    device_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """查询仪表盘当前最新温湿度数据。"""

    target_device = crud.get_device(db, device_id) if device_id else crud.get_first_device_by_type(
        db, constants.DeviceType.SENSOR_NODE
    )
    if not target_device:
        return schemas.DashboardLatestResponse(
            device_id=None,
            device_name=None,
            metrics=[],
        )
    return schemas.DashboardLatestResponse(
        device_id=target_device.id,
        device_name=target_device.name,
        metrics=crud.get_latest_metrics(db, target_device.id, sensor_types=constants.ENV_SENSOR_TYPES),
    )


@app.get("/analysis/environment")
def environment_analysis(db: Session = Depends(get_db)):
    """基于当前最新环境指标生成空间环境状态分析。"""

    return build_latest_environment_analysis(db)


@app.get("/dashboard/latest-motion", response_model=schemas.DashboardLatestResponse)
def dashboard_latest_motion(
    device_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """查询蓝牙采集节点的最新加速度和角速度数据。"""

    target_device = crud.get_device(db, device_id) if device_id else crud.get_first_device_by_type(
        db, constants.DeviceType.BLUETOOTH_NODE
    )
    if not target_device:
        return schemas.DashboardLatestResponse(
            device_id=None,
            device_name=None,
            metrics=[],
        )
    return schemas.DashboardLatestResponse(
        device_id=target_device.id,
        device_name=target_device.name,
        metrics=crud.get_latest_metrics(db, target_device.id, sensor_types=constants.MOTION_SENSOR_TYPES),
    )


@app.get("/sensor-data/history", response_model=schemas.HistoryResponse)
def sensor_history(
    device_id: int | None = Query(default=None),
    hours: int = Query(default=24, ge=1, le=168),
    db: Session = Depends(get_db),
):
    """查询指定设备最近若干小时的历史数据。"""

    target_device = crud.get_device(db, device_id) if device_id else crud.get_first_device_by_type(
        db, constants.DeviceType.SENSOR_NODE
    )
    if not target_device:
        return schemas.HistoryResponse(device_id=None, hours=hours, series=[])
    return schemas.HistoryResponse(
        device_id=target_device.id,
        hours=hours,
        series=crud.get_history(db, target_device.id, hours, sensor_types=constants.ENV_SENSOR_TYPES),
    )


@app.get("/object-index", response_model=schemas.ObjectIndexResponse)
def object_index(
    limit: int = Query(default=100, ge=1, le=500),
):
    """查询运行时对象存储索引。"""

    items = read_object_index(limit=limit)
    return schemas.ObjectIndexResponse(count=len(items), items=items)


@app.get("/knowledge-graph", response_model=schemas.KnowledgeGraphResponse)
def knowledge_graph(
    snapshot: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """查询轻量知识图谱三元组视图。"""

    if snapshot:
        return write_knowledge_graph_snapshot(db)
    return build_knowledge_graph(db)


@app.get("/dataset/query", response_model=schemas.DatasetQueryResponse)
def dataset_query(
    start_time: datetime = Query(...),
    end_time: datetime = Query(...),
    device_type: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """按时间范围和设备类型查询可导出的多模态数据摘要。"""

    if device_type:
        validate_device_type(device_type)
    start = parse_query_time(start_time)
    end = parse_query_time(end_time)
    if end < start:
        raise HTTPException(status_code=400, detail="end_time 必须晚于 start_time")
    return build_dataset_summary(db, start, end, device_type)


@app.get("/dataset/export")
def dataset_export(
    start_time: datetime = Query(...),
    end_time: datetime = Query(...),
    device_type: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """导出指定时间范围内的对齐多模态数据集。"""

    if device_type:
        validate_device_type(device_type)
    start = parse_query_time(start_time)
    end = parse_query_time(end_time)
    if end < start:
        raise HTTPException(status_code=400, detail="end_time 必须晚于 start_time")

    export_path = create_aligned_dataset_zip(db, start, end, device_type)
    return FileResponse(
        path=export_path,
        media_type="application/zip",
        filename=export_path.name,
    )


@app.get("/timeline/state", response_model=schemas.TimelineStateResponse)
def timeline_state(
    timestamp: datetime = Query(...),
    db: Session = Depends(get_db),
):
    """查询指定时刻附近的多模态状态。"""

    target_time = parse_timeline_time(timestamp)
    return build_timeline_state(db, target_time)


@app.get("/timeline/sensors")
def timeline_sensors(
    timestamp: datetime = Query(...),
    db: Session = Depends(get_db),
):
    """查询指定时刻附近的环境和运动传感器状态。"""

    target_time = parse_timeline_time(timestamp)
    return build_timeline_sensor_state(db, target_time)


@app.get("/timeline/frame")
def timeline_frame(
    timestamp: datetime = Query(...),
):
    """返回指定时刻附近的摄像头抓拍帧。"""

    target_time = parse_timeline_time(timestamp)
    frame = find_nearest_camera_frame(target_time)
    if not frame or not frame.get("frame_path"):
        raise HTTPException(status_code=404, detail="该时间附近暂无摄像头抓拍帧")
    return FileResponse(path=frame["frame_path"], media_type="image/jpeg")


@app.get("/timeline/video")
def timeline_video(
    timestamp: datetime = Query(...),
):
    """返回指定时刻附近的摄像头 MP4 回放片段。"""

    target_time = parse_timeline_time(timestamp)
    video_segment = find_nearest_camera_video_segment(target_time)
    if not video_segment or not video_segment.get("video_path"):
        raise HTTPException(status_code=404, detail="该时间附近暂无摄像头视频片段")
    return FileResponse(path=video_segment["video_path"], media_type="video/mp4")


@app.get("/timeline/video_frame")
def timeline_video_frame(
    timestamp: datetime = Query(...),
):
    """返回指定时刻对应的历史视频帧。"""

    target_time = parse_timeline_time(timestamp)
    frame = read_camera_video_frame(target_time)
    if not frame:
        raise HTTPException(status_code=404, detail="该时间附近暂无可读取的历史视频帧")
    return Response(content=frame, media_type="image/jpeg")


@app.get("/timeline/video_feed")
def timeline_video_feed(
    timestamp: datetime = Query(...),
):
    """把历史 MP4 分段转为 MJPEG 流输出。"""

    target_time = parse_timeline_time(timestamp)
    if not find_nearest_camera_video_segment(target_time):
        raise HTTPException(status_code=404, detail="该时间附近暂无摄像头视频片段")
    return StreamingResponse(
        historical_camera_mjpeg_stream(target_time),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/video_feed")
def video_feed():
    """输出摄像头视频流。"""

    return StreamingResponse(
        mjpeg_stream(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )
