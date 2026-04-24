"""数据库连接与会话管理。

本项目使用 SQLite 作为单机演示数据库，优点是：
- 配置简单
- 不依赖额外服务
- 便于毕设演示和本地运行

同时支持通过环境变量切换数据库 URL，便于：
- 在特殊环境下改用其他 SQLite 路径
- 在自动化验证时使用内存数据库，避免文件锁影响
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from app.storage import DB_PATH, ensure_runtime_directories


# 启动时先确保运行时数据目录存在。
ensure_runtime_directories()

# 优先使用显式配置的数据库 URL；未配置时默认落到 runtime_data/db。
DATABASE_URL = os.getenv("SMARTSPACE_DATABASE_URL", f"sqlite:///{DB_PATH.as_posix()}")

# 创建数据库引擎。
# check_same_thread=False 是 SQLite + FastAPI 常见配置，
# 允许不同请求线程共享同一个数据库文件连接能力。
engine_kwargs = {
    "connect_args": {"check_same_thread": False},
}

# 对于内存 SQLite，需要把连接固定在同一个进程内共享。
if DATABASE_URL in {"sqlite://", "sqlite:///:memory:"}:
    engine_kwargs["poolclass"] = StaticPool

engine = create_engine(DATABASE_URL, **engine_kwargs)
# SessionLocal 用于给每个请求创建独立数据库会话。
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
# Base 是所有 ORM 模型的基类。
Base = declarative_base()


def get_db():
    """为 FastAPI 依赖注入提供数据库会话。

    每次请求会获得一个新的 Session，请求结束后自动关闭。
    """

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
