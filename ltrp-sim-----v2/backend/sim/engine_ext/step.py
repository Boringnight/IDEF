# -*- coding: utf-8 -*-
"""引擎职责模块:主循环编排(EngineStepMixin)。

职责范围:把每 tick 的仿真流程编排为"部署 → 巡逻 → 拓扑 → 能量调度 → 自愈 →
感知/判死 → 路由 → 休眠 → 能量演化 → 数据面 → 摆渡 → 地月回传 → 统计"的显式流水线。
依赖:读 Engine 的 nodes/order/adj/t/world;各阶段方法分散在对应 mixin。
Calls: _deploy_carriers/_rover_phys/_update_adj/_power_step/_step_movement/
       _phase_beacon/_phase_expire_crit/_phase_relax/_phase_sleep/_phase_energy/
       _traffic/_ferry/_earth/_stats。
"""
from ..nodes import STATE_DEAD, STATE_DYING, STATE_PROTECTED
from .constants import *
from .engine_imports import P, zh


class EngineStepMixin:
    """主循环编排 mixin,由 Engine 继承,self 即引擎实例。

    Globals Used: DT, TIME_SCALE_DEFAULT, ENERGY_EVERY, BEACON_EVERY, RELAX_ROUNDS,
                  SLEEP_PERIOD, P, zh。
    Lifecycle: Engine() 构造后由 run_forever 每 DT 秒调用一次 step(dt)。
    """

    def set_time_scale(self, scale):
        """时间缩放(前端滑块): 物理步长 = 现实tick × time_scale, 移动/能耗/充电统一缩放。

        Args: scale=倍率(0.1~1000)。Returns: {"time_scale": 实际生效值}。
        """
        self.time_scale = max(0.1, min(1000.0, float(scale)))
        return {"time_scale": round(self.time_scale, 2)}

    def step(self, dt: float = DT):
        """单 tick 编排入口:按固定顺序推进各阶段,阶段内部各自保持去中心化语义。

        Globals Used: DT, TIME_SCALE_DEFAULT, ENERGY_EVERY。
        Args: dt=现实步长(秒), 物理步长 dts = dt × time_scale。
        Returns: None。
        """
        dts = dt * getattr(self, "time_scale", TIME_SCALE_DEFAULT)
        self.t += dts
        self.tick += 1
        heat = self.t < self.heat_until

        self._deploy_carriers(dts)                 # 0. 探针面包屑部署(未撒布节点保持离线)
        if self.deploy_done:                       # 1. 月球车巡逻(部署完成后才出动)
            for n in self.nodes.values():
                if n.role == "rover" and n.alive:
                    self._rover_phys(n, dts)
        self._update_adj()                         # 2. 邻接表刷新
        if self.tick % ENERGY_EVERY == 0:
            self._power_step(dts)                  # 2.5 激光无线充电(依赖本 tick 的 adj)
        self._step_movement(dts)                   # 3. 节点自愈移动

        self._phase_beacon()                       # 4. 信标交换(引擎只投递,节点本地处理)
        self._phase_expire_crit()                  # 5. 邻居判死 + 割点自识别
        self._phase_relax()                        # 6. DSDV 分布式松弛
        self._phase_sleep()                        # 7. 流量自适应休眠
        self._phase_energy(dts, heat)              # 8. 能量演化 + 状态机 + SEU 播报

        self._traffic()                            # 9. 数据面:遥测生成 → 逐跳转发/束化
        self._ferry()                              # 10. 月球车摆渡(存储-携带-转发)
        self._earth()                              # 11. 地月回传(潮汐锁定,恒可见)
        self._stats()                              # 12. 统计 + 快照

    def _phase_beacon(self):
        """信标交换:引擎只负责按邻接表投递,链路准入与邻居表更新全部由接收端本地完成。

        Globals Used: BEACON_EVERY, P。
        Calls: P.compose_hello/P.on_hello, self._snr。Args: None。Returns: None。
        """
        if self.tick % BEACON_EVERY != 0:
            return
        for i in self.order:
            n = self.nodes[i]
            if not n.alive or n.sleeping:
                continue
            h = P.compose_hello(n, self.t)
            n.spend(0.003)
            n.tx += 1
            for j in self.adj[i]:
                nj = self.nodes[j]
                lk = self._snr(n, nj)
                if not lk or not lk["up"]:
                    continue
                # 建链双门限 + 迟滞: 新链路按 gamma 建立;
                # 已有链路容忍 4dB 衰落(接收端只查自己邻居表,仍为本地判定),
                # 避免边缘链路被相关阴影衰落反复撕断(抑制路由抖动)
                thr = (P.PARAMS.gamma - 4.0) if n.id in nj.neighbors \
                    else P.PARAMS.gamma
                if lk["snr_db"] < thr:
                    continue
                # 水平分割:不把"下一跳是接收方 j"的目的通告给它(抑制 2 环路)。
                # 这里直接投递原始 Hello,由接收端在 dsdv_relax 读取时按 v.nh == 自己 过滤 ——
                # 语义等价,却省掉每条链路一次 Hello 拷贝与字典重建(信标是最热路径)。
                P.on_hello(nj, h, lk["snr_db"], self.t, lk["ber"])
                nj.spend(0.0018)
                nj.rx += 1

    def _phase_expire_crit(self):
        """邻居超时判死 + 割点自识别(连续 3 票防抖),并记录"最近消失的邻居"供自愈追回。

        Globals Used: P。Calls: P.expire_neighbors, self._crit_vote。
        Args: None。Returns: None。
        """
        for i in self.order:
            n = self.nodes[i]
            if not n.alive:
                continue
            nbr_counts = {
                j: len(n.neighbors[j].nbrs)
                for j in list(n.neighbors)
                if self.t - n.neighbors[j].last > P.PARAMS.n_fail * P.PARAMS.tb
            }
            dead = P.expire_neighbors(n, self.t)
            # 无条件记录"最近超时的邻居"为追回目标 —— 桥断(死)即 recorded,
            # recent_bridge 才能触发、两端同时朝断口靠拢合并(自愈核心)。
            for j in dead:
                n._last_gone = j
                n._last_gone_at = self.t
                n._last_gone_nbrs = nbr_counts.get(j, 0)
            self._crit_vote(n, i)

    def _crit_vote(self, n, i: str):
        """割点投票:拓扑未变时复用上次 DFS 结果,连续 3 票确认/解除关键割点身份。

        Globals Used: P。Calls: P.cut_vertex, self.emit。
        Args: n=节点; i=节点 id。Returns: None。
        """
        if len(n.neighbors) < 2:
            n.is_critical = False
            n._crit_votes = 0
            return
        if getattr(n, "_topo_dirty", True):
            cv = P.cut_vertex(n)
            n._topo_dirty = False
            n._cv_res = cv
        else:
            cv = n._cv_res
        n._crit_votes = n._crit_votes + 1 if cv else 0
        was = n.is_critical
        n.is_critical = n._crit_votes >= 3
        if n.is_critical and not was and n.role == "spike" \
                and not getattr(n, "_crit_emitted", False):
            n._crit_emitted = True
            self.emit("crit", "info",
                      f"{zh(i)}({i}) 自识别为关键割点:锁定常开、提升功率(节点仅凭 2 跳视图判定)", True)
        elif not n.is_critical and was:
            n._crit_emitted = False

    def _phase_relax(self):
        """DSDV 分布式松弛(多轮加速收敛):每轮都只读邻居通告的距离向量。

        Globals Used: RELAX_ROUNDS, P。Calls: P.dsdv_relax。Args: None。Returns: None。
        """
        for _ in range(RELAX_ROUNDS):
            for i in self.order:
                n = self.nodes[i]
                if n.alive and not n.sleeping:
                    P.dsdv_relax(n, self.nodes)

    def _phase_sleep(self):
        """流量自适应休眠:苏醒比例随负载升降;'睡谁'由本地安全判据决定
        (非割点/非唯一桥/非孤立才准睡,避免把通信路睡断)。

        Globals Used: SLEEP_PERIOD, STATE_DYING/PROTECTED/DEAD。
        Calls: _update_sleep_duty/_can_sleep。Args: None。Returns: None。
        """
        if not self.sleep_on:
            return
        self._update_sleep_duty()
        phase = int(self.t // SLEEP_PERIOD)
        sleep_ratio = 1.0 - self.sleep_duty        # 允许睡眠的比例
        ids = sorted(i for i, n in self.nodes.items()
                     if n.alive and n.role == "spike" and not n.border)
        for i in ids:                              # 按 id 串行决策(确定性)
            n = self.nodes[i]
            if n.state in (STATE_DYING, STATE_PROTECTED, STATE_DEAD):
                continue                           # 保护/垂死/已死:交给能量状态机
            idx = int(i.split("-")[1])
            # 同 tick 内已把前面的节点改为沉睡 → 后续 _can_sleep/_nbr_independent
            # 会把它视为不可用,从而避免"两个互为备用路径的节点同时睡"的多米诺。
            can = self._can_sleep(n)               # 割点/唯一桥/孤立 → False
            slot = (idx * 0.6180339887 + phase) % 1.0
            should_sleep = can and slot < sleep_ratio
            if should_sleep != n.sleeping:
                n.sleeping = should_sleep

    def _phase_energy(self, dts: float, heat: bool):
        """能量演化(电池/温度/辐射均为节点本地物理)+ SEU/宕机/复苏事件播报。

        Globals Used: STATE_DEAD, STATE_DYING, STATE_PROTECTED。Calls: Node.energy_step,
        self.emit, zh。
        Args: dts=物理步长(秒); heat=是否处于热浪。
        """
        for n in self.nodes.values():
            prev = n.state
            n.energy_step(dts, heat)
            if prev == STATE_DYING and n.state == STATE_PROTECTED:
                n._woke_hit = True     # 垂死→中继退避:激光补电复苏(瞬时转换,这里只做播报)
        for i, n in self.nodes.items():
            if getattr(n, "_woke_hit", False):
                n._woke_hit = False
                self.emit("heal", "good",
                          f"{zh(i)}({i}) 激光补电复苏:电量回到 {n.soc:.0f}%,"
                          f"退出保命休眠重新入网(≥55% 恢复正常中继)", True)
            if getattr(n, "_seu_hit", False):
                n._seu_hit = False
                self.emit("seu", "warn",
                          f"{zh(i)}({i}) 遭单粒子翻转:本地邻居表复位,凭信标重新发现邻居", False)
        just_dead = [i for i, n in self.nodes.items()
                     if not n.alive and not getattr(n, "_pending_deploy", False)
                     and not hasattr(n, "_death_logged")]
        for i in just_dead:
            self.nodes[i]._death_logged = True
            self.emit("dead", "warn", f"{zh(i)}({i}) 能量耗尽宕机", True)
