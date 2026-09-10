# -*- coding: utf-8 -*-
"""引擎职责模块:月球车摆渡(存储-携带-转发, FerryMixin)。

职责范围:月球车无路由时从积压/失联邻居收取数据束(容量感知),回到骨干覆盖区后
把束转交给有路由的邻居,沿其路由多跳回传 —— 即 DTN 的"存储-携带-转发"。
依赖:读 Engine 的 nodes/adj/t/ferry_marks;写各 Node 的 bundles 与月球车标志。
Calls: _ferry_pickup/_ferry_dropoff, emit。
"""
from ..nodes import Node, INF
from .constants import *
from .engine_imports import zh


class FerryMixin:
    """摆渡 mixin,由 Engine 继承,self 即引擎实例。

    Globals Used: FERRY_CAP, FERRY_GIVE, zh。
    Invocation: Engine.step() 每 tick 调用 _ferry()。
    """

    def _ferry(self):
        """月球车摆渡:无路由时接收积压/失联邻居的束,有路由时转给骨干邻居快速回传。"""
        for i, n in self.nodes.items():
            if n.role != "rover" or not n.alive:
                continue
            r = n.routing.get("BASE-00")
            has_route = r and r["cost"] < INF
            if not has_route:
                n._ferry_log = False
                self._ferry_pickup(i, n)
            else:
                self._ferry_dropoff(i, n)

    def _ferry_pickup(self, i: str, n: Node):
        """无路由的月球车:从积压(>2)或无路由邻居收取束(容量感知,不超载)。

        Globals Used: FERRY_CAP, zh。Args: i=月球车 id; n=月球车节点。Returns: None。
        """
        for j in self.adj[i]:
            nj = self.nodes[j]
            if nj.role == "rover":
                continue
            room = FERRY_CAP - len(n.bundles)
            if room <= 0:
                break
            rj = nj.routing.get("BASE-00")
            no_route = not (rj and rj["cost"] < INF)
            if len(nj.bundles) > 2 or no_route:
                take = nj.bundles[:room]
                nj.bundles = nj.bundles[room:]
                if take:
                    n.bundles += take
                    key = (i, j) if i < j else (j, i)
                    self.ferry_marks[key] = self.t
                    if self.t - self.ferry_log_t.get((i, j), -99) > 5:
                        self.ferry_log_t[(i, j)] = self.t
                        self.emit("ferry", "good",
                                  f"{zh(i)}按巡逻接触计划与失联节点 {zh(j)} 接触,携带 {len(take)} 束", True)

    def _ferry_dropoff(self, i: str, n: Node):
        """回到骨干覆盖区的月球车:束转给有骨干路由的邻居,沿其路由多跳回传。

        Globals Used: FERRY_GIVE, zh。Args: i=月球车 id; n=月球车节点。Returns: None。
        """
        if n.bundles and not getattr(n, "_ferry_log", False):
            n._ferry_log = True
            self.emit("ferry", "good",
                      f"{zh(i)}回到骨干覆盖区,{len(n.bundles)} 束开始多跳回传", True)
        if not n.bundles:
            return
        for j in self.adj[i]:
            nj = self.nodes[j]
            if nj.role == "rover" or not nj.alive or nj.sleeping:
                continue
            rj = nj.routing.get("BASE-00")
            if rj and rj["cost"] < INF:
                give = n.bundles[:FERRY_GIVE]
                n.bundles = n.bundles[FERRY_GIVE:]
                for pkt in give:
                    pkt.wait_since = None
                    pkt.prev = i
                    pkt.visited.add(i)
                    nj.packets.append(pkt)
                key = (i, j) if i < j else (j, i)
                self.ferry_marks[key] = self.t
                break
