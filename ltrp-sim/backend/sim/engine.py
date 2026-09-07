# -*- coding: utf-8 -*-
"""LTRP 仿真引擎:只做"物理世界"和消息投递,协议决策全部在各节点本地完成"""
import asyncio
import math
import random
import time

from . import protocol as P
from .nodes import INF, Node, RANGE, STATE_DEAD, STATE_DYING, STATE_PROTECTED
from .world import World, letter

DT = 0.3                # tick 周期(秒,实时)
BEACON_EVERY = 1        # 每 tick 都发信标(0.3s,加速初始收敛)
RELAX_ROUNDS = 3        # 每 tick 的 DSDV 松弛轮数
SLEEP_PERIOD = 16.0     # 轮值休眠窗口(长窗口降低拓扑抖动)

# ---- 流量自适应休眠(duty cycling):苏醒比例随负载升降 ----
SLEEP_DUTY_MIN = 0.25   # 最低苏醒比例(低负载:保住连通主干即可,其余省电)
SLEEP_DUTY_MAX = 0.80   # 最高苏醒比例(高负载:多节点转发换取吞吐,并均摊能耗)
LOAD_TARGET = 3.0       # 每存活节点的目标积压(bundles+packets)个
LOAD_HYST = 1.2         # 目标附近死区(避免频繁升降档)
DUTY_STEP = 0.05        # 每次调节苏醒比例的步长


# ---- 月球车物理(实体碰撞) ----
ROVER_R = 14.0        # 物理半径(与巨石/岩壁判定用)
ROVER_CLEAR = 26.0    # 与巨石的间距余量
ROVER_WALL_M = 12.0   # 靠壁安全间距
ROVER_LOOK = 160.0    # 前方勘探距离(转向提前量)
ROVER_LAT = 220.0     # 最大横向(转向)速度
ROVER_PREF = 34.0     # 相对中心线的巡逻偏好偏移
ROVER_STEER = 60.0    # (保留)巨石排斥折算系数
PATROL_STEP = 20      # 安全巡逻路径采样步长
PATROL_LOOK = 60.0    # 巡逻路径前瞻距离

# ---- 节点移动(自愈重连) ----
NODE_MOVE_SPEED = 26.0    # 节点移动速度(px/tick)
NODE_STOP = 5.0           # 到达目标即停止的距离
ISOLATION_T = 3.0         # 判定"孤立求救"的持续无邻居时间
LINK_SAFE = 0.82          # 桥接后与两端距离 ≤ RANGE*LINK_SAFE(留余量,防乒乓)
WOUND_WINDOW = 40.0       # 判定"近期断裂(伤口)"的时间窗(仅此窗口内触发桥接移动)
RELAY_HOLD = 60.0         # rover 注入的"朝网络方向"目标的有效时长
PARTITION_T = 8.0         # 判定"分区失联(持续无基站路由)"的时长;到点后仅"前沿"节点朝锚点桥接(去中心化,不齐动)


def zh(nid: str) -> str:
    if nid.startswith("BASE"):
        return "主基站"
    if nid.startswith("ROVER"):
        return f"月球车{nid.split('-')[1]}"
    if nid.startswith("PROBE"):
        return f"深处探测器{nid.split('-')[1]}"
    return f"{int(nid.split('-')[1]):02d}号道钉"


