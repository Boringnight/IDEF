# -*- coding: utf-8 -*-
"""引擎职责模块:月球车巡逻物理(RoverMotionMixin)."""

import math

from ..nodes import Node, RANGE, INF
from .constants import *



class RoverMotionMixin:
    """月球车巡逻物理 mixin,由 Engine 继承,self 即引擎实例。"""

    def _rover_bounds(self):
        return self.world.rover_bounds()

    def _col_intervals(self, x: float) -> list:
        """x 处月球车可通行的连续 y 区间(扣除岩壁与每块巨石的遮挡)"""
        w = self.world
        yc = w.yc(x)
        half = max(4.0, w.r_at(x) - ROVER_R - ROVER_WALL_M)
        y_lo, y_hi = yc - half, yc + half
        rv = ROVER_R + 2.0
        blocked = []
        for b in w.boulders:
            dx = x - b["x"]
            rr = b["r"] + rv
            if abs(dx) < rr:
                s = math.sqrt(rr * rr - dx * dx)
                blocked.append((b["y"] - s, b["y"] + s))
        blocked.sort()
        intervals = []
        cur = y_lo
        for a, b in blocked:
            if b <= cur:
                continue
            if a > cur:
                lo = max(cur, y_lo)
                hi = min(a, y_hi)
                if hi - lo >= 6:
                    intervals.append((lo, hi))
            cur = max(cur, b)
        if cur < y_hi and (y_hi - cur) >= 6:
            intervals.append((cur, y_hi))
        return intervals

    def _build_patrol(self, prune: bool = True):
        """用'连续自由区间'BFS 求一条横贯整条熔岩管的无碰撞安全走廊,
        作为月球车导航线:沿它走既不穿模、也不会被巨石卡死。
        prune=True(地图生成)时若走廊被夹断则移除最堵中线的巨石保证可通行;
        prune=False(用户拖石/塌方)时不删石,月球车在堵点前停下(仍不穿模)。"""
        from collections import deque
        w = self.world
        lb, rb = self._rover_bounds()
        XSTEP = 20.0
        xs = []
        x = lb
        while x <= rb + 1:
            xs.append(round(x, 1))
            x += XSTEP
        ncol = len(xs)

        def corridor():
            ints = [self._col_intervals(x) for x in xs]
            for i in range(len(ints)):
                if not ints[i]:
                    yc = w.yc(xs[i])
                    ints[i] = [(yc - 8.0, yc + 8.0)]
            scol = min(ints[0], key=lambda iv: abs((iv[0] + iv[1]) / 2 - w.yc(xs[0])))
            si = ints[0].index(scol)
            start = (0, si)
            came = {start: None}
            q = deque([start])
            goal = None
            while q:
                ci, ii = q.popleft()
                if ci == ncol - 1:
                    goal = (ci, ii)
                    break
                a = ints[ci][ii]
                for jj, bv in enumerate(ints[ci + 1]):
                    if min(a[1], bv[1]) - max(a[0], bv[0]) > -8:
                        nc = (ci + 1, jj)
                        if nc not in came:
                            came[nc] = (ci, ii)
                            q.append(nc)
            return ints, goal, came

        ints, goal, came = corridor()
        removed = 0
        while prune and goal is None and removed < 6 and self.world.boulders:
            # 移除最堵中线的大巨石(半径大且贴近中心线)
            bi = max(range(len(self.world.boulders)),
                     key=lambda i: (self.world.boulders[i]["r"]
                                    - abs(self.world.boulders[i]["y"]
                                          - w.yc(self.world.boulders[i]["x"]))))
            self.world.boulders.pop(bi)
            removed += 1
            ints, goal, came = corridor()
        if removed:
            self._recompute_static()   # 巨石变少,重新算视距/链路
        if goal is None:
            lane = [(x, w.yc(x)) for x in xs]        # 兜底:沿中心线
        else:
            cells = []
            cur = goal
            while cur is not None:
                cells.append(cur)
                cur = came[cur]
            cells.reverse()
            lane = []
            prev_y = None
            for (ci, ii) in cells:
                a, b = ints[ci][ii]
                y = (a + b) / 2
                if prev_y is not None:
                    y = min(max(prev_y, a), b)        # 夹进当前自由带,保证连续
                lane.append((xs[ci], y))
                prev_y = y
        for i in self.order:
            n = self.nodes[i]
            if n.role == "rover":
                n.patrol = lane
                n.y = self._patrol_y_at(n, n.x)       # 出生点对齐到安全走廊

    def _patrol_y_at(self, n: Node, x: float) -> float:
        """巡逻路径在 x 处的目标 y(线性插值)"""
        path = getattr(n, "patrol", None)
        if not path:
            return self.world.yc(x)
        lo, hi = path[0][0], path[-1][0]
        x = min(max(x, lo), hi)
        for k in range(len(path) - 1):
            x0, y0 = path[k]
            x1, y1 = path[k + 1]
            if x0 <= x <= x1:
                t = (x - x0) / (x1 - x0) if x1 > x0 else 0.0
                return y0 + (y1 - y0) * t
        return path[-1][1]

    def _rover_phys(self, n: Node, dt: float):
        """月球车物理:纵向巡线 + 沿安全巡逻路径避障转向 + 岩壁贴合,
        再加迭代强约束解算,确保任何时刻不穿巨石、不出岩壁、不卡死。"""
        w = self.world
        lb, rb = self._rover_bounds()

        def half_at(x):
            return max(8.0, w.r_at(x) - ROVER_R - ROVER_WALL_M)

        prev_x = n.x
        # 2) 巡逻路径的目标 y(带前瞻)
        ty = self._patrol_y_at(n, n.x + n.dir * PATROL_LOOK)
        # 1) 纵向推进(恒速,路径为连续无碰撞走廊,始终可走) + 端点反弹
        nx = n.x + n.dir * n.speed * dt
        if nx <= lb:
            nx, n.dir = lb, 1
        elif nx >= rb:
            nx, n.dir = rb, -1
        n.x = nx
        # 3) 朝巡逻路径的目标 y 转向(高速横移紧贴走廊)
        step = max(-ROVER_LAT * dt, min(ROVER_LAT * dt, ty - n.y))
        n.y += step
        # 3) 约束迭代(岩壁夹紧 + 巨石径向推出),快速收敛到同时满足
        for _ in range(3):
            yc = w.yc(n.x)
            half = half_at(n.x)
            n.y = max(yc - half, min(yc + half, n.y))
            for b in w.boulders:
                dx, dy = n.x - b["x"], n.y - b["y"]
                d = math.hypot(dx, dy)
                min_d = b["r"] + ROVER_R
                if d < min_d and d > 1e-6:
                    n.x = b["x"] + dx / d * min_d
                    n.y = b["y"] + dy / d * min_d
        # 卡死检测: 被塌方巨石挡住而基本未前进 → 持续一小会就反向撤退
        if abs(n.x - prev_x) < 0.6:
            n._stuck_t = getattr(n, "_stuck_t", 0.0) + dt
            if n._stuck_t > 1.0:
                n.dir *= -1
                n._stuck_t = 0.0
        else:
            n._stuck_t = 0.0

    # ---- 节点移动(自愈重连):只在失去连接时移动,兼顾效率与安全 ----
