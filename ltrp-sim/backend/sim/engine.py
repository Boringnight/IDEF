# -*- coding: utf-8 -*-
"""LTRP 仿真引擎(编排器):只做"物理世界"和消息投递,协议决策全部在各节点本地完成。

架构(Rule 2.1 分层):本文件是**业务编排层**,把职责分散到多个单一职责 mixin 组件,
不再维护 1500 行的 God Class。Engine 通过继承组合各组件:
  - WorldBuilderMixin   : 世界/布点/静态链路/邻接/信噪比(build.py)
  - RoverMotionMixin    : 月球车巡逻物理(rover.py)
  - SelfHealMixin       : 节点自愈移动/锚点/分级推进/链式跟进(heal.py)
  - SleepSchedulerMixin : 流量自适应休眠(sleep.py)
  - DataPlaneMixin      : 数据面/逐跳损伤/摆渡/地球链路(traffic.py)
  - StatsSnapshotMixin  : 统计/快照/init(stats.py)
  - DisasterMixin       : 灾难注入/上帝模式/disaster.py
  - EngineStepMixin     : 主循环 step() 编排

各组件方法体保持 self 指向 Engine 实例(继承后即运行时对象),逻辑零改动。
所有引擎常量统一在 engine/constants.py 配置(Rule 2.1 零硬编码)。
"""
import asyncio
import time

from . import protocol as P       # 协议核心:信标/割点/DSDV/参数
from .nodes import Node, RANGE, STATE_DEAD, STATE_DYING, STATE_PROTECTED
from .engine_ext.build import WorldBuilderMixin
from .engine_ext.placement import PlacementMixin
from .engine_ext.rover import RoverMotionMixin
from .engine_ext.heal_motion import SelfHealMotionMixin
from .engine_ext.heal_logic import SelfHealLogicMixin
from .engine_ext.sleep import SleepSchedulerMixin
from .engine_ext.traffic import DataPlaneMixin
from .engine_ext.stats import StatsSnapshotMixin
from .engine_ext.disaster import DisasterMixin
from .engine_ext.engine import EngineStepMixin
from .engine_ext.constants import (DT, BEACON_EVERY, RELAX_ROUNDS, SLEEP_PERIOD,
                                   SLEEP_DUTY_MIN)
from .engine_ext.engine_imports import zh


class Engine(WorldBuilderMixin, PlacementMixin, RoverMotionMixin,
             SelfHealMotionMixin,
             SelfHealLogicMixin, SleepSchedulerMixin, DataPlaneMixin,
             StatsSnapshotMixin, DisasterMixin, EngineStepMixin):
    """LTRP 仿真引擎编排器。

    Globals Used: DT, BEACON_EVERY, RELAX_ROUNDS, SLEEP_PERIOD, SLEEP_DUTY_MIN,
                  P, zh。
    Calls: 各 mixin 的 step 阶段方法; P.dsdv_relax / P.on_hello / P.compose_hello /
           P.expire_neighbors / P.cut_vertex; physics.link_budget。
    Lifecycle: __init__(WorldBuilderMixin) -> reset() -> step() 循环(每 DT 秒) ->
               snapshot() 推送。
    """


ENGINE = Engine()


async def run_forever(broadcast):
    """仿真服务器无休循环:每 DT 秒 step 一次并广播快照(发后即忘)。失败的单 tick 异常仅记日志不致命。

    Globals Used: ENGINE, DT。
    Calls: asyncio.sleep, ENGINE.step, ENGINE.emit, ENGINE.snapshot,
           ENGINE.init_payload, broadcast。
    Args: broadcast=回调(接收 dict,推送给全部 WS 客户端)。
    Returns: 仅在后台取消时退出。
    """
    last = time.monotonic()
    while True:
        await asyncio.sleep(DT)
        now = time.monotonic()
        try:
            ENGINE.step(min(now - last, 0.5))
        except Exception as e:  # noqa
            ENGINE.emit("error", "bad", f"引擎异常: {e}", False)
        last = now
        if getattr(ENGINE, "_need_init", False):
            ENGINE._need_init = False
            broadcast(ENGINE.init_payload())
        broadcast(ENGINE.snapshot())
