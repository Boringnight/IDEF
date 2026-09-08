# -*- coding: utf-8 -*-
"""引擎职责模块:灾难注入与上帝模式(DisasterMixin)."""

import math

from ..nodes import RANGE, INF, STATE_DEAD, STATE_DYING
from ..world import letter
from .constants import *
from .engine_imports import P, zh



class DisasterMixin:
    """灾难注入与上帝模式 mixin,由 Engine 继承,self 即引擎实例。"""

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
            # 温度浪潮: 节点温度快速升至 ≈75°C ——
            # kTB 噪声底抬升 + 高温 NF 恶化 → 边缘链路 SNR 跌破门限熔断;
            # 热降额放大耗电 → 加速电池衰减。链路/能耗恶化全部走物理耦合,无硬编码。
            self.heat_until = self.t + 22.0
            self.emit("disaster", "bad",
                      "温度浪潮来袭:节点升温至 75°C,噪声底抬升+灵敏度恶化,"
                      "边缘链路开始熔断,电池热降额加速耗电", True)
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

    # ------------------------------------------------------------------ 上帝模式

    def apply_override(self, node_id: str, params: dict):
        """节点级物理参数覆写(前端滑块): 只改参数,不做即时重算/广播 ——
        引擎每 0.3s 一个周期,参数最迟下一拍生效(避免滑块拖动风暴打满事件循环)"""
        node = self.nodes.get(node_id)
        if node is None:
            return {"ok": False, "error": "no such node"}
        for k, v in params.items():
            try:
                node.apply_override(k, v)
            except (KeyError, ValueError) as e:
                return {"ok": False, "error": str(e)}
        narration = None
        if node.alive and node.role != "base" and node.temp_c >= 100:
            node.alive = False
            node.state = STATE_DEAD
            node.sleeping = True
            node._death_logged = True
            narration = f"☠ 惨剧发生:{zh(node_id)} 温度突破 100°C 临界值,芯片烧毁,节点当场报废。"
            self.emit("dead", "bad", f"☠ {node_id} 临界报废(温度突破 100°C,芯片烧毁)",
                      True)
        elif params:
            self.emit("override", "info",
                      f"⚑ 上帝模式:{node_id} 参数覆写 {params}", False)
        return {"ok": True, "narration": narration}

    def set_global_param(self, params: dict):
        """全局协议参数覆写(信标周期/门限/度量权重等,前端滑块):
        引擎下一拍自然生效;范围与白名单校验在 Params.set 中完成"""
        for k, v in params.items():
            try:
                P.PARAMS.set(k, v)
            except (KeyError, ValueError) as e:
                return {"ok": False, "error": str(e)}
        if params:
            self.emit("override", "info",
                      f"⚑ 协议参数调整:{params}(引擎下一拍生效,全网节点即刻适用)", False)
        return {"ok": True, "params": P.PARAMS.export()}

    # ------------------------------------------------------------------ snap
