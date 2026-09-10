# -*- coding: utf-8 -*-
"""引擎职责模块:流量自适应休眠(SleepSchedulerMixin)."""

from ..nodes import RANGE, INF, STATE_DEAD, STATE_DYING, STATE_PROTECTED
from .constants import *
from .engine_imports import P



class SleepSchedulerMixin:
    """流量自适应休眠 mixin,由 Engine 继承,self 即引擎实例。"""

    def _load_factor(self) -> float:
        """当前负载信号:每存活非基站节点平均积压的束+包数。
        数值越高说明转发能力跟不上(拥塞),需要更多节点苏醒。"""
        alive = [n for n in self.nodes.values() if n.alive and n.role != "base"]
        if not alive:
            return 0.0
        backlog = sum(len(n.bundles) + len(n.packets) for n in alive)
        return backlog / len(alive)

    def _update_sleep_duty(self):
        """比例控制器(带死区):积压高于目标→提高苏醒比例(多转发/均摊),
        低于目标→降低苏醒比例(省电,只保连通主干)。"""
        load = self._load_factor()
        duty = self.sleep_duty
        if load > LOAD_TARGET + LOAD_HYST:
            duty += DUTY_STEP
        elif load < LOAD_TARGET - LOAD_HYST:
            duty -= DUTY_STEP
        self.sleep_duty = min(SLEEP_DUTY_MAX, max(SLEEP_DUTY_MIN, duty))

    def _nbr_independent(self, n) -> bool:
        """n 的每个清醒邻居,是否都有一条'不经 n'通往基站的路径。
        只要还有邻居只能靠 n 上行,睡 n 就会断其余节点的路由 → 返回 False。
        (对应'唯一桥'否决:给依赖 n 的邻居留好后路 n 才准睡。)"""
        for j in n.neighbors:
            nj = self.nodes.get(j)
            if nj is None or not nj.alive or nj.sleeping or nj.role in ("base", "rover"):
                continue
            has_alt = False
            for k in nj.neighbors:
                if k == n.id:
                    continue
                nk = self.nodes.get(k)
                if nk is not None and nk.alive and not nk.sleeping and nk.role != "rover":
                    r = nk.routing.get("BASE-00")
                    if r and r["cost"] < INF:
                        has_alt = True
                        break
            if not has_alt:
                return False
        return True

    def _can_sleep(self, n) -> bool:
        """本地睡眠安全判据(逐 tick 重算,自适应):仅当同时满足才允许 n 睡——
        非边界道钉、状态正常、不是割点(用 2 跳现算,不走延迟的 is_critical)、
        且每个清醒邻居都存在不依赖 n 的到基站路径。孤立节点也不睡(要发信标自愈)。"""
        if not self.sleep_on or n.role != "spike" or n.border or not n.alive:
            return False
        if n.sos:
            return False   # 自愈移动前锋/跟进者不睡(睡了就没人桥接了)
        if n.state in (STATE_PROTECTED, STATE_DYING, STATE_DEAD):
            return False
        awake = [j for j in n.neighbors
                 if self.nodes.get(j) and self.nodes[j].alive
                 and not self.nodes[j].sleeping and self.nodes[j].role != "rover"]
        if not awake:
            return False
        if P.cut_vertex(n):
            return False
        if not self._nbr_independent(n):
            return False
        return True

    # ------------------------------------------------------------------ tick
