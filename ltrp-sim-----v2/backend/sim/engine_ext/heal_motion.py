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

    Globals Used: MOVE_V, RANGE。
    Invocation: 由 SelfHealLogicMixin 的 _agent_move_loop / _step_movement 调用。
    """

    def _clamp_node(self, n: Node, r: float = 14.0):
        """移动后的物理约束:夹回管内、推出巨石。Args: n=节点; r=等效半径。Returns: None。"""
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
        """把目标点夹进管道内(避免移动到岩壁里)。Args: tgt=(x,y)。Returns: 夹取后的 (x,y)。"""
        w = self.world
        X = min(max(tgt[0], w.W * 0.02), w.W * 0.98)
        yc = w.yc(X)
        half = max(8.0, w.r_at(X) - 16)
        return (X, min(max(tgt[1], yc - half), yc + half))

    def _move_node(self, n: Node, tgt, dt: float):
        """沿管道自愈移动:横/纵各自限速朝目标推进(v0 同款结构),随后做物理约束。

        横向预算 NODE_MOVE_V·dt;纵向同样限速(不采用 v0 的瞬移),避免大跨度纵跳穿石。
        移动耗电;落点始终夹在管内并推出巨石。

        Globals Used: NODE_MOVE_V。Calls: _clamp_node。
        Args: n=节点; tgt=(x,y) 目标; dt=物理步长(秒)。Returns: None。
        """
        w = self.world
        budget = NODE_MOVE_V * dt
        n.x += max(-budget, min(budget, tgt[0] - n.x))
        n.x = min(max(n.x, w.W * 0.02), w.W * 0.98)
        yc = w.yc(n.x)
        half = max(8.0, w.r_at(n.x) - 16)
        ty = min(max(tgt[1], yc - half), yc + half)
        n.y += max(-budget, min(budget, ty - n.y))
        n.y = min(max(n.y, yc - half), yc + half)
        n.spend(0.004)
        self._clamp_node(n)

    def _separate(self, n: Node, sep: float = 60.0):
        """节点最小间距:移动节点永远不会贴到任何其它节点上(防聚成一点)。做两遍收敛。

        Args: n=被推开的节点; sep=最小间距(px)。Returns: None(原地改 n.x/n.y)。
        """
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
        """连通分量(排除月球车,因摆渡只是临时接触)。Returns: id->分量号 dict。"""
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

    def _nearest_alive(self, n: Node):
        """离 n 最近的存活非 rover 节点(孤立重连的目标)。Args: n=节点。Returns: Node 或 None。"""
        best, bd = None, 1e9
        for i in self.order:
            m = self.nodes[i]
            if not m.alive or m.role == "rover" or m.id == n.id or m.sleeping:
                continue
            d = math.hypot(m.x - n.x, m.y - n.y)
            if d < bd:
                bd, best = d, m
        return best

    def _nearest_routed(self, n: Node):
        """离 n 最近的"握有基站实时路由"的存活非 rover 节点 —— 失联 pocket 的朝网方向目标。

        与 _nearest_alive 的区别: 后者会返回 pocket 内的同伴(导致整簇聚团),
        本函数只认真正连得上基站的节点,因此给 pocket 指出"往哪边走"的正确方向。

        Globals Used: INF。Args: n=节点。Returns: Node 或 None。
        """
        best, bd = None, 1e9
        base_ids = getattr(self, "_static_base_ids", ())
        for i in self.order:
            m = self.nodes[i]
            if not m.alive or m.role == "rover" or m.id == n.id:
                continue
            # 只认**永久链路**连通基站的节点:路由可能临时穿过路过的月球车,
            # 若把这种"伪路由"当方向,锚点会指回失联岛内部(月球车停摆即失效)
            if i not in base_ids:
                continue
            d = math.hypot(m.x - n.x, m.y - n.y)
            if d < bd:
                bd, best = d, m
        return best

    def _seek_target(self, n: Node):
        """本地自愈目标:优先追回'最近消失的邻居'(它在连接路径上),
        其次朝最近的有基站路由节点(pocket 的朝网方向),最后才退到最近存活节点。

        Args: n=节点。Returns: 夹取后的目标 (x,y) 或 None。
        """
        g = getattr(n, "_last_gone", None)
        if g and g in self.nodes:
            m = self.nodes[g]
            return self._tube_pt((m.x, m.y))
        t = self._nearest_routed(n) or self._nearest_alive(n)
        return self._tube_pt((t.x, t.y)) if t else None

    def _counterpart(self, n: Node, comps: dict) -> Node | None:
        """返回与 n 处于不同连通分量的最近存活节点(断裂对端)。
        rover 把它同步给 n,让两端朝彼此移动接合(弯曲喉道也能靠拢)。

        Args: n=节点; comps=连通分量表。Returns: 对端 Node 或 None。
        """
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

        内部私有helper: 无参数/无返回; 遍历 alive 的非 base/rover 节点(含探针,与 v0 一致),
        若有非 rover 骨干下一跳则刷新其 _last_anchor 为下一跳位置。"""
        for i in self.order:
            n = self.nodes[i]
            if not n.alive or n.role in ("base", "rover") or n.sleeping:
                continue
            r = n.routing.get("BASE-00")
            if r and r["cost"] < INF and r.get("nh"):
                nh = self.nodes.get(r["nh"])
                if nh and nh.role not in ("rover", "probe") and nh.alive:
                    # 探针下一跳不入锚点记忆:部署期探针一路向右移动,留下的坐标
                    # 指向岛内深处 —— 断链后失联节点会朝反方向"到达"该锚点并死等
                    n._last_anchor = (nh.x, nh.y)

    def _rover_relay_anchors(self):
        """B) Rover 中继:① 有骨干路由的巡检车向邻近失联节点注入"朝网方向"锚点;
        ② 无论 rover 有无路由,都把"断裂对端"位置同步给失联节点(双向接合)。

        内部私有helper: 无参数/无返回; 仅对 rover 附近(LOS)的失联节点写 _last_anchor/
        rejoin_target/contact_at。"""
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
                    # 失联判定用静态连通(排除 rover 桥接): 路由可临时穿过路过的
                    # 月球车,若按路由判定,对端坐标/朝网锚点都不会注入,伪愈合
                    no_base = j not in getattr(self, "_static_base_ids", ())
                    if no_base and anch is not None:
                        n._last_anchor = (anch.x, anch.y)
                    if no_base:
                        ct = self._counterpart(n, comps)
                        if ct is not None:
                            n.rejoin_target = ct.id
                            n.contact_at = self.t
