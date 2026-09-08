# -*- coding: utf-8 -*-
"""强类型契约(Contract/DTO):跨模块传递的结构化数据 —— Rule 2.4 契约先行,
取代过去松散传递的裸 dict。所有 DTO 均为冻结 dataclass,字段语义集中在此定义。

用途:信标(Hello)/邻居条目(NeighborEntry)/报文(Packet)/节点快照(SnapshotNode)。
模块间只允许交换这些类型或其列表,禁止裸 dict 直接穿透业务层。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(frozen=True)
class Advertisement:
    """距离向量通告(DSDV):cost=累计代价; ms=到达本节点路径最弱 SoC(%);
    nh=下一跳; hc=到目的跳数(环路自灭依据)。"""
    cost: float
    ms: float
    nh: str | None = None
    hc: int = 0


@dataclass(frozen=True)
class Hello:
    """信标负载(compose_hello 输出, on_hello 消费)。
    携带:自身 id / 时间戳 / 电量 / 状态 / 邻居 id 集(2 跳视图) / 距离向量通告 adv /
    关键标志(crit, sos) / 角色与位置 / 积压(分布式负载感知信息素)。"""
    id: str
    t: float
    soc: float
    state: str
    nbrs: tuple[str, ...]
    adv: dict[str, Advertisement]
    crit: bool
    sos: bool
    role: str
    border: bool
    domain: int
    px: float
    py: float
    bl: int


@dataclass
class NeighborEntry:
    """节点邻居表中一个邻居的本地观测状态(信标 EWMA 平滑)。
    snr=EWMA 信噪比 dB;  ber=对数域 EWMA 误码;  load=积压 EWMA(ACO 信息素);
    adv=该邻居通告的距离向量; nbrs=其邻居 id 集; soc/state/px/py/sos 即时状态。"""
    first: float
    last: float
    snr: float
    adv: dict[str, Advertisement]
    nbrs: set[str] = field(default_factory=set)
    soc: float = 100.0
    state: str = "NORMAL"
    px: float | None = None
    py: float | None = None
    ber: float = 1e-12
    load: float = 0.0
    sos: bool = False


@dataclass
class Packet:
    """一条在途数据包(store-and-forward)。
    id=唯一序号; dst=汇聚点; prio=优先级(9 控制/2 普通); born=出生时刻;
    hops=已跳数; visited=已访问节点集(环路检测, 非全集判定); bytes=长度(BER 掷骰);
    retries=当前跳连续误码次数; group=关键束双路冗余组 id; prev=上一跳。"""
    id: int
    src: str
    dst: str
    prio: int
    born: float
    hops: int = 0
    visited: set[str] = field(default_factory=set)
    prev: str | None = None
    group: int | str = 0
    bytes: int = 256
    retries: int = 0
    wait_since: float | None = None


@dataclass(frozen=True)
class SnapshotNode:
    """前端节点快照(engine.snapshot 输出)。phys=上帝模式滑块可调物理参数;
    nbrs=邻居观测 [[id, snr, ber], ...]。"""
    id: str
    role: str
    x: float
    y: float
    soc: float
    state: str
    alive: bool
    sleeping: bool
    crit: bool
    domain: int
    border: bool
    sos: bool
    moving: bool
    bundles: int
    pkts: int
    temp: float
    seu: int
    phys: dict[str, Any]
    nbrs: list[list[Any]]
    nh: str | None
    cost: float | None
    ms: float | None
    anchor: bool


def adv_to_dicts(adv: dict[str, Advertisement]) -> dict[str, tuple]:
    """Advertisement 契约 → 信标线上载体(dict[str, tuple])。"""
    return {k: (v.cost, v.ms, v.nh, v.hc) for k, v in adv.items()}


def dicts_to_adv(adv: dict[str, tuple]) -> dict[str, Advertisement]:
    """信标线上载体 → Advertisement 契约。"""
    return {k: Advertisement(*v) for k, v in adv.items()}


def dto(obj) -> dict:
    """dataclass → 可 JSON 序列化 dict(供前端/日志)。"""
    return asdict(obj) if hasattr(obj, "__dataclass_fields__") else obj
