# -*- coding: utf-8 -*-
"""无头仿真冒烟测试 / 回归指纹(不依赖 pytest, 直接 `py -3.13 tests/smoke_sim.py`)。

职责:在没有任何前端与网络的情况下驱动 sim.engine,验证重构"行为不回退"。
两种模式:
  --fingerprint  确定性 1500 tick 场景,输出全网状态指纹 sha256(重构前后必须一致)
  --scenarios    全功能场景(灾害/摆渡/休眠/随机地图/上帝模式/拖巨石),只断言不变量

Globals Used: 无(全部通过 ENGINE 单例访问, 测试自身不引入全局可变状态)。
Calls: sim.engine.ENGINE.reset/step/snapshot/init_payload/inject_disaster/
       move_obstacle/set_sleep/set_time_scale/new_map/apply_override/set_global_param。
Args: 命令行参数见上。Returns: 进程退出码 0=通过, 1=失败(供 CI/脚本判定)。
"""
import hashlib                      # 指纹摘要:把快照归一化后哈希成可比较的字符串
import json                         # 快照序列化:验证 JSON 可序列化并生成稳定指纹
import math                         # 数值校验:检测 NaN / inf 脏数据
import os                           # 环境变量:PYTHONHASHSEED 提示
import sys                          # 退出码:脚本化判定成功/失败
import time                         # 性能基线:统计每 tick 耗时

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.engine import ENGINE       # 被测对象:仿真引擎单例
from sim import protocol as P       # 协议参数:用于断言参数覆写白名单生效
from sim.engine_ext.constants import MAP_SEED  # 演示版固定地图种子
from sim.contracts import dto       # DTO → dict:模拟 API 层的唯一序列化出口

TICKS = 1500                        # 指纹模式总 tick 数
MARKS = (300, 600, 900, 1200, 1500)  # 采样 tick(覆盖部署期/收敛期/稳态)

# 前端线上契约金标准(键集合必须完全一致,顺序无关) —— 防止 DTO 迁移改变 JSON 形状
WIRE_TOP = {"boulders", "cmd", "deploy", "events", "ferries", "flows", "links",
            "nodes", "params", "power_links", "stats", "t"}
WIRE_NODE = {"alive", "border", "bundles", "charge_ma", "cost", "crit", "domain",
             "energy", "id", "laser_in", "laser_out", "moving", "ms", "nbrs", "nh",
             "pending", "phys", "pkts", "pv_w", "role", "seu", "sleeping", "soc",
             "sos", "state", "stock", "temp", "x", "y"}
WIRE_STATS = {"alive", "avg_hops", "avg_soc", "avg_temp", "awake", "bundles",
              "coverage", "critical", "damaged_drops", "delivered", "earth_flushed",
              "earth_up", "ferry_bundles", "heat", "lost", "min_soc",
              "partitions", "range", "retries", "signal_cover", "sleep_duty",
              "sleep_on", "sleeping", "time_scale", "total"}
WIRE_PHYS = {"battery_mah", "i_tx", "supercap_pct", "tx_power_dbm",
             "rx_sensitivity_dbm", "ant_gain_dbi", "tilt_deg", "temp_c",
             "radiation_rad"}


def _check_wire(s: dict) -> None:
    """线上契约校验:顶层/节点/统计/物理参数四张键表必须与金标准完全一致。"""
    if set(s.keys()) != WIRE_TOP:
        raise AssertionError(f"顶层键变化: {sorted(set(s.keys()) ^ WIRE_TOP)}")
    for n in s["nodes"]:
        if set(n.keys()) != WIRE_NODE:
            raise AssertionError(f"节点键变化: {sorted(set(n.keys()) ^ WIRE_NODE)}")
        if set(n["phys"].keys()) != WIRE_PHYS:
            raise AssertionError(f"phys 键变化: {sorted(set(n['phys'].keys()) ^ WIRE_PHYS)}")
    if set(s["stats"].keys()) != WIRE_STATS:
        raise AssertionError(f"stats 键变化: {sorted(set(s['stats'].keys()) ^ WIRE_STATS)}")


def _node_sig(n: dict) -> list:
    """节点签名:只取与协议行为相关的字段,浮点统一量化避免无意义抖动。"""
    return [n["id"], round(n["x"], 3), round(n["y"], 3), round(n["soc"], 6),
            n["state"], int(n["alive"]), int(n["sleeping"]), int(n["crit"]),
            n["bundles"], n["pkts"], n["nh"], n["cost"], n["ms"], n["domain"]]


def _snap_sig(s: dict) -> dict:
    """快照签名:节点 + 关键统计量,组成可哈希的稳定结构。"""
    st = s["stats"]
    return {
        "t": s["t"], "nodes": [_node_sig(n) for n in s["nodes"]],
        "alive": st["alive"], "coverage": round(st["coverage"], 6),
        "partitions": st["partitions"], "delivered": st["delivered"],
        "lost": st["lost"], "bundles": st["bundles"],
        "sleeping": st["sleeping"], "critical": st["critical"],
        "avg_soc": round(st["avg_soc"], 6), "signal_cover": round(st["signal_cover"], 6),
        "links": sorted(s["links"]),
    }


