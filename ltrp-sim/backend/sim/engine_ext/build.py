# -*- coding: utf-8 -*-
"""引擎职责模块:世界构建与节点布点(WorldBuilderMixin)."""

import math

from ..nodes import Node, RANGE, INF
from .. import physics
from ..world import World, letter
from .constants import *
from .engine_imports import P, random



class WorldBuilderMixin:
    """世界构建与节点布点 mixin,由 Engine 继承,self 即引擎实例。"""

    def __init__(self):
        self.reset()

    # ------------------------------------------------------------------ setup

    def reset(self, seed: int = 7):
        self._need_init = False
        self.world = World(seed)
        self.t = 0.0
        self.tick = 0
        self.nodes: dict[str, Node] = {}
        self.order: list[str] = []
        self.static_links: list[tuple] = []
        self.adj: dict[str, set] = {}
        self.flows: dict[tuple, float] = {}       # (a,b) -> 最近数据时刻
        self.ferry_marks: dict[tuple, float] = {}
        self.ferry_log_t: dict[tuple, float] = {}
        self.events: list[dict] = []
        self.ev_sent = 0
        self.heat_until = -1.0
        self._fade: dict[tuple, float] = {}   # 每对链路的慢变阴影衰落(OU 过程)
        self.sleep_on = False      # 默认关闭(按钮开启后:冗余道钉流量自适应休眠)
        self.sleep_duty = SLEEP_DUTY_MIN   # 目标苏醒比例(引擎按流量负载调控)
        self.delivered = 0
        self.hops_sum = 0
        self.earth_queue = 0
        self.earth_flushed = 0
        self.lost = 0
        self.retries = 0            # 逐跳误码重传总数
        self.damaged_drops = 0      # 连续误码丢弃的报文数
        self._dmg_log_t: dict[str, float] = {}   # 误码丢弃事件限流(每节点 3s 一条)
        self._heal_moved_any = False        # 本 tick 是否有节点自愈移动(刷静态链路用)
        P.PARAMS.reset()            # 协议参数恢复默认(上帝模式调参随重置失效)
        self.dupe_seen: set = set()
        self.prev_coverage = 1.0
        self.was_healing = False
        self.partitions = 0
        self.coverage = 1.0
        self._earth_up = True          # 潮汐锁定:地球始终可见,无升降/遮挡
        self.rng = random.Random(11)
        self._pkt_seq = 0
        self._spawn()
        self._recompute_static()
        self._build_patrol()
        self.emit("boot", "info", "系统上电:节点仅凭本地信标开始邻居发现(无全局视图)", True)

    def new_map(self, seed: int | None = None):
        """重新生成一张随机几何地图(seed=None 时随机取),并让所有客户端重绘"""
        if seed is None:
            seed = self.rng.randrange(1, 10 ** 6)
        self.reset(seed)
        self._need_init = True
        self.emit("map", "info",
                  f"已生成随机地图(seed={seed}):{len(self.world.chambers)} 个腔室 · "
                  f"{len(self.world.boulders)} 块巨石,全网视距重算,期待自组织", True)

    def _place_node(self, x: float, prefer_y: float, margin: float = 16.0) -> float:
        """把节点落点放到'岩壁内且不落进巨石'的最邻近位置(避免节点刷新在石头里)"""
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

    def _spawn(self):
        w = self.world

        def add(n: Node):
            self.nodes[n.id] = n
            self.order.append(n.id)

        bx = int(0.06 * w.W)
        add(Node("BASE-00", "base", bx, self._place_node(bx, w.yc(bx)), 0, False))
        spike_i = 0
        for ci, ch in enumerate(w.chambers):
            # 上下壁成对的"梯子型"布点,腔室内高冗余(偏移按腔室半长缩放,适配变长腔室)
            for k in (-0.7, -0.35, 0.0, 0.35, 0.7):
                off = ch["hl"] * k
                x = ch["cx"] + off
                if k == 0:
                    spike_i += 1
                    y = self._place_node(x, w.yc(x))
                    add(Node(f"SPIKE-{spike_i:02d}", "spike",
                             round(x, 1), round(y, 1), ci, False))
                    continue
                for side in (1, -1):
                    y = self._place_node(x, w.yc(x) + side * max(6.0, w.r_at(x) - 25))
                    spike_i += 1
                    add(Node(f"SPIKE-{spike_i:02d}", "spike",
                             round(x, 1), round(y, 1), ci, False))
        for ti, (a, b) in enumerate(w.throats()):
            dom, _ = w.domain_of((a + b) / 2)
            for kk, fx in enumerate([0.22, 0.62]):
                x = a + (b - a) * fx
                spike_i += 1
                y = self._place_node(x, w.yc(x) + (18 if kk == 0 else -18), 14)
                add(Node(f"SPIKE-{spike_i:02d}", "spike", round(x, 1), round(y, 1), dom, True))
        last_dom = len(w.chambers) - 1
        # 探测器:从右往左找"能与道钉有视距"的落点,保证与网络保持连接(不特殊化成孤岛)
        for pi in range(2):
            base_off = (45 if pi == 0 else -25)
            cand_x = [int(w.W * 0.955 - k * 0.02 * w.W) for k in range(10)]
            cand_x += [int(w.chambers[-1]["cx"] + w.chambers[-1]["hl"] * f)
                       for f in (0.9, 0.7, 0.5, 0.3)]
            px, e_y = None, None
            for x in cand_x:
                base_y = w.yc(x) + base_off
                for dy in ([0, -20, 20, -40, 40, 60, -60] if pi == 0
                           else [0, 20, -20, 40, -40, 60, -60]):
                    yy = base_y + dy
                    if not w.inside(x, yy, 10):
                        continue
                    links = sum(1 for m in self.nodes.values()
                                if m.role == "spike" and m.alive
                                and math.hypot(m.x - x, m.y - yy) <= RANGE
                                and w.los((x, yy), (m.x, m.y)))
                    if links >= 1:
                        px, e_y = x, yy
                        break
                if px is not None:
                    break
            if px is None:
                px = int(w.chambers[-1]["cx"] + w.chambers[-1]["hl"] * 0.3)
                e_y = w.yc(px)
            e_y = self._place_node(px, e_y, 16)
            add(Node(f"PROBE-{pi+1}", "probe", round(px, 1), round(e_y, 1), last_dom, False))
        for ri, (x0, d) in enumerate([(int(w.W * 0.16), 1), (int(w.W * 0.72), -1)]):
            r = Node(f"ROVER-{ri+1}", "rover", x0, w.yc(x0), 0, False)
            r.dir = d
            r.speed = 55.0
            r.y = w.yc(x0) + (ROVER_PREF if ri == 0 else -ROVER_PREF)
            add(r)
        self._fix_base_position()
        self._fix_node_positions()

    def _fix_node_positions(self):
        """最终兜底:把仍落进巨石/出岩壁的节点,沿管道截面就近挪到最近安全落点"""
        w = self.world
        N = 14.0
        for n in self.nodes.values():
            yc = w.yc(n.x)
            half = max(6.0, w.r_at(n.x) - N)
            lo, hi = yc - half, yc + half

            def clear(yy):
                return all(math.hypot(n.x - b["x"], yy - b["y"]) >= b["r"] + N
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

    def _recompute_static(self):
        ids = [i for i, n in self.nodes.items()
               if n.alive and n.role != "rover"]
        self.static_links = []
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                a, b = self.nodes[ids[x]], self.nodes[ids[y]]
                if a.dist(b) <= RANGE and self.world.los((a.x, a.y), (b.x, b.y)):
                    self.static_links.append((a.id, b.id))

    def _update_adj(self):
        adj = {i: set() for i in self.nodes}
        live = lambda n: n.alive and not n.sleeping
        for a, b in self.static_links:
            na, nb = self.nodes[a], self.nodes[b]
            if live(na) and live(nb):
                adj[a].add(b)
                adj[b].add(a)
        rovers = [n for n in self.nodes.values() if n.role == "rover" and live(n)]
        for r in rovers:
            for j in self.order:
                nj = self.nodes[j]
                if j == r.id or not live(nj):
                    continue
                if r.dist(nj) <= RANGE and self.world.los((r.x, r.y), (nj.x, nj.y)):
                    adj[r.id].add(j)
                    adj[j].add(r.id)
        self.adj = adj

    def emit(self, type_: str, sev: str, msg: str, narr: bool = False):
        self.events.append({"i": len(self.events), "t": round(self.t, 1),
                            "type": type_, "sev": sev, "msg": msg, "narr": narr})

    # ------------------------------------------------------------------ 物理

    def _snr(self, a: Node, b: Node) -> dict | None:
        """链路预算物理模型(FSPL+热噪声+倾角惩罚+温度耦合) + 慢变阴影衰落:
        熔岩管壁多径/局部遮挡用每对链路的 OU 过程近似(均值 0,稳态 σ≈2.5dB,
        相关时间 ≈5s),再叠加 ±2.5dB 测量抖动。衰落同时作用于 SNR/BER/余量,
        让边缘链路偶发跌入高 BER 区间 —— 逐跳损伤/重传因此真实可见。"""
        lb = physics.link_budget(a, b)
        if lb is None:
            return None
        key = (a.id, b.id) if a.id < b.id else (b.id, a.id)
        f = self._fade.get(key, 0.0)
        f = max(-7.0, min(7.0, f * 0.80 + self.rng.gauss(0.0, 1.05)))
        self._fade[key] = f
        snr = lb["snr_db"] + f + self.rng.uniform(-2.5, 2.5)
        margin = lb["margin_db"] + f
        ber = physics.ber_from_snr(snr)
        return {"snr_db": snr, "ber": ber,
                "up": margin > 0 and ber < 1e-3}
