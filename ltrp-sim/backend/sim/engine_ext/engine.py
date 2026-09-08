# -*- coding: utf-8 -*-
"""引擎职责模块:主循环编排(EngineStepMixin)."""

from ..nodes import RANGE, INF, STATE_DEAD, STATE_DYING, STATE_PROTECTED
from .constants import *
from .engine_imports import P, zh



class EngineStepMixin:
    """主循环编排 mixin,由 Engine 继承,self 即引擎实例。"""

    def step(self, dt: float = DT):
        self.t += dt
        self.tick += 1
        heat = self.t < self.heat_until

        # 1. 月球车巡线(其巡逻路线即"可预知接触计划");带实体碰撞:绕开巨石、贴合岩壁
        for n in self.nodes.values():
            if n.role == "rover" and n.alive:
                self._rover_phys(n, dt)

        self._update_adj()

        # 2. 节点自愈移动(孤立/分区时移动重连)
        self._step_movement(dt)

        # 3. 信标交换(引擎只做投递;节点本地处理)
        beacon = self.tick % BEACON_EVERY == 0
        if beacon:
            for i in self.order:
                n = self.nodes[i]
                if not n.alive or n.sleeping:
                    continue
                h = P.compose_hello(n, self.t)
                n.spend(0.003)
                n.tx += 1
                for j in self.adj[i]:
                    nj = self.nodes[j]
                    lk = self._snr(n, nj)
                    if lk and lk["up"]:
                        # 建链双门限 + 迟滞: 新链路按 gamma 建立;
                        # 已有链路容忍 4dB 衰落(接收端只查自己邻居表,仍为本地判定),
                        # 避免边缘链路被相关阴影衰落反复撕断(抑制路由抖动)
                        thr = (P.PARAMS.gamma - 4.0) if n.id in nj.neighbors \
                            else P.PARAMS.gamma
                        if lk["snr_db"] >= thr:
                            # 水平分割:发给 j 时剔除"下一跳为 j"的目的(抑制 2 环路)
                            hj = {**h, "adv": {d: v for d, v in h["adv"].items()
                                               if v[2] != j}}
                            P.on_hello(nj, hj, lk["snr_db"], self.t, lk["ber"])
                            nj.spend(0.0018)
                            nj.rx += 1

        # 3. 邻居超时(判死) + 割点自识别(连续 2 票防抖)
        for i in self.order:
            n = self.nodes[i]
            if not n.alive:
                continue
            nbr_counts = {
                j: len(n.neighbors[j]["nbrs"])
                for j in list(n.neighbors)
                if self.t - n.neighbors[j]["last"] > P.PARAMS.n_fail * P.PARAMS.tb
            }
            dead = P.expire_neighbors(n, self.t)
            # 还原 v0: 无条件记录"最近超时的邻居"为追回目标 —— 桥断(死)即 recorded,
            # recent_bridge 才能触发、两端同时朝断口靠拢合并(自愈核心)。
            for j in dead:
                n._last_gone = j
                n._last_gone_at = self.t
                n._last_gone_nbrs = nbr_counts.get(j, 0)
            if len(n.neighbors) >= 2:
                if getattr(n, "_topo_dirty", True):
                    cv = P.cut_vertex(n)
                    n._topo_dirty = False
                    n._cv_res = cv
                else:
                    cv = n._cv_res
                n._crit_votes = n._crit_votes + 1 if cv else 0
                was = n.is_critical
                n.is_critical = n._crit_votes >= 3
                if n.is_critical and not was and n.role == "spike" \
                        and not getattr(n, "_crit_emitted", False):
                    n._crit_emitted = True
                    self.emit("crit", "info",
                              f"{zh(i)}({i}) 自识别为关键割点:锁定常开、提升功率(节点仅凭 2 跳视图判定)", True)
                elif not n.is_critical and was:
                    n._crit_emitted = False
            else:
                n.is_critical = False
                n._crit_votes = 0

        # 4. DSDV 分布式松弛(多轮加速收敛)
        for _ in range(RELAX_ROUNDS):
            for i in self.order:
                n = self.nodes[i]
                if n.alive and not n.sleeping:
                    P.dsdv_relax(n, self.nodes)

        # 5. 流量自适应休眠:苏醒比例随负载升降;'睡谁'由本地安全判据决定
        #    (非割点/非唯一桥/非孤立才准睡,避免把通信路睡断)
        if self.sleep_on:
            self._update_sleep_duty()
            phase = int(self.t // SLEEP_PERIOD)
            sleep_ratio = 1.0 - self.sleep_duty        # 允许睡眠的比例
            ids = sorted(i for i, n in self.nodes.items()
                         if n.alive and n.role == "spike" and not n.border)
            for i in ids:                              # 按 id 串行决策(确定性)
                n = self.nodes[i]
                if n.state in (STATE_DYING, STATE_PROTECTED, STATE_DEAD):
                    continue                           # 保护/垂死/已死:交给能量状态机
                idx = int(i.split("-")[1])
                # 同 tick 内已把前面的节点改为沉睡 → 后续 _can_sleep/_nbr_independent
                # 会把它视为不可用,从而避免"两个互为备用路径的节点同时睡"的多米诺。
                can = self._can_sleep(n)              # 割点/唯一桥/孤立 → False
                slot = (idx * 0.6180339887 + phase) % 1.0
                should_sleep = can and slot < sleep_ratio
                if should_sleep != n.sleeping:
                    n.sleeping = should_sleep

        # 6. 能量与状态机(电池/温度/辐射均为节点本地物理演化)
        for n in self.nodes.values():
            n.energy_step(dt, heat)
        for i, n in self.nodes.items():
            if getattr(n, "_seu_hit", False):
                n._seu_hit = False
                self.emit("seu", "warn",
                          f"{zh(i)}({i}) 遭单粒子翻转:本地邻居表复位,凭信标重新发现邻居", False)
        just_dead = [i for i, n in self.nodes.items() if not n.alive and not hasattr(n, "_death_logged")]
        for i in just_dead:
            self.nodes[i]._death_logged = True
            self.emit("dead", "warn", f"{zh(i)}({i}) 能量耗尽宕机", True)

        # 7. 数据面:遥测生成 → 逐跳转发 / 束化
        self._traffic(dt, heat)

        # 8. 月球车摆渡(存储-携带-转发)
        self._ferry()

        # 9. 地球可见窗口
        self._earth()

        # 10. 统计 + 自愈播报
        self._stats()

    # ------------------------------------------------------------------ data
