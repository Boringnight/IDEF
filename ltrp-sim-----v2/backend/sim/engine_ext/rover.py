# -*- coding: utf-8 -*-
"""引擎职责模块:月球车/探针推进物理(RoverMotionMixin)。

职责范围:月球车沿巡逻走廊巡线推进、避障强约束、卡死反向;探针单向(只向右)推进。
走廊求解与指派见 corridor.py(CorridorMixin)。
依赖:读 Engine 的 world/nodes/order;写 rover/probe 的 x/y/dir。
Calls: _patrol_y_at/_rover_constrain/_rover_stuck_check/_col_intervals。
"""
import math

from ..nodes import Node
from .constants import *


class RoverMotionMixin:
    """月球车/探针物理 mixin,由 Engine 继承,self 即引擎实例。

    Globals Used: MOVE_V, PATROL_LOOK, ROVER_R, ROVER_WALL_M。
    Invocation: Engine.step() → _rover_phys(部署完成后) / DeployMixin → _carrier_phys。
    """

    def _rover_bounds(self):
        """月球车/探针可巡逻的 x 范围。Args: None。Returns: (左端, 右端)。"""
        return self.world.rover_bounds()

    def _rover_phys(self, n: Node, dt: float):
        """月球车物理:纵向巡线 + 沿安全巡逻路径避障转向,硬性不穿石/不穿壁。

        速度:纵向 ROVER_V(55cm/s)、横向同样限速 —— 月球车是自愈的"信使",
        把断裂对端位置摆渡给失联节点;速度过慢会让失联侧长期拿不到对端坐标。

        硬性物理约束:本 tick 的候选落点(推进 x + 转向 y)必须落在自由带内
        (与探针同一真值源 _col_intervals)才真正移动;被巨石/岩壁挡住时退化
        为纯纵向 / 纯横向对齐,再不行本 tick 原地不动(宁停不穿,卡死检测会
        反向撤退)。原实现"先整步推进、再把车径向弹出石面"是**事后修正**:
        贴壁挤压时岩壁夹紧与巨石推出互相打架,车会在石头里外瞬移,
        视觉上即"巡查机器人穿过塌方落石"。_rover_constrain 仅保留为兜底
        (处理拖放巨石直接压到车顶等外部瞬变)。

        Globals Used: ROVER_V, PATROL_LOOK。
        Calls: _patrol_y_at/_carrier_blocked/_rover_constrain/_rover_stuck_check。
        Args: n=月球车节点; dt=物理步长(秒)。Returns: None。
        """
        lb, rb = self._rover_bounds()
        prev_x = n.x
        budget = ROVER_V * dt
        nx = n.x + n.dir * budget
        if nx <= lb:
            # 到端点折返并**至少全速朝管内退一步**:折返目标若恰好等于当前位置
            # (车已停在 bound∓budget 处),x 每拍零推进,卡死检测又把刚扳正的
            # 方向翻回去 → 边界死锁(实测两辆车在边界冻结 1200+ tick)
            nx, n.dir = min(rb, max(lb + budget, n.x + budget)), 1
        elif nx >= rb:
            nx, n.dir = max(lb, min(rb - budget, n.x - budget)), -1
        # 朝巡逻路径的目标 y 转向(横向限速与纵向同级 → 合速度 ≤ √2·ROVER_V)
        ty = self._patrol_y_at(n, n.x + n.dir * PATROL_LOOK)
        ny = n.y + max(-budget, min(budget, ty - n.y))
        if self._carrier_blocked(nx, ny):
            # 前瞻目标被巨石/急弯挡住(目标 y 落在本列岩壁或石身里)→ 夹进
            # "与当前带可达"的带内再按限速趋近(与探针同款); 没有它, 管道入口
            # 急弯处全移动/纵向对齐全被否决, 车在边界上以 0.05px/tick 冻结
            fixed = self._carrier_target_y(n, nx)
            if fixed is not None:
                ny = n.y + max(-budget, min(budget, fixed - n.y))
        if not self._carrier_blocked(nx, ny):
            n.x, n.y = nx, ny
        elif not self._carrier_blocked(n.x, ny):
            n.y = ny                                 # 只纵向对齐(绕行石沿)
        elif not self._carrier_blocked(nx, n.y):
            n.x = nx                                 # 只横向推进(贴石滑行)
        else:
            # 空气墙脱困:三个候选全被否决 —— 当前位置本身落在"看不见的禁区"里
            # (拖石/塌方压顶后被 _rover_constrain 弹到石面 2px 环带内,或被挤进
            # 26px 靠壁余量),逐 tick 落点检查会让车在自由带 56px 外原地冻结
            # (实测 300 tick 位移 0.0px)。朝最近自由带纵向逃逸,允许多 tick 穿越
            # 禁区(环带/余量里并没有真障碍),_rover_constrain 保证不真穿石。
            ey = self._escape_y(n.x, n.y)
            if ey is None:
                ey = self._escape_y(nx, n.y)
            if ey is not None:
                n.y += max(-budget, min(budget, ey - n.y))
                if not self._carrier_blocked(nx, n.y):
                    n.x = nx
        self._rover_constrain(n)                     # 兜底:外部瞬变(拖石压顶)时弹出
        self._rover_stuck_check(n, prev_x, dt)

    def _rover_constrain(self, n: Node):
        """迭代强约束:岩壁夹紧 + 巨石径向推出,快速收敛到同时满足。

        巨石推出半径取 b.r + ROVER_R + 2.5(而非 +ROVER_R):弹出落点必须落在
        _col_intervals 的禁区(b.r + ROVER_R + 2)之外,否则车被弹到"石面 2px
        环带"里,下一拍所有移动候选都被空气墙否决。

        Globals Used: ROVER_R, ROVER_WALL_M。Args: n=移动体。Returns: None。
        """
        w = self.world
        for _ in range(3):
            yc = w.yc(n.x)
            half = max(8.0, w.r_at(n.x) - ROVER_R - ROVER_WALL_M)
            n.y = max(yc - half, min(yc + half, n.y))
            for b in w.boulders:
                dx, dy = n.x - b["x"], n.y - b["y"]
                d = math.hypot(dx, dy)
                min_d = b["r"] + ROVER_R + 2.5
                if d < min_d and d > 1e-6:
                    n.x = b["x"] + dx / d * min_d
                    n.y = b["y"] + dy / d * min_d

    def _escape_y(self, x: float, y: float):
        """x 列内离 y 最近的自由带目标(带内留 1px 余量)。

        Calls: _col_intervals。Args: x/y=当前位置。Returns: 逃逸目标 y;
        该列完全无自由带时返回 None。
        """
        ints = self._col_intervals(x)
        if not ints:
            return None
        band = min(ints, key=lambda iv: abs((iv[0] + iv[1]) / 2 - y))
        return min(max(y, band[0] + 1.0), band[1] - 1.0)

    def _rover_stuck_check(self, n: Node, prev_x: float, dt: float):
        """卡死检测: 被塌方巨石挡住而基本未前进 → 持续一小会就反向撤退。

        判定用**总位移**(x+y):带切换点月球车需要连续数拍纵向机动才能穿越
        (x 暂时不动、y 每拍都在动)——若只看 |Δx|,卡死检测会在机动中把方向
        每拍翻转,前瞻目标随之反向,车在两带之间 y 向乒乓永不收敛(实测
        seed 2 中段喉道切断后两辆车原地振荡 800+ tick,自愈信使停摆)。
        真正的死路 = x 和 y 都不动 → 才反向。

        Args: n=移动体; prev_x=推进前 x; dt=物理步长(秒)。Returns: None。
        """
        prev_y = getattr(n, "_prev_y_stuck", n.y)
        if math.hypot(n.x - prev_x, n.y - prev_y) < 0.6:
            n._stuck_t = getattr(n, "_stuck_t", 0.0) + dt
            if n._stuck_t > 1.0:
                n.dir *= -1
                n._stuck_t = 0.0
        else:
            n._stuck_t = 0.0
        n._prev_y_stuck = n.y

    def _carrier_phys(self, c, dt: float):
        """探针(面包屑载体)物理:沿自由带走廊向右推进,合速度恒 ≤ MOVE_V(10cm/s),绝不穿石/出壁。

        与 _rover_phys 的关键区别: 探针 x 单调不减,没有"到端点反向巡逻/卡死反向"逻辑
        (唯一例外:卡死看门狗触发的短暂倒车脱困,见下)。

        速度:先求本 tick 期望落点(横向预算内的最右 x + 走廊目标 y),再朝它按单位矢量前进 ——
        纵向与横向共用同一个位移预算,所以合成速度恒为 MOVE_V,不会出现"纵向鬼畜"。
        (原实现直接把 y 设为走廊值,巨石把走廊切成上下两条自由带时 y 会整段瞬移,既超速又穿石。)

        安全:走廊点若被巨石吃掉,先用 _carrier_target_y 退到"与当前带重叠最大的自由带";
        仍不可达则只做纵向对齐,再不行则本 tick 原地不动(宁停不穿)。
        末尾的靠壁兜底夹紧同样计入速度预算(限用本 tick 剩余位移),任何情况下
        单 tick 合位移都不超过 MOVE_V·dt —— xy 合速度恒 ≤ 10cm/s。

        卡死看门狗:巨石与岩壁夹出的闭合凹袋会让前进方向所有走法都被判不可通行
        (即使走廊已做中点校验,仍可能存在更细尺度的夹缝)→ 停滞 CARRIER_STALL_N
        tick 后沿当前带倒退 CARRIER_RETREAT px 脱困(真实月球车的倒车绕行),
        退够距离后自动恢复前进;倒退期间不撤链(与上一颗距离只会缩小)。

        Globals Used: MOVE_V, CARRIER_STALL_N, CARRIER_RETREAT, ROVER_R, ROVER_WALL_M。
        Calls: _patrol_y_at/_carrier_target_y/_carrier_blocked/_carrier_clamp_y。
        Args: c=探针节点; dt=物理步长(秒)。Returns: None。
        """
        w = self.world
        lb, rb = self._rover_bounds()
        ox, oy = c.x, c.y
        retreating = getattr(c, "_retreat_to", None) is not None
        if retreating:
            nx = max(c.x - MOVE_V * dt, lb)          # 倒车脱困:只求回到带宽足够的列
            ty = self._carrier_target_y(c, nx)
            ny = ty if ty is not None else c.y       # 跟随同侧可达带,不追走廊(走廊可能仍指向凹袋)
            if c.x <= c._retreat_to + 0.5:
                c._retreat_to = None                 # 退够距离,恢复前进
        else:
            nx = min(c.x + MOVE_V * dt, rb)          # 横向预算内的最右 x
            ny = self._patrol_y_at(c, nx)            # 默认:直接跟随走廊(绝大多数路段可用)
            if self._carrier_blocked(nx, ny):        # 走廊点被巨石吃掉 → 退到"可达同侧自由带"内
                fixed = self._carrier_target_y(c, nx)
                if fixed is not None:
                    ny = fixed
        dx, dy = nx - c.x, ny - c.y
        d = math.hypot(dx, dy)
        if d > 1e-9:
            step = min(MOVE_V * dt, d)               # 合速度恒 MOVE_V(横向纵向分量按单位矢量分配)
            px, py = c.x + dx / d * step, c.y + dy / d * step
            if not self._carrier_blocked(px, py):
                c.x, c.y = px, py
            elif not self._carrier_blocked(c.x, py):
                c.y = py                             # 只纵向对齐,本 tick 不前进
            elif abs(dx) > 1e-9 and not self._carrier_blocked(
                    c.x + math.copysign(step, dx), c.y):
                # 纯横向滑行:斜穿/纵穿都被巨石挡住时,沿当前自由带水平滑过石尖 ——
                # 巨石只挡截面上一段,带宽在石尖右侧重新合并后即可恢复纵向对齐。
                # 没有这条回退,走廊带切换处的探针会对着巨石尖端永久卡死(seed 7 PROBE-2)。
                c.x += math.copysign(step, dx)
        c.x = min(max(c.x, w.W * 0.02), w.W * 0.98)
        # 空气墙脱困(探针版):被外力压进禁区(拖石/塌方压顶)时,带内候选全部被
        # 否决、看门狗倒车也无处可去 —— 用本 tick 剩余速度预算朝最近自由带纵向
        # 逃逸(多 tick 穿越,合速度仍恒 ≤ MOVE_V)
        if self._carrier_blocked(c.x, c.y):
            ey = self._escape_y(c.x, c.y)
            if ey is not None:
                rem0 = max(0.0, MOVE_V * dt - math.hypot(c.x - ox, c.y - oy))
                c.y += max(-rem0, min(rem0, ey - c.y))
        # 靠壁兜底夹紧:只允许花掉本 tick 剩余的位移预算,避免喉道处带宽骤变时 y 单 tick 瞬移
        rem = MOVE_V * dt - math.hypot(c.x - ox, c.y - oy)
        cy = self._carrier_clamp_y(c.x, c.y)
        if abs(cy - c.y) > rem:
            cy = c.y + (rem if cy > c.y else -rem)
        c.y = cy
        # 看门狗计数:未到终点却原地不动 → 累计;倒车中不计(倒车本身就是脱困动作)
        if abs(c.x - ox) < 0.3 and c.x < rb - 1.0 and not retreating:
            c._stall_n = getattr(c, "_stall_n", 0) + 1
            if c._stall_n >= CARRIER_STALL_N:
                c._retreat_to = max(lb, c.x - CARRIER_RETREAT)
                c._stall_n = 0
        else:
            c._stall_n = 0

    def _carrier_blocked(self, x: float, y: float) -> bool:
        """该点是否不可通行 —— 以 _col_intervals 的自由带为**唯一真值来源**。

        早先这里独立用"圆半径 + 余量"判定,与 _col_intervals 的带边界存在浮点口径差,
        导致"自由带边缘上的点"被判为撞石 → 探针贴着巨石边界原地卡死(seed 7/11 均复现)。
        现在统一问自由带:落在任一带内(含 0.5px 容差)即可通行。

        Calls: _col_intervals。Args: x/y=待判定点。Returns: True=不可通行。
        """
        for lo, hi in self._col_intervals(x):
            if lo - 0.5 <= y <= hi + 0.5:
                return False
        return True

    def _carrier_target_y(self, c, nx: float):
        """求探针在 x=nx 处的纵向目标:选与"当前自由带"重叠最大的那条带,再在其中贴近走廊高度。

        只按"离走廊高度最近"选带,会在巨石把截面切开的 x 处要求探针横穿巨石 →
        原地卡死(实测 seed 7 两枚探针卡在 x=372 全程不动、seed 11 的 PROBE-2 卡在 x=249)。
        按"与当前带重叠最大"选带,目标点必然落在探针本 tick 可达的同一侧,不会跨石。

        Calls: _col_intervals/_patrol_y_at。
        Args: c=探针; nx=目标 x。Returns: 目标 y; 该列无自由带时返回 None。
        """
        ints_next = self._col_intervals(nx)
        if not ints_next:
            return None
        ints_now = self._col_intervals(c.x)
        cur = None
        for iv in ints_now:
            if iv[0] - 2.0 <= c.y <= iv[1] + 2.0:
                cur = iv
                break
        if cur is None and ints_now:
            cur = min(ints_now, key=lambda iv: abs((iv[0] + iv[1]) / 2 - c.y))
        if cur is None:
            return None
        band = max(ints_next, key=lambda iv: min(cur[1], iv[1]) - max(cur[0], iv[0]))
        lo, hi = band[0] + CARRIER_INSET, band[1] - CARRIER_INSET
        if lo > hi:                                  # 带太窄:退到带中心
            lo = hi = (band[0] + band[1]) / 2
        return min(max(self._patrol_y_at(c, nx), lo), hi)

    def _carrier_clamp_y(self, x: float, y: float) -> float:
        """兜底夹紧:把 y 夹回该 x 处的靠壁安全带内。

        Globals Used: ROVER_R, ROVER_WALL_M。Args: x/y=位置。Returns: 夹紧后的 y。
        """
        w = self.world
        yc = w.yc(x)
        half = max(8.0, w.r_at(x) - ROVER_R - ROVER_WALL_M)
        return max(yc - half, min(yc + half, y))
