# -*- coding: utf-8 -*-
"""强类型契约(Contract/DTO):跨模块传递的结构化数据 —— Rule 2.4 契约先行,
取代过去松散传递的裸 dict。所有 DTO 字段语义集中在此定义。

分三类:
  1. 协议帧(信标)  : Advertisement / Hello  —— 节点间"线上"交换的载荷;
  2. 本地状态表    : NeighborEntry / Packet —— 节点内部维护的结构化状态;
  3. 对外快照      : SnapshotNode / PhysDTO / StatsDTO / DeployDTO / Snapshot /
                    InitPayload —— 引擎交给 API 层的强类型输出。
模块间只允许交换这些类型;JSON 序列化只发生在 API 层(main.py 调 dto())。
"""
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
class Event:
    """协议过程事件(时间线播报)。i=序号; t=仿真时刻; type=事件类型; sev=严重度;
    msg=文案; narr=是否作为顶部解说条展示。"""
    i: int
    t: float
    type: str
    sev: str
    msg: str
    narr: bool


@dataclass(frozen=True)
class PhysDTO:
    """节点物理参数快照(上帝模式滑块的当前值)。"""
    battery_mah: float
    i_tx: float
    supercap_pct: float
    tx_power_dbm: float
    rx_sensitivity_dbm: float
    ant_gain_dbi: float
    tilt_deg: float
    temp_c: float
    radiation_rad: float


@dataclass(frozen=True)
class SnapshotNode:
    """前端节点快照(engine.snapshot 输出)。phys=物理参数; nbrs=邻居观测
    [[id, snr, ber], ...]; nh/cost/ms=到基站的下一跳/代价/路径最弱 SoC。"""
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
    phys: PhysDTO
    nbrs: list[list[Any]]
    nh: str | None
    cost: float | None
    ms: float | None
    pending: bool
    stock: int
    pv_w: float
    laser_in: float
    laser_out: float
    charge_ma: float
    energy: bool


@dataclass(frozen=True)
class StatsDTO:
    """全网统计快照(HUD 指标条的数据源)。"""
    alive: int
    total: int
    awake: int
    coverage: float
    avg_soc: float
    min_soc: float
    critical: int
    sleeping: int
    bundles: int
    ferry_bundles: int
    partitions: int
    delivered: int
    lost: int
    retries: int
    damaged_drops: int
    avg_temp: float
    earth_up: bool
    earth_flushed: int
    sleep_on: bool
    sleep_duty: float
    heat: bool
    avg_hops: float
    range: float
    signal_cover: float
    time_scale: float


@dataclass(frozen=True)
class DeployDTO:
    """首次部署进度(前端"探索迷雾"数据源)。"""
    front: float
    base: float
    end: float
    done: bool


@dataclass(frozen=True)
class Snapshot:
    """单 tick 全网快照(引擎 → API 层的唯一输出契约)。"""
    cmd: str
    t: float
    nodes: list[SnapshotNode]
    params: dict[str, float]
    links: list[list[str]]
    power_links: list[list[Any]]
    flows: list[list[Any]]
    ferries: list[list[Any]]
    events: list[Event]
    boulders: list[dict[str, Any]]
    deploy: DeployDTO
    stats: StatsDTO


@dataclass(frozen=True)
class InitPayload:
    """WebSocket 建连首帧:世界几何 + 协议参数 + 当前快照(让前端一次性对齐)。"""
    cmd: str
    world: dict[str, Any]
    params: dict[str, float]
    snapshot: Snapshot


def dto(obj):
    """dataclass → 可 JSON 序列化 dict(仅 API 层使用; 递归处理嵌套 DTO 与列表)。"""
    return asdict(obj) if hasattr(obj, "__dataclass_fields__") else obj
