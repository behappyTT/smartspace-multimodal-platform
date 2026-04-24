"""本地启动入口。

直接运行该文件即可启动 FastAPI 服务，
适合在 PyCharm 或命令行中快速演示。
"""

import uvicorn


if __name__ == "__main__":
    # 这里关闭热重载，避免开发模式生成额外进程，
    # 造成摄像头被重复占用或退出后释放不及时。
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
