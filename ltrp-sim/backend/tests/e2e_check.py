# -*- coding: utf-8 -*-
"""端到端集成检查:真实 uvicorn 进程 + WS 首帧/快照 + REST 动作往返。

用法:
  python main.py 起后端后   -> python tests/e2e_check.py          (直连 :5000)
  npm run dev 起前端后      -> python tests/e2e_check.py 5174     (经 Vite 代理同源验证)
断言: init 帧结构、至少 3 帧快照、DTO 键集合、休眠开关关闭不再 500、灾害注入生效。
"""
import asyncio          # 异步 WS 客户端
import json             # 解析 WS 帧
import sys              # 退出码 / 端口参数
import urllib.request   # REST 调用(标准库,避免额外依赖)

import websockets       # WS 客户端

PORT = sys.argv[1] if len(sys.argv) > 1 else "5000"
BASE = f"http://127.0.0.1:{PORT}"
WS = f"ws://127.0.0.1:{PORT}/ws"


def post(path: str, body: dict) -> dict:
    """POST 一个 JSON 动作并返回响应体。Args: path/body。Returns: 响应 dict。"""
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


async def main() -> int:
    """连接 WS,收 1 帧 init + 3 帧快照,期间做 REST 动作往返。Returns: 退出码。"""
    async with websockets.connect(WS, open_timeout=5) as ws:
        init = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert init["cmd"] == "init", f"首帧不是 init: {init.get('cmd')}"
        assert init["world"]["W"] > 0 and init["snapshot"]["nodes"], "init 载荷为空"
        print(f"init OK: W={init['world']['W']} H={init['world']['H']} "
              f"nodes={len(init['snapshot']['nodes'])}")
        # 修复验证:休眠开关"关闭"必须返回 200(原实现在此抛 ModuleNotFoundError → 500)
        assert post("/action/sleep", {"on": True}).get("ok"), "开启休眠失败"
        assert post("/action/sleep", {"on": False}).get("ok"), "关闭休眠失败(回归!)"
        assert post("/action/time_scale", {"scale": 200}).get("time_scale") == 200.0
        assert post("/action/disaster", {"kind": "heat"}).get("ok"), "热浪注入失败"
        assert not post("/action/param", {"params": {"gamma": 999}}).get("ok"), "越界参数未拒绝"
        snaps = []
        for _ in range(3):
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if m.get("cmd") == "snap":
                snaps.append(m)
        assert len(snaps) == 3, f"只收到 {len(snaps)} 帧快照"
        s = snaps[-1]
        assert s["stats"]["heat"] is True, "热浪状态未反映到统计"
        assert abs(s["stats"]["time_scale"] - 200.0) < 1e-6, "时间缩放未生效"
        assert set(s["nodes"][0]) == {
            "alive", "border", "bundles", "charge_ma", "cost", "crit", "domain",
            "energy", "id", "laser_in", "laser_out", "moving", "ms", "nbrs", "nh",
            "pending", "phys", "pkts", "pv_w", "role", "seu", "sleeping", "soc",
            "sos", "state", "stock", "temp", "x", "y"}, "节点键集合漂移"
        print(f"snapshots OK: t={s['t']} alive={s['stats']['alive']} "
              f"coverage={s['stats']['coverage']} heat={s['stats']['heat']} "
              f"ts={s['stats']['time_scale']}")
    print("E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
