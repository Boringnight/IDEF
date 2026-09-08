# -*- coding: utf-8 -*-
"""引擎职责模块:失联判定与自愈目标解析(SelfHealLogicMixin).

职责范围:节点断连检测 / 锚点选择 / 前锋分级推进 / 链式跟进;调用定位移动模块执行。
依赖:读 Engine 的 nodes/order/world/adj/t;写各 Node 的 sos/seek_target/_stall_*。
Calls: _components/_counterpart/_seek_target/_front_target/_move_node/_tube_pt。
"""
import math

from ..nodes import Node, RANGE, INF
from .constants import *


class SelfHealLogicMixin:
    """失联判定 mixin,由 Engine 继承,self 即引擎实例。

    Globals Used: ISOLATION_T, PARTITION_T, WOUND_WINDOW, RELAY_HOLD, LINK_SAFE,
                  NODE_MOVE_SPEED, NODE_STOP, STALL_PUSH_T, STALL_SWEEP_T,
                  STALL_GIVEUP, SWEEP_DWELL, CHAIN_FOLLOW。
    Invocation: Engine._step_movement() 每 tick 调。
    """


    def _step_movement(self, dt: float):
        """节点自愈:只凭本地路由/断链/孤立判断失联;前沿朝锚点桥接,其余链式跟进。

        Globals Used: WOUND_WINDOW, ISOLATION_T, PARTITION_T。
        Calls: _remember_anchors/_rover_relay_anchors/_agent_move_loop/
               _recompute_static/_update_adj。 Args: dt=物理步长(秒)。 Returns: None。
        """
        self._remember_anchors()
        self._rover_relay_anchors()
        self._agent_move_loop(dt)
        if self._heal_moved_any:
            self._recompute_static()
            self._update_adj()

    def _agent_move_loop(self, dt: float):
        """本地 agent 判定与移动(v0 行为, 恢复"喉道断口两端同时移动闭合")。

        失联三类(桥接断裂/孤立/分区) → 仅[前沿]节点朝锚点桥接, 非前沿原地等待被链式接入;
        桥接断裂(recent_bridge)【不】要求 has_base —— 只要刚失去的邻居是桥(连接两端, 邻居数>1)
        且已死亡, 就视为分裂、两侧同时朝断口靠拢合并, 而非只有失联侧乱动、基站侧一动不动。
        """
        moved_any = False
        movers = set()
        for i in self.order:
            n = self.nodes[i]
            if not n.alive or n.role in ("base", "rover") or n.sleeping:
                n.sos = False
                n.seek_target = None
                continue
            route = n.routing.get("BASE-00")
            has_base = route is not None and route["cost"] < INF
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
            # 持续孤立计时(避免上电/瞬态抖动误报)
            if isolated:
                if n.sos_since is None:
                    n.sos_since = self.t
            else:
                n.sos_since = None
            isolated_sos = (isolated and n.sos_since is not None
                            and (self.t - n.sos_since) >= ISOLATION_T)
            # 持续无基站实时路由计时(分区/断连): 记得"朝网方向"锚点 + 有邻居 = 喉道断口两侧。
            if not has_base:
                if n._since_nobase is None:
                    n._since_nobase = self.t
            else:
                n._since_nobase = None
            partition_sos = (not has_base and n._since_nobase is not None
                             and (self.t - n._since_nobase) >= PARTITION_T
                             and n._last_anchor is not None
                             and len(nbrs) > 0)
            disconnected = recent_bridge or isolated_sos or partition_sos
            if not disconnected:
                n.sos = False
                n.seek_target = None
                n.rejoin_target = None
                continue
            n.sos = True
            # 锚点: 优先 rover 同步的断裂对端(两端实时对移); 其次持续分区的记忆锚点(指向网方向);
            # 否则退回断口(刚失去的邻居)/最近存活节点 —— 让两侧都朝断口靠拢。
            rj = self.nodes.get(getattr(n, "rejoin_target", "")) \
                if getattr(n, "rejoin_target", None) else None
            if rj is not None and rj.alive \
                    and (self.t - getattr(n, "contact_at", -99)) < RELAY_HOLD:
                anchor = (rj.x, rj.y)
            elif partition_sos and not recent_bridge:
                anchor = n._last_anchor
            else:
                anchor = self._seek_target(n) or n._last_anchor
            if anchor is None:
                continue
            d_self = math.hypot(n.x - anchor[0], n.y - anchor[1])
            # 前沿判定: 若存在比"我"更靠近锚点的邻居 → 我不是前沿, 原地等待(链式接入,避免聚团)
            front = isolated or all(
                math.hypot(self.nodes[j].x - anchor[0], self.nodes[j].y - anchor[1])
                >= d_self - 1 for j in nbrs)
            if not front:
                continue
            # 朝锚点桥接: 移到"与锚点保持 RANGE*LINK_SAFE"的落点(不直接扎到锚点上)
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
                continue
            self._move_node(n, tgt, dt)
            movers.add(i)
            moved_any = True
            # 复位: 只有连回"实时基站路由"才算真正恢复 → 停止并清 SOS;
            # 否则到达本步目标就清 seek_target, 下个 tick 重新选目标持续自愈。
            if has_base:
                n.sos = False
                n.seek_target = None
                n.rejoin_target = None
            elif math.hypot(n.x - tgt[0], n.y - tgt[1]) <= NODE_STOP:
                n.seek_target = None
        for i in movers:
            self._separate(self.nodes[i])
        self._heal_moved_any = moved_any

    # 注: 原 _real_base 递归"信任"判定已被"物理连通分量"判定取代(见 _disconnect_state/
    # _is_disconnected)——它既无法区分"借道 rover 中转的正常节点"(误判失联引发回归),
    # 也挡不住 count-to-infinity 伪路由, 故移除。
    def _disconnect_state(self, n, comps=None, base_c=None):
        """判单节点是否失联,返回 (disconnected, has_base, isolated, anchor, live_anchor)
        或 None(未失联)。

        内部私有helper: n=目标节点; comps/base_c=连通分量与基站分量号(可选,缺省现算);
        返回五元组或 None; 只读邻居/路由/锚点,不改状态。
        Calls: _is_disconnected(路由/邻居判定) / _select_anchor(锚点解析)。
        """
        # "是否失联"最可靠判据是物理连通分量: 节点是否真处于基站所在分量。
        # 路由表里的有限 cost 可能是伪路由/经 rover 摆渡中转, 无法区分"真连基站"与"假象";
        # 故用 comps(引擎物理视距分量)判定 has_base 与 nbrs_noroute, 不被伪路由/rover 干扰。
        has_base = (comps is not None and base_c is not None
                    and comps.get(n.id) == base_c)
        nbrs = [j for j in n.neighbors
                if self.nodes[j].alive and self.nodes[j].role != "rover"]
        isolated = len(nbrs) == 0

        # 用独立判定(路由/邻居/计时), 返回 (recent_bridge, isolated_sos, partition_sos)
        recent_bridge, isolated_sos, partition_sos = \
            self._is_disconnected(n, nbrs, isolated, has_base, comps, base_c)
        disconnected = recent_bridge or isolated_sos or partition_sos
        if not disconnected:
            return None
        anchor, live_anchor = self._select_anchor(
            n, nbrs, recent_bridge, isolated_sos, partition_sos, comps)
        if anchor is None:
            return None
        return (True, has_base, isolated, anchor, live_anchor)

    def _is_disconnected(self, n, nbrs, isolated, has_base, comps=None, base_c=None):
        """判定三类失联条件(桥接断裂/孤立/分区),返回三元布尔。

        内部私有helper: n=节点; nbrs=有效邻居id列表; isolated=孤立标志; has_base=有无路由。
        Returns: (recent_bridge, isolated_sos, partition_sos)。
        """
        lg = getattr(n, "_last_gone", None)
        lg_role = self.nodes[lg].role if lg in self.nodes else None
        recent_bridge = (lg is not None and lg_role == "spike"
                         and not self.nodes[lg].alive
                         and n._last_gone_at > 0
                         and (self.t - n._last_gone_at) < WOUND_WINDOW
                         and n._last_gone_nbrs > 1
                         and not has_base)
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
        # 分区失联:连续无 BASE 路由足够久、有邻居、且**所有邻居也都无 BASE 路由**。
        # "所有邻居也无路由"是整片 pocket 的特征,可区分真失联与单点暂时断连
        # (单点暂时断连时总有邻居握着路由,路由下一拍收敛即恢复,不应移动)。
        # 无 _last_anchor 的深处 pocket 也能触发:锚点回退到 _seek_target(朝最近存活节点)。
        # "整片都连不上基站"用分量判定: 所有邻居都不在基站分量, 才触发分区自愈。
        # 比看路由 cost 更可靠(可识破伪路由), 也不会误伤借道 rover 中转的正常节点
        # (它们物理上仍在基站分量, comps 判定 has_base=True)。
        nbrs_noroute = all(comps is not None and base_c is not None
                           and comps.get(j) != base_c for j in nbrs)
        partition_sos = (not has_base and n._since_nobase is not None
                         and (self.t - n._since_nobase) >= PARTITION_T
                         and len(nbrs) > 0 and nbrs_noroute)
        return (recent_bridge, isolated_sos, partition_sos)

    def _select_anchor(self, n, nbrs, recent_bridge, isolated_sos, partition_sos,
                       comps=None):
        """解析失联节点的桥梁锚点(优先 rover 同步对端,其次记忆锚点,再退回最近存活)。

        内部私有helper: n=节点; nbrs=邻居id列表; recent_bridge/isolated_sos/partition_sos=标志。
        Returns: (anchor坐标, live_anchor布尔) 或 (None, False)。
        """
        rj = self.nodes.get(getattr(n, "rejoin_target", "")) \
            if getattr(n, "rejoin_target", None) else None
        live_anchor = False
        anchor = None
        if rj is not None and rj.alive \
                and (self.t - getattr(n, "contact_at", -99)) < RELAY_HOLD:
            anchor = (rj.x, rj.y)
            live_anchor = True
        elif (partition_sos and not recent_bridge and n._last_anchor is not None):
            anchor = n._last_anchor
        else:
            anchor = self._seek_target(n) or n._last_anchor
        # 断口方向兜底: 只要节点仍处非基站分量, 就优先朝"最近的不同分量节点"(断口对端)推进
        # ——隔离区整体朝基站侧压缩, 前锋借此与对端闭合(尤其最左喉道这类宽断口非它不可)。
        # 用"front 推进 + 后排链式跟进"分级而非全员猛冲; 配合 _separate 分摊避免挤压震荡;
        # 且已移除非必要的逐点 LOS 绕障采样(那才是导致乱绕/退化的冗杂部分)。
        ct = self._counterpart(n, comps if comps is not None else self._components())
        if ct is not None:
            anchor = (ct.x, ct.y)
        if anchor is None:
            return None, False
        return anchor, live_anchor

    def _front_target(self, n, anchor, d_self, has_base, live_anchor):
        """前锋推进(收敛版): '远→推进到 LINK_SAFE 停点 → 到位即停等信标确认(滞回) →
        停够 STALL_PUSH_T 仍不通则直插对端攻坚'。去掉随机绕行扫描(易抖/来回)。

        Globals Used: STALL_PUSH_T, RANGE, LINK_SAFE。
        Calls: _tube_pt。 Args: n=节点; anchor=锚点; d_self=到锚点距离; has_base/live_anchor。
        Returns: 目标坐标元组; 返回 None 表示"到位即停"(不动, 保留 seek 给 stall 计时, 收敛)。
        """
        if n._home is None:
            n._home = (n.x, n.y)
        if n.seek_target is not None and not has_base and math.hypot(
                n.x - n.seek_target[0], n.y - n.seek_target[1]) <= NODE_STOP:
            if n._stall_since is None:
                n._stall_since = self.t
        stall_age = (self.t - n._stall_since) if n._stall_since is not None else 0.0
        if live_anchor:
            return self._tube_pt(anchor)
        if d_self > RANGE * LINK_SAFE:
            tt = (d_self - RANGE * LINK_SAFE) / d_self
            return self._tube_pt((n.x + (anchor[0] - n.x) * tt,
                                  n.y + (anchor[1] - n.y) * tt))
        # 已进入 LINK_SAFE 链距: 到位即停(滞回), 等信标确认连通; 停够时长仍不通则直插攻坚
        if stall_age >= STALL_PUSH_T:
            return self._tube_pt(anchor)
        if n._stall_since is None:
            n._stall_since = self.t        # 到位即开始计时(滞回), 超 PUSH_T 后直插攻坚
        return None

    def _chain_follow(self, n, nbrs, anchor, d_self):
        """链式跟进:跟住"离锚点更近且正在推进(sos)"的邻居,保持 LINK_SAFE 链距。

        Globals Used: CHAIN_FOLLOW, LINK_SAFE, RANGE。
        Args: n=节点; nbrs=邻居id列表; anchor=锚点; d_self=到锚点距离。
        Returns: 目标坐标元组或 None(无可跟进领头/未掉出链距)。
        """
        leader, ld = None, 1e9
        for j in nbrs:
            e = n.neighbors.get(j)
            if not e or not e.get("sos"):
                continue
            jm = self.nodes[j]
            dj = math.hypot(jm.x - anchor[0], jm.y - anchor[1])
            if dj < d_self - 1 and dj < ld:
                ld, leader = dj, jm
        if leader is not None:
            d = n.dist(leader)
            if d > RANGE * LINK_SAFE:
                tt = (d - RANGE * LINK_SAFE) / d
                return self._tube_pt((leader.x + (n.x - leader.x) * tt,
                                      leader.y + (n.y - leader.y) * tt))
        return None

    # ------------------------------------------------------------------ 流量自适应休眠
