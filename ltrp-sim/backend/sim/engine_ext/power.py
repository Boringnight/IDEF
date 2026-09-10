# -*- coding: utf-8 -*-
"""引擎职责模块:激光无线充电调度(PowerMixin)。

职责范围:地表核裂变 → BASE-00 巨型电池 → 沿"含睡眠节点"的能量树逐跳
bucket-brigade 下发(每跳自用优先、富余部分留存本地、其余反射下游),
按子树缺电需求加权分配,链尾富余充入本地电池。
依赖:读 Engine 的 nodes/static_links/order/world;写各 Node 的 laser_in_w/laser_out_w/
      pv_w/charge_ma/energy_active 与 self.power_links。
Calls: _energy_tree/_subtree_need/_power_distribute/_power_share_base/_power_share/_power_node。
"""
import math

from ..nodes import RANGE
from .constants import *


class PowerMixin:
    """激光充电 mixin,由 Engine 继承,self 即引擎实例。

    Globals Used: LASER_PS, LASER_ETA_EL, LASER_ALPHA, LASER_ETA_LE, LASER_ETA_R,
                  LASER_STORE, BATTERY_V, RANGE。
    Invocation: Engine.step() 每 ENERGY_EVERY tick 调用 _power_step(dts)。
    """

    def _power_step(self, dt: float):
        """激光充电主入口:重置本 tick 能量量 → 基地注入 → 建能量树 → 需求聚合 → 逐跳下发。

        Globals Used: LASER_PS, LASER_ETA_EL, LASER_ETA_LE, BATTERY_V。
        Calls: _energy_tree/_subtree_need/_power_distribute。
        Args: dt=物理步长(秒,当前仅用于接口一致性;功率为瞬时量)。Returns: None。
        """
        base = self.nodes.get("BASE-00")
        if not base:
            return
        for n in self.nodes.values():
            n.laser_in_w = n.laser_out_w = n.pv_w = 0.0
            n.charge_ma = 0.0
            n.energy_active = False
        if not base.alive:
            return          # 基站失能:本 tick 全网无供能(必须先清零,否则沿用上一 tick 的充电值)
        # 地表核裂变 -> BASE-00 巨型储能电池(净充电流)
        base.charge_ma = (LASER_PS * LASER_ETA_EL * LASER_ETA_LE) / BATTERY_V * 1000.0
        order_l, parent, children = self._energy_tree(base)
        need = self._subtree_need(order_l, children, base.id)
        self._power_distribute(base, order_l, parent, children, need)

    def _energy_tree(self, base):
        """构建能量树: 复用通信静态 LOS 链(含睡眠节点,睡眠只省无线电), 另把月球车接入。

        Globals Used: RANGE。Calls: world.los。
        Args: base=基站节点。Returns: (order_l, parent, children) 三元组。
        """
        from collections import deque
        live = lambda n: n.alive and not getattr(n, "_pending_deploy", False)
        padj = {i: set() for i in self.nodes}
        for a, b in self.static_links:
            na, nb = self.nodes[a], self.nodes[b]
            if live(na) and live(nb):
                padj[a].add(b)
                padj[b].add(a)
        for r in self.nodes.values():
            if r.role != "rover" or not live(r):
                continue
            for j in self.order:
                if j == r.id or not live(self.nodes[j]):
                    continue
                nj = self.nodes[j]
                if r.dist(nj) <= RANGE and self.world.los((r.x, r.y), (nj.x, nj.y)):
                    padj[r.id].add(j)
                    padj[j].add(r.id)
        parent = {base.id: None}
        queue = deque([base.id])
        order_l = [base.id]
        while queue:
            u = queue.popleft()
            for v in padj.get(u, ()):
                if v not in parent and live(self.nodes[v]):
                    parent[v] = u
                    queue.append(v)
                    order_l.append(v)
        children = {u: [] for u in order_l}
        for v in order_l:
            if parent[v]:
                children[parent[v]].append(v)
        return order_l, parent, children

    def _subtree_need(self, order_l: list, children: dict, base_id: str) -> dict:
        """子树"缺电需求"聚合: 按 (1 - soc/100) 加权, 能量优先流向更缺电的分支。

        Args: order_l=能量树 BFS 序; children=父→子列表; base_id=基站 id。
        Returns: id -> 该子树累计缺电需求(无量纲)。
        """
        need = {}
        for u in reversed(order_l):
            nu = self.nodes[u]
            s = (1.0 - nu.soc / 100.0) if u != base_id else 0.0
            for ch in children[u]:
                s += need.get(ch, 0.0)
            need[u] = s
        return need

    def _power_distribute(self, base, order_l: list, parent: dict,
                          children: dict, need: dict):
        """自基站向下逐跳下发功率:基站按需求加权分配给子节点,其余节点自用优先再转发。

        Globals Used: LASER_PS, LASER_ETA_EL。
        Calls: _power_share_base/_power_node。Args: 见上。Returns: None。
        """
        upstream = {u: 0.0 for u in order_l}
        upstream[base.id] = LASER_PS * LASER_ETA_EL
        self.power_links = []
        for u in order_l:
            if u == base.id:
                self._power_share_base(children[u], need, upstream[u], upstream)
            else:
                self._power_node(u, parent, children, upstream, need)

    def _power_share_base(self, kids: list, need: dict, inject: float, upstream: dict):
        """基站按子树缺电需求加权下发(深处的长链 need 大 → 得到更多注入,能量能递送到管尾)。

        Args: kids=基站子节点; need=需求表; inject=注入功率 W; upstream=待写入的上游功率表。
        Returns: None。
        """
        bneed = sum(max(0.0, need.get(ch, 0.0)) for ch in kids)
        if bneed <= 1e-6:
            share = inject / max(1, len(kids))
            for ch in kids:
                upstream[ch] += share
            return
        for ch in kids:
            upstream[ch] += inject * (max(0.0, need.get(ch, 0.0)) / bneed)

    def _power_node(self, u: str, parent: dict, children: dict,
                    upstream: dict, need: dict):
        """单节点能量处理:接收上游 → 光伏转换 → 自用优先 → 富余留存/转发下游。

        Globals Used: LASER_ALPHA, LASER_ETA_LE, LASER_ETA_R, LASER_STORE, BATTERY_V。
        Calls: _power_share。Args: 见上。Returns: None。
        """
        nu = self.nodes[u]
        pu = self.nodes[parent[u]]
        dm = max(1.0, math.hypot(nu.x - pu.x, nu.y - pu.y))
        Pe = upstream[u] * math.exp(-LASER_ALPHA * dm) * LASER_ETA_LE
        cur_w = nu.avg_current_ma * BATTERY_V / 1000.0
        nu.laser_in_w = upstream[u]
        if Pe <= 1e-6:
            nu.pv_w = 0.0
            return
        if not children[u]:                        # 链尾: 全部光电→自用优先, 富余充入本地电池
            nu.pv_w = Pe
            nu.charge_ma = (Pe - cur_w) / BATTERY_V * 1000.0
            nu.energy_active = True
            return
        locally = min(cur_w, Pe)                   # 自用优先
        excess = max(0.0, Pe - locally)
        store = excess * LASER_STORE               # 富余留存本地电池
        out = (excess - store) / LASER_ETA_LE * LASER_ETA_R   # 其余转发下游(电->激光)
        nu.pv_w = locally + store
        nu.charge_ma = (nu.pv_w - cur_w) / BATTERY_V * 1000.0
        nu.laser_out_w = out
        nu.energy_active = upstream[u] > 0
        self._power_share(children[u], need, out, upstream, u)

    def _power_share(self, kids: list, need: dict, out: float,
                     upstream: dict, u: str):
        """非基站节点把待转发功率按子节点缺电需求加权分配,并记录 power_links 供前端画线。

        Args: kids=子节点; need=需求表; out=待转发功率 W; upstream=上游功率表; u=本节点 id。
        Returns: None。
        """
        cneed = sum(need.get(ch, 0.0) for ch in kids)
        if cneed <= 1e-6:
            share = out / len(kids)
            for ch in kids:
                upstream[ch] += share
                self.power_links.append([u, ch, round(share, 2)])
            return
        for ch in kids:
            wgt = max(0.0, need.get(ch, 0.0))
            share = out * (wgt / cneed)
            upstream[ch] += share
            self.power_links.append([u, ch, round(share, 2)])
