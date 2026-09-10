# -*- coding: utf-8 -*-
"""引擎职责模块:灾难注入与上帝模式(DisasterMixin)。

职责范围:塌方 / 温度浪潮 / 定向摧毁喉道三类灾难的注入,巨石拖放与压坏判定,
轮值休眠总开关,以及节点级物理参数与全局协议参数的覆写(上帝模式)。
依赖:读 Engine 的 world/nodes/rng;写节点存活状态、巨石几何、协议参数。
Calls: _collapse/_heat/_cut_throat/_crush_nodes/_recompute_static/_build_patrol/emit。
"""
import math

from ..nodes import RANGE, INF, STATE_DEAD, STATE_DYING
from ..world import letter
from .constants import *
from .engine_imports import P, zh


class DisasterMixin:
    """灾难注入与上帝模式 mixin,由 Engine 继承,self 即引擎实例。

    Globals Used: RANGE, INF, STATE_DEAD, STATE_DYING, P, zh。
    Invocation: API 层 /action/disaster|obstacle|sleep|param → 本 mixin 方法。
    """

    def inject_disaster(self, kind: str):
        """灾难注入入口:按 kind 分发到对应处置(策略表,替代 if-elif 分支链)。

        Args: kind ∈ {collapse, heat, critical}。Returns: None。
        """
        handler = self._DISASTERS.get(kind)
        if handler is None:
            self.emit("disaster", "warn", f"未知灾难类型:{kind}", False)
            return
        handler(self)

    def _collapse(self):
        """塌方:随机腔室内 2 枚道钉被掩埋 + 一块**大**落石贴壁堆积,全网视距重算。

        落石半径 COLLAPSE_R_MIN~MAX(明显大于天然巨石 34~56),但**贴一侧放置**、
        对侧硬性保留 ≥COLLAPSE_PASS_W 的可通行带宽,并预检巡逻走廊 BFS 仍贯通 ——
        逐级(挪位→缩半径)尝试直到满足;再不满足(该腔已多石)就退到能放下的
        最大尺寸,绝不封死通路。原实现固定 r=70 放腔室正中,恰好把走廊夹断,
        BFS 兜底成"穿过石头的中心线",月球车只好贴着石面蹭过去(穿石观感的来源之一)。

        Globals Used: STATE_DEAD, COLLAPSE_R_MIN/MAX, COLLAPSE_PASS_W。
        Calls: _place_collapse_boulder/_crush_nodes/_recompute_static/_build_patrol/emit。
        Returns: None。
        """
        ci = self.rng.randrange(1, len(self.world.chambers))
        cand = [i for i, n in self.nodes.items()
                if n.alive and n.role == "spike" and n.domain == ci and not n.border]
        self.rng.shuffle(cand)
        for i in cand[:2]:
            n = self.nodes[i]
            n.alive = False
            n.state = STATE_DEAD
            n._death_logged = True
        b = self._place_collapse_boulder(ci)
        self._crush_nodes("塌方巨石")
        self._recompute_static()
        self._build_patrol(False)
        self.emit("disaster", "bad",
                  f"腔室{letter(ci)}发生塌方:2 枚道钉被掩埋,巨石(r={b['r']:.0f})堆积,"
                  f"全网视距重算", True)

    def _place_collapse_boulder(self, ci: int) -> dict:
        """塌方落石落点求解:尽可能大,但硬性保证通路(通行带 + 走廊贯通)。

        候选空间 = 半径阶梯(110→54, 从大到小) × 腔室内多个 x 位置 × 两侧 × 贴壁程度,
        取第一个满足【管内 + 最宽自由带 ≥COLLAPSE_PASS_W + 走廊 BFS 仍贯通(对照塌方
        前基线)】的**最大**候选 —— 宽敞腔室落 84~110 的大石(天然巨石仅 34~56),
        已有巨石对峙的窄腔室自动降级到放得下的尺寸。全部候选都给不出 48px 时,
        退到"最宽带 ≥34px(月球车直径+余量)"的最大者;再不行取全局最宽者,
        **绝不完全封死截面**。带宽检查廉价、走廊 BFS 昂贵,先宽度后 BFS 剪枝。

        Globals Used: COLLAPSE_R_MAX, COLLAPSE_R_MIN, COLLAPSE_PASS_W。
        Calls: _col_intervals/_patrol_columns/_corridor_bfs, world.inside/yc/r_at。
        Args: ci=腔室编号。Returns: 实际放入的巨石 dict(已加入 world.boulders)。
        """
        ch = self.world.chambers[ci]
        yc0 = self.world.yc(ch["cx"])
        side0 = self.rng.choice((-1, 1))
        # 塌方前走廊基线:本来就贯通才要求塌方后仍贯通(天然夹断的地图只要求带宽)
        _xs, _ = self._patrol_columns()
        _in, goal_before, _c = self._corridor_bfs(_xs, strict=False)
        need_corridor = goal_before is not None
        radii = [COLLAPSE_R_MAX, (COLLAPSE_R_MAX + COLLAPSE_R_MIN) / 2,
                 COLLAPSE_R_MIN, 74.0, 64.0, 54.0]
        x_offs = [0.0, 0.18, -0.18, 0.34, -0.34]
        fracs = (1.0, 0.85, 0.7, 0.55, 0.4, 0.25, 0.0)
        chosen = None           # 首选:管内 + ≥48px + 走廊贯通(半径优先,即最大石)
        plan_b = None           # 次选:管内 + ≥34px(放弃 48 或放弃走廊)
        best_any = None         # 兜底:管内 + 最宽带(哪怕 <34,只要没全封死)
        for rr in radii:
            for xo in x_offs:
                cx = ch["cx"] + xo * ch["hl"]
                yc = self.world.yc(cx)
                r_at = self.world.r_at(cx)
                for side in (side0, -side0):
                    for frac in fracs:
                        y = yc + side * frac * max(0.0, r_at - rr - 8.0)
                        cand = {"x": round(cx, 1), "y": round(y, 1), "r": round(rr, 1)}
                        self.world.boulders.append(cand)
                        inside = self.world.inside(cx, y, rr + 6.0)
                        widest = max((hi - lo for lo, hi in self._col_intervals(cx)),
                                     default=0.0)
                        goal = None
                        if inside and widest >= COLLAPSE_PASS_W:
                            xs, _ = self._patrol_columns()
                            _ints, goal, _came = self._corridor_bfs(xs, strict=False)
                        self.world.boulders.pop()
                        if not inside:
                            continue
                        if best_any is None or widest > best_any[0] + 1e-9:
                            best_any = (widest, cand)      # 全局最宽(保底不封死)
                        if widest >= 34.0 and (
                                plan_b is None or widest > plan_b[0] + 1e-9):
                            plan_b = (widest, cand)        # ≥34px 的最宽者
                        if widest >= COLLAPSE_PASS_W and goal is not None \
                                or (widest >= COLLAPSE_PASS_W and not need_corridor):
                            return self._commit_boulder(cand)   # 半径阶梯从大到小 → 首个即最大
        for pick in (plan_b, best_any):
            if pick is not None:
                return self._commit_boulder(pick[1])
        return self._commit_boulder({           # 理论不可达:贴壁最小石也不留带
            "x": round(ch["cx"], 1), "y": round(yc0, 1), "r": 30.0})

    def _commit_boulder(self, cand: dict) -> dict:
        """把选定的塌方巨石真正加入世界。Args: cand=巨石 dict。Returns: 同一 dict。"""
        self.world.boulders.append(cand)
        return cand

    def _heat(self):
        """温度浪潮: 节点温度快速升至 ≈75°C —— kTB 噪声底抬升 + 高温 NF 恶化 →
        边缘链路 SNR 跌破门限熔断;热降额放大耗电 → 加速电池衰减。
        链路/能耗恶化全部走物理耦合,无硬编码。

        时长以"真实秒"给定(HEAT_DURATION_S),按当前 time_scale 折算成仿真秒 ——
        否则默认 100x 下一个 tick 就跨过整个窗口,热浪等于没发生。

        Globals Used: HEAT_DURATION_S, TIME_SCALE_DEFAULT。Returns: None。
        """
        scale = getattr(self, "time_scale", TIME_SCALE_DEFAULT)
        self.heat_until = self.t + HEAT_DURATION_S * scale
        self.emit("disaster", "bad",
                  "温度浪潮来袭:节点升温至 75°C,噪声底抬升+灵敏度恶化,"
                  "边缘链路开始熔断,电池热降额加速耗电", True)

    def _cut_throat(self):
        """定向摧毁整条喉道:击杀该喉道窗口内的**全部**道钉(含上下两条链的
        边界节点),制造真实分区,考验协议自愈与摆渡。

        窗口从 ±60px 逐级加宽直到确实分区(与 physics_check 同口径)。原实现
        只杀 2 颗边界道钉 —— 双链部署后另一条链直接把断口吸收,按钮按下去
        partitions=0、两端节点自然"动都不动",分区→摆渡→自愈的演示全然
        不发生(实测 seed 7 连点 5 次全部未分区)。

        Globals Used: STATE_DEAD。Calls: _recompute_static/_update_adj/_stats/emit。
        Returns: None。
        """
        throats = self.world.throats()
        throat = self.rng.randrange(len(throats))
        a, b = throats[throat]
        mid = (a + b) / 2
        killed = 0
        for half in (60, 90, 120, 160, 220):
            victims = [n for n in self.nodes.values()
                       if n.alive and n.role == "spike" and abs(n.x - mid) <= half]
            for n in victims:
                n.alive = False
                n.state = STATE_DEAD
                n._death_logged = True
            killed += len(victims)
            self._recompute_static()
            self._update_adj()
            self._stats()
            if self.partitions > 0:
                break
        self.emit("disaster", "bad",
                  f"定向摧毁 {throat+1} 号喉道:两侧共 {killed} 枚道钉阵亡(含上下链边界),"
                  f"网络分区,失联区数据转入束存储,等待自愈/摆渡", True)

    _DISASTERS = {"collapse": _collapse, "heat": _heat, "critical": _cut_throat}

    def _crush_nodes(self, reason: str = "巨石"):
        """巨石落到/拖放到节点上:直接把它压坏(宕机),而不是被 SOS 一步步顶出巨石边缘。
        被压节点立即 alive=False / state=DEAD,既不休眠、也不触发自愈移动。

        Globals Used: STATE_DEAD。Args: reason=播报用原因。Returns: None。
        """
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
                    n._death_logged = True
                    killed.append(i)
                    break
        if killed:
            names = "、".join(f"{zh(i)}({i})" for i in killed)
            self.emit("dead", "warn",
                      f"{reason}砸中 {len(killed)} 个节点:{names} 被压坏宕机", True)

    def move_obstacle(self, idx: int, x: float, y: float):
        """拖放一块巨石到新位置:压坏其下节点 → 全网视距重算 → 重建巡逻走廊 → 播报链路增减。

        Args: idx=巨石序号; x/y=新坐标。Returns: None。
        """
        if not (0 <= idx < len(self.world.boulders)):
            return
        before = set(self.static_links)
        b = self.world.boulders[idx]
        b["x"], b["y"] = x, y
        self._crush_nodes("巨石")
        self._recompute_static()
        self._build_patrol(False)
        after = set(self.static_links)
        self.emit("geo", "info",
                  f"巨石拖放:视距重算完成,当前可行链路 {len(after)} 条"
                  f"(断开 {len(before - after)} / 新增 {len(after - before)})", False)

    def set_sleep(self, on: bool):
        """休眠调度总开关:关闭时把所有非垂危节点的 sleeping 标志复位(唤醒),并广播事件。

        Globals Used: STATE_DYING。Calls: self.emit。
        Args: on=True 开启轮值休眠 / False 关闭并唤醒。Returns: None。
        """
        self.sleep_on = on
        if not on:
            for n in self.nodes.values():
                if n.state != STATE_DYING:
                    n.sleeping = False
        state = ("开启(苏醒比例随流量负载自适应:高负载多醒/低负载省电,"
                 "配本地安全判据避免睡断路由)" if on else "关闭")
        self.emit("sleep", "info", f"轮值休眠调度已{state}", True)

    # ------------------------------------------------------------------ 上帝模式

    def apply_override(self, node_id: str, params: dict):
        """节点级物理参数覆写(前端滑块): 只改参数,不做即时重算/广播 ——
        引擎每 0.3s 一个周期,参数最迟下一拍生效(避免滑块拖动风暴打满事件循环)。

        Globals Used: STATE_DEAD, zh。Calls: Node.apply_override/self.emit。
        Args: node_id=目标节点; params=参数名→值。Returns: {"ok": bool, ...}。
        """
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
            self.emit("dead", "bad", f"☠ {node_id} 临界报废(温度突破 100°C,芯片烧毁)", True)
        elif params:
            self.emit("override", "info",
                      f"⚑ 上帝模式:{node_id} 参数覆写 {params}", False)
        return {"ok": True, "narration": narration}

    def set_global_param(self, params: dict):
        """全局协议参数覆写(信标周期/门限/度量权重等,前端滑块):
        引擎下一拍自然生效;范围与白名单校验在 Params.set 中完成。

        Globals Used: P。Args: params=参数名→值。Returns: {"ok": bool, ...}。
        """
        for k, v in params.items():
            try:
                P.PARAMS.set(k, v)
            except (KeyError, ValueError) as e:
                return {"ok": False, "error": str(e)}
        if params:
            self.emit("override", "info",
                      f"⚑ 协议参数调整:{params}(引擎下一拍生效,全网节点即刻适用)", False)
        return {"ok": True, "params": P.PARAMS.export()}
