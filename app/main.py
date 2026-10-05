"""FastAPI 入口：静态页面 + 推演接口。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .simulator import SimulationError, simulate

app = FastAPI(title="雨水调蓄池推演", version="1.0.0")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/simulate")
def run_simulation(payload: dict):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="请求体必须是 JSON 对象")
    try:
        return simulate(payload)
    except SimulationError as exc:
        # 输入非法 / 推演失败：422；接口无状态，不保留任何既有结果。
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
