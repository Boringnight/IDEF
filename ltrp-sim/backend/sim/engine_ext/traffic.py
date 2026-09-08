# -*- coding: utf-8 -*-
"""引擎职责模块:数据面(DataPlaneMixin)."""

from ..nodes import Node, RANGE, INF
from .. import physics
from .constants import *
from .engine_imports import P, zh



class DataPlaneMixin:
    """数据面 mixin,由 Engine 继承,self 即引擎实例。"""

    def _mk_pkt(self, src: Node, ctrl: bool, group=None):
        self._pkt_seq += 1
        return {"id": self._pkt_seq, "src": src.id, "dst": "BASE-00",
                "prio": 9 if ctrl else 2, "born": self.t, "hops": 0,
                "visited": {src.id}, "prev": None,
                "group": group if group is not None else self._pkt_seq,
                "bytes": 128 if ctrl else 256,   # 报文长度(BER 掷骰用)
                "retries": 0}                     # 当前跳连续误码次数

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
                if self.t - pkt["born"] > P.PARAMS.pkt_ttl:
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
                    # (环路防护 = prev 单步回退检查 + 通告跳数上限,不用 visited 全集:
                    #  束重注入后的合法新路径可能再经过旧节点,全集判定会误杀好路由)
                    if nh is not None and nh in self.nodes and not self.nodes[nh].alive:
                        rr = n.routing.get(pkt["dst"])
                        if rr:
                            rr["cost"] = INF
                    # 无路由/环路风险:先等待 T_WAIT(过滤瞬态抖动),超时才进入束存储
                    if pkt.get("wait_since") is None:
                        pkt["wait_since"] = self.t
                    if self.t - pkt["wait_since"] < P.PARAMS.t_wait:
                        rest.append(pkt)
                        continue
                    pkt.pop("wait_since", None)
                    n.bundles.append(pkt)
                    if len(n.bundles) > P.PARAMS.q_cap:
                        n.bundles.sort(key=lambda p: p["prio"])
                        self.lost += 1
                        n.bundles.pop(0)
                    continue
                # 逐跳信道损伤(BER 掷骰: 数据帧 + 14B ACK 双重判定):
                # 用发送时刻的实测信道(发射机本地可测),而非信标历史 EWMA ——
                # 深衰落窗口内的每一跳都真实承受高误码;失败留在本节点下一拍重传
                # (本地行为,无全局视野),连续 4 次损坏则丢弃。
                lk = self._snr(n, nj)
                if lk is None or not lk["up"]:
                    p_dmg = 1.0          # 此刻链路物理不可用(深衰落) → 必然失败
                    ber = 1.0
                else:
                    ber = lk["ber"]
                    p_dmg = (1.0 - (1.0 - physics.damage_prob(ber, pkt["bytes"]))
                             * (1.0 - physics.damage_prob(ber, physics.ACK_BYTES)))
                if self.rng.random() < p_dmg:
                    pkt["retries"] += 1
                    self.retries += 1
                    if pkt["retries"] > 3:
                        self.lost += 1
                        self.damaged_drops += 1
                        # 链路层反馈: 连续损坏说明该链路(此刻)不可用 →
                        # 毒化此路由,促使本地 DSDV 下一轮改道(非对称链路失效同理)
                        rr = n.routing.get(pkt["dst"])
                        if rr:
                            rr["cost"] = INF
                        if self.t - self._dmg_log_t.get(i, -99) > 3:
                            self._dmg_log_t[i] = self.t
                            self.emit("msg", "bad",
                                      f"{zh(i)} → {nh} 连续 4 次误码/深衰落,报文丢弃并毒化路由"
                                      f"({f'BER={ber:.0e}' if ber < 1.0 else '链路此刻中断'})", False)
                    else:
                        rest.append(pkt)
                    continue
                pkt["retries"] = 0
                pkt.pop("wait_since", None)
                pkt["hops"] += 1
                n.hops_total += 1
                if pkt["hops"] > P.PARAMS.hops_max:
                    self.lost += 1
                    continue
                pkt["prev"] = n.id
                pkt["visited"].add(n.id)
                nj.packets.append(pkt)
                n.spend(0.012)
                nj.spend(0.006)
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
