"""摄像头视频流服务。

本模块负责两件事：
- 后端启动后持续读取本地 USB 摄像头
- 持续保存 MP4 录像，并定时把画面抓拍保存到本地目录，同时向前端输出 MJPEG 视频流

这样即使前端暂时没有打开视频页面，平台也会保留运行时采集到的
视频模态数据，满足本地存储与来源留痕要求。
"""

from collections.abc import Generator
from datetime import datetime, timedelta, timezone
import threading
import time
from typing import Optional

import cv2
import numpy as np

from app.storage import (
    CAMERA_SAVE_INTERVAL_SECONDS,
    CAMERA_VIDEO_FPS,
    CAMERA_VIDEO_SEGMENT_SECONDS,
    build_camera_video_path,
    record_camera_video_session,
    save_camera_frame,
)


class VideoCamera:
    """摄像头封装类。"""

    def __init__(self, camera_index: int = 0):
        # 这里只保存摄像头编号，不在导入模块时立即打开摄像头。
        # 这样只有真正启动 FastAPI 服务时，才会去占用摄像头资源。
        self.camera_index = camera_index
        self.capture: Optional[cv2.VideoCapture] = None
        self.latest_frame = None
        self.last_saved_at: datetime | None = None
        self.video_writer: Optional[cv2.VideoWriter] = None
        self.video_path: str | None = None
        self.video_started_at: datetime | None = None
        self._frame_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._reader_thread: threading.Thread | None = None

    def open(self) -> None:
        """打开摄像头。

        如果摄像头已经打开，则不重复初始化。
        成功打开后会启动后台读取线程，持续抓取最新画面并按间隔保存。
        """

        if self.capture is not None and self.capture.isOpened():
            return
        self.capture = cv2.VideoCapture(self.camera_index)
        self._stop_event.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def get_frame(self) -> bytes:
        """读取单帧图像并编码为 JPEG 字节流。"""

        # 正常情况下，摄像头会在 FastAPI 启动时打开。
        # 如果当前尚未打开，则返回占位画面，避免页面崩溃。
        if self.capture is None or not self.capture.isOpened():
            return self._build_placeholder_frame("Camera not started")

        with self._frame_lock:
            frame = None if self.latest_frame is None else self.latest_frame.copy()

        if frame is None:
            # 后台线程可能还在等待首帧，这里先返回占位图避免页面报错。
            return self._build_placeholder_frame("Waiting for frame")

        ret, jpeg = cv2.imencode(".jpg", frame)
        if not ret:
            return b""
        return jpeg.tobytes()

    def _reader_loop(self) -> None:
        """后台持续读取摄像头画面并做本地抓拍。"""

        while not self._stop_event.is_set():
            if self.capture is None or not self.capture.isOpened():
                time.sleep(0.2)
                continue

            ok, frame = self.capture.read()
            if not ok or frame is None:
                time.sleep(0.2)
                continue

            with self._frame_lock:
                self.latest_frame = frame.copy()

            self._write_video_frame_if_needed(frame)
            self._save_frame_if_needed(frame)

    def _write_video_frame_if_needed(self, frame) -> None:
        """持续把当前帧写入 MP4 文件，并按固定时长分段。"""

        now = datetime.now(timezone.utc)
        if (
            self.video_writer is not None
            and self.video_started_at is not None
            and now - self.video_started_at >= timedelta(seconds=CAMERA_VIDEO_SEGMENT_SECONDS)
        ):
            self._close_current_video_segment()

        if self.video_writer is None:
            height, width = frame.shape[:2]
            video_path = build_camera_video_path(self.camera_index)
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(video_path), fourcc, CAMERA_VIDEO_FPS, (width, height))
            if not writer.isOpened():
                return

            self.video_writer = writer
            self.video_path = str(video_path)
            self.video_started_at = now
            record_camera_video_session(
                camera_index=self.camera_index,
                file_path=self.video_path,
                fps=CAMERA_VIDEO_FPS,
                frame_width=width,
                frame_height=height,
            )

        self.video_writer.write(frame)

    def _close_current_video_segment(self) -> None:
        """关闭当前 MP4 分段，确保文件被正常封口。"""

        if self.video_writer is not None:
            self.video_writer.release()
        self.video_writer = None
        self.video_path = None
        self.video_started_at = None

    def _save_frame_if_needed(self, frame) -> None:
        """按固定时间间隔保存摄像头抓拍帧。

        为了保持项目轻量，这里不做整段视频录像，而是定时保存图片帧。
        """

        now = datetime.now(timezone.utc)
        if self.last_saved_at is None or now - self.last_saved_at >= timedelta(seconds=CAMERA_SAVE_INTERVAL_SECONDS):
            save_camera_frame(frame, self.camera_index)
            self.last_saved_at = now

    def _build_placeholder_frame(self, text: str) -> bytes:
        """构造占位图，用于摄像头未启动或不可用的情况。"""

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(
            frame,
            text,
            (110, 240),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (255, 255, 255),
            2,
        )
        ret, jpeg = cv2.imencode(".jpg", frame)
        if not ret:
            return b""
        return jpeg.tobytes()

    def release(self) -> None:
        """释放摄像头资源。"""

        self._stop_event.set()
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2)

        if self.capture is not None and self.capture.isOpened():
            self.capture.release()
        self._close_current_video_segment()
        self.capture = None
        with self._frame_lock:
            self.latest_frame = None
        self.last_saved_at = None
        self._reader_thread = None


# 这里只创建摄像头管理对象，不立即打开硬件设备。
camera = VideoCamera()


def mjpeg_stream() -> Generator[bytes, None, None]:
    """持续生成 MJPEG 数据块，供 StreamingResponse 输出。"""

    while True:
        frame = camera.get_frame()
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )
