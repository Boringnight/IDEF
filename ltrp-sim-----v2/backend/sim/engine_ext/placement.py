# -*- coding: utf-8 -*-
"""引擎职责模块:节点落点校正(PlacementMixin).

职责范围:保证关键节点(尤其基站)与网络物理连通 —— 基站若被巨石/岩壁挡住 LOS,
沿管道重选一个"能直连最多 spike"的安全落点(修复 '基站零静态链路' 类地图缺陷)。
依赖:读 Engine 的 world/nodes;写 BASE-00 的 x/y。
Calls: world.yc / world.r_at / world.los / world.inside / _place_node。
"""
import math

from ..nodes import Node, RANGE, INF
from .constants import *


class PlacementMixin:
    """落点校正 mixin,由 Engine 继承,self 即引擎实例。

    Globals Used: RANGE。
    Invocation: Engine._spawn() 末尾调用 _fix_base_position()。
    """

    def _fix_base_position(self):
        """确保基站不与网络隔离:若基站对任意 spike 均无 LOS(被巨石/岩壁挡住),
        沿管道往内重选落点,优先取"能直连最多 spike"且自身安全的位置。

        Globals Used: RANGE。
        Calls: _base_los_count/_place_node, world.yc/r_at/inside。
        Args: None。 Returns: None(原地改 BASE-00 的 x/y)。
        """
        w = self.world
        base = self.nodes["BASE-00"]
        spikes = [n for n in self.nodes.values() if n.role == "spike" and n.alive]
        if self._base_los_count(base.x, base.y, spikes) > 0:
            return   # 基站本就有视距直连,无需调整
        bx = base.x
        best, best_cnt = (bx, base.y), 0
        x = bx
        while x <= w.W * 0.45:
            yc = w.yc(x)
            half = max(6.0, w.r_at(x) - 16)
            for dy in (0, 20, -20, 40, -40, 60, -60):
                yy = min(max(yc + dy, yc - half), yc + half)
                if not w.inside(x, yy, 10):
                    continue
                cnt = self._base_los_count(x, yy, spikes)
                if cnt > best_cnt:
                    best, best_cnt = (x, yy), cnt
            x += 60
        if best_cnt > 0:
            base.x, base.y = best
            base.y = self._place_node(base.x, base.y)

    def _base_los_count(self, x: float, y: float, spikes: list) -> int:
        """(x,y) 处能 LOS 直连的 spike 数量;0 表示完全被挡。

        Globals Used: RANGE。Calls: world.los。
        Args: x/y=候选落点; spikes=道钉列表。Returns: 直连数量。
        """
        cnt = 0
        for s in spikes:
            if math.hypot(s.x - x, s.y - y) <= RANGE and self.world.los((x, y), (s.x, s.y)):
                cnt += 1
        return cnt
