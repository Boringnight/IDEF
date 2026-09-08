# -*- coding: utf-8 -*-
"""节点状态:每个节点只有本地视野(邻居表/路由表/束队列),无全局信息
能量/温度/辐射为节点本地物理模型(电池 mAh 记账 + 热降额 + RTG 涓流 + SEU),
协议仍然只看 soc(0-100%) —— 状态机/睡眠调度/DLER 度量完全不变。"""
import math
import random

STATE_NORMAL = "NORMAL"
STATE_PROTECTED = "PROTECTED"   # 20%~40%:退出中继,只收不发别人的
STATE_DYING = "DYING"          # <20%:强制休眠保命
STATE_DEAD = "DEAD"

INF = 1e9
RANGE = 260.0                   # 通信半径(米)

# ---- 物理环境常量 ----
AMBIENT_C = -20.0     # 熔岩管环境基准温度(°C)
HEAT_TEMP_C = 75.0    # 热浪期间温度趋近目标(°C): 噪声底+NF 恶化 ≈6.7dB,熔断远距劣质链路
HEAT_RATE = 0.15      # 热浪升温时间常数(1/s)
COOL_RATE = 0.06      # 热浪结束后向环境基准回落的时间常数(1/s)
TIME_ACCEL = 40.0    # 电气时间加速: 1 真实秒 = 40 电气秒(忙中继约 15 分钟耗尽,
                     # 避免演示期能量过速级联崩塌;过小则节点永不死,失去寿命演示价值)
RTG_MAH_S = 1.2       # RTG 同位素涓流充电(mAh/真实秒): 忙节点净耗、休眠节点净回升