class Engine:
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
        self.sleep_on = False      # 默认关闭(按钮开启后:冗余道钉流量自适应休眠)
        self.sleep_duty = SLEEP_DUTY_MIN   # 目标苏醒比例(引擎按流量负载调控)
        self.delivered = 0
        self.hops_sum = 0
        self.earth_queue = 0
        self.earth_flushed = 0
        self.lost = 0
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

    def emit(self, type_: str, sev: str, msg: str, narr: bool = False):
        self.events.append({"i": len(self.events), "t": round(self.t, 1),
                            "type": type_, "sev": sev, "msg": msg, "narr": narr})

    # ------------------------------------------------------------------ 物理
    def _recompute_static(self):
        ids = [i for i, n in self.nodes.items()
               if n.alive and n.role != "rover"]
        self.static_links = []
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                a, b = self.nodes[ids[x]], self.nodes[ids[y]]
                if a.dist(b) <= RANGE and self.world.los((a.x, a.y), (b.x, b.y)):
                    self.static_links.append((a.id, b.id))

    def _snr(self, a: Node, b: Node) -> float:
        d = max(a.dist(b), 50.0)
        s = 34 - 22 * math.log10(d / 100.0)
        if self.t < self.heat_until:
            s -= 6
        return s + self.rng.uniform(-2.5, 2.5)

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
    def _clamp_node(self, n: Node, r: float = 14.0):
        """移动后的物理约束:夹回管内、推出巨石"""
        w = self.world
        yc = w.yc(n.x)
        half = max(8.0, w.r_at(n.x) - r)
        for _ in range(2):
            n.y = min(max(n.y, yc - half), yc + half)
            for b in w.boulders:
                dx, dy = n.x - b["x"], n.y - b["y"]
                d = math.hypot(dx, dy)
                md = b["r"] + r
                if d < md and d > 1e-6:
                    n.x = b["x"] + dx / d * md
                    n.y = b["y"] + dy / d * md

    def _tube_pt(self, tgt):
        """把目标点夹进管道内(避免移动到岩壁里)"""
        w = self.world
        X = min(max(tgt[0], w.W * 0.02), w.W * 0.98)
        yc = w.yc(X)
        half = max(8.0, w.r_at(X) - 16)
        return (X, min(max(tgt[1], yc - half), yc + half))

    def _bridge_point(self, a: Node, b: Node):
        """从 a 朝 b 移动到'与 b 距离 ≈ RANGE*LINK_SAFE'的桥接落点"""
        dx, dy = b.x - a.x, b.y - a.y
        d = math.hypot(dx, dy)
        if d <= RANGE * LINK_SAFE:
            return None
        tt = (d - RANGE * LINK_SAFE) / d
        return (a.x + dx * tt, a.y + dy * tt)

    def _move_node(self, n: Node, tgt, dt: float):
        """沿管道行进:x 朝目标 x 推进、y 贴合中心线并夹在管内(避免穿墙);移动耗电。"""
        w = self.world
        dx = tgt[0] - n.x
        n.x += max(-NODE_MOVE_SPEED * dt, min(NODE_MOVE_SPEED * dt, dx))
        n.x = min(max(n.x, w.W * 0.02), w.W * 0.98)
        yc = w.yc(n.x)
        half = max(8.0, w.r_at(n.x) - 16)
        ty = min(max(tgt[1], yc - half), yc + half)
        n.y += (ty - n.y) * min(1.0, 4.0 * dt)
        n.y = min(max(n.y, yc - half), yc + half)
        n.spend(0.004, self.t < self.heat_until)
        self._clamp_node(n)

    def _separate(self, n: Node, sep: float = 60.0):
        """节点最小间距:移动节点永远不会贴到任何其它节点上(防聚成一点)。做两遍收敛。"""
        for _ in range(2):
            for i in self.order:
                m = self.nodes[i]
                if m.id == n.id or not m.alive or m.role in ("rover", "base"):
                    continue
                dx, dy = n.x - m.x, n.y - m.y
                d = math.hypot(dx, dy)
                if 0 < d < sep:
                    push = sep - d
                    n.x += dx / d * push
                    n.y += dy / d * push

    def _components(self) -> dict:
        """连通分量(排除月球车,因摆渡只是临时接触);返回 id->分量号"""
        seen = {}
        cid = 0
        for i in self.order:
            n = self.nodes[i]
            if not n.alive or n.role == "rover":
                continue
            if i in seen:
                continue
            seen[i] = cid
            st = [i]
            while st:
                u = st.pop()
                for v in self.adj[u]:
                    if self.nodes[v].alive and self.nodes[v].role != "rover" \
                            and v not in seen:
                        seen[v] = cid
                        st.append(v)
            cid += 1
        return seen

    def _nearest_base(self, n: Node, comps: dict, base_comp: int):
        """离 n 最近的 base 分量节点(重连目标/桥接参照)"""
        best, bd = None, 1e9
        for i in self.order:
            m = self.nodes[i]
            if not m.alive or m.role == "rover" or comps.get(i) != base_comp:
                continue
            d = math.hypot(m.x - n.x, m.y - n.y)
            if d < bd:
                bd, best = d, m
        return best

    def _arm_move(self, n: Node, target_node: Node):
        """给节点 n 设桥接落点(与 target_node 保持 RANGE*LINK_SAFE),标记为重连前锋"""
        tgt = self._bridge_point(n, target_node)
        if tgt:
            n.move_target = self._tube_pt(tgt)
            n.sos = True

    def _nearest_alive(self, n: Node):
        """离 n 最近的存活非 rover 节点(孤立重连的目标)"""
        best, bd = None, 1e9
        for i in self.order:
            m = self.nodes[i]
            if not m.alive or m.role == "rover" or m.id == n.id or m.sleeping:
                continue
            d = math.hypot(m.x - n.x, m.y - n.y)
            if d < bd:
                bd, best = d, m
        return best

    def _nearest_base_other(self, n: Node, comps: dict, base_comp: int, excl: str):
        """离 n 最近的其它 base 分量节点(双端桥接的安全锚),排除 excl"""
        best, bd = None, 1e9
        for i in self.order:
            m = self.nodes[i]
            if not m.alive or m.role == "rover" or m.id in (n.id, excl) \
                    or comps.get(i) != base_comp:
                continue
            d = math.hypot(m.x - n.x, m.y - n.y)
            if d < bd:
                bd, best = d, m
        return best

    def _has_nbr(self, n: Node) -> bool:
        return any(self.nodes[j].alive and self.nodes[j].role != "rover"
                   for j in n.neighbors)

    def _seek_target(self, n: Node):
        """本地自愈目标:优先追回'最近消失的邻居'(它在连接路径上),
        否则朝最近的存活非 rover 节点移动(孤立/探测器接入)。"""
        g = getattr(n, "_last_gone", None)
        if g and g in self.nodes:
            m = self.nodes[g]
            return self._tube_pt((m.x, m.y))
        t = self._nearest_alive(n)
        return self._tube_pt((t.x, t.y)) if t else None

    def _counterpart(self, n: Node, comps: dict) -> Node | None:
        """返回与 n 处于不同连通分量的最近存活节点(断裂对端)。
        rover 把它同步给 n,让两端朝彼此移动接合(弯曲喉道也能靠拢)。"""
        c = comps.get(n.id)
        if c is None:
            return None
        best, bd = None, 1e9
        for i in self.order:
            m = self.nodes[i]
            if not m.alive or m.role == "rover" or m.id == n.id:
                continue
            if comps.get(i) == c:
                continue
            d = math.hypot(m.x - n.x, m.y - n.y)
            if d < bd:
                bd, best = d, m
        return best

    def _step_movement(self, dt: float):
        """节点即本地 agent(无上帝视角):只凭'自身到基站的实时路由 + 是否刚断关键邻居/是否孤立'
        判断失联。节点通过连接彼此知道位置:失联后'前沿'(比所有邻居都更靠近锚点)的节点
        立即朝锚点桥接,其余原地等待被链式接入 —— 避免骨牌效应/聚团;Rover 负责把'朝网方向的
        锚点'摆渡给失联节点。"""
        # A) 每个节点记住"最近一次到基站的下一跳"位置(锚点) —— 断链后立即用它
        for i in self.order:
            n = self.nodes[i]
            if not n.alive or n.role in ("base", "rover") or n.sleeping:
                continue
            r = n.routing.get("BASE-00")
            if r and r["cost"] < INF and r.get("nh"):
                nh = self.nodes.get(r["nh"])
                if nh and nh.role != "rover" and nh.alive:
                    n._last_anchor = (nh.x, nh.y)

        # B) Rover 中继: ① 有骨干路由的巡检车向邻近失联节点注入"朝网方向"锚点;
        #    ② 无论 rover 有无路由,都把"断裂对端"位置同步给失联节点(双向接合,
        #       解决弯曲喉道里节点不知道对端在哪、不知该往哪挪的问题)。
        comps = self._components()
        for i in self.order:
            r = self.nodes[i]
            if r.role != "rover" or not r.alive or r.sleeping:
                continue
            rr = r.routing.get("BASE-00")
            have_route = rr is not None and rr["cost"] < INF
            anch = self.nodes.get(rr.get("nh")) if have_route else None
            for j in self.order:
                n = self.nodes[j]
                if not n.alive or n.role in ("rover", "base") or n.sleeping:
                    continue
                if math.hypot(n.x - r.x, n.y - r.y) <= RANGE \
                        and self.world.los((n.x, n.y), (r.x, r.y)):
                    nrt = n.routing.get("BASE-00")
                    no_base = nrt is None or nrt["cost"] >= INF
                    # ① 朝网方向锚点(仅当 rover 有骨干路由可提供有效指向)
                    if no_base and anch is not None:
                        n._last_anchor = (anch.x, anch.y)
                        n.relay_at = self.t
                    # ② 断裂对端接合点(rover 充当"信使",把对端位置带过来)
                    if no_base:
                        ct = self._counterpart(n, comps)
                        if ct is not None:
                            n.rejoin_target = ct.id   # 存对端 id,移动时实时解析其坐标(对端也在动)
                            n.contact_at = self.t

        # C) 本地 agent 判定与移动
        moved_any = False
        movers = set()
        for i in self.order:
            n = self.nodes[i]
            if not n.alive or n.role in ("base", "rover") or n.sleeping:
                n.sos = False
                n.seek_target = None
                continue
            route = n.routing.get("BASE-00")
            has_base = route is not None and route["cost"] < INF
            nbrs = [j for j in n.neighbors
                    if self.nodes[j].alive and self.nodes[j].role != "rover"]
            isolated = len(nbrs) == 0
            # 去中心化"桥接断裂": 刚失去的邻居是"桥"(它连接两端,邻居数>1)且已死亡。
            # 无论当前是否仍有(经巡检车摆渡的)到某节点路由,都视为分裂 → 立即朝断口靠拢合并。
            recent_bridge = (getattr(n, "_last_gone", None) is not None
                             and n._last_gone in self.nodes
                             and not self.nodes[n._last_gone].alive
                             and n._last_gone_at > 0
                             and (self.t - n._last_gone_at) < WOUND_WINDOW
                             and n._last_gone_nbrs > 1)
            # 持续孤立计时(避免上电/瞬态抖动误报): 一直无任何邻居超过 ISOLATION_T 才算孤立
            if isolated:
                if n.sos_since is None:
                    n.sos_since = self.t
            else:
                n.sos_since = None
            isolated_sos = (isolated and n.sos_since is not None
                            and (self.t - n.sos_since) >= ISOLATION_T)
            # 持续无基站实时路由计时(分区/断连): 一直连不上基站,且此前知道"朝网方向"锚点。
            # 仅"前沿"节点会朝锚点桥接,去中心化,避免整簇齐动/聚团。有邻居但无路由=典型"喉道断口两侧"。
            if not has_base:
                if n._since_nobase is None:
                    n._since_nobase = self.t
            else:
                n._since_nobase = None
            partition_sos = (not has_base and n._since_nobase is not None
                             and (self.t - n._since_nobase) >= PARTITION_T
                             and n._last_anchor is not None
                             and len(nbrs) > 0)
            disconnected = recent_bridge or isolated_sos or partition_sos
            if not disconnected:
                n.sos = False
                n.seek_target = None
                n.rejoin_target = None
                continue
            n.sos = True
            # 去中心化: 锚点 = 断口(刚死掉/刚失去的那个邻居)的位置 —— 断链后朝断口靠拢,把两个分组重新合并,
            # 而不是追着某个"中心(Base0)"。若没有可追的断口,再退回最近记忆/最近节点。
            # 对"持续分区"而言,优先用"上次到基站下一跳"的记忆锚点(指向网络方向),避免朝簇内同伴聚团。
            # 若 rover 刚同步过"断裂对端",则优先朝对端实时位置移动(双向接合,弯曲喉道更易靠拢)。
            rj = self.nodes.get(getattr(n, "rejoin_target", "")) \
                if getattr(n, "rejoin_target", None) else None
            if rj is not None and rj.alive \
                    and (self.t - getattr(n, "contact_at", -99)) < RELAY_HOLD:
                anchor = (rj.x, rj.y)
            elif partition_sos and not recent_bridge:
                anchor = n._last_anchor
            else:
                anchor = self._seek_target(n) or n._last_anchor
            if anchor is None:
                continue
            d_self = math.hypot(n.x - anchor[0], n.y - anchor[1])
            # 前沿判定: 若存在比"我"更靠近锚点的邻居 → 我不是前沿, 原地等待(链式接入)
            front = isolated or all(
                math.hypot(self.nodes[j].x - anchor[0], self.nodes[j].y - anchor[1])
                >= d_self - 1 for j in nbrs)
            if not front:
                continue
            # 朝锚点桥接: 移到"与锚点保持 RANGE*LINK_SAFE"的落点(不直接扎到锚点上)
            if n.seek_target is None:
                dxb = anchor[0] - n.x
                dyb = anchor[1] - n.y
                db = math.hypot(dxb, dyb)
                if db > RANGE * LINK_SAFE:
                    tt = (db - RANGE * LINK_SAFE) / db
                    n.seek_target = self._tube_pt((n.x + dxb * tt, n.y + dyb * tt))
                else:
                    n.seek_target = self._tube_pt(anchor)
            tgt = n.seek_target
            if tgt is None:
                continue
            self._move_node(n, tgt, dt)
            movers.add(i)
            moved_any = True
            # 复位: 丢邻居数不算是"恢复"(分区里邻居很多却仍连不上基站),只有连回"实时基站路由"
            # 才算真正恢复 → 停止并清 SOS;否则仅到达本步目标,清 seek_target,下个 tick 重新选目标持续自愈。
            if has_base:
                n.sos = False
                n.seek_target = None
                n.rejoin_target = None
            elif math.hypot(n.x - tgt[0], n.y - tgt[1]) <= NODE_STOP:
                n.seek_target = None
        # 移动节点防聚(不与任何节点重叠)
        for i in movers:
            self._separate(self.nodes[i])
        # D) 移动后刷新静态链路(否则 LOS 不更新, 移动后互不认邻居)
        if moved_any:
            self._recompute_static()
            self._update_adj()

    # ------------------------------------------------------------------ 流量自适应休眠
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
    def step(self, dt: float = DT):
        self.t += dt
        self.tick += 1
        heat = self.t < self.heat_until

        # 1. 月球车巡线(其巡逻路线即"可预知接触计划");带实体碰撞:绕开巨石、贴合岩壁
        for n in self.nodes.values():
            if n.role == "rover" and n.alive:
                self._rover_phys(n, dt)

        self._update_adj()

        # 2. 节点自愈移动(孤立/分区时移动重连)
        self._step_movement(dt)

        # 3. 信标交换(引擎只做投递;节点本地处理)
        beacon = self.tick % BEACON_EVERY == 0
        if beacon:
            for i in self.order:
                n = self.nodes[i]
                if not n.alive or n.sleeping:
                    continue
                h = P.compose_hello(n, self.t)
                n.spend(0.003, heat)
                n.tx += 1
                for j in self.adj[i]:
                    nj = self.nodes[j]
                    snr = self._snr(n, nj)
                    if snr >= P.GAMMA:
                        # 水平分割:发给 j 时剔除"下一跳为 j"的目的(抑制 2 环路)
                        hj = {**h, "adv": {d: v for d, v in h["adv"].items()
                                           if v[2] != j}}
                        P.on_hello(nj, hj, snr, self.t)
                        nj.spend(0.0018, heat)
                        nj.rx += 1

        # 3. 邻居超时(判死) + 割点自识别(连续 2 票防抖)
        for i in self.order:
            n = self.nodes[i]
            if not n.alive:
                continue
            nbr_counts = {
                j: len(n.neighbors[j]["nbrs"])
                for j in list(n.neighbors)
                if self.t - n.neighbors[j]["last"] > P.N_FAIL * P.T_B
            }
            dead = P.expire_neighbors(n, self.t)
            for j in dead:
                n._last_gone = j
                n._last_gone_at = self.t
                n._last_gone_nbrs = nbr_counts.get(j, 0)
            if len(n.neighbors) >= 2:
                if getattr(n, "_topo_dirty", True):
                    cv = P.cut_vertex(n)
                    n._topo_dirty = False
                    n._cv_res = cv
                else:
                    cv = n._cv_res
                n._crit_votes = n._crit_votes + 1 if cv else 0
                was = n.is_critical
                n.is_critical = n._crit_votes >= 3
                if n.is_critical and not was and n.role == "spike" \
                        and not getattr(n, "_crit_emitted", False):
                    n._crit_emitted = True
                    self.emit("crit", "info",
                              f"{zh(i)}({i}) 自识别为关键割点:锁定常开、提升功率(节点仅凭 2 跳视图判定)", True)
                elif not n.is_critical and was:
                    n._crit_emitted = False
            else:
                n.is_critical = False
                n._crit_votes = 0

        # 4. DSDV 分布式松弛(多轮加速收敛)
        for _ in range(RELAX_ROUNDS):
            for i in self.order:
                n = self.nodes[i]
                if n.alive and not n.sleeping:
                    P.dsdv_relax(n, self.nodes)

        # 5. 流量自适应休眠:苏醒比例随负载升降;'睡谁'由本地安全判据决定
        #    (非割点/非唯一桥/非孤立才准睡,避免把通信路睡断)
        if self.sleep_on:
            self._update_sleep_duty()
            phase = int(self.t // SLEEP_PERIOD)
            sleep_ratio = 1.0 - self.sleep_duty        # 允许睡眠的比例
            ids = sorted(i for i, n in self.nodes.items()
                         if n.alive and n.role == "spike" and not n.border)
            for i in ids:                              # 按 id 串行决策(确定性)
                n = self.nodes[i]
                if n.state in (STATE_DYING, STATE_PROTECTED, STATE_DEAD):
                    continue                           # 保护/垂死/已死:交给能量状态机
                idx = int(i.split("-")[1])
                # 同 tick 内已把前面的节点改为沉睡 → 后续 _can_sleep/_nbr_independent
                # 会把它视为不可用,从而避免"两个互为备用路径的节点同时睡"的多米诺。
                can = self._can_sleep(n)              # 割点/唯一桥/孤立 → False
                slot = (idx * 0.6180339887 + phase) % 1.0
                should_sleep = can and slot < sleep_ratio
                if should_sleep != n.sleeping:
                    n.sleeping = should_sleep

        # 6. 能量与状态机
        for n in self.nodes.values():
            n.energy_step(dt, heat)
        just_dead = [i for i, n in self.nodes.items() if not n.alive and not hasattr(n, "_death_logged")]
        for i in just_dead:
            self.nodes[i]._death_logged = True
            self.emit("dead", "warn", f"{zh(i)}({i}) 能量耗尽宕机", True)

        # 7. 数据面:遥测生成 → 逐跳转发 / 束化
        self._traffic(dt, heat)

        # 8. 月球车摆渡(存储-携带-转发)
        self._ferry()

        # 9. 地球可见窗口
        self._earth()

        # 10. 统计 + 自愈播报
        self._stats()

    # ------------------------------------------------------------------ data
    def _mk_pkt(self, src: Node, ctrl: bool, group=None):
        self._pkt_seq += 1
        return {"id": self._pkt_seq, "src": src.id, "dst": "BASE-00",
                "prio": 9 if ctrl else 2, "born": self.t, "hops": 0,
                "visited": {src.id}, "prev": None,
                "group": group if group is not None else self._pkt_seq}

    def _traffic(self, dt: float, heat: bool):
        # 生成
        for i, n in self.nodes.items():
            if not n.alive or n.sleeping or n.role == "base":
                continue
            if self.t >= n.gen_t:
                if len(n.bundles) >= 40:
                    # 断连深度省电:束严重积压时暂停采样
                    n.gen_t = self.t + 15.0
                elif len(n.bundles) >= 24:
                    # 断连省电:降频采样
                    n.gen_t = self.t + 6.0
                else:
                    n.gen_t = self.t + 2.2 + self.rng.random() * 0.8
                if self.rng.random() < 0.08:
                    g = f"C{self._pkt_seq + 1}"
                    a = self._mk_pkt(n, True, g)
                    b = self._mk_pkt(n, True, g)   # 关键束:双路冗余
                    n.packets += [a, b]
                else:
                    n.packets.append(self._mk_pkt(n, False))
        # 转发
        for i in self.order:
            n = self.nodes[i]
            if not n.alive or n.sleeping:
                continue
            # 束 → 包(路由恢复了)
            if n.bundles:
                r = n.routing.get("BASE-00")
                if r and r["cost"] < INF:
                    for _ in range(min(6, len(n.bundles))):
                        n.packets.append(n.bundles.pop(0))
            rest = []
            for pkt in n.packets:
                if self.t - pkt["born"] > P.PKT_TTL:
                    self.lost += 1
                    continue
                nh = self._next_hop(n, pkt)
                nj = self.nodes[nh] if nh else None
                blocked = (nh is None or nj is None or nj.sleeping
                           or nh not in self.adj[i]
                           or pkt["prev"] == nh)
                if blocked:
                    # 下一跳已宕机(非休眠):立即作废该路由,促上游节点下轮 DSDV 改道;
                    # 数据本身交给束缓存兜底(配合月球车摆渡)——即"信号回传上游"。
                    if nh is not None and nh in self.nodes and not self.nodes[nh].alive:
                        rr = n.routing.get(pkt["dst"])
                        if rr:
                            rr["cost"] = INF
                    # 无路由/环路风险:先等待 T_WAIT(过滤瞬态抖动),超时才进入束存储
                    if pkt.get("wait_since") is None:
                        pkt["wait_since"] = self.t
                    if self.t - pkt["wait_since"] < P.T_WAIT_BUNDLE:
                        rest.append(pkt)
                        continue
                    pkt.pop("wait_since", None)
                    n.bundles.append(pkt)
                    if len(n.bundles) > P.Q_CAP:
                        n.bundles.sort(key=lambda p: p["prio"])
                        self.lost += 1
                        n.bundles.pop(0)
                    continue
                pkt.pop("wait_since", None)
                pkt["hops"] += 1
                n.hops_total += 1
                if pkt["hops"] > P.HOPS_MAX:
                    self.lost += 1
                    continue
                pkt["prev"] = n.id
                pkt["visited"].add(n.id)
                nj.packets.append(pkt)
                n.spend(0.012, heat)
                nj.spend(0.006, heat)
                key = (n.id, nh) if n.id < nh else (nh, n.id)
                self.flows[key] = self.t
            n.packets = rest

    def _next_hop(self, n: Node, pkt: dict):
        r = n.routing.get(pkt["dst"])
        if r and r["cost"] < INF and r["nh"] in n.neighbors:
            return r["nh"]
        return None

    def _ferry(self):
        for i, n in self.nodes.items():
            if n.role != "rover" or not n.alive:
                continue
            r = n.routing.get("BASE-00")
            has_route = r and r["cost"] < INF
            if not has_route:
                n._ferry_log = False
                # 接收:积压(>2)或无路由邻居的束(容量感知,不超载)
                for j in self.adj[i]:
                    nj = self.nodes[j]
                    if nj.role == "rover":
                        continue
                    room = 128 - len(n.bundles)
                    if room <= 0:
                        break
                    rj = nj.routing.get("BASE-00")
                    no_route = not (rj and rj["cost"] < INF)
                    if len(nj.bundles) > 2 or no_route:
                        take = nj.bundles[:room]
                        nj.bundles = nj.bundles[room:]
                        if take:
                            n.bundles += take
                            key = (i, j) if i < j else (j, i)
                            self.ferry_marks[key] = self.t
                            if self.t - self.ferry_log_t.get((i, j), -99) > 5:
                                self.ferry_log_t[(i, j)] = self.t
                                self.emit("ferry", "good",
                                          f"{zh(i)}按巡逻接触计划与失联节点 {zh(j)} 接触,携带 {len(take)} 束", True)
            else:
                if n.bundles and not getattr(n, "_ferry_log", False):
                    n._ferry_log = True
                    self.emit("ferry", "good",
                              f"{zh(i)}回到骨干覆盖区,{len(n.bundles)} 束开始多跳回传", True)
                # 卸载:束直接转给有骨干路由的邻居,沿其路由快速回传
                if n.bundles:
                    for j in self.adj[i]:
                        nj = self.nodes[j]
                        if nj.role == "rover" or not nj.alive or nj.sleeping:
                            continue
                        rj = nj.routing.get("BASE-00")
                        if rj and rj["cost"] < INF:
                            give = n.bundles[:4]
                            n.bundles = n.bundles[4:]
                            for pkt in give:
                                pkt.pop("wait_since", None)
                                pkt["prev"] = i
                                pkt["visited"].add(i)
                                nj.packets.append(pkt)
                            key = (i, j) if i < j else (j, i)
                            self.ferry_marks[key] = self.t
                            break

    def _earth(self):
        """潮汐锁定:月球始终同一面朝向地球,地球永不升起/落下,
        地月链路始终可见,数据实时回传,无遮挡缓冲。"""
        up = True
        self._earth_up = up
        # 汇聚交付(始终实时回传)
        base = self.nodes["BASE-00"]
        for pkt in base.packets:
            if pkt["group"] in self.dupe_seen:
                continue
            self.dupe_seen.add(pkt["group"])
            if len(self.dupe_seen) > 4096:
                self.dupe_seen = set(list(self.dupe_seen)[-2048:])
            self.delivered += 1
            self.hops_sum += pkt["hops"]
            self.earth_flushed += 1
        base.packets = []

    def _stats(self):
        alive = [n for n in self.nodes.values() if n.alive]
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
            if self.nodes[i].role == "rover":
                for j in self.adj[i]:
                    if j in uf:
                        uf[find(i)] = find(j)
        parts = set()
        for i in uf:
            if find(i) != find("BASE-00"):
                parts.add(find(i))
        self.partitions = len(parts)
        covered = sum(1 for i in uf if find(i) == find("BASE-00"))
        cov = covered / len(alive) if alive else 0.0
        # 自愈播报
        if cov < 0.9:
            self.was_healing = True
        elif self.was_healing and cov >= 0.95:
            self.was_healing = False
            self.emit("heal", "good", "路由收敛完成:覆盖恢复,网络自愈(全程无人工参与)", True)
        self.prev_coverage = cov
        self.coverage = cov

    # ------------------------------------------------------------------ api
    def inject_disaster(self, kind: str):
        if kind == "collapse":
            ci = self.rng.randrange(1, len(self.world.chambers))
            cand = [i for i, n in self.nodes.items()
                    if n.alive and n.role == "spike" and n.domain == ci and not n.border]
            self.rng.shuffle(cand)
            for i in cand[:2]:
                n = self.nodes[i]
                n.alive = False
                n.state = STATE_DEAD
                n._death_logged = True
            ch = self.world.chambers[ci]
            self.world.boulders.append({
                "x": ch["cx"], "y": self.world.yc(ch["cx"]), "r": 70})
            self._crush_nodes("塌方巨石")
            self._recompute_static()
            self._build_patrol(False)
            self.emit("disaster", "bad", f"腔室{letter(ci)}发生塌方:2 枚道钉被掩埋,巨石堆积,全网视距重算", True)
        elif kind == "heat":
            self.heat_until = self.t + 22.0
            self.emit("disaster", "bad", "热浪来袭:全网能耗 ×5,链路信噪比恶化 6dB,劣质链路开始脱落", True)
        elif kind == "critical":
            # 定向摧毁整条喉道(两枚边界道钉):制造真实分区,考验协议自愈与摆渡
            throat = self.rng.randrange(len(self.world.throats()))
            cand = [i for i, n in self.nodes.items()
                    if n.alive and n.role == "spike" and n.border
                    and self.world.domain_of(n.x)[0] == throat
                    and abs(n.x - (self.world.throats()[throat][0] + 100)) < 110]
            if not cand:
                cand = [i for i, n in self.nodes.items()
                        if n.alive and n.border and n.role == "spike"]
            for i in cand[:2]:
                n = self.nodes[i]
                n.alive = False
                n.state = STATE_DEAD
                n._death_logged = True
            self.emit("disaster", "bad",
                      f"定向摧毁 {throat+1} 号喉道两枚边界道钉:网络分区,失联区数据转入束存储,等待自愈/摆渡", True)
    def _crush_nodes(self, reason: str = "巨石"):
        """巨石落到/拖放到节点上:直接把它压坏(宕机),而不是被 SOS 一步步顶出巨石边缘。
        被压节点立即 alive=False / state=DEAD,既不休眠、也不触发自愈移动。"""
        killed = []
        for i in self.order:
            n = self.nodes[i]
            if not n.alive or n.role in ("base", "rover"):
                continue
            for b in self.world.boulders:
                if math.hypot(n.x - b["x"], n.y - b["y"]) <= b["r"]:
                    n.alive = False
                    n.state = STATE_DEAD
                    n.sleeping = False
                    n.sos = False
                    n.seek_target = None
                    n.move_target = None
                    n._death_logged = True
                    killed.append(i)
                    break
        if killed:
            names = "、".join(f"{zh(i)}({i})" for i in killed)
            self.emit("dead", "warn",
                      f"{reason}砸中 {len(killed)} 个节点:{names} 被压坏宕机", True)

    def move_obstacle(self, idx: int, x: float, y: float):
        if 0 <= idx < len(self.world.boulders):
            b = self.world.boulders[idx]
            b["x"], b["y"] = x, y
            self._crush_nodes("巨石")
            self._recompute_static()
            self._build_patrol(False)
            cut = sum(1 for a, bb in self.static_links)
            self.emit("geo", "info", f"巨石拖放:视距重算完成,当前可行链路 {cut} 条", False)

    def set_sleep(self, on: bool):
        self.sleep_on = on
        if not on:
            from .nodes import STATE_DYING
            for n in self.nodes.values():
                if n.state != STATE_DYING:
                    n.sleeping = False
        self.emit("sleep", "info", f"轮值休眠调度已{'开启(苏醒比例随流量负载自适应:高负载多醒/低负载省电,'
                                 f'配本地安全判据避免睡断路由)' if on else '关闭'}", True)

    # ------------------------------------------------------------------ snap
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
                "sos": n.sos, "moving": n.move_target is not None,
                "bundles": len(n.bundles), "pkts": len(n.packets),
                "nbrs": [[j, round(e["snr"], 1)] for j, e in n.neighbors.items()],
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
                "params": {"tb": P.T_B, "range": RANGE, "alpha": P.ALPHA,
                           "e_die": P.E_DIE},
                "snapshot": self.snapshot()}


ENGINE = Engine()


async def run_forever(broadcast):
    last = time.monotonic()
    while True:
        await asyncio.sleep(DT)
        now = time.monotonic()
        try:
            ENGINE.step(min(now - last, 0.5))
        except Exception as e:  # noqa
            ENGINE.emit("error", "bad", f"引擎异常: {e}", False)
        last = now
        if getattr(ENGINE, "_need_init", False):
            ENGINE._need_init = False
            broadcast(ENGINE.init_payload())
        broadcast(ENGINE.snapshot())
