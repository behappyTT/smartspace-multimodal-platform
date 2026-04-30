"""摄像头视频流服务。

本模块负责两件事：
- 后端启动后持续读取本地 USB 摄像头
- 持续保存 MP4 录像，并定时把画面抓拍保存到本地目录，同时向前端输出 MJPEG 视频流

这样即使前端暂时没有打开视频页面，平台也会保留运行时采集到的
视频模态数据，满足本地存储与来源留痕要求。
"""

import atexit
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from shutil import copy2
import threading
import time
from typing import Optional

import cv2
import numpy as np

from app.storage import (
    CAMERA_SAVE_INTERVAL_SECONDS,
    CAMERA_VIDEO_DIR,
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
        self.video_recording_path: str | None = None
        self.video_path: str | None = None
        self.video_started_at: datetime | None = None
        self.video_ended_at: datetime | None = None
        self.video_frame_width: int | None = None
        self.video_frame_height: int | None = None
        self.video_is_partial = False
        self._frame_lock = threading.Lock()
        self._video_lock = threading.RLock()
        self._release_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._recorder_thread: threading.Thread | None = None

    def open(self) -> None:
        """打开摄像头。

        如果摄像头已经打开，则不重复初始化。
        成功打开后会启动后台读取线程，持续抓取最新画面并按间隔保存。
        """

        if self.capture is not None and self.capture.isOpened():
            return
        self._recover_orphan_recordings()
        self.capture = cv2.VideoCapture(self.camera_index)
        self._stop_event.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._recorder_thread = threading.Thread(target=self._recorder_loop, daemon=True)
        self._reader_thread.start()
        self._recorder_thread.start()

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

    def _recorder_loop(self) -> None:
        """后台按固定节奏保存录像和抓拍，避免阻塞实时画面读取。"""

        frame_interval = 1 / max(CAMERA_VIDEO_FPS, 1)
        next_record_at = time.monotonic()
        while not self._stop_event.is_set():
            now_monotonic = time.monotonic()
            if now_monotonic < next_record_at:
                time.sleep(min(next_record_at - now_monotonic, 0.02))
                continue

            try:
                with self._frame_lock:
                    frame = None if self.latest_frame is None else self.latest_frame.copy()

                if frame is not None:
                    with self._video_lock:
                        self._write_video_frame_if_needed(frame)
                    self._save_frame_if_needed(frame)
            except Exception as exc:
                # 录像索引或磁盘写入异常不能拖死实时摄像头画面。
                print(f"Camera recorder error: {exc}")

            next_record_at += frame_interval
            if time.monotonic() - next_record_at > frame_interval:
                next_record_at = time.monotonic() + frame_interval

    def _current_minute_window(self, now: datetime) -> tuple[datetime, datetime]:
        """返回当前帧所属的整分钟录像窗口。"""

        start_at = now.replace(second=0, microsecond=0)
        end_at = start_at + timedelta(seconds=CAMERA_VIDEO_SEGMENT_SECONDS)
        return start_at, end_at

    def _write_video_frame_if_needed(self, frame) -> None:
        """持续把当前帧写入 MP4 文件，并按固定时长分段。"""

        now = datetime.now(timezone.utc)
        segment_started_at, segment_ended_at = self._current_minute_window(now)
        if self.video_writer is not None and self.video_ended_at is not None and now >= self.video_ended_at:
            self._close_current_video_segment(discard=False)

        if self.video_writer is None:
            height, width = frame.shape[:2]
            is_partial_segment = now - segment_started_at > timedelta(seconds=2)
            first_frame_at = now if is_partial_segment else segment_started_at
            video_path = build_camera_video_path(self.camera_index, started_at=first_frame_at)
            recording_path = video_path.with_name(f"{video_path.stem}.recording.mp4")
            writer = cv2.VideoWriter(
                str(recording_path),
                cv2.VideoWriter_fourcc(*"mp4v"),
                CAMERA_VIDEO_FPS,
                (width, height),
            )
            if not writer.isOpened():
                return

            self.video_writer = writer
            self.video_recording_path = str(recording_path)
            self.video_path = str(video_path)
            self.video_started_at = first_frame_at
            self.video_ended_at = segment_ended_at
            self.video_frame_width = width
            self.video_frame_height = height
            self.video_is_partial = is_partial_segment

        if self.video_writer is not None:
            self.video_writer.write(frame)

    def _close_current_video_segment(self, *, discard: bool = False, ended_at: datetime | None = None) -> None:
        """关闭当前 MP4 分段。

        正常跨过分钟边界时，release 当前 MP4 并登记起止时间；
        如果程序在分钟中途启动或退出，也会把已有残段正常封口成 MP4。
        """

        with self._video_lock:
            self._close_current_video_segment_locked(discard=discard, ended_at=ended_at)

    def _close_current_video_segment_locked(
        self,
        *,
        discard: bool = False,
        ended_at: datetime | None = None,
    ) -> None:
        """在持有视频锁时关闭当前 MP4 分段。"""

        ended_at = ended_at or datetime.now(timezone.utc)
        recorded_end_at = ended_at
        if self.video_ended_at is not None and ended_at >= self.video_ended_at:
            recorded_end_at = self.video_ended_at
        is_partial_segment = (
            self.video_is_partial
            or (
                self.video_ended_at is not None
                and recorded_end_at < self.video_ended_at - timedelta(seconds=1)
            )
        )
        is_complete_segment = (
            self.video_path is not None
            and self.video_started_at is not None
            and self.video_frame_width is not None
            and self.video_frame_height is not None
            and not discard
        )
        if self.video_writer is not None:
            self.video_writer.release()
        self.video_writer = None

        if is_complete_segment:
            final_video_path = Path(self.video_path)
            if self.video_recording_path and self.video_path:
                self._finalize_recording_file(Path(self.video_recording_path), final_video_path)
            if not final_video_path.exists():
                is_complete_segment = False

        if is_complete_segment:
            record_camera_video_session(
                camera_index=self.camera_index,
                file_path=self.video_path,
                fps=CAMERA_VIDEO_FPS,
                frame_width=self.video_frame_width,
                frame_height=self.video_frame_height,
                started_at=self.video_started_at,
                ended_at=recorded_end_at,
                partial=is_partial_segment,
            )

        self.video_recording_path = None
        self.video_path = None
        self.video_started_at = None
        self.video_ended_at = None
        self.video_frame_width = None
        self.video_frame_height = None
        self.video_is_partial = False

    def _finalize_recording_file(self, recording_path: Path, final_video_path: Path) -> None:
        """把临时 recording 文件转成最终 MP4。

        Windows 上 OpenCV release 后文件句柄偶尔会延迟释放，所以这里用短重试，
        避免最后一段因为刚 release 就 rename 而遗留不可用临时文件。
        """

        if not recording_path.exists():
            return

        final_video_path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(10):
            try:
                recording_path.replace(final_video_path)
                return
            except OSError:
                time.sleep(0.05)

        # 如果 rename 仍失败，退化为复制最终文件；随后继续尝试删除临时文件。
        try:
            copy2(recording_path, final_video_path)
        except OSError:
            return

        for _ in range(10):
            try:
                recording_path.unlink(missing_ok=True)
                return
            except OSError:
                time.sleep(0.05)

    def _recover_orphan_recordings(self) -> None:
        """启动时恢复上次遗留的已封口 recording 文件。

        如果进程退出时已 release 但 rename 失败，`.recording.mp4` 其实是可读的；
        这里把它补成正式 MP4 并写入索引。若文件本身缺少 moov atom，则说明上次
        是强制终止，MP4 没有完成封口，无法可靠恢复，只能保留在目录中供人工排查。
        """

        for recording_path in CAMERA_VIDEO_DIR.glob("*/*.recording.mp4"):
            final_video_path = Path(str(recording_path).replace(".recording.mp4", ".mp4"))
            if final_video_path.exists():
                try:
                    recording_path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue

            capture = cv2.VideoCapture(str(recording_path))
            try:
                if not capture.isOpened() or int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0) <= 0:
                    continue
                frame_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                fps = float(capture.get(cv2.CAP_PROP_FPS) or CAMERA_VIDEO_FPS)
            finally:
                capture.release()

            self._finalize_recording_file(recording_path, final_video_path)
            if not final_video_path.exists():
                continue

            started_at = self._parse_video_start_time(final_video_path)
            if started_at is None:
                continue
            camera_index = self._parse_video_camera_index(final_video_path)
            modified_at = datetime.fromtimestamp(final_video_path.stat().st_mtime, timezone.utc)
            nominal_end_at = started_at + timedelta(seconds=CAMERA_VIDEO_SEGMENT_SECONDS)
            ended_at = min(modified_at, nominal_end_at) if modified_at > started_at else nominal_end_at
            record_camera_video_session(
                camera_index=camera_index if camera_index is not None else self.camera_index,
                file_path=str(final_video_path),
                fps=fps,
                frame_width=frame_width,
                frame_height=frame_height,
                started_at=started_at,
                ended_at=ended_at,
                partial=(ended_at - started_at).total_seconds() < CAMERA_VIDEO_SEGMENT_SECONDS - 1,
            )

    def _parse_video_start_time(self, video_path: Path) -> datetime | None:
        """从视频文件名解析 UTC 起始时间。"""

        try:
            date_text = video_path.parent.name
            time_text = video_path.name.split("_", 1)[0]
            return datetime.strptime(f"{date_text}{time_text}", "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except (IndexError, ValueError):
            return None

    def _parse_video_camera_index(self, video_path: Path) -> int | None:
        """从视频文件名解析摄像头编号。"""

        parts = video_path.stem.split("_")
        try:
            camera_label_index = parts.index("camera")
            return int(parts[camera_label_index + 1])
        except (ValueError, IndexError):
            return None

    def _save_frame_if_needed(self, frame) -> None:
        """按固定时间间隔保存摄像头抓拍帧。

        摄像头视频由 MP4 分段负责，这里只做每 5 秒左右的图片抓拍。
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

        with self._release_lock:
            self._stop_event.set()
            if self._reader_thread is not None and self._reader_thread.is_alive():
                self._reader_thread.join(timeout=2)
            if self._recorder_thread is not None and self._recorder_thread.is_alive():
                self._recorder_thread.join(timeout=2)

            with self._video_lock:
                self._close_current_video_segment_locked(discard=False)

            if self.capture is not None and self.capture.isOpened():
                self.capture.release()
            self.capture = None
            with self._frame_lock:
                self.latest_frame = None
            self.last_saved_at = None
            self._reader_thread = None
            self._recorder_thread = None


# 这里只创建摄像头管理对象，不立即打开硬件设备。
camera = VideoCamera()
atexit.register(camera.release)


def mjpeg_stream() -> Generator[bytes, None, None]:
    """持续生成 MJPEG 数据块，供 StreamingResponse 输出。"""

    while True:
        frame = camera.get_frame()
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )
        time.sleep(1 / 20)
