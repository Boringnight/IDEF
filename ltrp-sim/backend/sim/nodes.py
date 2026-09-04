# -*- coding: utf-8 -*-
"""节点状态:每个节点只有本地视野(邻居表/路由表/束队列),无全局信息"""
import math

STATE_NORMAL = "NORMAL"
STATE_PROTECTED = "PROTECTED"   # 20%~40%:退出中继,只收不发别人的
STATE_DYING = "DYING"          # <20%:强制休眠保命
STATE_DEAD = "DEAD"

INF = 1e9
RANGE = 260.0                   # 通信半径(米)


class Node:
    def __init__(self, nid: str, role: str, x: float, y: float,
                 domain: int, border: bool):
        self.id = nid
        self.role = role            # base / spike / probe / rover
        self.x, self.y = x, y
        self.domain, self.border = domain, border
        self.soc = 100.0            # 剩余电量 %
        self.state = STATE_NORMAL
        self.alive = True
        self.sleeping = False
        self.is_critical = False    # 割点自识别(2 跳视图)
        self._crit_votes = 0
        self.is_anchor = False      # 域锚
        # ---- 本地协议状态 ----
        self.neighbors = {}         # id -> {last, snr, adv, nbrs, soc, state, first}
        self.routing = {}           # dest -> {cost, nh, ms}
        self.bundles = []           # 无路由时暂存的数据束
        self.packets = []           # 等待逐跳转发的数据包
        self.gen_t = 0.0            # 下次生成遥测
        self.tx = 0
        self.rx = 0
        self.hops_total = 0
        self._topo_dirty = True   # 邻居拓扑变化标记(割点检测结果缓存用)
        self._cv_res = False      # 最近一次割点判定结果
        # ---- 移动(自愈重连) ----
        self.sos = False          # 孤立求救:已无法与任何人联络
        self.sos_since = None     # 开始孤立的时间
        self.rejoin_target = None # (x,y) 孤岛尝试重连的目标
        self.move_target = None   # (x,y) 移动目标
        self._last_gone = None    # 最近一个消失的邻居 id(用于追回)
        self._last_gone_at = 0.0  # 该邻居消失的时间(用于判定"近期断裂")
        self.bridge = False       # 是否正充当桥接移动前锋
        self.seek_target = None   # (x,y) 本地自愈:断开时朝它移动,直到重新连上
        self.relay_at = 0.0       # rover 注入网络方向锚点的时刻(作为目标有效窗口)
        self._last_anchor = None  # (x,y) 最近一次"到基站下一跳"的位置(断链后立即朝它桥接)
        self._last_gone_nbrs = 0  # 最近消失邻居的邻居数(>1 => 它是连接两端的桥,断=分裂)
        self._since_nobase = None  # 持续"无基站实时路由"的起始时刻(分区失联判据,None=有路由)

    # ---- 能量模型(每 tick) ----
    def energy_step(self, dt: float, heat: bool, passive_only: bool = False):
        if not self.alive or self.role == "base":
            return
        k = 5.0 if heat else 1.0
        if self.state == STATE_DYING or self.sleeping:
            self.soc -= 0.004 * k * dt
        else:
            self.soc -= 0.020 * k * dt
        # 状态机(带迟滞)
        if self.state == STATE_NORMAL and self.soc < 40:
            self.state = STATE_PROTECTED
        elif self.state == STATE_PROTECTED and self.soc < 20:
            self.state = STATE_DYING
            self.sleeping = True
        if self.soc <= 0.5:
            self.soc = 0.0
            self.alive = False
            self.state = STATE_DEAD
            self.sleeping = True

    def spend(self, amount: float, heat: bool):
        if self.role == "base" or not self.alive:
            return
        self.soc = max(0.0, self.soc - amount * (5.0 if heat else 1.0))

    def dist(self, other) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)
