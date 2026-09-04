# -*- coding: utf-8 -*-
"""LTRP 仿真后端:WebSocket 实时推送 + REST 交互"""
import asyncio
import contextlib
import json

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sim.engine import ENGINE, run_forever

app = FastAPI(title="LTRP 熔岩管去中心化路由保持协议仿真")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

CLIENTS: set[WebSocket] = set()
INFLIGHT: dict[WebSocket, asyncio.Task] = {}


async def _send_to(ws: WebSocket, data: str):
    try:
        await asyncio.wait_for(ws.send_text(data), timeout=10)
    except Exception:
        CLIENTS.discard(ws)
    finally:
        INFLIGHT.pop(ws, None)


def broadcast(message: dict):
    """发后即忘:每个客户端一个独立任务,引擎循环绝不 await 任何客户端。
    单个僵死连接(TCP 缓冲满)只会踢掉它自己,不再冻结整个仿真;上一帧还没发完
    的客户端本帧直接跳过(避免并发写同一连接)。"""
    data = json.dumps(message, ensure_ascii=False)
    for ws in list(CLIENTS):
        if ws in INFLIGHT:
            continue
        INFLIGHT[ws] = asyncio.create_task(_send_to(ws, data))


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    CLIENTS.add(ws)
    try:
        await ws.send_text(json.dumps(ENGINE.init_payload(), ensure_ascii=False))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        CLIENTS.discard(ws)


class DisasterBody(BaseModel):
    kind: str


class ObstacleBody(BaseModel):
    idx: int
    x: float
    y: float


class SleepBody(BaseModel):
    on: bool


class MapBody(BaseModel):
    seed: int | None = None


@app.post("/action/disaster")
async def disaster(body: DisasterBody):
    ENGINE.inject_disaster(body.kind)
    return {"ok": True}


@app.post("/action/obstacle")
async def obstacle(body: ObstacleBody):
    ENGINE.move_obstacle(body.idx, body.x, body.y)
    return {"ok": True}


@app.post("/action/sleep")
async def sleep(body: SleepBody):
    ENGINE.set_sleep(body.on)
    return {"ok": True}


@app.post("/action/reset")
async def reset():
    ENGINE.reset()
    return {"ok": True}


@app.post("/action/map")
async def new_map(body: MapBody | None = None):
    ENGINE.new_map(body.seed if body else None)
    return {"ok": True}


@app.on_event("startup")
async def startup():
    ctx = asyncio.create_task(run_forever(broadcast))
    ENGINE._runner = ctx


@app.on_event("shutdown")
async def shutdown():
    task = getattr(ENGINE, "_runner", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)
