from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from calculus_agent.api import router
from calculus_agent.config import get_settings
from calculus_agent.db import create_schema
from calculus_agent.workbench.app import app as workbench_app


settings = get_settings()
create_schema(settings.database_url)
app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)

# 静态文件：上传的 OCR 原图
uploads_dir = Path("uploads")
uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

# 题目校验工作台（PDF OCR + Markdown编辑 + 实时预览 + 差异对比）
# 工作台现在直接使用主库（SQLAlchemy），不再使用独立 SQLite。
app.mount("/workbench", workbench_app)
