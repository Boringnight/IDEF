# -*- coding: utf-8 -*-
"""引擎职责模块:统计与快照(StatsSnapshotMixin)。

职责范围:并查集覆盖率/分区统计 → 组装强类型快照(Snapshot/StatsDTO/SnapshotNode)
→ init 首帧载荷;另提供"信号覆盖比例"与"平均跳数"两个派生指标。
依赖:读 Engine 的 nodes/order/adj/static_links/flows/ferry_marks/events/world。
Calls: contracts 的 DTO 构造;P.PARAMS.export。
"""
import math

from ..contracts import (DeployDTO, InitPayload, PhysDTO, Snapshot,
                         SnapshotNode, StatsDTO)
from ..nodes import RANGE, INF, AMBIENT_C
from .constants import *
from .engine_imports import P


class StatsSnapshotMixin:
    """统计与快照 mixin,由 Engine 继承,self 即引擎实例。

    Globals Used: RANGE, INF, AMBIENT_C, TIME_SCALE_DEFAULT, P。
    Invocation: Engine.step() 每 tick 末尾调用 _stats();snapshot/init_payload 供 API 层。
    """

    def _stats(self):
        """覆盖率与分区:引擎侧物理真值(BFS/并查集,含月球车桥接),并触发自愈播报。"""
        cov, parts = self._coverage()
        self.partitions = parts
        if cov < 0.9:
            self.was_healing = True
        elif self.was_healing and cov >= 0.95:
            self.was_healing = False
            self.emit("heal", "good", "路由收敛完成:覆盖恢复,网络自愈(全程无人工参与)", True)
        self.coverage = cov

    def _coverage(self) -> tuple:
        """并查集求 (覆盖率, 分区数)。覆盖率只统计非 rover 节点。

        Calls: 内部 find(路径压缩)。
        Args: None。Returns: (coverage 0~1, partitions 个数)。
        """
        uf = {i: i for i in self.nodes if self.nodes[i].alive}

        def find(x):
            while uf[x] != x:
                uf[x] = uf[uf[x]]
                x = uf[x]
            return x

        for a, b in self.static_links:
            if a in uf and b in uf:
                uf[find(a)] = find(b)
        for i in uf:
            if self.nodes[i].role != "rover":
                continue
            for j in self.adj[i]:
                if j in uf:
                    uf[find(i)] = find(j)
        # 网络分区 = 不含 BASE 且**含非 rover 节点**的连通分量。
        # rover 单独巡逻分量不算分区(它是摆渡载体,会重新接触网络,不是网络断裂)。
        base_root = find("BASE-00") if "BASE-00" in uf else None
        parts = set()
        for i in uf:
            if base_root is None or find(i) != base_root:
                parts.add(find(i))
        partitions = sum(
            1 for root in parts
            if any(self.nodes[i].role != "rover"
                   for i in uf if find(i) == root))
        # 覆盖率仅统计非 rover 节点(rover 是摆渡载体,不纳入需覆盖的骨干)
        covered = sum(
            1 for i in uf if base_root is not None and find(i) == base_root
            and self.nodes[i].role != "rover")
        cover_base = sum(1 for i in uf if self.nodes[i].role != "rover")
        return (covered / cover_base if cover_base else 0.0), partitions

    # ------------------------------------------------------------------ api

    def snapshot(self) -> Snapshot:
        """组装本 tick 的全网快照 DTO(引擎 → API 层的唯一输出契约)。

        Globals Used: RANGE, INF, AMBIENT_C, TIME_SCALE_DEFAULT。
        Calls: _node_dto/_stats_dto/_avg_hops/_signal_cover。
        Args: None。Returns: Snapshot。
        """
        nodes = [self._node_dto(i, self.nodes[i]) for i in self.order]
        links = set()
        for i, n in self.nodes.items():
            for j in n.neighbors:
                links.add((i, j) if i < j else (j, i))
        flows = []
        now = self.t
        for (a, b), tt in self.flows.items():
            if now - tt < 1.2:
                flows.append([a, b, round(now - tt, 2)])
        ferries = []
        for (a, b), tt in self.ferry_marks.items():
            if now - tt < 3.0:
                ferries.append([a, b, round(now - tt, 2)])
        new_ev = self.events[self.ev_sent:]
        self.ev_sent = len(self.events)
        return Snapshot(
            cmd="snap", t=round(self.t, 1), nodes=nodes,
            params=P.PARAMS.export(),
            links=[list(l) for l in links],
            power_links=getattr(self, "power_links", []),
            flows=flows, ferries=ferries, events=new_ev,
            boulders=[dict(b) for b in self.world.boulders],
            deploy=DeployDTO(front=round(getattr(self, "deploy_front", 1e9), 1),
                             base=round(self.nodes["BASE-00"].x, 1),
                             end=self.world.rover_bounds()[1],
                             done=getattr(self, "deploy_done", True)),
            stats=self._stats_dto(),
        )

    def _node_dto(self, i: str, n) -> SnapshotNode:
        """单个节点的快照 DTO。Args: i=id; n=节点。Returns: SnapshotNode。"""
        r = n.routing.get("BASE-00")
        return SnapshotNode(
            id=i, role=n.role, x=round(n.x, 1), y=round(n.y, 1),
            soc=round(n.soc, 1), state=n.state, alive=n.alive,
            sleeping=n.sleeping, crit=n.is_critical,
            domain=n.domain, border=n.border,
            sos=n.sos, moving=n.seek_target is not None,
            bundles=len(n.bundles), pkts=len(n.packets),
            temp=round(n.temp_c, 1), seu=n.seu_flips,
            phys=PhysDTO(
                battery_mah=round(n.battery_mah, 0), i_tx=n.i_tx,
                supercap_pct=round(n.supercap_pct, 1),
                tx_power_dbm=n.tx_power_dbm,
                rx_sensitivity_dbm=n.rx_sensitivity_dbm,
                ant_gain_dbi=n.ant_gain_dbi, tilt_deg=n.tilt_deg,
                temp_c=round(n.temp_c, 1),
                radiation_rad=round(n.radiation_rad, 0),
            ),
            nbrs=[[j, round(e.snr, 1), float(f"{e.ber:.1e}")]
                  for j, e in n.neighbors.items()],
            nh=r["nh"] if r and r["cost"] < INF else None,
            cost=round(r["cost"], 1) if r and r["cost"] < INF else None,
            ms=round(r["ms"], 1) if r else None,
            pending=bool(getattr(n, "_pending_deploy", False)),
            stock=getattr(n, "stock", 0),
            pv_w=round(getattr(n, "pv_w", 0.0), 2),
            laser_in=round(getattr(n, "laser_in_w", 0.0), 2),
            laser_out=round(getattr(n, "laser_out_w", 0.0), 2),
            charge_ma=round(getattr(n, "charge_ma", 0.0), 2),
            energy=bool(getattr(n, "energy_active", False)),
        )

    def _stats_dto(self) -> StatsDTO:
        """全网统计 DTO。Args: None。Returns: StatsDTO。"""
        alive = [n for n in self.nodes.values() if n.alive]
        awake = [n for n in alive if not n.sleeping]
        return StatsDTO(
            alive=len(alive), total=len(self.nodes), awake=len(awake),
            coverage=round(self.coverage, 3),
            avg_soc=round(sum(n.soc for n in alive) / len(alive), 1) if alive else 0,
            min_soc=round(min(n.soc for n in alive), 1) if alive else 0,
            critical=sum(1 for n in alive if n.is_critical),
            sleeping=sum(1 for n in alive if n.sleeping),
            bundles=sum(len(n.bundles) for n in alive),
            ferry_bundles=sum(len(n.bundles) for n in alive if n.role == "rover"),
            partitions=self.partitions,
            delivered=self.delivered, lost=self.lost,
            retries=self.retries, damaged_drops=self.damaged_drops,
            avg_temp=round(sum(n.temp_c for n in alive) / len(alive), 1) if alive else AMBIENT_C,
            earth_up=getattr(self, "_earth_up", True),
            earth_flushed=self.earth_flushed,
            sleep_on=self.sleep_on, sleep_duty=round(self.sleep_duty, 2),
            heat=self.t < self.heat_until,
            avg_hops=self._avg_hops(), range=RANGE,
            signal_cover=round(self._signal_cover(), 3),
            time_scale=getattr(self, "time_scale", TIME_SCALE_DEFAULT),
        )

    def _cover_points(self) -> list:
        """洞穴覆盖采样点(只依赖世界几何,按地图缓存一次,避免每 tick 重算几何)。

        Args: None。Returns: [(x, y), ...] 管内采样点。
        """
        pts = getattr(self, "_cover_pts", None)
        if pts is not None:
            return pts
        w = self.world
        lb, rb = self._rover_bounds()
        pts = []
        x = int(lb)
        while x <= int(rb):
            for fy in (-0.6, 0.0, 0.6):
                y = w.yc(x) + fy * w.r_at(x)
                if w.inside(x, y, 8):
                    pts.append((x, y))
            x += 90
        self._cover_pts = pts
        return pts

    def _signal_cover(self) -> float:
        """洞穴"信号覆盖比例": 在管道内采样格点, 统计"存在存活节点距其 ≤ RANGE"的格点占比 ——
        即每个节点通信半径圆共同覆盖整个洞穴的几分之几(供前端"信号覆盖范围"按钮显示)。

        Globals Used: RANGE。Calls: _cover_points。
        Args: None。Returns: 0~1 的覆盖比例。
        """
        pts = self._cover_points()
        rng = RANGE
        alive = [self.nodes[i] for i in self.order if self.nodes[i].alive]
        cov = 0
        for (x, y) in pts:
            for nn in alive:
                if math.hypot(nn.x - x, nn.y - y) <= rng:
                    cov += 1
                    break
        return cov / len(pts) if pts else 0.0

    def _avg_hops(self) -> float:
        """平均交付跳数。Args: None。Returns: 已交付报文的平均跳数(无交付则 0)。"""
        return round(self.hops_sum / self.delivered, 1) if self.delivered else 0

    def init_payload(self) -> InitPayload:
        """WebSocket 建连首帧:世界几何 + 协议参数 + 当前快照。

        Args: None。Returns: InitPayload。
        """
        return InitPayload(
            cmd="init", world=self.world.export(),
            params={"range": RANGE, **P.PARAMS.export()},
            snapshot=self.snapshot(),
        )
