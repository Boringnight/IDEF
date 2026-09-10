# -*- coding: utf-8 -*-
"""引擎职责模块:数据面(DataPlaneMixin)。

职责范围:遥测生成 → 逐跳转发(含逐跳 BER 掷骰/重传/毒化路由) → 无路由束化 →
月球车摆渡(存储-携带-转发) → 地月回传交付。
依赖:读 Engine 的 nodes/adj/t/rng;写各 Node 的 packets/bundles 与全局流量统计。
Calls: _mk_pkt/_next_hop/_snr, physics.damage_prob, emit。
"""
from ..contracts import Packet
from ..nodes import Node, INF
from .. import physics
from .constants import *
from .engine_imports import P, zh


class DataPlaneMixin:
    """数据面 mixin,由 Engine 继承,self 即引擎实例。

    Globals Used: P, zh, ACK_BYTES(经 physics)。
    Invocation: Engine.step() 每 tick 调用 _traffic / _ferry / _earth。
    """

    def _mk_pkt(self, src: Node, ctrl: bool, group=None) -> Packet:
        """生成一条报文 DTO。Args: src=源节点; ctrl=是否控制类(高优先级/短帧);
        group=冗余组 id(None=自成一组)。Returns: Packet。"""
        self._pkt_seq += 1
        return Packet(
            id=self._pkt_seq, src=src.id, dst="BASE-00",
            prio=9 if ctrl else 2, born=self.t,
            visited={src.id}, prev=None,
            group=group if group is not None else self._pkt_seq,
            bytes=128 if ctrl else 256,   # 报文长度(BER 掷骰用)
        )

    def _traffic(self):
        """数据面主流程:先按节点本地采样节奏生成报文,再逐跳转发或束化。

        Globals Used: P, zh。Calls: _mk_pkt/_next_hop/_snr, physics.damage_prob。
        Args: None。Returns: None。
        """
        self._gen_packets()
        for i in self.order:
            n = self.nodes[i]
            if not n.alive or n.sleeping:
                continue
            self._drain_bundles(n)
            n.packets = self._forward_all(i, n)

    def _gen_packets(self):
        """遥测生成:束积压越深采样越慢(断连省电);8% 概率生成关键束(双路冗余)。"""
        for n in self.nodes.values():
            if not n.alive or n.sleeping or n.role == "base":
                continue
            if self.t < n.gen_t:
                continue
            if len(n.bundles) >= 40:
                n.gen_t = self.t + 15.0            # 断连深度省电:束严重积压时暂停采样
            elif len(n.bundles) >= 24:
                n.gen_t = self.t + 6.0             # 断连省电:降频采样
            else:
                n.gen_t = self.t + 2.2 + self.rng.random() * 0.8
            if self.rng.random() < 0.08:
                g = f"C{self._pkt_seq + 1}"
                n.packets.extend((self._mk_pkt(n, True, g),
                                  self._mk_pkt(n, True, g)))   # 关键束:双路冗余
            else:
                n.packets.append(self._mk_pkt(n, False))

    def _drain_bundles(self, n: Node):
        """路由恢复后把束转回待发包(每 tick 最多 6 条,避免突发打满队列)。"""
        if not n.bundles:
            return
        r = n.routing.get("BASE-00")
        if r and r["cost"] < INF:
            for _ in range(min(6, len(n.bundles))):
                n.packets.append(n.bundles.pop(0))

    def _forward_all(self, i: str, n: Node) -> list:
        """逐条转发本节点待发包,返回仍留在本节点的包(重传等待/新束化的)。

        Globals Used: P, zh。Calls: _next_hop/_snr/_damage。Args: i=节点 id; n=节点。
        Returns: 剩余 Packet 列表。
        """
        rest = []
        for pkt in n.packets:
            if self.t - pkt.born > P.PARAMS.pkt_ttl:
                self.lost += 1
                continue
            self._deliver_or_store(i, n, pkt, rest)
        return rest

    def _deliver_or_store(self, i: str, n: Node, pkt: Packet, rest: list):
        """单包一跳推进:可达则转发(过损伤判定),不可达则等待/束化。

        Args: i=节点 id; n=节点; pkt=报文; rest=本 tick 留在本节点的包列表。
        Returns: None。
        """
        nh = self._next_hop(n, pkt)
        nj = self.nodes[nh] if nh else None
        blocked = (nh is None or nj is None or nj.sleeping
                   or nh not in self.adj[i] or pkt.prev == nh)
        if blocked:
            self._store_bundle(n, pkt, nh, rest)
        else:
            self._hop(n, i, nj, nh, pkt, rest)

    def _store_bundle(self, n: Node, pkt: Packet, nh, rest: list):
        """下一跳不可用:先等 T_WAIT 过滤瞬态抖动,超时才进入束存储(容量满则丢最低优先级)。

        Args: n=节点; pkt=报文; nh=原下一跳(可能为 None); rest=等待重传列表。
        Returns: None。
        """
        if nh is not None and nh in self.nodes and not self.nodes[nh].alive:
            # 下一跳已宕机(非休眠):立即作废该路由,促上游节点下轮 DSDV 改道
            rr = n.routing.get(pkt.dst)
            if rr:
                rr["cost"] = INF
        if pkt.wait_since is None:
            pkt.wait_since = self.t
        if self.t - pkt.wait_since < P.PARAMS.t_wait:
            rest.append(pkt)
            return
        pkt.wait_since = None
        n.bundles.append(pkt)
        if len(n.bundles) > P.PARAMS.q_cap:
            n.bundles.sort(key=lambda p: p.prio)
            self.lost += 1
            n.bundles.pop(0)

    def _hop(self, n: Node, i: str, nj: Node, nh: str, pkt: Packet, rest: list):
        """执行一跳:BER 掷骰(数据帧+ACK 双判定),失败留待重传,连续 4 次损坏则丢弃并毒化路由。

        Globals Used: zh。Calls: _snr, physics.damage_prob。
        Args: n=发送节点; i=其 id; nj/nh=下一跳节点与 id; pkt=报文; rest=重传等待列表。
        Returns: None。
        """
        lk = self._snr(n, nj)
        p_dmg, ber = self._hop_damage(lk, pkt)
        if self.rng.random() < p_dmg:
            pkt.retries += 1
            self.retries += 1
            if pkt.retries > 3:
                self.lost += 1
                self.damaged_drops += 1
                rr = n.routing.get(pkt.dst)
                if rr:
                    rr["cost"] = INF
                if self.t - self._dmg_log_t.get(i, -99) > 3:
                    self._dmg_log_t[i] = self.t
                    self.emit("msg", "bad",
                              f"{zh(i)} → {nh} 连续 4 次误码/深衰落,报文丢弃并毒化路由"
                              f"({f'BER={ber:.0e}' if ber < 1.0 else '链路此刻中断'})", False)
                return
            rest.append(pkt)
            return
        pkt.retries = 0
        pkt.wait_since = None
        pkt.hops += 1
        n.hops_total += 1
        if pkt.hops > P.PARAMS.hops_max:
            self.lost += 1
            return
        pkt.prev = n.id
        pkt.visited.add(n.id)
        nj.packets.append(pkt)
        n.spend(0.012)
        nj.spend(0.006)
        key = (n.id, nh) if n.id < nh else (nh, n.id)
        self.flows[key] = self.t

    def _hop_damage(self, lk, pkt: Packet) -> tuple:
        """本跳的损伤概率与等效 BER:用发送时刻的实测信道(发射机本地可测),
        数据帧 + 14B ACK 双重判定;链路物理不可用(深衰落)时为必然失败。

        Args: lk=_snr 结果(可能为 None); pkt=报文。Returns: (p_dmg, ber)。
        """
        if lk is None or not lk["up"]:
            return 1.0, 1.0
        ber = lk["ber"]
        p_dmg = (1.0 - (1.0 - physics.damage_prob(ber, pkt.bytes))
                 * (1.0 - physics.damage_prob(ber, physics.ACK_BYTES)))
        return p_dmg, ber

    def _next_hop(self, n: Node, pkt: Packet):
        """按本地路由表取下一跳(必须仍是当前邻居)。Returns: 邻居 id 或 None。"""
        r = n.routing.get(pkt.dst)
        if r and r["cost"] < INF and r["nh"] in n.neighbors:
            return r["nh"]
        return None

    def _earth(self):
        """潮汐锁定:月球始终同一面朝向地球,地球永不升起/落下,
        地月链路始终可见,数据实时回传,无遮挡缓冲。"""
        self._earth_up = True
        base = self.nodes["BASE-00"]
        for pkt in base.packets:
            if pkt.group in self.dupe_seen:
                continue
            self.dupe_seen[pkt.group] = None
            if len(self.dupe_seen) > DUPE_SEEN_MAX:
                self.dupe_seen.popitem(last=False)   # 淘汰最旧条目(确定性,非随机截断)
            self.delivered += 1
            self.hops_sum += pkt.hops
            self.earth_flushed += 1
        base.packets = []
