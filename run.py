"""本地启动入口。

直接运行该文件即可启动 FastAPI 服务，
适合在 PyCharm 或命令行中快速演示。
"""

import uvicorn

from app.services.video import camera


if __name__ == "__main__":
    # 这里关闭热重载，避免开发模式生成额外进程，
    # 造成摄像头被重复占用或退出后释放不及时。
    try:
        uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
    finally:
        # 兜底释放摄像头录像 writer，保证 Ctrl+C 退出时最后一个不足 1 分钟的片段也能封口。
        camera.release()
