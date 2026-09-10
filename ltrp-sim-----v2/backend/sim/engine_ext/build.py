# -*- coding: utf-8 -*-
"""引擎职责模块:世界构建与链路真值(WorldBuilderMixin)。

职责范围:世界生成 / 重置编排 / 静态链路与邻接表 / 事件流 / 链路预算(SNR+阴影衰落)。
节点布点已拆到 spawn.py(SpawnMixin),探针撒布在 deploy.py(DeployMixin)。
依赖:读 Engine 的 nodes/order/world;写 static_links/adj/events/_fade。
Calls: World/reseed/P.PARAMS.reset, physics.link_budget, Event。
"""
from collections import OrderedDict   # 交付去重表:有序字典充当"先进先出"的组 id 集合

from ..contracts import Event
from ..nodes import RANGE, INF, reseed
from .. import physics
from ..world import World
from .constants import *
from .engine_imports import P, random


class WorldBuilderMixin:
    """世界构建与链路 mixin,由 Engine 继承,self 即引擎实例。

    Globals Used: SLEEP_DUTY_MIN, TIME_SCALE_DEFAULT, RANGE, INF, P, random。
    Lifecycle: Engine() → reset(seed) → step() 循环(信标投递靠 adj,链路质量靠 _snr)。
    """

    def __init__(self):
        self.reset()

    # ------------------------------------------------------------------ setup

    def reset(self, seed: int = MAP_SEED):
        """重置仿真:重建世界与节点、恢复参数与统计、重算链路与巡逻走廊,并播报上电。

        演示版:地图已固定 —— 传入任何 seed 都会被强制为 MAP_SEED(同一张默认加宽地图)。

        Globals Used: TIME_SCALE_DEFAULT, MAP_SEED。
        Calls: _reset_state/_spawn/_recompute_static/_build_patrol/emit。
        Args: seed=地图种子(演示版忽略,恒为 MAP_SEED)。Returns: None。
        """
        self._reset_state(MAP_SEED)
        self._spawn()
        # 首次部署 / 面包屑撒布:从基站(最左)向右推进的探索前沿(每次重置都要清空)
        self.deploy_front = self.nodes["BASE-00"].x
        self.deploy_done = False
        self._deploy_gap = None
        self.time_scale = TIME_SCALE_DEFAULT
        self._recompute_static()
        self._build_patrol()
        self.emit("boot", "info", "系统上电:节点仅凭本地信标开始邻居发现(无全局视图)", True)

    def _reset_state(self, seed: int):
        """清空并初始化全部引擎状态(世界/统计/随机源/协议参数),不布点。

        Globals Used: SLEEP_DUTY_MIN, MAP_SEED。Calls: World/reseed/P.PARAMS.reset。
        Args: seed=地图种子(演示版恒为 MAP_SEED)。Returns: None。
        """
        seed = MAP_SEED                            # 演示版固定地图:只有这一张
        self._need_init = False
        self.world = World(seed)
        self._cover_pts = None          # 洞穴覆盖采样点缓存(随地图失效)
        reseed(seed)                    # 节点物理层随机源按地图种子播种 → 实验可复现
        self.t = 0.0
        self.tick = 0
        self.nodes: dict = {}
        self.order: list[str] = []
        self.static_links: list[tuple] = []
        self.adj: dict[str, set] = {}
        self.flows: dict[tuple, float] = {}       # (a,b) -> 最近数据时刻
        self.ferry_marks: dict[tuple, float] = {}
        self.ferry_log_t: dict[tuple, float] = {}
        self.events: list[Event] = []
        self.ev_sent = 0
        self.heat_until = -1.0
        self._fade: dict[tuple, float] = {}       # 每对链路的慢变阴影衰落(OU 过程)
        self.sleep_on = False      # 默认关闭(按钮开启后:冗余道钉流量自适应休眠)
        self.sleep_duty = SLEEP_DUTY_MIN   # 目标苏醒比例(引擎按流量负载调控)
        self._reset_counters()
        P.PARAMS.reset()            # 协议参数恢复默认(上帝模式调参随重置失效)
        self._earth_up = True          # 潮汐锁定:地球始终可见,无升降/遮挡
        self.rng = random.Random(seed)  # 引擎随机源:与地图同种子 → 同一 seed 全流程可复现
        self._pkt_seq = 0

    def _reset_counters(self):
        """清零交付/丢失/误码等统计量。Args: None。Returns: None。"""
        self.delivered = 0
        self.hops_sum = 0
        self.earth_flushed = 0
        self.lost = 0
        self.retries = 0            # 逐跳误码重传总数
        self.damaged_drops = 0      # 连续误码丢弃的报文数
        self._dmg_log_t: dict[str, float] = {}   # 误码丢弃事件限流(每节点 3s 一条)
        self._heal_moved_any = False        # 本 tick 是否有节点自愈移动(刷静态链路用)
        self.dupe_seen = OrderedDict()      # 交付去重(组 id):有序 → 超限按最旧淘汰,确定性
        self.was_healing = False
        self.partitions = 0
        self.coverage = 1.0

    def new_map(self, seed: int | None = None):
        """演示版:地图已固定 —— 无论请求什么 seed(含随机),都重置回同一张
        默认加宽地图;前端"随机地图"按钮因此等效于"重置演示"。

        Globals Used: MAP_SEED。Args: seed=请求种子(忽略)。Returns: None。
        """
        self.reset(MAP_SEED)
        self._need_init = True
        self.emit("map", "info",
                  f"演示版使用固定默认地图(seed={MAP_SEED},纵向加宽):"
                  f"{len(self.world.chambers)} 个腔室 · {len(self.world.boulders)} 块巨石,"
                  f"已重置,期待自组织", True)

    def _recompute_static(self):
        """重算全部静态链路(视距+距离),供引擎投递与能量树使用。

        复杂度 O(n²)·LOS;仅在布点/撒布/拖巨石/自愈移动后调用。
        Args: None。Returns: None。
        """
        ids = [i for i, n in self.nodes.items()
               if n.alive and n.role != "rover"]
        self.static_links = []
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                a, b = self.nodes[ids[x]], self.nodes[ids[y]]
                if a.dist(b) <= RANGE and self.world.los((a.x, a.y), (b.x, b.y)):
                    self.static_links.append((a.id, b.id))

    def _update_adj(self):
        """刷新邻接表(只含清醒节点)+ 月球车的动态可达邻居。

        Args: None。Returns: None(写 self.adj)。
        """
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
        """记录一条协议过程事件(供前端时间线/顶部解说条)。

        Args: type_=事件类型; sev=严重度(good/warn/bad/info); msg=文案;
              narr=是否作为解说条展示。Returns: None。
        """
        self.events.append(Event(i=len(self.events), t=round(self.t, 1),
                                 type=type_, sev=sev, msg=msg, narr=narr))

    # ------------------------------------------------------------------ 物理

    def _snr(self, a, b) -> dict | None:
        """链路预算物理模型(FSPL+热噪声+倾角惩罚+温度耦合) + 慢变阴影衰落:
        熔岩管壁多径/局部遮挡用每对链路的 OU 过程近似(均值 0,稳态 σ≈2.5dB,
        相关时间 ≈5s),再叠加 ±2.5dB 测量抖动。衰落同时作用于 SNR/BER/余量,
        让边缘链路偶发跌入高 BER 区间 —— 逐跳损伤/重传因此真实可见。

        Args: a=发送节点; b=接收节点。Returns: {"snr_db","ber","up"} 或 None(物理不通)。
        """
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