class Node:
    # 支持前端上帝模式: 允许直接覆写的物理参数白名单
    MUTABLE = {
        "temp_c", "tx_power_dbm", "rx_sensitivity_dbm", "ant_gain_dbi",
        "tilt_deg", "battery_mah", "radiation_rad", "i_tx", "supercap_pct",
    }

    def __init__(self, nid: str, role: str, x: float, y: float,
                 domain: int, border: bool):
        self.id = nid
        self.role = role            # base / spike / probe / rover
        self.x, self.y = x, y
        self.domain, self.border = domain, border
        self.soc = 100.0            # 剩余电量 %(由 battery_mah 派生,协议唯一能量状态源)
        self.state = STATE_NORMAL
        self.alive = True
        self.sleeping = False
        self.is_critical = False    # 割点自识别(2 跳视图)
        self._crit_votes = 0
        self.is_anchor = False      # 域锚
        # ---- 本地协议状态 ----
        self.neighbors = {}         # id -> {last, snr, ber, load, adv, nbrs, soc, state, first}
        self.routing = {}           # dest -> {cost, nh, ms}
        self.bundles = []           # 无路由时暂存的数据束
        self.packets = []           # 等待逐跳转发的数据包
        self.gen_t = 0.0            # 下次生成遥测
        self.tx = 0
        self.rx = 0
        self.hops_total = 0
        self._topo_dirty = True   # 邻居拓扑变化标记(割点检测结果缓存用)
        self._cv_res = False      # 最近一次割点判定结果
        # ---- 物理层(本地) ----
        self.battery_capacity = 12000.0   # 电池满容量 mAh
        self.battery_mah = 12000.0        # 电池剩余容量 mAh
        self.i_tx = 420.0                 # 发射功耗 mA
        self.i_rx = 95.0                  # 接收功耗 mA
        self.i_sleep = 2.5                # 睡眠功耗 mA
        self.supercap_pct = 100.0         # 超级电容缓冲 %
        self.temp_c = AMBIENT_C           # 实时温度 °C
        self.radiation_rad = random.uniform(0.0, 60.0)  # 累积辐射剂量 rad
        self.seu_flips = 0                # 单粒子翻转累计次数
        self.tx_power_dbm = 18.0
        self.rx_sensitivity_dbm = -102.0
        self.ant_gain_dbi = 3.0
        self.tilt_deg = 0.0
        self._temp_pinned = False         # 上帝模式设温后不再自动回归环境基准
        self._seu_hit = False             # 本 tick 遭遇 SEU(引擎取走发事件)
        # ---- 移动(自愈重连) ----
        self.sos = False          # 孤立求救:已无法与任何人联络
        self.sos_since = None     # 开始孤立的时间
        self.move_target = None   # (x,y) 移动目标
        self._last_gone = None    # 最近一个消失的邻居 id(用于追回)
        self._last_gone_at = 0.0  # 该邻居消失的时间(用于判定"近期断裂")
        self.bridge = False       # 是否正充当桥接移动前锋
        self.seek_target = None   # (x,y) 本地自愈:断开时朝它移动,直到重新连上
        self.relay_at = 0.0       # rover 注入网络方向锚点的时刻(作为目标有效窗口)
        self.rejoin_target = None # rover 同步的"断裂对端"节点 id(朝它实时移动以重连)
        self.contact_at = -99.0   # 上次 rover 同步对端的时刻(有效窗口用)
        self._last_anchor = None  # (x,y) 最近一次"到基站下一跳"的位置(断链后立即朝它桥接)
        self._last_gone_nbrs = 0  # 最近消失邻居的邻居数(>1 => 它是连接两端的桥,断=分裂)
        self._since_nobase = None  # 持续"无基站实时路由"的起始时刻(分区失联判据,None=有路由)
        self._stall_since = None   # 前锋"到达目标仍未恢复"的起始时刻(分级推进的计龄依据)
        self._home = None          # 断连出发侧位置(绕行扫描的方向基准)
        self._seek_anchor = None   # 当前寻路锚点(明显变化=获得新信息 → 复位推进分级)

    # ---- 物理属性 ----
    @property
    def queue_pct(self) -> float:
        """本地待发队列占用率: 只算待发包(束是断连暂存,不占发射机);
        驱动发射占空比与能耗,避免'积压→耗电加速→垂危→更多积压'的死亡螺旋"""
        return min(100.0, len(self.packets) * 100.0 / 24.0)

    @property
    def duty_tx(self) -> float:
        """发射占空比: 队列越满, 发射越频繁 -> 耗电越多"""
        return min(0.9, 0.1 + self.queue_pct / 100.0 * 0.8)

    @property
    def avg_current_ma(self) -> float:
        """加权平均电流(清醒态); 睡眠由 energy_step 乘 0.15"""
        return (self.i_tx * self.duty_tx + self.i_rx * 0.5 + self.i_sleep * 0.3)

    @property
    def thermal_derating(self) -> float:
        """极端温差下的电池放电效率衰减系数(>45°C 加速老化, <-20°C 内阻骤增)"""
        t = self.temp_c
        if t > 45:
            return max(0.4, 1.0 - (t - 45) * 0.012)
        if t < -20:
            return max(0.3, 1.0 - (-20 - t) * 0.015)
        return 1.0

    def effective_rx_sensitivity(self) -> float:
        """有效接收灵敏度 = 热噪声底 + 解调门限 + 高温NF恶化 + 硬件老化偏置。
        温度通过 kTB 噪声底与器件噪声系数双重恶化灵敏度 ——
        这是"高温 -> SNR下降 -> 链路熔断"耦合链的物理根基。"""
        from . import physics
        noise = physics.thermal_noise_floor_dbm(self, physics.BAND["bandwidth_hz"])
        nf_penalty = max(0.0, self.temp_c - 25.0) * 0.12   # 高温 NF 恶化 dB/°C
        aging = (-102.0) - self.rx_sensitivity_dbm          # 老化/手动恶化 dB
        return noise + physics.BAND["snr_req_db"] + nf_penalty + aging

    def apply_override(self, key: str, value):
        if key not in Node.MUTABLE:
            raise KeyError(f"parameter '{key}' is not mutable")
        setattr(self, key, value)
        if key == "temp_c":
            self._temp_pinned = True   # 手动设温后,温度不再自动回归环境基准

    # ---- 能量模型(每 tick) ----
    def _temp_step(self, dt: float, heat: bool):
        """温度演化: 热浪期间快速趋近 HEAT_TEMP_C,结束后向环境基准回落;叠加微扰"""
        if self._temp_pinned:
            return
        if heat:
            self.temp_c += (HEAT_TEMP_C - self.temp_c) * min(1.0, HEAT_RATE * dt)
        else:
            self.temp_c += (AMBIENT_C - self.temp_c) * min(1.0, COOL_RATE * dt)
        self.temp_c += random.uniform(-0.15, 0.15)

    def _rad_step(self):
        """辐射累积 + 单粒子翻转(SEU): 剂量越高翻转概率越大;
        翻转时清空本地邻居表 —— 节点被迫重新发现邻居,由信标协议自然恢复"""
        self.radiation_rad += random.uniform(0.0, 0.12)
        p_seu = min(0.02, self.radiation_rad / 500000.0)
        if random.random() < p_seu:
            self.seu_flips += 1
            self.neighbors = {}
            self._topo_dirty = True
            self._seu_hit = True

    def energy_step(self, dt: float, heat: bool = False):
        if not self.alive or self.role == "base":
            return
        self._temp_step(dt, heat)
        self._rad_step()
        # 电池 mAh 记账: 平均电流×(加速电气时间),睡眠×0.15,热降额放大;RTG 涓流回充
        sleeping = self.state == STATE_DYING or self.sleeping
        cur = self.avg_current_ma * (0.15 if sleeping else 1.0) / self.thermal_derating
        drain = cur * dt * TIME_ACCEL / 3600.0        # mAh
        self.battery_mah = max(0.0, min(self.battery_capacity,
                                        self.battery_mah - drain + RTG_MAH_S * dt))
        self.soc = self.battery_mah / self.battery_capacity * 100.0
        # 超级电容缓冲(指示性): 高占空比放电,空闲涓流充电
        if self.duty_tx > 0.5:
            self.supercap_pct = max(0.0, self.supercap_pct - 2.5 * dt)
        else:
            self.supercap_pct = min(100.0, self.supercap_pct + 0.8 * dt)
        # 状态机(带迟滞) —— 协议语义不变
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

    def spend(self, amount: float):
        """事件性能耗(信标/数据/移动),amount 单位为百分点(与旧模型一致)"""
        if self.role == "base" or not self.alive:
            return
        self.battery_mah = max(0.0, self.battery_mah
                               - amount * self.battery_capacity / 100.0)
        self.soc = self.battery_mah / self.battery_capacity * 100.0

    def dist(self, other) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)
