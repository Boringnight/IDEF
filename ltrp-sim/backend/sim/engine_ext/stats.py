# -*- coding: utf-8 -*-
"""引擎职责模块:统计与快照(StatsSnapshotMixin)."""

from ..nodes import RANGE, INF, AMBIENT_C
from .constants import *
from .engine_imports import P



class StatsSnapshotMixin:
    """统计与快照 mixin,由 Engine 继承,self 即引擎实例。"""

    def _stats(self):
        # 覆盖率与分区:引擎侧物理真值(BFS,含月球车桥接)
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
        parts = set()
        for i in uf:
            if find(i) != find("BASE-00"):
                parts.add(find(i))
        self.partitions = sum(
            1 for root in parts
            if any(self.nodes[i].role != "rover"
                   for i in uf if find(i) == root))
        # 覆盖率仅统计非 rover 节点(rover 是摆渡载体,不纳入需覆盖的骨干)
        covered = sum(
            1 for i in uf if find(i) == find("BASE-00")
            and self.nodes[i].role != "rover")
        cover_base = sum(
            1 for i in uf if self.nodes[i].role != "rover")
        cov = covered / cover_base if cover_base else 0.0
        # 自愈播报
        if cov < 0.9:
            self.was_healing = True
        elif self.was_healing and cov >= 0.95:
            self.was_healing = False
            self.emit("heal", "good", "路由收敛完成:覆盖恢复,网络自愈(全程无人工参与)", True)
        self.prev_coverage = cov
        self.coverage = cov

    # ------------------------------------------------------------------ api

    def snapshot(self) -> dict:
        nodes = []
        for i in self.order:
            n = self.nodes[i]
            r = n.routing.get("BASE-00")
            nodes.append({
                "id": i, "role": n.role, "x": round(n.x, 1), "y": round(n.y, 1),
                "soc": round(n.soc, 1), "state": n.state, "alive": n.alive,
                "sleeping": n.sleeping, "crit": n.is_critical,
                "domain": n.domain, "border": n.border,
                "sos": n.sos, "moving": n.move_target is not None or n.seek_target is not None,
                "bundles": len(n.bundles), "pkts": len(n.packets),
                "temp": round(n.temp_c, 1), "seu": n.seu_flips,
                "phys": {
                    "battery_mah": round(n.battery_mah, 0),
                    "i_tx": n.i_tx,
                    "supercap_pct": round(n.supercap_pct, 1),
                    "tx_power_dbm": n.tx_power_dbm,
                    "rx_sensitivity_dbm": n.rx_sensitivity_dbm,
                    "ant_gain_dbi": n.ant_gain_dbi,
                    "tilt_deg": n.tilt_deg,
                    "temp_c": round(n.temp_c, 1),
                    "radiation_rad": round(n.radiation_rad, 0),
                },
                "nbrs": [[j, round(e["snr"], 1),
                          float(f"{e.get('ber', 1e-12):.1e}")] for j, e in n.neighbors.items()],
                "nh": r["nh"] if r and r["cost"] < INF else None,
                "cost": round(r["cost"], 1) if r and r["cost"] < INF else None,
                "ms": round(r["ms"], 1) if r else None,
                "anchor": n.is_anchor,
            })
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
        alive = [n for n in self.nodes.values() if n.alive]
        awake = [n for n in alive if not n.sleeping]
        new_ev = self.events[self.ev_sent:]
        self.ev_sent = len(self.events)
        return {
            "cmd": "snap", "t": round(self.t, 1),
            "nodes": nodes,
            "params": P.PARAMS.export(),
            "links": [list(l) for l in links],
            "flows": flows, "ferries": ferries,
            "events": new_ev,
            "boulders": [dict(b) for b in self.world.boulders],
            "stats": {
                "alive": len(alive), "total": len(self.nodes),
                "awake": len(awake), "coverage": round(self.coverage, 3),
                "avg_soc": round(sum(n.soc for n in alive) / len(alive), 1) if alive else 0,
                "min_soc": round(min(n.soc for n in alive), 1) if alive else 0,
                "critical": sum(1 for n in alive if n.is_critical),
                "sleeping": sum(1 for n in alive if n.sleeping),
                "bundles": sum(len(n.bundles) for n in alive),
                "ferry_bundles": sum(len(n.bundles) for n in alive if n.role == "rover"),
                "partitions": self.partitions,
                "delivered": self.delivered, "lost": self.lost,
                "retries": self.retries, "damaged_drops": self.damaged_drops,
                "avg_temp": round(sum(n.temp_c for n in alive) / len(alive), 1) if alive else AMBIENT_C,
                "earth_up": getattr(self, "_earth_up", True),
                "earth_queue": self.earth_queue,
                "earth_flushed": self.earth_flushed,
                "sleep_on": self.sleep_on,
                "sleep_duty": round(self.sleep_duty, 2),
                "heat": self.t < self.heat_until,
                "avg_hops": round(self._avg_hops(), 1),
            },
        }

    def _avg_hops(self):
        return round(self.hops_sum / self.delivered, 1) if self.delivered else 0

    def init_payload(self) -> dict:
        return {"cmd": "init", "world": self.world.export(),
                "params": {"range": RANGE, **P.PARAMS.export()},
                "snapshot": self.snapshot()}