def _check_clean(s: dict) -> None:
    """不变量:快照必须 JSON 可序列化、无 NaN/inf、节点坐标有限。"""
    json.dumps(s, ensure_ascii=False)          # 抛异常即为不合格
    for n in s["nodes"]:
        for k in ("x", "y", "soc", "temp"):
            v = n[k]
            if not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v):
                raise AssertionError(f"脏数值 {n['id']}.{k}={v!r}")
        if n["soc"] < 0.0 or n["soc"] > 100.0:
            raise AssertionError(f"{n['id']} SoC 越界: {n['soc']}")
    st = s["stats"]
    if not (0.0 <= st["coverage"] <= 1.0):
        raise AssertionError(f"覆盖率越界: {st['coverage']}")
    if not (0.0 <= st["signal_cover"] <= 1.0):
        raise AssertionError(f"信号覆盖越界: {st['signal_cover']}")


def run_fingerprint() -> int:
    """确定性场景:固定 seed 跑 1500 tick,逐采样点累积签名并打印 sha256。
    复现性由 Engine.reset(seed) 内部播种(几何/引擎/节点物理共用同一 seed)保证。"""
    ENGINE.reset(7)
    ENGINE.set_time_scale(100)
    acc, t0 = [], time.perf_counter()
    for i in range(1, TICKS + 1):
        ENGINE.step(0.3)
        if i in MARKS:
            s = dto(ENGINE.snapshot())
            _check_clean(s)
            _check_wire(s)
            acc.append(_snap_sig(s))
    el = time.perf_counter() - t0
    raw = json.dumps(acc, sort_keys=True, ensure_ascii=False).encode("utf-8")
    print(f"ticks={TICKS} elapsed={el:.2f}s per_tick={el / TICKS * 1000:.2f}ms")
    print(f"deploy_done={ENGINE.deploy_done} front={ENGINE.deploy_front:.1f}")
    print(f"fingerprint={hashlib.sha256(raw).hexdigest()}")
    print(f"delivered={acc[-1]['delivered']} alive={acc[-1]['alive']} "
          f"coverage={acc[-1]['coverage']} partitions={acc[-1]['partitions']}")
    return 0


def _run_disaster_phase() -> None:
    """场景前半段:按时间轴注入塌方/断喉道/热浪、开关休眠、拖巨石,并周期校验不变量。"""
    for i in range(1, 901):
        ENGINE.step(0.3)
        if i == 200:
            ENGINE.inject_disaster("collapse")
        if i == 320:
            ENGINE.inject_disaster("critical")
        if i == 440:
            ENGINE.inject_disaster("heat")
        if i == 560:
            ENGINE.set_sleep(True)
        if i == 700:
            ENGINE.move_obstacle(0, ENGINE.world.W * 0.5, ENGINE.world.yc(ENGINE.world.W * 0.5))
        if i == 800:
            ENGINE.set_sleep(False)
        if i % 100 == 0:
            _check_clean(dto(ENGINE.snapshot()))


def _check_param_overrides() -> None:
    """校验上帝模式参数白名单与范围校验(合法接受、越界/未知拒绝)。"""
    assert ENGINE.set_global_param({"gamma": 20.0}).get("ok"), "全局参数覆写失败"
    assert P.PARAMS.gamma == 20.0, "全局参数未生效"
    assert not ENGINE.set_global_param({"gamma": 999.0}).get("ok"), "越界参数未被拒绝"
    assert not ENGINE.set_global_param({"nope": 1}).get("ok"), "未知参数未被拒绝"
    some = ENGINE.order[5]
    assert ENGINE.apply_override(some, {"tx_power_dbm": 24.0}).get("ok"), "节点参数覆写失败"
    assert not ENGINE.apply_override(some, {"nope": 1}).get("ok"), "未知节点参数未被拒绝"


def run_scenarios() -> int:
    """全功能场景:灾害注入 / 休眠 / 拖巨石 / 随机地图 / 上帝模式,断言不变量。"""
    ENGINE.reset(7)
    ENGINE.set_time_scale(100)
    _run_disaster_phase()
    assert ENGINE.heat_until > 0, "热浪未生效"
    _check_param_overrides()
    ENGINE.new_map(42)
    # 演示版固定地图:任何 seed 请求都被强制回 MAP_SEED(7)并完整重置
    assert ENGINE.world.seed == MAP_SEED and ENGINE.t == 0.0, \
        "演示版应强制固定地图(MAP_SEED)并重置世界"
    init = dto(ENGINE.init_payload())
    assert init["cmd"] == "init" and init["world"]["W"] > 0, "init 载荷结构错误"
    json.dumps(init, ensure_ascii=False)
    for _ in range(400):
        ENGINE.step(0.3)
    _check_clean(dto(ENGINE.snapshot()))
    print(f"scenarios OK: seed={ENGINE.world.seed} t={ENGINE.t:.1f} "
          f"alive={dto(ENGINE.snapshot())['stats']['alive']}")
    return 0


def main() -> int:
    """入口:按命令行参数分发模式;任一断言失败以退出码 1 报告。"""
    mode = sys.argv[1] if len(sys.argv) > 1 else "--fingerprint"
    if os.environ.get("PYTHONHASHSEED") != "0":
        print("提示: 为获得可复现指纹请设置 PYTHONHASHSEED=0", file=sys.stderr)
    try:
        if mode == "--fingerprint":
            return run_fingerprint()
        if mode == "--scenarios":
            return run_scenarios()
        print(f"未知模式: {mode}", file=sys.stderr)
        return 1
    except AssertionError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
