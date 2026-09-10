# -*- coding: utf-8 -*-
"""LTRP 仿真后端:Presentation/API 层(Rule 2.1)。

仅负责协议转换(WS/REST)、参数格式校验与路由分发,不包含业务逻辑;
业务全部委托给 sim.engine(仿真实体层)。所有请求体用 pydantic 强类型校验,
所有出站消息统一经 sim.contracts.dto() 从强类型 DTO 序列化为 JSON。
"""
import asyncio                        # 异步任务:每客户端独立广播任务
import contextlib                     # 优雅关闭:屏蔽 asyncio.CancelledError
import json                           # 消息 JSON 序列化

import uvicorn                        # ASGI 服务器启动
from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # REST+WS 框架
from fastapi.middleware.cors import CORSMiddleware           # 跨域(前端 :5174)
from pydantic import BaseModel        # 请求体强类型校验

from sim.contracts import dto         # DTO → JSON 可序列化 dict(唯一序列化出口)
from sim.engine import ENGINE, run_forever   # 仿真引擎单例与主循环

CLIENTS: set[WebSocket] = set()               # 当前已连接的 WS 客户端
INFLIGHT: dict[WebSocket, asyncio.Task] = {}  # 每客户端在途发送任务(背压控制)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期:启动时拉起仿真主循环,关闭时取消并等待其退出。

    Args: app=FastAPI 实例(未使用,仅为签名约定)。Yields: None。
    """
    runner = asyncio.create_task(run_forever(broadcast))
    ENGINE._runner = runner
    try:
        yield
    finally:
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner


app = FastAPI(title="LTRP 熔岩管去中心化路由保持协议仿真", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


async def _send_to(ws: WebSocket, data: str):
    """向单个客户端发送一帧(带超时);失败即剔除该连接。Args: ws=连接; data=JSON 文本。"""
    try:
        await asyncio.wait_for(ws.send_text(data), timeout=10)
    except Exception:
        CLIENTS.discard(ws)
    finally:
        INFLIGHT.pop(ws, None)


def broadcast(message):
    """发后即忘:每个客户端一个独立任务,引擎循环绝不 await 任何客户端。
    单个僵死连接(TCP 缓冲满)只会踢掉它自己,不再冻结整个仿真;上一帧还没发完
    的客户端本帧直接跳过(避免并发写同一连接)。

    Args: message=任意强类型 DTO(Snapshot/InitPayload)。
    Returns: None。
    """
    data = json.dumps(dto(message), ensure_ascii=False)
    for ws in list(CLIENTS):
        if ws in INFLIGHT:
            continue
        INFLIGHT[ws] = asyncio.create_task(_send_to(ws, data))


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    """WS 端点:建连即推送 init 首帧,此后只接收(引擎侧定时广播快照)。"""
    await ws.accept()
    CLIENTS.add(ws)
    try:
        await ws.send_text(json.dumps(dto(ENGINE.init_payload()), ensure_ascii=False))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        CLIENTS.discard(ws)


class DisasterBody(BaseModel):
    """灾难注入请求体。kind ∈ {collapse, heat, critical}。"""
    kind: str


class ObstacleBody(BaseModel):
    """巨石拖放请求体:idx=巨石序号, x/y=新位置。"""
    idx: int
    x: float
    y: float


class SleepBody(BaseModel):
    """休眠调度开关请求体。"""
    on: bool


class MapBody(BaseModel):
    """随机地图请求体:seed 为空则随机取。"""
    seed: int | None = None


class ParamBody(BaseModel):
    """上帝模式:带 node 字段 → 节点物理参数覆写;不带 → 全局协议参数调整。"""
    node: str | None = None
    params: dict


class TimeScaleBody(BaseModel):
    """时间缩放请求体。scale=倍率。"""
    scale: float


@app.post("/action/disaster")
async def disaster(body: DisasterBody):
    """注入灾难(collapse/heat/critical)。Returns: {"ok": True}。"""
    ENGINE.inject_disaster(body.kind)
    return {"ok": True}


@app.post("/action/param")
async def param(body: ParamBody):
    """参数覆写(节点级/全局)。Returns: 覆写结果(含校验错误信息)。"""
    # 滑块拖动可达 60+ msg/s: 只写参数,不触发即时重算/广播;
    # 引擎 0.3s 周期自然生效,参数最迟下一拍起作用
    if body.node:
        return ENGINE.apply_override(body.node, body.params)
    return ENGINE.set_global_param(body.params)


@app.post("/action/obstacle")
async def obstacle(body: ObstacleBody):
    """拖动巨石并触发全网视距重算。Returns: {"ok": True}。"""
    ENGINE.move_obstacle(body.idx, body.x, body.y)
    return {"ok": True}


@app.post("/action/sleep")
async def sleep(body: SleepBody):
    """开关轮值休眠调度。Returns: {"ok": True}。"""
    ENGINE.set_sleep(body.on)
    return {"ok": True}


@app.post("/action/reset")
async def reset():
    """重置仿真(保留当前地图 seed)。Returns: {"ok": True}。"""
    ENGINE.reset()
    return {"ok": True}


@app.post("/action/map")
async def new_map(body: MapBody | None = None):
    """生成一张新的随机几何地图。Returns: {"ok": True}。"""
    ENGINE.new_map(body.seed if body else None)
    return {"ok": True}


@app.post("/action/time_scale")
async def time_scale(body: TimeScaleBody):
    """设置时间缩放倍率。Returns: {"time_scale": 实际生效值}。"""
    return ENGINE.set_time_scale(body.scale)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)
