# -*- coding: utf-8 -*-
"""引擎职责模块:巡逻走廊求解与指派(CorridorMixin)。

职责范围:按 x 采样求"可通行自由区间"→ BFS 求横贯全管的无碰撞走廊 →
必要时移除堵死通路的巨石 → 把主走廊指派给月球车、上/下偏移走廊分派给两枚探针
(探针在腔室里上下分带、在窄喉道自然并线,两条面包屑链互为冗余)。
依赖:读 Engine 的 world/order;写 rover/probe 的 patrol 与出生 y。
Calls: _col_intervals/_corridor_bfs/_prune_blockers/_lane_from_cells/_assign_patrol_lanes。
"""
import math

from ..nodes import Node
from .constants import *


class CorridorMixin:
    """巡逻走廊 mixin,由 Engine 继承,self 即引擎实例。

    Globals Used: PATROL_XSTEP, ROVER_R, ROVER_WALL_M, CARRIER_OFF。
    Invocation: Engine.reset() → _build_patrol();拖巨石/塌方后 _build_patrol(False)。
    """

    def _col_intervals(self, x: float) -> list:
        """x 处月球车可通行的连续 y 区间(扣除岩壁与每块巨石的遮挡)。

        Args: x=纵向坐标。Returns: [(y_lo, y_hi), ...] 连续自由区间。
        """
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
        另求上/下两条偏移走廊(+CARRIER_OFF / -CARRIER_OFF)分派给两枚探针:
        腔室里上下分带撒成两股互为冗余的链,窄喉道处两带合一并线通过。

        Calls: _patrol_columns/_corridor_bfs/_prune_blockers/_lane_from_cells/
               _probe_lane/_feasible_lane/_assign_patrol_lanes。
        Args: prune=True(地图生成)时若走廊被夹断则移除最堵中线的巨石保证可通行;
              prune=False(用户拖石/塌方)时不删石,月球车在堵点前停下(仍不穿模)。
        Returns: None。
        """
        xs, _ncol = self._patrol_columns()
        ints, goal, came = self._corridor_bfs(xs)
        if prune and goal is None:
            ints, goal, came = self._prune_blockers(xs)
        lane = self._feasible_lane(self._lane_from_cells(xs, ints, goal, came))
        if goal is None:
            # 严格口径夹断(拖石/塌方后常见):退到宽松口径重建 —— 只要物理上有路
            # (S 形斜穿通道)就给月球车一条真的绕行导航线。仍失败(用户把管拖死、
            # 或既有巨石叠加夹断)则逐列贪心链:能延续就延续、断处跳最近带 ——
            # 比穿过石头的中心线兜底好得多(后者让车在石侧无限往返,即
            # "遇空气墙卡住"的观感);真正的死封处车会顶着反向撤退,符合物理。
            _i2, goal2, came2 = self._corridor_bfs(xs, strict=False)
            if goal2 is not None:
                lane = self._feasible_lane(self._lane_from_cells(xs, _i2, goal2, came2))
            else:
                lane = self._feasible_lane(self._greedy_lane(xs))
        up = self._probe_lane(xs, +CARRIER_OFF)
        dn = self._probe_lane(xs, -CARRIER_OFF)
        self._assign_patrol_lanes(lane, up, dn)

    def _greedy_lane(self, xs: list) -> list:
        """兜底导航线:逐列贪心选带 —— 与上一列落点搭接(±6px 容差)的带里取
        最连续者,完全断开时跳到离它最近的带。只服务月球车(有硬约束+脱困+
        反向兜底,跟得住带间跳变);探针不使用。

        Calls: _col_intervals, world.yc。Args: xs=采样列。Returns: [(x,y),...]。
        """
        w = self.world
        lane, prev = [], None
        for x in xs:
            ints = self._col_intervals(x)
            if not ints:
                yc = w.yc(x)
                ints = [(yc - 8.0, yc + 8.0)]
            if prev is None:
                band = min(ints, key=lambda iv: abs((iv[0] + iv[1]) / 2 - w.yc(x)))
                y = min(max(w.yc(x), band[0]), band[1])
            else:
                cont = [iv for iv in ints if iv[0] - 6.0 <= prev <= iv[1] + 6.0]
                band = (min(cont, key=lambda iv: abs((iv[0] + iv[1]) / 2 - prev))
                        if cont else
                        min(ints, key=lambda iv: abs((iv[0] + iv[1]) / 2 - prev)))
                y = min(max(prev, band[0]), band[1])
            lane.append((x, y))
            prev = y
        return lane

    def _probe_lane(self, xs: list, off: float) -> list:
        """求解一枚探针的偏移走廊:偏移 BFS + 带内贴合期望偏移取点 + 坡度整形。

        Globals Used: CARRIER_MAX_SLOPE。Calls: _corridor_bfs/_lane_from_cells/_feasible_lane。
        Args: xs=采样列; off=相对中心线的期望偏移(px)。Returns: 可行导航线。
        """
        ints, goal, came = self._corridor_bfs(xs, off=off)
        return self._feasible_lane(self._lane_from_cells(xs, ints, goal, came, off))

    def _feasible_lane(self, lane: list) -> list:
        """把 BFS 走廊整理成"探针跟得上"的导航线。

        BFS 只在采样列之间要求自由带"有重叠",相邻列的 y 仍可能突变几十像素;
        而探针限速 MOVE_V(每 tick 最多 3px),跟不上突变 → 落在带外、被巨石夹住。
        这里从右向左回拉:每点的 y 不得偏离下一列超过 max_slope·Δx,再夹回该列自由带。
        夹带时保持该点**原走廊的带归属**(ref_y):否则回拉把 y 拽进带间缝隙后,
        "就近吸附"会跳到另一条带,走廊凭空出现 100+px 带间跳变 —— 探针照着走
        就一头撞在巨石上(seed 7 PROBE-2 在 x=379 全程卡死的根因)。
        结果是一条坡度可控、逐点都在原自由带内的可行走廊。

        Globals Used: CARRIER_MAX_SLOPE。Calls: _col_intervals。
        Args: lane=[(x,y),...]。Returns: 整理后的走廊。
        """
        if not lane:
            return lane
        pts = list(lane)
        for i in range(len(pts) - 2, -1, -1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            lim = CARRIER_MAX_SLOPE * max(1.0, x1 - x0)
            y = min(max(y0, y1 - lim), y1 + lim)
            pts[i] = (x0, self._clamp_into_band(x0, y, ref_y=y0))
        return pts

    def _clamp_into_band(self, x: float, y: float, ref_y: float | None = None) -> float:
        """把 y 夹进 x 处"包含/最接近它的自由带"内(带内留 0.5px 余量)。

        ref_y 给出时优先选择包含 ref_y 的自由带(保持原走廊的带归属,见 _feasible_lane)。

        Calls: _col_intervals。Args: x/y=待夹点; ref_y=该列原走廊高度。Returns: 夹取后的 y。
        """
        ints = self._col_intervals(x)
        if not ints:
            return y
        band = None
        if ref_y is not None:
            for iv in ints:
                if iv[0] - 2.0 <= ref_y <= iv[1] + 2.0:
                    band = iv
                    break
        if band is None:
            for iv in ints:
                if iv[0] - 2.0 <= y <= iv[1] + 2.0:
                    band = iv
                    break
        if band is None:
            band = min(ints, key=lambda iv: abs((iv[0] + iv[1]) / 2 - y))
        return min(max(y, band[0] + 0.5), band[1] - 0.5)

    def _patrol_columns(self) -> tuple:
        """巡逻走廊的采样列。Globals Used: PATROL_XSTEP。
        Args: None。Returns: (xs 列坐标列表, 列数)。"""
        lb, rb = self._rover_bounds()
        xs = []
        x = lb
        while x <= rb + 1:
            xs.append(round(x, 1))
            x += PATROL_XSTEP
        return xs, len(xs)

    def _corridor_bfs(self, xs: list, off: float = 0.0, strict: bool = True) -> tuple:
        """在相邻列的"自由区间"之间做 BFS,求一条从左端贯通到右端的走廊。

        同一套算法既求月球车主走廊(off=0,贴中心线),也求两枚探针的上下偏移走廊:
        BFS 只允许连接"纵向有重叠"的自由带,因此得到的走廊天然连续 ——
        不会出现"逐列独立取最接近目标高度的自由带"那种上下横跳(探针纵向鬼畜的根因)。

        连边校验(两档口径):
          strict=True(建走廊用):相邻列带直接重叠 >0 **且**列间中点存在与两侧
            都搭接的带 —— 既要物理可通,也要带连续(限速探针/月球车跟得上,
            S 形斜穿通道虽然物理可走但走廊会在带间跳变,跟线必然卡死);
          strict=False(连通性预检用,如塌方落石求解):只要求中点链搭接
            (10px 分辨率的物理可通行),回答"有没有路",不管好不好跟。
        只看两端列(旧 -8 容差)会漏判两列之间完全闭合的采样混叠 ——
        走廊把探针引进闭合凹袋就地冻结(seed 20 PROBE-1 卡死 2197 tick 的根因)。

        Args: xs=采样列坐标; off=期望高度相对中心线的偏移(px);
              strict=True 要求带连续(建走廊)/ False 只要求物理连通(预检)。
        Returns: (ints, goal, came),goal 为 None 表示被夹断。
        """
        from collections import deque
        w = self.world
        ints = [self._col_intervals(x) for x in xs]
        for i in range(len(ints)):
            if not ints[i]:
                yc = w.yc(xs[i])
                ints[i] = [(yc - 8.0, yc + 8.0)]
        mids = [self._col_intervals((xs[i] + xs[i + 1]) / 2)
                for i in range(len(xs) - 1)]

        def bridged(ci: int, a: tuple, b: tuple) -> bool:
            if strict and min(a[1], b[1]) - max(a[0], b[0]) <= 0:
                return False
            for m in mids[ci]:
                if min(a[1], m[1]) - max(a[0], m[0]) > 0 \
                        and min(m[1], b[1]) - max(m[0], b[0]) > 0:
                    return True
            return False

        want0 = w.yc(xs[0]) + off
        scol = min(ints[0], key=lambda iv: abs((iv[0] + iv[1]) / 2 - want0))
        start = (0, ints[0].index(scol))
        came = {start: None}
        queue = deque([start])
        goal, ncol = None, len(xs)
        while queue:
            ci, ii = queue.popleft()
            if ci == ncol - 1:
                goal = (ci, ii)
                break
            a = ints[ci][ii]
            cands = [(jj, bv) for jj, bv in enumerate(ints[ci + 1])
                     if bridged(ci, a, bv)]
            if off:      # 偏移走廊:优先走更贴近目标高度的自由带
                want = w.yc(xs[ci + 1]) + off
                cands.sort(key=lambda t: abs((t[1][0] + t[1][1]) / 2 - want))
            for jj, _bv in cands:
                nc = (ci + 1, jj)
                if nc not in came:
                    came[nc] = (ci, ii)
                    queue.append(nc)
        return ints, goal, came

    def _prune_blockers(self, xs: list) -> tuple:
        """走廊被夹断时,移除最堵中线的大巨石(最多 6 块)并重试。

        Args: xs=采样列坐标。Returns: 重新求得后的 (ints, goal, came)。
        """
        ints, goal, came = self._corridor_bfs(xs)
        removed = 0
        while goal is None and removed < 6 and self.world.boulders:
            bs = self.world.boulders
            bi = max(range(len(bs)),
                     key=lambda i: (bs[i]["r"] - abs(bs[i]["y"] - self.world.yc(bs[i]["x"]))))
            bs.pop(bi)
            removed += 1
            ints, goal, came = self._corridor_bfs(xs)
        if removed:
            self._recompute_static()   # 巨石变少,重新算视距/链路
        return ints, goal, came

    def _lane_from_cells(self, xs: list, ints: list, goal, came,
                         off: float = 0.0) -> list:
        """把 BFS 得到的格子路径转成 (x, y) 导航线;被夹断时兜底走中心线。

        每列目标高度 = 该列中心线 yc+off **夹进所在自由带**,而不是取自由带中点:
        取中点(旧实现)会让所有走廊在宽阔腔室里都塌向管道中心 —— 两枚探针牵成
        一条线的根因;夹"期望偏移"则上/下走廊在腔室里保持分带,被巨石挤开时
        沿 BFS 连通带绕行、带变宽后自动回到期望偏移。连续性由 _feasible_lane 的
        坡度整形保证(探针限速 10cm/s 跟得上)。

        Args: xs=列坐标; ints=每列自由区间; goal/came=BFS 结果; off=期望偏移(px)。
        Returns: [(x, y), ...] 走廊导航线。
        """
        w = self.world
        if goal is None:
            return [(x, w.yc(x)) for x in xs]
        cells = []
        cur = goal
        while cur is not None:
            cells.append(cur)
            cur = came[cur]
        cells.reverse()
        lane = []
        for (ci, ii) in cells:
            a, b = ints[ci][ii]
            want = w.yc(xs[ci]) + off
            lane.append((xs[ci], min(max(want, a), b)))
        return lane

    def _assign_patrol_lanes(self, lane: list, lane_up: list, lane_dn: list):
        """走廊分派:月球车走主走廊(贴中心线);PROBE-1 走上带(+CARRIER_OFF)、
        PROBE-2 走下带(-CARRIER_OFF) —— 两枚探针在腔室里上下分开推进,
        而不是前后脚跟在同一条线上,撒出的两股面包屑链横向互为冗余。

        Args: lane=主走廊; lane_up/lane_dn=上/下偏移走廊。Returns: None。
        """
        for i in self.order:
            n = self.nodes[i]
            if n.role == "rover":
                n.patrol = lane
                n.y = self._patrol_y_at(n, n.x)       # 出生点对齐到安全走廊
        for c in getattr(self, "_carriers", []):
            c.patrol = lane_dn if c.id.endswith("2") else lane_up
            c.y = self._patrol_y_at(c, c.x)

    def _patrol_y_at(self, n: Node, x: float) -> float:
        """巡逻路径在 x 处的目标 y(线性插值)。

        Args: n=移动体; x=纵向坐标。Returns: 目标 y。
        """
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
