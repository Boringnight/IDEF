# -*- coding: utf-8 -*-
"""引擎职责模块:失联判定与自愈目标解析(SelfHealLogicMixin)。

职责范围:节点断连检测(桥接断裂/孤立/分区三类) / 锚点选择 / 前沿朝锚点桥接推进。
依赖:读 Engine 的 nodes/order/world/adj/t;写各 Node 的 sos/seek_target。
Calls: _remember_anchors / _rover_relay_anchors / _loss_state / _heal_anchor /
       _advance_front / _seek_target / _tube_pt / _move_node / _separate。
"""
import math
from typing import NamedTuple

from ..nodes import RANGE, INF
from .constants import *


class LossState(NamedTuple):
    """单节点的失联判定结果(局部视图,不跨节点共享)。

    has_base=当前是否有到基站的实时路由; nbrs=存活非 rover 邻居 id 列表;
    isolated=完全无邻居; recent_bridge=刚失去的邻居是"桥"且已死;
    isolated_sos=持续孤立超时; partition_sos=持续无路由且整片邻居都无路由;
    disconnected=三者取或(是否触发自愈)。
    """
    has_base: bool
    nbrs: list
    isolated: bool
    recent_bridge: bool
    isolated_sos: bool
    partition_sos: bool
    disconnected: bool


