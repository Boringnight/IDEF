# -*- coding: utf-8 -*-
"""LTRP 协议核心:信标 / 2 跳视图 / 割点自识别 / 分布式 Bellman-Ford(DSDV) / 度量双模式
所有函数只访问节点本地状态 —— 没有任何全局视图。"""
import math

from .nodes import INF, STATE_DYING

# ---- 协议参数 ----
T_B = 0.9            # 信标周期 s
N_FAIL = 3           # 判死:连续 N 个信标超时
T_WAIT_BUNDLE = 2.5   # 无路由超时 → 束模式
GAMMA = 12.0          # 建链 SNR 门限 dB
ALPHA = 8.0           # 逐跳实时性罚项
E_DIE = 40.0          # 安全路径能量判据:路径最弱节点 SoC 低于此值 → 切换 max-min 能量模式
Q_CAP = 48            # 每目的束队列上限
HOPS_MAX = 60
PKT_TTL = 240.0
MAX_COST = 3000.0     # 通告代价上限:超过视为不可达(抑制计数到无穷)


def link_cost(a, b) -> float:
    """DLER 式单跳代价:发射功率随距离增长 + 实时性罚项"""
    d = math.hypot(a.x - b.x, a.y - b.y)
    return 12.0 + d / 40.0 + ALPHA


def compose_hello(n, t: float) -> dict:
    """信标:携带邻居表(供 2 跳视图)+ 距离向量通告(含下一跳,供水平分割过滤)
    保护态节点照常通告 —— 由各节点的安全路径判据自然绕行(DLER 双模式)"""
    adv = {}
    for d, v in n.routing.items():
        if v["cost"] <= MAX_COST:
            adv[d] = (v["cost"], min(v["ms"], n.soc), v["nh"])
    return {
        "id": n.id, "t": t, "soc": n.soc, "state": n.state,
        "nbrs": sorted(n.neighbors.keys()),
        "adv": adv, "crit": n.is_critical,
        "role": n.role, "border": n.border, "domain": n.domain,
        "px": round(n.x, 1), "py": round(n.y, 1),   # 携带位置,供邻居知悉/重连决策
    }


def on_hello(n, h: dict, snr: float, t: float):
    e = n.neighbors.get(h["id"])
    if e is None:
        n.neighbors[h["id"]] = {
            "first": t, "last": t, "snr": snr, "adv": h["adv"],
            "nbrs": set(h["nbrs"]), "soc": h["soc"], "state": h["state"],
            "px": h.get("px"), "py": h.get("py"),
        }
        n._topo_dirty = True
    else:
        old_nbrs = e["nbrs"]
        e.update(last=t, adv=h["adv"], nbrs=set(h["nbrs"]),
                 soc=h["soc"], state=h["state"], px=h.get("px"), py=h.get("py"))
        e["snr"] = 0.7 * e["snr"] + 0.3 * snr   # EWMA 平滑
        if e["nbrs"] != old_nbrs:
            n._topo_dirty = True


def expire_neighbors(n, t: float):
    dead = [j for j, e in n.neighbors.items() if t - e["last"] > N_FAIL * T_B]
    for j in dead:
        del n.neighbors[j]
    if dead:
        n._topo_dirty = True
    return dead


def cut_vertex(n) -> bool:
    """割点自识别:在 2 跳视图上检查'删掉我之后邻居们是否散架'(纯本地判定)"""
    nbrs = set(n.neighbors.keys())
    if len(nbrs) < 2:
        return False
    adj = {a: set() for a in nbrs}
    for a in nbrs:
        for b in n.neighbors[a]["nbrs"]:
            if b in nbrs and b != a:
                adj[a].add(b)
                adj[b].add(a)
    seen, comps = set(), 0
    for a in nbrs:
        if a in seen:
            continue
        comps += 1
        stack, seen_add = [a], seen.add
        while stack:
            u = stack.pop()
            seen_add(u)
            stack.extend(v for v in adj[u] if v not in seen)
    return comps > 1


def dsdv_relax(n, nodes_by_id: dict):
    """分布式 Bellman-Ford 一轮松弛:只用邻居通告的距离向量
    度量双模式(DLER):存在安全路径(最弱节点≥E_DIE)→最小代价;
    否则 →最大-最小剩余能量(网络生存时间优先)"""
    routing = {n.id: {"cost": 0.0, "nh": None, "ms": n.soc}}
    cands = {}
    for j, e in n.neighbors.items():
        jn = nodes_by_id.get(j)
        if jn is None or not jn.alive or jn.sleeping or jn.state == STATE_DYING:
            continue
        lc = link_cost(n, jn)  # 与目的地无关,每个邻居只算一次
        for d, (cost, ms, _nh) in e["adv"].items():
            if d == n.id or cost >= INF or cost > MAX_COST:
                continue
            c = lc + cost
            m = min(ms, jn.soc)
            cands.setdefault(d, []).append((c, m, j))
    for d, lst in cands.items():
        safe = [x for x in lst if x[1] >= E_DIE]
        if safe:
            best = min(safe, key=lambda x: x[0])
        else:
            best = max(lst, key=lambda x: x[1])
        # 迟滞:上一轮的下一跳若仍可用且代价接近,则保持(抑制路由抖动/乒乓)
        prev = n.routing.get(d)
        if prev and prev["nh"]:
            for x in lst:
                if x[2] == prev["nh"] and x[0] <= best[0] * 1.08:
                    best = x
                    break
        routing[d] = {"cost": best[0], "nh": best[2], "ms": best[1]}
    n.routing = routing
