# -*- coding: utf-8 -*-
"""引擎职责模块:节点定位与移动约束(SelfHealMotionMixin).

职责范围:节点自愈所需的几何落点求解 / 管道内移动 / 防聚 / 连通分量与最近节点查询。
依赖:读 Engine 的 nodes/order/world/adj;写各 Node 的 x/y(runtime 移动)。
Calls: world.yc / world.r_at / world.W / _tube_pt / _clamp_node。
"""
import math

from ..nodes import Node, RANGE, INF
from .constants import *


class SelfHealMotionMixin:
    """节点定位移动 mixin,由 Engine 继承,self 即引擎实例。

    Globals Used: NODE_MOVE_SPEED, NODE_STOP, RANGE, INF。
    Invocation: 由 SelfHealLogicMixin 的 _step_movement 调用。
    """


    def _clamp_node(self, n: Node, r: float = 14.0):
        """移动后的物理约束:夹回管内、推出巨石"""
        w = self.world
        yc = w.yc(n.x)
        half = max(8.0, w.r_at(n.x) - r)
        for _ in range(2):
            n.y = min(max(n.y, yc - half), yc + half)
            for b in w.boulders:
                dx, dy = n.x - b["x"], n.y - b["y"]
                d = math.hypot(dx, dy)
                md = b["r"] + r
                if d < md and d > 1e-6:
                    n.x = b["x"] + dx / d * md
                    n.y = b["y"] + dy / d * md

    def _tube_pt(self, tgt):
        """把目标点夹进管道内(避免移动到岩壁里)"""
        w = self.world
        X = min(max(tgt[0], w.W * 0.02), w.W * 0.98)
        yc = w.yc(X)
        half = max(8.0, w.r_at(X) - 16)
        return (X, min(max(tgt[1], yc - half), yc + half))

    def _bridge_point(self, a: Node, b: Node):
        """从 a 朝 b 移动到'与 b 距离 ≈ RANGE*LINK_SAFE'的桥接落点"""
        dx, dy = b.x - a.x, b.y - a.y
        d = math.hypot(dx, dy)
        if d <= RANGE * LINK_SAFE:
            return None
        tt = (d - RANGE * LINK_SAFE) / d
        return (a.x + dx * tt, a.y + dy * tt)

    def _move_node(self, n: Node, tgt, dt: float):
        """沿管道行进:x 朝目标 x 推进、y 贴合中心线并夹在管内(避免穿墙);移动耗电。
        若前方被巨石挡住, 由 _clamp_node/_front_target 绕行扫描处理, 此处不重复采样绕障
        (避免重采样 LOS、且让每个节点各自乱绕导致的震荡)。"""
        w = self.world
        dx = tgt[0] - n.x
        n.x += max(-NODE_MOVE_SPEED * dt, min(NODE_MOVE_SPEED * dt, dx))
        n.x = min(max(n.x, w.W * 0.02), w.W * 0.98)
        yc = w.yc(n.x)
        half = max(8.0, w.r_at(n.x) - 16)
        ty = min(max(tgt[1], yc - half), yc + half)
        n.y += (ty - n.y) * min(1.0, 4.0 * dt)
        n.y = min(max(n.y, yc - half), yc + half)
        n.spend(0.004)
        self._clamp_node(n)

    def _separate(self, n: Node, sep: float = 60.0):
        """节点最小间距:移动节点永远不会贴到任何其它节点上(防聚成一点)。做两遍收敛。"""
        for _ in range(2):
            for i in self.order:
                m = self.nodes[i]
                if m.id == n.id or not m.alive or m.role in ("rover", "base"):
                    continue
                dx, dy = n.x - m.x, n.y - m.y
                d = math.hypot(dx, dy)
                if 0 < d < sep:
                    push = sep - d
                    n.x += dx / d * push
                    n.y += dy / d * push

    def _components(self) -> dict:
        """连通分量(排除月球车,因摆渡只是临时接触);返回 id->分量号"""
        seen = {}
        cid = 0
        for i in self.order:
            n = self.nodes[i]
            if not n.alive or n.role == "rover":
                continue
            if i in seen:
                continue
            seen[i] = cid
            st = [i]
            while st:
                u = st.pop()
                for v in self.adj[u]:
                    if self.nodes[v].alive and self.nodes[v].role != "rover" \
                            and v not in seen:
                        seen[v] = cid
                        st.append(v)
            cid += 1
        return seen

    def _nearest_base(self, n: Node, comps: dict, base_comp: int):
        """离 n 最近的 base 分量节点(重连目标/桥接参照)"""
        best, bd = None, 1e9
        for i in self.order:
            m = self.nodes[i]
            if not m.alive or m.role == "rover" or comps.get(i) != base_comp:
                continue
            d = math.hypot(m.x - n.x, m.y - n.y)
            if d < bd:
                bd, best = d, m
        return best

    def _arm_move(self, n: Node, target_node: Node):
        """给节点 n 设桥接落点(与 target_node 保持 RANGE*LINK_SAFE),标记为重连前锋"""
        tgt = self._bridge_point(n, target_node)
        if tgt:
            n.move_target = self._tube_pt(tgt)
            n.sos = True

    def _nearest_alive(self, n: Node):
        """离 n 最近的存活非 rover 节点(孤立重连的目标)"""
        best, bd = None, 1e9
        for i in self.order:
            m = self.nodes[i]
            if not m.alive or m.role == "rover" or m.id == n.id or m.sleeping:
                continue
            d = math.hypot(m.x - n.x, m.y - n.y)
            if d < bd:
                bd, best = d, m
        return best

    def _nearest_base_other(self, n: Node, comps: dict, base_comp: int, excl: str):
        """离 n 最近的其它 base 分量节点(双端桥接的安全锚),排除 excl"""
        best, bd = None, 1e9
        for i in self.order:
            m = self.nodes[i]
            if not m.alive or m.role == "rover" or m.id in (n.id, excl) \
                    or comps.get(i) != base_comp:
                continue
            d = math.hypot(m.x - n.x, m.y - n.y)
            if d < bd:
                bd, best = d, m
        return best

    def _has_nbr(self, n: Node) -> bool:
        return any(self.nodes[j].alive and self.nodes[j].role != "rover"
                   for j in n.neighbors)

    def _seek_target(self, n: Node):
        """本地自愈目标:优先追回'最近消失的邻居'(它在连接路径上),
        否则朝最近的存活非 rover 节点移动(孤立/探测器接入)。"""
        g = getattr(n, "_last_gone", None)
        if g and g in self.nodes:
            m = self.nodes[g]
            return self._tube_pt((m.x, m.y))
        t = self._nearest_alive(n)
        return self._tube_pt((t.x, t.y)) if t else None

    def _counterpart(self, n: Node, comps: dict) -> Node | None:
        """返回与 n 处于不同连通分量的最近存活节点(断裂对端)。
        rover 把它同步给 n,让两端朝彼此移动接合(弯曲喉道也能靠拢)。"""
        c = comps.get(n.id)
        if c is None:
            return None
        best, bd = None, 1e9
        for i in self.order:
            m = self.nodes[i]
            if not m.alive or m.role == "rover" or m.id == n.id:
                continue
            if comps.get(i) == c:
                continue
            d = math.hypot(m.x - n.x, m.y - n.y)
            if d < bd:
                bd, best = d, m
        return best


    def _remember_anchors(self):
        """A) 每个节点记住"最近一次到基站的下一跳"位置(锚点)。

        内部私有helper: 无参数/无返回; 遍历 alive 的非 base/rover 节点,若有非 rover
        骨干下一跳则刷新其 _last_anchor 为下一跳位置。"""
        for i in self.order:
            n = self.nodes[i]
            if not n.alive or n.role in ("base", "rover") or n.sleeping:
                continue
            r = n.routing.get("BASE-00")
            if r and r["cost"] < INF and r.get("nh"):
                nh = self.nodes.get(r["nh"])
                if nh and nh.role != "rover" and nh.alive:
                    n._last_anchor = (nh.x, nh.y)

    def _rover_relay_anchors(self):
        """B) Rover 中继:① 有骨干路由的巡检车向邻近失联节点注入"朝网方向"锚点;
        ② 无论 rover 有无路由,都把"断裂对端"位置同步给失联节点(双向接合)。

        内部私有helper: 无参数/无返回; 仅对 rover 附近(LOS)的失联节点写 _last_anchor/
        rejoin_target/contact_at/relay_at。"""
        comps = self._components()
        for i in self.order:
            r = self.nodes[i]
            if r.role != "rover" or not r.alive or r.sleeping:
                continue
            rr = r.routing.get("BASE-00")
            have_route = rr is not None and rr["cost"] < INF
            anch = self.nodes.get(rr.get("nh")) if have_route else None
            for j in self.order:
                n = self.nodes[j]
                if not n.alive or n.role in ("rover", "base") or n.sleeping:
                    continue
                if math.hypot(n.x - r.x, n.y - r.y) <= RANGE \
                        and self.world.los((n.x, n.y), (r.x, r.y)):
                    nrt = n.routing.get("BASE-00")
                    no_base = nrt is None or nrt["cost"] >= INF
                    if no_base and anch is not None:
                        n._last_anchor = (anch.x, anch.y)
                        n.relay_at = self.t
                    if no_base:
                        ct = self._counterpart(n, comps)
                        if ct is not None:
                            n.rejoin_target = ct.id
                            n.contact_at = self.t
