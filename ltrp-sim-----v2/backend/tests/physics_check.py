# -*- coding: utf-8 -*-
"""物理约束与自愈能力度量(不依赖 pytest,直接 `py -3.13 tests/physics_check.py`)。

两项检查:
  1. --physics  逐 tick 校验"没有任何移动体穿进巨石/穿出岩壁",并统计每 tick 位移,
                据此判定合速度是否超过各自上限(探针 MOVE_V=10cm/s,节点 NODE_MOVE_V);
  2. --heal     注入喉道摧毁 → 统计"覆盖率恢复到 ≥95% 且分区归零"所需 tick 数,
                作为自愈强度的量化指标(数值越小越强)。

Globals Used: 无(仅通过 ENGINE 单例与 constants 读取)。
Calls: ENGINE.reset/step/snapshot/inject_disaster, sim.engine_ext.constants。
"""
import math                          # 距离/模长计算
import os                            # 定位包路径
import sys                           # 退出码与模式分发
import time                          # 计时(顺带报告每 tick 耗时)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.engine import ENGINE        # 被测引擎单例
from sim.contracts import dto        # DTO → dict
from sim.engine_ext.constants import (MOVE_V, NODE_MOVE_V, ROVER_V,
                                      TIME_SCALE_DEFAULT)

DT = 0.3                             # 现实 tick 周期
SPEED_TOL = 1.15                     # 速度容差:数值/取整误差允许超 15%


def _step_scale() -> float:
    """本局物理步长(秒)= DT × time_scale。Returns: dts。"""
    return DT * getattr(ENGINE, "time_scale", TIME_SCALE_DEFAULT)


def _budget(kind: str) -> float:
    """该角色每 tick 允许的最大位移(px)。Args: kind=role。Returns: 位移上限。"""
    dts = _step_scale()
    if kind == "probe":
        return MOVE_V * dts * SPEED_TOL          # 用户硬要求:探针合成速度 ≤10cm/s
    if kind == "rover":
        return ROVER_V * dts * 1.6 * SPEED_TOL   # 纵向+横向各有限速,另加约束解算余量
    return NODE_MOVE_V * dts * 2.0 * SPEED_TOL   # 节点:横纵各自限速,另有 _separate 推开量


def _inside_boulder(x: float, y: float, margin: float = 0.0):
    """点是否落在任一巨石内(含 margin)。Returns: 命中的巨石或 None。"""
    for b in ENGINE.world.boulders:
        if math.hypot(x - b["x"], y - b["y"]) < b["r"] + margin:
            return b
    return None


def run_physics(ticks: int = 1200) -> int:
    """逐 tick 校验穿透与超速。Args: ticks=检查时长。Returns: 退出码。"""
    ENGINE.reset(7)
    ENGINE.set_time_scale(100)
    prev = {}
    worst_speed = {"probe": 0.0, "rover": 0.0, "spike": 0.0}
    speed_fail = []
    pen_fail = []
    for tick in range(1, ticks + 1):
        ENGINE.step(DT)
        for n in ENGINE.nodes.values():
            if not n.alive or getattr(n, "_pending_deploy", False):
                continue
            b = _inside_boulder(n.x, n.y)
            if b is not None:
                pen_fail.append((tick, n.id, round(math.hypot(n.x - b["x"], n.y - b["y"]), 1),
                                 b["r"]))
            p = prev.get(n.id)
            if p is not None:
                d = math.hypot(n.x - p[0], n.y - p[1])
                kind = n.role if n.role in worst_speed else "spike"
                worst_speed[kind] = max(worst_speed[kind], d)
                if d > _budget(kind):
                    speed_fail.append((tick, n.id, kind, round(d, 2),
                                       round(_budget(kind), 2)))
            prev[n.id] = (n.x, n.y)
    print("worst per-tick displacement (px):", {k: round(v, 2) for k, v in worst_speed.items()})
    print("budget per tick (px):",
          {k: round(_budget(k), 2) for k in ("probe", "rover", "spike")})
    if pen_fail:
        print(f"FAIL 穿模 {len(pen_fail)} 次,前 5:", pen_fail[:5])
    if speed_fail:
        print(f"FAIL 超速 {len(speed_fail)} 次,前 5:", speed_fail[:5])
    if not pen_fail and not speed_fail:
        print("physics OK: 无穿模、无超速")
        return 0
    return 1


