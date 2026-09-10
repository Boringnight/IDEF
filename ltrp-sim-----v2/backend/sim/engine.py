# -*- coding: utf-8 -*-
"""LTRP 仿真引擎(编排器):只做"物理世界"和消息投递,协议决策全部在各节点本地完成。

架构(Rule 2.1 分层):本文件是**业务编排层**,通过多重继承组合各单一职责组件
(self 在运行时即 Engine 实例,组件之间只通过该实例协作):
  - WorldBuilderMixin   : 世界生成/重置/静态链路/邻接/事件流/链路预算(build.py)
  - SpawnMixin          : 基站/库存道钉/探针/月球车的初始布点与落点校正(spawn.py)
  - PlacementMixin      : 基站落点防孤立校正(placement.py)
  - DeployMixin         : 探针面包屑撒布(首次部署)(deploy.py)
  - PowerMixin          : 激光无线充电调度(能量树/bucket-brigade)(power.py)
  - CorridorMixin       : 无碰撞巡逻走廊求解与指派(corridor.py)
  - RoverMotionMixin    : 月球车/探针推进物理(rover.py)
  - SelfHealMotionMixin : 落点求解/移动约束/连通分量/锚点同步(heal_motion.py)
  - SelfHealLogicMixin  : 失联判定与前沿推进(heal_logic.py)
  - SleepSchedulerMixin : 流量自适应休眠(sleep.py)
  - DataPlaneMixin      : 数据面/逐跳损伤/地月回传(traffic.py)
  - FerryMixin          : 月球车摆渡(存储-携带-转发)(ferry.py)
  - StatsSnapshotMixin  : 统计/快照/init 载荷(stats.py)
  - DisasterMixin       : 灾难注入/上帝模式(disaster.py)
  - EngineStepMixin     : 主循环 step() 阶段编排(step.py)

所有引擎常量统一在 engine_ext/constants.py 配置(Rule 2.1 零硬编码)。
"""
import asyncio                      # 仿真主循环的异步定时
import time                         # 单调时钟:测量真实 tick 间隔

from .engine_ext.build import WorldBuilderMixin
from .engine_ext.spawn import SpawnMixin
from .engine_ext.placement import PlacementMixin
from .engine_ext.deploy import DeployMixin
from .engine_ext.power import PowerMixin
from .engine_ext.corridor import CorridorMixin
from .engine_ext.rover import RoverMotionMixin
from .engine_ext.heal_motion import SelfHealMotionMixin
from .engine_ext.heal_logic import SelfHealLogicMixin
from .engine_ext.sleep import SleepSchedulerMixin
from .engine_ext.traffic import DataPlaneMixin
from .engine_ext.ferry import FerryMixin
from .engine_ext.stats import StatsSnapshotMixin
from .engine_ext.disaster import DisasterMixin
from .engine_ext.step import EngineStepMixin
from .engine_ext.constants import DT


class Engine(WorldBuilderMixin, SpawnMixin, PlacementMixin, DeployMixin, PowerMixin,
             CorridorMixin, RoverMotionMixin, SelfHealMotionMixin,
             SelfHealLogicMixin, SleepSchedulerMixin, DataPlaneMixin, FerryMixin,
             StatsSnapshotMixin, DisasterMixin, EngineStepMixin):
    """LTRP 仿真引擎编排器(纯组合,无自有业务逻辑)。

    Globals Used: DT(见 run_forever)。
    Lifecycle: Engine() → reset(seed) → step(dt) 循环 → snapshot() 推送。
    """


ENGINE = Engine()


async def run_forever(broadcast):
    """仿真服务器无休循环:每 DT 秒 step 一次并广播快照(发后即忘)。
    失败的单 tick 异常仅记日志不致命,保证长跑演示不中断。

    Globals Used: ENGINE, DT。
    Calls: asyncio.sleep, ENGINE.step/emit/init_payload/snapshot, broadcast。
    Args: broadcast=回调(接收 Snapshot/InitPayload DTO,推送给全部 WS 客户端)。
    Returns: 仅在后台取消时退出。
    """
    last = time.monotonic()
    while True:
        await asyncio.sleep(DT)
        now = time.monotonic()
        try:
            ENGINE.step(min(now - last, 0.5))
        except Exception as e:  # noqa: BLE001 — 单 tick 失败不能拖垮整个仿真
            ENGINE.emit("error", "bad", f"引擎异常: {e}", False)
        last = now
        if getattr(ENGINE, "_need_init", False):
            ENGINE._need_init = False
            broadcast(ENGINE.init_payload())
        broadcast(ENGINE.snapshot())
