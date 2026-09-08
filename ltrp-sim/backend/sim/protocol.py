# -*- coding: utf-8 -*-
"""LTRP 协议核心:信标 / 2 跳视图 / 割点自识别 / 分布式 Bellman-Ford(DSDV) / 度量双模式
所有函数只访问节点本地状态 —— 没有任何全局视图。
协议参数集中在 PARAMS(可被前端上帝模式滑块实时覆写),默认值与原常量一致。"""
import math

from .nodes import INF, STATE_DYING


class Params:
    """可实时调的协议参数: 前端滑块 → /action/param → PARAMS.set()
    SPEC 给出每个参数的合法范围(白名单+范围校验)。"""
    DEFAULTS = {
        "tb": 0.9,          # 信标周期 s
        "gamma": 12.0,      # 建链 SNR 门限 dB
        "alpha": 8.0,       # 逐跳实时性罚项
        "e_die": 40.0,      # 安全路径能量判据:路径最弱节点 SoC 阈值
        "q_cap": 48,        # 每目的束队列上限
        "hops_max": 60,     # 最大跳数
        "pkt_ttl": 240.0,   # 报文 TTL s
    }
    SPEC = {
        "tb": (0.3, 5.0), "gamma": (0.0, 30.0), "alpha": (0.0, 30.0),
        "e_die": (0.0, 80.0), "q_cap": (8, 200), "hops_max": (10, 200),
        "pkt_ttl": (30.0, 1200.0),
    }

    def __init__(self):
        self.reset()

    def reset(self):
        self.tb = 0.9
        self.n_fail = 3            # 判死:连续 N 个信标超时(不暴露给滑块)
        self.t_wait = 2.5          # 无路由超时 → 束模式(不暴露给滑块)
        self.gamma = 12.0
        self.alpha = 8.0
        self.e_die = 40.0
        self.q_cap = 48
        self.hops_max = 60
        self.pkt_ttl = 240.0
        self.max_cost = 3000.0     # 通告代价上限(不暴露给滑块)

    def set(self, key: str, value):
        if key not in Params.SPEC:
            raise KeyError(f"parameter '{key}' is not mutable")
        lo, hi = Params.SPEC[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) \
                or not (lo <= value <= hi):
            raise ValueError(f"'{key}' must be a number in [{lo}, {hi}]")
        setattr(self, key, type(getattr(self, key))(value))

    def export(self) -> dict:
        return dict(Params.DEFAULTS) | {k: getattr(self, k) for k in Params.DEFAULTS}


PARAMS = Params()


def link_cost(a, b, snr: float | None = None, ber: float = 0.0,
              tx_load: float = 0.0, rx_load: float = 0.0) -> float:
    """DLER 式单跳代价 + 链路质量项 + 拥塞项(分布式负载感知)。
    输入全部是节点本地可见量: 邻居信标携带的 EWMA SNR/BER/积压,加上自己的积压 ——
    相当于把集中式 ACO 信息素分布到了每条信标里,仍无全局视图。"""
    d = math.hypot(a.x - b.x, a.y - b.y)
    c = 12.0 + d / 40.0 + PARAMS.alpha
    if snr is not None and snr < 15.0:
        c += (15.0 - snr) * 0.25          # 低信噪比惩罚
    if ber > 1e-9:
        c += min(3.0, ber * 3000.0)       # 高误码惩罚(熔断边缘 ≈3)
    c += min(4.0, (tx_load + rx_load) * 0.05)   # 双端队列积压惩罚
    return c


def compose_hello(n, t: float) -> dict:
    """信标:携带邻居表(供 2 跳视图)+ 距离向量通告(含下一跳,供水平分割过滤)
    + 本地积压 bl(供邻居做分布式负载感知选路)。
    保护态节点照常通告 —— 由各节点的安全路径判据自然绕行(DLER 双模式)"""
    adv = {}
    for d, v in n.routing.items():
        if v["cost"] <= PARAMS.max_cost:
            adv[d] = (v["cost"], min(v["ms"], n.soc), v["nh"],
                      v.get("hc", 0))   # hc=到目的跳数,环路旋转每圈+1,超限废弃
    return {
        "id": n.id, "t": t, "soc": n.soc, "state": n.state,
        "nbrs": sorted(n.neighbors.keys()),
        "adv": adv, "crit": n.is_critical,
        "role": n.role, "border": n.border, "domain": n.domain,
        "px": round(n.x, 1), "py": round(n.y, 1),   # 携带位置,供邻居知悉/重连决策
        "bl": len(n.bundles) + len(n.packets),       # 本地积压(束+包)
        "sos": n.sos,   # 求救标志:自愈移动中 → 邻居可据此链式跟进接力(长断口多节点串联)
    }