def _run_until(cond, limit: int) -> int:
    """步进直到 cond(snapshot) 为真。Args: cond=判定函数; limit=最大 tick。Returns: 消耗 tick。"""
    for k in range(1, limit + 1):
        ENGINE.step(DT)
        if cond(dto(ENGINE.snapshot())):
            return k
    return -1


def _force_partition(fixed_half: int = 0):
    """在中间喉道处逐级清空一段 x 窗口内的存活节点,直到确实产生分区。

    比 inject_disaster("critical")(只杀两枚边界道钉)更强:保证把网络真正切成两半,
    才能量化"自愈恢复时间"。fixed_half>0 时只试这一个宽度(用于前后版本同口径对比)。

    Returns: 生效的半宽(px),失败返回 None。
    """
    w = ENGINE.world
    throats = w.throats()
    a, b = throats[len(throats) // 2]
    mid = (a + b) / 2
    halves = (fixed_half,) if fixed_half else (50, 80, 120, 180, 260, 360)
    for half in halves:
        for n in ENGINE.nodes.values():
            if n.alive and n.role not in ("base", "rover") and abs(n.x - mid) <= half:
                n.alive = False
                n.state = "DEAD"
                n._death_logged = True
        ENGINE._recompute_static()
        ENGINE._update_adj()
        ENGINE._stats()
        if ENGINE.partitions > 0:
            return half
    return None


def run_heal(fixed_half: int = 0) -> int:
    """量化自愈:强制切断喉道 → 统计覆盖率恢复到 ≥95% 且分区归零所需 tick。Returns: 退出码。"""
    ENGINE.reset(7)
    ENGINE.set_time_scale(100)
    _run_until(lambda s: s["deploy"]["done"], 3000)
    _run_until(lambda s: s["stats"]["coverage"] >= 0.98, 2000)
    s0 = dto(ENGINE.snapshot())
    print(f"baseline: t={s0['t']:.0f} alive={s0['stats']['alive']} "
          f"coverage={s0['stats']['coverage']} partitions={s0['stats']['partitions']}")
    half = _force_partition(fixed_half)
    if half is None:
        print(f"FAIL 无法构造分区(fixed_half={fixed_half})")
        return 1
    s1 = dto(ENGINE.snapshot())
    print(f"after cut(±{half}px): alive={s1['stats']['alive']} "
          f"partitions={s1['stats']['partitions']} coverage={s1['stats']['coverage']} "
          f"bundles={s1['stats']['bundles']}")
    t0 = time.perf_counter()
    ticks = _run_until(lambda s: s["stats"]["coverage"] >= 0.95
                       and s["stats"]["partitions"] == 0, 4000)
    el = time.perf_counter() - t0
    if ticks < 0:
        s2 = dto(ENGINE.snapshot())
        print(f"FAIL 4000 tick 内未恢复;coverage={s2['stats']['coverage']} "
              f"partitions={s2['stats']['partitions']}(elapsed={el:.1f}s)")
        return 1
    print(f"heal OK: 恢复耗时 {ticks} tick ≈ {ticks * DT:.1f} 真实秒")
    return 0


def main() -> int:
    """入口:按参数选择检查项。Returns: 退出码。"""
    mode = sys.argv[1] if len(sys.argv) > 1 else "--physics"
    if mode == "--physics":
        return run_physics()
    if mode == "--heal":
        fixed = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        return run_heal(fixed)
    print(f"未知模式: {mode}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
