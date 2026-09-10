# -*- coding: utf-8 -*-
"""引擎职责模块:首次部署 / 面包屑撒布(DeployMixin)。

职责范围:两枚探针(上/下分带)从基站向右推进,每前进 ≥DEPLOY_GAP 撒下一颗库存道钉
(离线→上线);落点经四级防放置求解:在管内、离岩壁/巨石保距、与链上上一颗保视距、
与既有道钉保间距 —— 保证撒出的链不被地形挡断;超时兜底沿链铺开收尾。
依赖:读 Engine 的 world/nodes/_carriers/t;写库存 Node 的 x/y/alive/_pending_deploy/domain。
Calls: _carrier_phys/_col_intervals/_recompute_static/emit/_patrol_y_at, world.los/inside/domain_of。
"""
import math

from ..nodes import RANGE
from .constants import *


class DeployMixin:
    """首次部署 mixin,由 Engine 继承,self 即引擎实例。

    Globals Used: MOVE_V, DEPLOY_GAP, RANGE。
    Invocation: Engine.step() 每 tick 首阶段调用 _deploy_carriers(dts)。
    """

    def _deploy_carriers(self, dt: float):
        """面包屑真部署:两条探针从基地向右推进,每前进 ≥DEPLOY_GAP 撒下一颗道钉。
        未撒布的道钉保持 alive=False/_pending_deploy,全程不参与信标/路由/数据面/统计;
        撒布后置 alive=True 并重算静态链路,让新道钉实时接入自组织网。

        Globals Used: MOVE_V。
        Args: dt=物理步长(秒)。Returns: None。
        """
        if self.deploy_done:
            return
        end = self._rover_bounds()[1]
        # 部署超时兜底: 探针全程耗时按**走廊实际长度**估算(管道蜿蜒 + 纵向爬升都要算),
        # 而不是只算 x 跨度 —— 否则爬坡路段会被误判为"卡死"而提前收尾,把剩余道钉堆在一点。
        # (用模拟秒而非现实 tick, 避免时间缩放后误触发)
        need_s = max((self._lane_length(getattr(c, "patrol", None))
                      for c in getattr(self, "_carriers", []) or [None]), default=0.0)
        if need_s <= 0.0:
            need_s = end - self.nodes["BASE-00"].x
        if self.t > max(DEPLOY_TIMEOUT_MIN, need_s / MOVE_V * 1.6):
            self._deploy_finish("首次部署完成:两枚探针已撒布全程(含收尾),面包屑网络就位")
            return
        basex = round(self.nodes["BASE-00"].x, 1)
        basey = round(self.nodes["BASE-00"].y, 1)
        dp = self._deploy_gap_init(end, basex)
        for c in getattr(self, "_carriers", []):
            self._carrier_advance(c, dt, end, dp, basex, basey)
        if all(not getattr(c, "onboard", None) for c in getattr(self, "_carriers", [])):
            self._deploy_finish("首次部署完成:两枚探针已沿熔岩管各自撒布,形成互为冗余的面包屑网络")

    @staticmethod
    def _lane_length(path) -> float:
        """走廊折线总长度(px)。Args: path=[(x,y),...]。Returns: 长度; 无路径返回 0。"""
        if not path:
            return 0.0
        return sum(math.hypot(path[k + 1][0] - path[k][0], path[k + 1][1] - path[k][1])
                   for k in range(len(path) - 1))

    def _deploy_gap_init(self, end: float, basex: float) -> float:
        """动态撒布间距(部署开始时固定一次): 每条链节点均匀铺满各自走廊, 不重叠。

        Globals Used: RANGE。
        Args: end=推进终点 x; basex=基站 x。Returns: 本局固定间距(px)。
        """
        if getattr(self, "_deploy_gap", None) is None:
            per = 1
            if getattr(self, "_carriers", None):
                per = max(1, len(self._carriers[0].onboard))
            self._deploy_gap = max(50.0, min(RANGE * 0.8, (end - basex) / (per + 1) * 0.95))
        return self._deploy_gap

    def _carrier_advance(self, c, dt: float, end: float, dp: float,
                         basex: float, basey: float):
        """单条探针推进:先做探针物理,再按间距撒布,到终点就地撒完,最后刷新探索前沿。

        两枚探针**相位错开**:第二枚的首颗只走半个间距,之后仍按 dp 撒布 ——
        两条链的落点交替插入,合成间距 ≈ dp/2(原实现两枚都从基地量起,落点几乎重合,
        等于把 28 颗道钉撒成 14 个点,巨石密集处必然出现视距断口 → 永久分区)。
        另有"链保活":一旦与上一颗的视距已断,不再等间距,立即补一颗。

        Args: c=探针节点; dt=步长; end=终点 x; dp=撒布间距; basex/basey=基站坐标。
        Returns: None。
        """
        self._carrier_phys(c, dt)
        lx, ly = c.last_drop
        need = dp * c.phase if getattr(c, "_first_drop", True) else dp
        while getattr(c, "onboard", None) and math.hypot(c.x - lx, c.y - ly) >= need:
            # 落点由 _drop_spot 防放置求解(保距+保视距,含跨链借视距),始终贴着探针路径
            ref = (basex, basey) if (round(lx, 1) == basex and round(ly, 1) == basey) \
                else (lx, ly)
            if not self._drop_one(c, ref):
                break        # 无连通落点(LOS 阴影/库存空):绝不堆叠,推进后下 tick 重试
            lx, ly = c.last_drop
            need = dp
            c._first_drop = False
        # 链保活(仅距离触发):载体离上一颗已逼近可靠链路上限(RANGE·LINK_SAFE,
        # 0.8R 以外 SNR 余量快速恶化)→ 立即补一颗,不等间距。弯道处 LOS 暂时被洞壁
        # 挡住**不算断口** —— _drop_spot 本身强制"落点与 ref 有视距",等间距撒布时
        # 自然回退到弯道内侧的可见点;若在这里按 LOS 触发,弯道处会每 tick 连撒一串
        # 锯齿点,把库存耗光(实测 seed 7:PROBE-2 前 10 颗全堆在基站附近)。
        if getattr(c, "onboard", None) and math.hypot(c.x - lx, c.y - ly) > RANGE * LINK_SAFE:
            self._drop_one(c, (lx, ly))
            c._first_drop = False
        if c.x >= end - 1 and getattr(c, "onboard", None):
            self._drop_remaining(c)
        # 探索前沿 = 载体已推进到的最右
        self.deploy_front = max(self.deploy_front, c.x)

    def _drop_one(self, c, ref) -> bool:
        """撒下一颗库存道钉(落点由 _drop_spot 防放置求解)。

        连"仅物理安全"的落点都找不到时**不撒**(返回 False):绝不为了撒而堆在
        上一颗的位置上 —— 堆叠点互相遮挡、毫无拓扑增益,还会瞬间耗光库存
        (实测旧兜底在 LOS 阴影里连撒 11 颗同一点)。

        Args: c=探针; ref=(x,y) 上一颗/基站。Returns: True=已撒出一颗。
        """
        if not getattr(c, "onboard", None):
            return False
        spot = self._drop_spot(c, ref)
        if spot is None:
            return False
        s = c.onboard.pop(0)
        px, py = spot
        s.x, s.y = round(px, 1), round(py, 1)
        s.alive = True
        s._pending_deploy = False
        s.domain, s.border = self.world.domain_of(s.x)
        # 部署即给"朝网方向"先验:锚点=载体当前位置(载体始终连在基地侧)。
        # 这样即便该节点此后从未拿到过路由(被断口隔开),也能靠 _last_anchor 触发自愈。
        s._last_anchor = (c.x, c.y)
        c.last_drop = (s.x, s.y)
        c.stock = len(c.onboard)
        self._recompute_static()
        self.emit("node_deploy", "info",
                  f"{c.id} 撒布 {s.id} @({s.x:.0f},{s.y:.0f}) · 载体余 {len(c.onboard)} 颗", False)
        return True

    def _drop_spot(self, c, ref):
        """求一颗道钉的安全落点(防放置核心,两轮×有序候选):

        候选(按优先级):① 探针当前位置 → ② 沿 ref→探针连线回退采样 →
        ③ 探针邻近列(±80px)自由带内扫描(贴近走廊高度优先)。
        判定(两轮,逐级放宽):
          R1 与 **ref**(链上上一颗)视距可达且距离 ≤RANGE·LINK_SAFE;
          R2 与**任一已有基站路由的道钉/基站**可达(跨链借视距:本链被巨石/弯道
             遮挡时,落点仍可挂在另一股链上,全网连通优先于链形完美;
             锚点只认已路由节点,杜绝借到孤岛上)。
        全部轮次共同要求:管内离岩壁 ≥SPIKE_WALL_M、离巨石 ≥SPIKE_BOULDER_M,
        并尽量与已放置道钉保持 ≥SPIKE_MIN_SEP;达不到间距时取间距最大者。
        两轮都失败(长视距阴影)→ None:库存随载体穿过阴影后再接回,
        绝不放无链路孤钉(孤钉触发 pocket 自愈集体漫游,反而撕碎网络)。

        Globals Used: RANGE, LINK_SAFE, SPIKE_WALL_M, SPIKE_BOULDER_M, SPIKE_MIN_SEP。
        Calls: _col_intervals/_patrol_y_at/_is_routed, world.inside/los。
        Args: c=探针节点; ref=(x,y) 链上上一颗(或基站)。Returns: (px,py) 或 None。
        """
        w = self.world

        def safe(x, y) -> bool:
            return w.inside(x, y, SPIKE_WALL_M) and not any(
                math.hypot(x - b["x"], y - b["y"]) < b["r"] + SPIKE_BOULDER_M
                for b in w.boulders)

        anchors = [(n.x, n.y) for n in self.nodes.values()
                   if n.alive and n.role in ("spike", "base") and self._is_routed(n)]

        def linked_to_ref(x, y) -> bool:
            return (math.hypot(x - ref[0], y - ref[1]) <= RANGE * LINK_SAFE
                    and w.los((x, y), ref))

        def linked_to_any(x, y) -> bool:
            return any(math.hypot(x - a[0], y - a[1]) <= RANGE * LINK_SAFE
                       and w.los((x, y), a) for a in anchors)

        placed = [(n.x, n.y) for n in self.nodes.values()
                  if n.alive and n.role == "spike"]

        def spaced(x, y, floor: float) -> bool:
            return all(math.hypot(x - p[0], y - p[1]) >= floor for p in placed)

        def nearest_gap(x, y) -> float:
            return min((math.hypot(x - p[0], y - p[1]) for p in placed), default=1e9)

        # 有序候选序列:① 当前位置 → ② 连线回退(开阔段有效) → ③ 列扫描。
        # 列扫描覆盖 **ref→载体全程**(而不只是载体附近 ±80px):急弯/盲区里
        # "上一颗→载体"的弦线会切出岩壁(弦上的回退点全部 unsafe),而沿走廊
        # 的列点必然在管内 —— 链正是靠这些列点以 40~80px 短跳爬过弯道的。
        cands = [(c.x, c.y)]
        for tt in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2):
            cands.append((ref[0] + (c.x - ref[0]) * tt,
                          ref[1] + (c.y - ref[1]) * tt))
        x_lo = min(ref[0] + 20.0, c.x)
        dxs = [0.0]
        k = 20.0
        while c.x - k >= x_lo:
            dxs.append(-k)
            k += 20.0
        dxs.extend((20.0, 40.0, 60.0, 80.0))
        for dx in dxs:
            xx = c.x + dx
            ty = self._patrol_y_at(c, xx)
            for lo, hi in self._col_intervals(xx):
                span = hi - lo
                for yy in (min(max(ty, lo + 2), hi - 2), (lo + hi) / 2,
                           lo + span * 0.25, hi - span * 0.25):
                    cands.append((xx, yy))
        # 两轮连通判定:先保本链(ref),再跨链借任一**已有基站路由**节点的视距。
        # 锚点只认已路由节点(基站天然在列):否则跨链可能"借"到一座无路由的孤岛上,
        # 落点看似有伴、实则整座孤岛与主干隔绝,自愈还得拖着它们走(实测分区 5→10)。
        for link in (linked_to_ref, linked_to_any):
            best = None
            for x, y in cands:
                if not (safe(x, y) and link(x, y)):
                    continue
                if spaced(x, y, SPIKE_MIN_SEP):
                    return x, y
                gap = nearest_gap(x, y)      # 达不到最小间距:记间距最大者做次优
                if best is None or gap > best[0]:
                    best = (gap, x, y)
            if best is not None:
                return best[1], best[2]
        # 第三轮(盲区短跳):两轮都失败 = 载体进入了视距盲区(管道急弯/巨石群,
        # 连基站都被挡)。此时退化为**短跳链**:在"与 ref 仍有视距"的候选里取
        # 距离 ≤SPIKE_HOP_MAX 的最远一点 —— 短距 LOS 在窄喉道里几乎必然成立,
        # 链以 40~80px 一跳沿走廊爬过盲区,爬出后 DSDV 路由沿短跳链传播,R2
        # 锚点随之恢复,撒布回到正常间距。这正是"基站被弯道挡死"地图
        # (seed 1/2/3/5/13: 基站沿走廊 100~700px 全部不可见)唯一的组网方式;
        # 若在这里返回 None,探针会把全部库存原样背到管尾(实测 SPIKE-01 落在
        # x=2970,全程 2780px 颗粒未撒)。
        best = None
        for x, y in cands:
            d_ref = math.hypot(x - ref[0], y - ref[1])
            if not (SPIKE_HOP_MIN <= d_ref <= SPIKE_HOP_MAX) \
                    or not safe(x, y) or not w.los((x, y), ref):
                continue
            if spaced(x, y, SPIKE_MIN_SEP):
                return x, y
            gap = nearest_gap(x, y)
            if best is None or gap > best[0]:
                best = (gap, x, y)
        return (best[1], best[2]) if best else None

    @staticmethod
    def _is_routed(n) -> bool:
        """该节点是否已握有到基站的有效路由(或自身就是基站)。

        Args: n=节点。Returns: True=可作为跨链锚点。
        """
        if n.role == "base":
            return True
        r = n.routing.get("BASE-00")
        return r is not None and r.get("cost") is not None and r["cost"] < 1e9

    def _drop_remaining(self, c):
        """收尾撒布:把剩余库存逐颗经 _drop_spot 铺开(每颗都以"上一颗"为 ref,
        间距约束自然把它们沿链铺开),而不是全部堆在探针脚下 —— 堆在一点的
        节点互相遮挡、对拓扑毫无增益。连 _drop_spot 都找不到连通点时(极端
        阴影),退而在载体附近**沿走廊左右交替铺开**(只用 _place_node 保物理安全):
        钉距 ≥SPIKE_MIN_SEP,短距内几乎必然互相视距成串;串首一旦碰到任一
        锚点即可整体入网。绝不在同一列叠一摞(一摞孤钉会触发 pocket 自愈
        集体漫游,实测把网络撕成 6 块、覆盖率跌到 0.08)。

        Args: c=探针。Returns: None。
        """
        lb, rb = self._rover_bounds()
        relaxed_n = 0
        while getattr(c, "onboard", None):
            if self._drop_one(c, c.last_drop):
                continue
            s = c.onboard.pop(0)
            side = 1 if relaxed_n % 2 == 0 else -1     # 左右交替: 0,+1,-1,+2,-2,...
            off = ((relaxed_n + 1) // 2) * SPIKE_MIN_SEP * side
            px = min(max(c.x + off, lb + SPIKE_MIN_SEP), rb - SPIKE_MIN_SEP)
            py = self._place_node(px, self._patrol_y_at(c, px), SPIKE_BOULDER_M)
            s.x, s.y = round(px, 1), round(py, 1)
            s.alive = True
            s._pending_deploy = False
            s.domain, s.border = self.world.domain_of(s.x)
            s._last_anchor = c.last_drop
            c.last_drop = (s.x, s.y)
            c.stock = len(c.onboard)
            relaxed_n += 1
            self._recompute_static()

    def _deploy_finish(self, msg: str):
        """收尾:把所有探针剩余库存沿链逐颗铺开并置部署完成(幂等)。

        Args: msg=完成播报文案。Returns: None。
        """
        for c in getattr(self, "_carriers", []):
            self._drop_remaining(c)
        self.deploy_done = True
        self.deploy_front = self._rover_bounds()[1]
        self._recompute_static()
        self.emit("deploy", "good", msg, True)
