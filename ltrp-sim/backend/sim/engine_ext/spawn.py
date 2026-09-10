# -*- coding: utf-8 -*-
"""引擎职责模块:节点布点与落点校正(SpawnMixin)。

职责范围:主基站 / 库存道钉 / 探针载体 / 月球车的初始布设,以及"不落进巨石与岩壁"的
落点求解与兜底校正。首次部署(撒布)由 deploy.py 负责,此处只负责"出生"。
依赖:读 Engine 的 world;写 Engine 的 nodes/order/_carriers。
Calls: _place_node/_register, world.yc/r_at/domain_of/throats。
"""
import math

from ..nodes import Node
from .constants import *


class SpawnMixin:
    """布点 mixin,由 Engine 继承,self 即引擎实例。

    Globals Used: BASE_STORAGE_MAH, DEPLOY_GAP, CARRIER_OFF, ROVER_PREF。
    Invocation: Engine.reset() → _spawn()。
    """

    def _spawn(self):
        """节点布点总入口:基站 → 库存道钉 → 探针载体 → 月球车 → 落点兜底。

        Args: None。Returns: None。
        """
        self._spawn_base()
        spikes = self._spawn_spikes()
        self._spawn_carriers(spikes)
        self._spawn_rovers()
        self._fix_base_position()
        self._fix_node_positions()

    def _register(self, n: Node):
        """把节点登记进 nodes/order。Args: n=节点。Returns: None。"""
        self.nodes[n.id] = n
        self.order.append(n.id)

    def _spawn_base(self):
        """布设主基站 BASE-00(巨型储能电池,接收地表核裂变→激光→电)。

        Globals Used: BASE_STORAGE_MAH。Args: None。Returns: None。
        """
        w = self.world
        bx = int(0.06 * w.W)
        base = Node("BASE-00", "base", bx, self._place_node(bx, w.yc(bx)), 0, False)
        base.battery_capacity = BASE_STORAGE_MAH
        base.battery_mah = BASE_STORAGE_MAH
        base.soc = 100.0
        self._register(base)

    def _spawn_spikes(self) -> list:
        """创建库存道钉:每腔室 4 颗(上下成对)+ 每喉道 2 颗边界;全部离线待撒布。

        数量控制: 与管道长度匹配, 才能按"动态间距"均匀撒满全程不重叠
        (面包屑=少量中继, 而非把管道塞满)。

        Args: None。Returns: 库存道钉列表(未上线, alive=False)。
        """
        w = self.world
        base = self.nodes["BASE-00"]
        spikes, spike_i = [], 0
        for ci, _ch in enumerate(w.chambers):
            for _k in (-0.5, 0.5):
                for _side in (1, -1):
                    spike_i += 1
                    s = Node(f"SPIKE-{spike_i:02d}", "spike", base.x, base.y, ci, False)
                    s._pending_deploy = True
                    s.alive = False
                    spikes.append(s)
        for _ti, _throat in enumerate(w.throats()):
            for _kk in (0, 1):
                spike_i += 1
                s = Node(f"SPIKE-{spike_i:02d}", "spike", base.x, base.y, 0, True)
                s._pending_deploy = True
                s.alive = False
                spikes.append(s)
        for s in spikes:            # 库存道钉也要进节点表(仅为 id/位置登记, alive=False 全程离线)
            self._register(s)
        return spikes

    def _spawn_carriers(self, spikes: list):
        """创建两枚探针载体(面包屑撒布车),并把库存道钉交替装船。

        两条探针相位错开约半个撒布间距、上下分带: 交替往同一条主链上撒布 →
        形成一条从基地直连、连续到管尾的面包屑主干,且横向分层可互为备份。

        Globals Used: DEPLOY_GAP, CARRIER_OFF。
        Args: spikes=库存道钉列表。Returns: None(写 self._carriers)。
        """
        base = self.nodes["BASE-00"]
        carriers = []
        for pi in range(2):
            lag = DEPLOY_GAP * 0.5 if pi == 1 else 0.0
            p = Node(f"PROBE-{pi+1}", "probe",
                     base.x + 6 - lag,
                     base.y + (CARRIER_OFF if pi == 0 else -CARRIER_OFF), 0, False)
            p.alive = True
            p.dir = 1
            p.onboard = []
            p.stock = 0
            p.last_drop = (base.x, base.y)
            p.phase = 1.0 if pi == 0 else 0.5   # 首颗相位:第二枚只走半个间距 → 两链交替插入
            self._register(p)
            carriers.append(p)
        for idx, s in enumerate(spikes):
            c = carriers[idx % 2]
            s.x, s.y = round(c.x, 1), round(c.y, 1)
            c.onboard.append(s)
            c.stock += 1
        self._carriers = carriers

    def _spawn_rovers(self):
        """布设两辆月球车(方向相反、上下错开巡逻)。

        Globals Used: ROVER_PREF。Args: None。Returns: None。
        """
        w = self.world
        for ri, (x0, d) in enumerate([(int(w.W * 0.16), 1), (int(w.W * 0.72), -1)]):
            r = Node(f"ROVER-{ri+1}", "rover", x0, w.yc(x0), 0, False)
            r.dir = d
            r.y = w.yc(x0) + (ROVER_PREF if ri == 0 else -ROVER_PREF)
            self._register(r)

    def _place_node(self, x: float, prefer_y: float, margin: float = 16.0) -> float:
        """把节点落点放到'岩壁内且不落进巨石'的最邻近位置(避免节点刷新在石头里)。

        Args: x=目标 x; prefer_y=偏好 y; margin=与岩壁/巨石的余量。
        Returns: 校正后的 y。
        """
        w = self.world
        yc = w.yc(x)
        half = max(6.0, w.r_at(x) - margin)
        y_lo, y_hi = yc - half, yc + half

        def clear(yy: float) -> bool:
            for b in w.boulders:
                if math.hypot(x - b["x"], yy - b["y"]) < b["r"] + margin:
                    return False
            return True

        y = min(max(prefer_y, y_lo), y_hi)
        if clear(y):
            return y
        for dy in (0, 8, -8, 16, -16, 24, -24, 34, -34, 46, -46, 60, -60, 80, -80):
            yy = min(max(prefer_y + dy, y_lo), y_hi)
            if clear(yy):
                return yy
        return (y_lo + y_hi) / 2

    def _fix_node_positions(self):
        """最终兜底:把仍落进巨石/出岩壁的节点,沿管道截面就近挪到最近安全落点。

        Args: None。Returns: None。
        """
        w = self.world
        n_margin = 14.0
        for n in self.nodes.values():
            if getattr(n, "_pending_deploy", False):
                continue          # 库存道钉: 位置由撒布决定, 不在此校正
            yc = w.yc(n.x)
            half = max(6.0, w.r_at(n.x) - n_margin)
            lo, hi = yc - half, yc + half

            def clear(yy):
                return all(math.hypot(n.x - b["x"], yy - b["y"]) >= b["r"] + n_margin
                           for b in w.boulders)

            y = min(max(n.y, lo), hi)
            if clear(y):
                n.y = y
                continue
            best = None
            yy = lo
            while yy <= hi:
                if clear(yy) and (best is None or abs(yy - y) < abs(best - y)):
                    best = yy
                yy += 3
            n.y = float(best) if best is not None else (lo + hi) / 2