class SelfHealLogicMixin:
    """失联判定 mixin,由 Engine 继承,self 即引擎实例。

    Globals Used: ISOLATION_T, PARTITION_T, WOUND_WINDOW, RELAY_HOLD, LINK_SAFE,
                  NODE_STOP, RANGE, INF。
    Invocation: Engine.step() 每 tick 调用 _step_movement(dt)。
    """

    def _step_movement(self, dt: float):
        """节点自愈:只凭本地路由/断链/孤立判断失联;前沿朝锚点桥接,其余原地等待被链式接入。

        自愈的"是否已连基站"一律以**永久链路**(静态连通分量,排除月球车)为准:
        月球车开进断口时 DSDV 路由会临时穿过它,若按路由判定,两侧瞬间"恢复"、
        自愈移动被抑制、对端坐标也不再注入 —— 网络停留在"依赖月球车路过"的
        伪愈合(实测摧毁喉道后 42 tick partitions=0 但两端零节点移动)。

        Globals Used: WOUND_WINDOW, ISOLATION_T, PARTITION_T。
        Calls: _components/_remember_anchors/_rover_relay_anchors/_agent_move_loop/
               _recompute_static/_update_adj。 Args: dt=物理步长(秒)。 Returns: None。
        """
        comps = self._components()                    # 静态连通分量(不含 rover 桥接)
        base_comp = comps.get("BASE-00")
        self._static_base_ids = {i for i, c in comps.items() if c == base_comp}
        self._remember_anchors()
        self._rover_relay_anchors()
        self._agent_move_loop(dt)
        if self._heal_moved_any:
            self._recompute_static()
            self._update_adj()

    def _agent_move_loop(self, dt: float):
        """本地 agent 判定与移动(v0 行为: 喉道断口两端同时移动闭合)。

        失联三类(桥接断裂/孤立/分区) → 仅[前沿]节点朝锚点桥接, 非前沿原地等待被链式接入;
        桥接断裂(recent_bridge)【不】要求 has_base —— 只要刚失去的邻居是桥(连接两端, 邻居数>1)
        且已死亡, 就视为分裂、两侧同时朝断口靠拢合并, 而非只有失联侧乱动、基站侧一动不动。

        Args: dt=物理步长(秒)。 Returns: None(写 n.sos/n.seek_target 并置 _heal_moved_any)。
        """
        moved_any = False
        movers = set()
        # 部署期探针只管撒布(不参与自愈);部署完成后与 v0 一致,探针也是可自愈节点 ——
        # 否则走到管尾的探针一旦失去视距就永久孤立,覆盖率永远差那一格。
        skip = ("base", "rover") if self.deploy_done else ("base", "rover", "probe")
        for i in self.order:
            n = self.nodes[i]
            if not n.alive or n.role in skip or n.sleeping:
                n.sos = False
                n.seek_target = None
                continue
            st = self._loss_state(n)
            if not st.disconnected:
                n.sos = False
                n.seek_target = None
                n.rejoin_target = None
                continue
            n.sos = True
            anchor = self._heal_anchor(n, st)
            if anchor is None:
                continue
            if self._advance_front(n, anchor, st, dt):
                movers.add(i)
                moved_any = True
        for i in movers:
            self._separate(self.nodes[i])
        self._heal_moved_any = moved_any

    def _loss_state(self, n) -> LossState:
        """判定节点 n 是否失联,并维护"孤立/无路由"计时器(带防抖)。

        has_base 用**静态连通**(self._static_base_ids,排除月球车桥接)而非路由表:
        路由可临时穿过路过的月球车,会把伪愈合当成恢复、掐灭自愈移动。

        Returns: LossState。
        """
        has_base = n.id in getattr(self, "_static_base_ids", ())
        nbrs = [j for j in n.neighbors
                if self.nodes[j].alive and self.nodes[j].role != "rover"]
        isolated = len(nbrs) == 0
        # 去中心化"桥接断裂": 刚失去的邻居是桥(连接两端,邻居数>1)且已死亡。
        # 无论当前是否仍有(经巡检车摆渡的)到某节点路由,都视为分裂 → 立即朝断口靠拢合并。
        recent_bridge = (getattr(n, "_last_gone", None) is not None
                         and n._last_gone in self.nodes
                         and not self.nodes[n._last_gone].alive
                         and n._last_gone_at > 0
                         and (self.t - n._last_gone_at) < WOUND_WINDOW
                         and n._last_gone_nbrs > 1)
        if isolated:
            if n.sos_since is None:
                n.sos_since = self.t
        else:
            n.sos_since = None
        isolated_sos = (isolated and n.sos_since is not None
                        and (self.t - n.sos_since) >= ISOLATION_T)
        if not has_base:
            if n._since_nobase is None:
                n._since_nobase = self.t
        else:
            n._since_nobase = None
        # 分区失联(pocket): 持续无基站路由、有邻居、且**所有邻居也都没有基站路由** ——
        # "整片都连不上"才是 pocket;单点暂时断连时总有邻居握着路由,不应移动。
        # 阈值用 PARTITION_SETTLE(而非"立刻"): 新撒布的节点先留足 DSDV 收敛时间,
        # 否则刚上线(还没有路由)就会朝载体方向回撤,反而把链拆散。
        partition_sos = (not has_base and n._since_nobase is not None
                         and (self.t - n._since_nobase) >= PARTITION_SETTLE
                         and len(nbrs) > 0 and self._nbrs_all_unrouted(nbrs)
                         and n._last_anchor is not None)
        return LossState(has_base, nbrs, isolated, recent_bridge, isolated_sos,
                         partition_sos,
                         recent_bridge or isolated_sos or partition_sos)

    def _nbrs_all_unrouted(self, nbrs: list) -> bool:
        """所有邻居是否都**静态连不上**基站 —— pocket(整片失联)的特征判据。
        用静态连通而非路由表(路由可能临时经月球车,把 pocket 误判为已恢复)。

        Args: nbrs=存活非 rover 邻居 id 列表。Returns: True=整片无基站连接。
        """
        base_ids = getattr(self, "_static_base_ids", ())
        return all(j not in base_ids for j in nbrs)

    def _heal_anchor(self, n, st: LossState):
        """解析失联节点的桥接锚点:优先 rover 同步的断裂对端,其次分区记忆锚点,
        最后退回断口(刚失去的邻居)/最近存活节点 —— 让两侧都朝断口靠拢。

        Args: n=节点; st=失联判定结果。Returns: (x,y) 锚点或 None。
        """
        rj = self.nodes.get(getattr(n, "rejoin_target", "")) \
            if getattr(n, "rejoin_target", None) else None
        if rj is not None and rj.alive \
                and (self.t - getattr(n, "contact_at", -99)) < RELAY_HOLD:
            return (rj.x, rj.y)
        if st.partition_sos and not st.recent_bridge:
            return n._last_anchor or self._seek_target(n)
        return self._seek_target(n) or n._last_anchor

    def _advance_front(self, n, anchor, st: LossState, dt: float) -> bool:
        """前沿推进:只有比邻居更靠近锚点的节点才移动,落点保持 RANGE*LINK_SAFE 链距。

        Args: n=节点; anchor=(x,y); st=失联判定; dt=物理步长。
        Returns: True=本 tick 发生了移动。
        """
        d_self = math.hypot(n.x - anchor[0], n.y - anchor[1])
        # 前沿判定: 若存在比"我"更靠近锚点的邻居 → 我不是前沿, 原地等待(链式接入,避免聚团)
        front = st.isolated or all(
            math.hypot(self.nodes[j].x - anchor[0], self.nodes[j].y - anchor[1])
            >= d_self - 1 for j in st.nbrs)
        if not front:
            return False
        if n.seek_target is None:
            dxb = anchor[0] - n.x
            dyb = anchor[1] - n.y
            db = math.hypot(dxb, dyb)
            if db > RANGE * LINK_SAFE:
                tt = (db - RANGE * LINK_SAFE) / db
                n.seek_target = self._tube_pt((n.x + dxb * tt, n.y + dyb * tt))
            else:
                n.seek_target = self._tube_pt(anchor)
        tgt = n.seek_target
        if tgt is None:
            return False
        self._move_node(n, tgt, dt)
        # 复位: 只有连回"实时基站路由"才算真正恢复 → 停止并清 SOS;
        # 否则到达本步目标就清 seek_target, 下个 tick 重新选目标持续自愈。
        if st.has_base:
            n.sos = False
            n.seek_target = None
            n.rejoin_target = None
        elif math.hypot(n.x - tgt[0], n.y - tgt[1]) <= NODE_STOP:
            n.seek_target = None
        return True
