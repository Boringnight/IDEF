# -*- coding: utf-8 -*-
"""引擎常量(配置层,Rule 2.1 统一配置、零硬编码)。

所有引擎级可调常量集中于此,各职责模块 import 引用,避免主引擎与子模块的循环依赖。
"""
import random

# ---- 引擎 tick / 定时 ----
DT = 0.3                # tick 周期(秒,实时)
BEACON_EVERY = 1        # 每 tick 都发信标(0.3s,加速初始收敛)
RELAX_ROUNDS = 3        # 每 tick 的 DSDV 松弛轮数
SLEEP_PERIOD = 16.0     # 轮值休眠窗口(长窗口降低拓扑抖动)

# ---- 流量自适应休眠(duty cycling):苏醒比例随负载升降 ----
SLEEP_DUTY_MIN = 0.25   # 最低苏醒比例(低负载:保住连通主干即可,其余省电)
SLEEP_DUTY_MAX = 0.80   # 最高苏醒比例(高负载:多节点转发换取吞吐,并均摊能耗)
LOAD_TARGET = 3.0       # 每存活节点的目标积压(bundles+packets)个
LOAD_HYST = 1.2         # 目标附近死区(避免频繁升降档)
DUTY_STEP = 0.05        # 每次调节苏醒比例的步长

# ---- 月球车物理(实体碰撞) ----
ROVER_R = 14.0          # 物理半径(与巨石/岩壁判定用)
ROVER_CLEAR = 26.0      # 与巨石的间距余量
ROVER_WALL_M = 12.0     # 靠壁安全间距
ROVER_LOOK = 160.0      # 前方勘探距离(转向提前量)
ROVER_LAT = 220.0       # 最大横向(转向)速度
ROVER_PREF = 34.0       # 相对中心线的巡逻偏好偏移
ROVER_STEER = 60.0      # (保留)巨石排斥折算系数
PATROL_STEP = 20        # 安全巡逻路径采样步长
PATROL_LOOK = 60.0      # 巡逻路径前瞻距离

# ---- 节点移动(自愈重连) ----
NODE_MOVE_SPEED = 26.0     # 节点移动速度(px/s)
NODE_STOP = 5.0            # 到达目标即停止的距离
ISOLATION_T = 3.0          # 判定"孤立求救"的持续无邻居时间
LINK_SAFE = 0.82           # 桥接后与两端距离 ≤ RANGE*LINK_SAFE(留余量,防乒乓)
WOUND_WINDOW = 40.0        # 判定"近期断裂(伤口)"的时间窗(仅此窗口内触发桥接移动)
RELAY_HOLD = 60.0          # rover 注入的"朝网络方向"目标的有效时长
PARTITION_T = 8.0          # 判定"分区失联(持续无基站路由)"的时长;到点后仅"前沿"节点朝锚点桥接(去中心化,不齐动)

# ---- 自愈推进分级(前锋"到了但没连上"的逐级加码,核心自愈强化) ----
STALL_PUSH_T = 2.0         # 在余量停点观察多久仍失联 → 直插锚点(断口中点)
STALL_SWEEP_T = 6.0        # 直插锚点后观察多久仍失联 → 绕行扫描
STALL_GIVEUP = 42.0        # 扫描多久仍无果 → 回锚点驻守(等 rover 带来对端信息后重启)
SWEEP_DWELL = 3.5          # 每个扫描点的驻留时长
CHAIN_FOLLOW = True        # 非前沿节点对"推进中的邻居(sos)"链式跟进(长断口多节点接力)