def on_hello(n, h: dict, snr: float, t: float, ber: float = 1e-12):
    e = n.neighbors.get(h["id"])
    if e is None:
        n.neighbors[h["id"]] = {
            "first": t, "last": t, "snr": snr, "adv": h["adv"],
            "nbrs": set(h["nbrs"]), "soc": h["soc"], "state": h["state"],
            "px": h.get("px"), "py": h.get("py"),
            "ber": max(ber, 1e-12), "load": float(h.get("bl", 0)),
            "sos": bool(h.get("sos", False)),
        }
        n._topo_dirty = True
    else:
        old_nbrs = e["nbrs"]
        e.update(last=t, adv=h["adv"], nbrs=set(h["nbrs"]),
                 soc=h["soc"], state=h["state"], px=h.get("px"), py=h.get("py"))
        e["sos"] = bool(h.get("sos", False))
        e["snr"] = 0.7 * e["snr"] + 0.3 * snr   # EWMA 平滑
        # BER 跨数量级:在 log 域做 EWMA
        ob, nb = max(e.get("ber", 1e-12), 1e-12), max(ber, 1e-12)
        e["ber"] = 10 ** (0.7 * math.log10(ob) + 0.3 * math.log10(nb))
        # 积压(分布式信息素):EWMA 平滑
        e["load"] = 0.8 * e.get("load", 0.0) + 0.2 * float(h.get("bl", 0))
        if e["nbrs"] != old_nbrs:
            n._topo_dirty = True


def expire_neighbors(n, t: float):
    dead = [j for j, e in n.neighbors.items()
            if t - e["last"] > PARAMS.n_fail * PARAMS.tb]
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
    否则 →最大-最小剩余能量(网络生存时间优先)。
    链路代价含 SNR/BER 质量项与双端积压拥塞项 —— 全部来自本地邻居表。
    防环:通告携带跳数 hc;水平分割拦 2 跳环,stale 通告形成的多跳环
    其 hc 每传一圈 +1,超过 hops_max 即废弃 → 环路自灭(纯本地判定)。"""
    routing = {n.id: {"cost": 0.0, "nh": None, "ms": n.soc, "hc": 0}}
    my_load = float(len(n.bundles) + len(n.packets))
    cands = {}
    for j, e in n.neighbors.items():
        jn = nodes_by_id.get(j)
        if jn is None or not jn.alive or jn.sleeping or jn.state == STATE_DYING:
            continue
        lc = link_cost(n, jn, e.get("snr"), e.get("ber", 0.0),
                       my_load, e.get("load", 0.0))  # 与目的地无关,每个邻居只算一次
        for d, v in e["adv"].items():
            cost, ms, _nh = v[0], v[1], v[2]
            hc = v[3] if len(v) > 3 else 0
            if d == n.id or cost >= INF or cost > PARAMS.max_cost:
                continue
            if hc + 1 > PARAMS.hops_max:
                continue   # 跳数超限(环路旋转时每圈+1) → 废弃该 stale 通告
            c = lc + cost
            if c > PARAMS.max_cost:
                continue   # 累计代价封顶: count-to-infinity 伪路由不会无限膨胀才归 INF
            m = min(ms, jn.soc)
            cands.setdefault(d, []).append((c, m, j, hc))
    for d, lst in cands.items():
        safe = [x for x in lst if x[1] >= PARAMS.e_die]
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
        routing[d] = {"cost": best[0], "nh": best[2], "ms": best[1],
                      "hc": best[3] + 1}
    n.routing = routing
