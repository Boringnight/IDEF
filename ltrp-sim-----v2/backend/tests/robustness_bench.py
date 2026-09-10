# -*- coding: utf-8 -*-
"""LTRP 鲁棒性基准(多次运行取大数平均,结果写入独立 Markdown 报告)。

五项指标(每项均在多张随机地图上重复,样本数见报告):
  M1 断裂喉道后恢复连接的时间:逐喉道强制切断(逐级加宽窗口直到确实分区),
     统计 coverage≥0.95 且 partitions=0 所需 tick,折算现实秒(1 tick = 0.3s);
  M2 可承受的并发喉道断裂数:同时切断 k 条喉道,k=1..n-1 递增,
     "承受"= 预算内完全恢复;给出每图最大承受 k 与 k-成功率曲线;
  M3 单节点孤立重连时间:把一枚存活道钉传送到离全网 ≥1.1×RANGE 的孤立点,
     统计它重新拿到基站路由所需 tick;
  M4 链路(分区)恢复率:上述所有分区事件的预算内恢复比例汇总;
  M5 鲁棒性 vs 节点失效比例曲线:随机击杀 p% 存活道钉,p∈{5..50}%,
     记录恢复成功率 / 恢复耗时 / 最终覆盖率。

方法学:每个样本都从**全新 reset+部署**开始(确定性:同 seed 部署结果一致,
重复实验仅灾难注入不同);受害者选择用独立随机源 random.Random(tag) 保证可复现。
结果增量写入 bench_results.jsonl,每阶段结束重渲染报告(中断不丢数据、可续跑)。

用法: py -3.13 tests/robustness_bench.py [--maps 15] [--out ../鲁棒性基准报告.md]
"""
import json                          # JSONL 增量落盘 + 报告渲染
import math                          # 统计
import os                            # 路径
import random                        # 受害者抽样(独立随机源)
import sys                           # 参数与退出码
import time                          # 进度计时

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.engine import ENGINE        # 被测引擎单例
from sim.nodes import RANGE          # 通信半径(孤立点判据用)

DT = 0.3                             # 现实 tick 周期(秒) —— 折算现实时间的口径
SETTLE = 120                         # 部署完成后的 DSDV 收敛等待(tick)
RECOVER_BUDGET = 4000                # 恢复预算(tick) —— 与 physics_check 同口径
RECOVER_BUDGET_M5 = 3000             # M5 曲线用的恢复预算(略缩,控制总时长)
INF = 1e9


# ------------------------------------------------------------------ 基础工具

def stats(xs: list) -> dict:
    """均值/中位数/p95/最大值。Args: xs=样本列表。Returns: 统计 dict。"""
    if not xs:
        return {"n": 0, "mean": None, "median": None, "p95": None, "max": None}
    s = sorted(xs)
    return {
        "n": len(s),
        "mean": round(sum(s) / len(s), 1),
        "median": round(s[len(s) // 2], 1),
        "p95": round(s[min(len(s) - 1, max(0, math.ceil(0.95 * len(s)) - 1))], 1),
        "max": round(s[-1], 1),
    }


def secs(t) -> str:
    """tick → 现实秒字符串。"""
    return f"{t * DT:.1f}s"


def deploy_settle(seed: int):
    """全新部署一张地图并等待协议收敛(部署耗时可复现)。"""
    ENGINE.reset(seed)
    ENGINE.set_time_scale(100)
    for _ in range(6000):
        ENGINE.step(DT)
        if ENGINE.deploy_done:
            break
    for _ in range(SETTLE):
        ENGINE.step(DT)


def refresh():
    """击杀/传送后刷新链路真值与统计(与 physics_check 同款三连)。"""
    ENGINE._recompute_static()
    ENGINE._update_adj()
    ENGINE._stats()


def kill_spikes(victims: list):
    """击杀一组道钉(节点永久宕机,与塌方语义一致)。"""
    for n in victims:
        n.alive = False
        n.state = "DEAD"
        n._death_logged = True
    refresh()


def alive_spikes() -> list:
    return [n for n in ENGINE.nodes.values()
            if n.alive and n.role == "spike"]


def wait_recovery(budget: int) -> int:
    """步进直到 coverage≥0.95 且 partitions=0。Returns: tick 数,超时 -1。"""
    for k in range(1, budget + 1):
        ENGINE.step(DT)
        if ENGINE.coverage >= 0.95 and ENGINE.partitions == 0:
            return k
    return -1


def cut_window(mid: float, halves=(50, 80, 120, 180, 260)):
    """在 mid±half 窗口内逐级加宽击杀道钉,直到确实产生分区。Returns: 生效半宽。"""
    for half in halves:
        kill_spikes([n for n in alive_spikes() if abs(n.x - mid) <= half])
        if ENGINE.partitions > 0:
            return half
    return None


def throat_mids(seed: int) -> list:
    """该地图全部喉道的中心 x(只读几何,无需部署)。"""
    ENGINE.reset(seed)
    return [(a + b) / 2 for a, b in ENGINE.world.throats()]


def _lap(name: str, t0: float):
    print(f"== {name} 阶段完成, 累计 {time.perf_counter() - t0:.0f}s ==", flush=True)


def _all_rows(jsonl: str) -> list:
    rows = []
    if os.path.exists(jsonl):
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


# ------------------------------------------------------------------ 各阶段实验

def run_m1(maps: list, log, done: set, t0: float):
    """M1: 逐喉道切断 → 恢复时间(每样本全新部署,灾难注入互相独立)。"""
    for seed in maps:
        mids = throat_mids(seed)
        for ti, mid in enumerate(mids):
            if ("m1", seed, ti, 0, 0) in done:
                continue
            deploy_settle(seed)
            base_alive = len(alive_spikes())
            half = cut_window(mid)
            if half is None:
                log({"phase": "m1", "seed": seed, "throat": ti, "cut_half": None})
                continue
            part = ENGINE.partitions
            ticks = wait_recovery(RECOVER_BUDGET)
            log({"phase": "m1", "seed": seed, "throat": ti, "cut_half": half,
                 "killed": base_alive - len(alive_spikes()), "partitions": part,
                 "recover_ticks": ticks, "final_cov": round(ENGINE.coverage, 3)})
            print(f"  [M1] seed{seed} throat{ti}/{len(mids)} half={half} "
                  f"recover={ticks}t", flush=True)
    _lap("M1", t0)


def run_m2(maps: list, log, done: set, t0: float):
    """M2: 同时切断 k 条喉道,k=1..n-1(喉道全断 = 每腔室互相孤立)。"""
    for seed in maps:
        mids = throat_mids(seed)
        n = len(mids)
        rng = random.Random(90000 + seed)
        for k in range(1, n):
            for draw in range(2):
                if ("m2", seed, k, draw, 0) in done:
                    continue
                subset = rng.sample(range(n), k)
                deploy_settle(seed)
                halves = [cut_window(mids[ti], (80, 120, 180, 260))
                          for ti in subset]
                if not any(halves):
                    log({"phase": "m2", "seed": seed, "k": k, "draw": draw,
                         "recover_ticks": None, "note": "无法构造分区"})
                    continue
                ticks = wait_recovery(RECOVER_BUDGET)
                log({"phase": "m2", "seed": seed, "k": k, "draw": draw,
                     "throats": subset, "cut_halves": halves,
                     "recover_ticks": ticks, "final_cov": round(ENGINE.coverage, 3)})
                print(f"  [M2] seed{seed} k={k} throats={subset} "
                      f"recover={ticks}t", flush=True)
    _lap("M2", t0)


def run_m3(maps: list, log, done: set, t0: float):
    """M3: 单节点孤立重连时间(传送到离全网 ≥1.1×RANGE 的管内安全点)。"""
    for seed in maps:
        rng = random.Random(70000 + seed)
        for vi in range(3):
            if ("m3", seed, vi, 0, 0) in done:
                continue
            deploy_settle(seed)
            spikes = alive_spikes()
            if not spikes:
                continue
            v = spikes[rng.randrange(len(spikes))]
            w = ENGINE.world
            spot = None
            for _ in range(80):
                xx = rng.uniform(w.W * 0.08, w.W * 0.92)
                yy = ENGINE._place_node(xx, w.yc(xx), 14.0)
                if all(math.hypot(xx - m.x, yy - m.y) > RANGE * 1.1
                       for m in ENGINE.nodes.values()
                       if m.alive and m.role != "rover"):
                    spot = (xx, yy)
                    break
            if spot is None:
                log({"phase": "m3", "seed": seed, "victim": vi, "ok": False,
                     "rejoin_ticks": -1, "note": "找不到孤立点"})
                continue
            v.x, v.y = round(spot[0], 1), round(spot[1], 1)
            refresh()
            ticks, ok = -1, False
            for kk in range(RECOVER_BUDGET):
                ENGINE.step(DT)
                r = v.routing.get("BASE-00")
                if r and r["cost"] < INF:
                    ticks, ok = kk + 1, True
                    break
            log({"phase": "m3", "seed": seed, "victim": vi, "ok": ok,
                 "rejoin_ticks": ticks, "isolated_at": [round(v.x), round(v.y)],
                 "final_cov": round(ENGINE.coverage, 3)})
            print(f"  [M3] seed{seed} node{vi} rejoin={ticks}t ok={ok}", flush=True)
    _lap("M3", t0)


def run_m5(maps: list, log, done: set, t0: float):
    """M5: 随机击杀 p% 存活道钉 → 恢复成功率/耗时/最终覆盖率。"""
    ratios = [0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50]
    for seed in maps:
        for p in ratios:
            for draw in range(2):
                if ("m5", seed, 0, draw, p) in done:
                    continue
                rng = random.Random(int(50000 + seed * 100 + p * 1000 + draw))
                deploy_settle(seed)
                spikes = alive_spikes()
                kk = max(1, round(len(spikes) * p))
                kill_spikes(rng.sample(spikes, kk))
                part0, cov0 = ENGINE.partitions, ENGINE.coverage
                ticks = wait_recovery(RECOVER_BUDGET_M5)
                log({"phase": "m5", "seed": seed, "p": p, "draw": draw, "killed": kk,
                     "partitions_after_kill": part0, "cov_after_kill": round(cov0, 3),
                     "recover_ticks": ticks, "final_cov": round(ENGINE.coverage, 3)})
                print(f"  [M5] seed{seed} p={p:.0%} killed={kk} part={part0} "
                      f"recover={ticks}t cov={ENGINE.coverage:.2f}", flush=True)
    _lap("M5", t0)


# ------------------------------------------------------------------ 报告渲染

def render(rows: list, maps: list, out_path: str, elapsed: float):
    """把 JSONL 行渲染成 Markdown 报告。"""
    m1 = [r for r in rows if r["phase"] == "m1" and r.get("recover_ticks") is not None]
    m1_ok = [r["recover_ticks"] for r in m1 if r["recover_ticks"] > 0]
    m2 = [r for r in rows if r["phase"] == "m2" and r.get("recover_ticks") is not None]
    m3 = [r for r in rows if r["phase"] == "m3" and r.get("rejoin_ticks") is not None]
    m5 = [r for r in rows if r["phase"] == "m5" and r.get("recover_ticks") is not None]
    m2_ok = sum(1 for r in m2 if r["recover_ticks"] > 0)
    ok3 = [r for r in m3 if r.get("ok")]
    m5_ok = [r for r in m5 if r["recover_ticks"] > 0]

    L = []
    ap = L.append
    ap("# LTRP 鲁棒性基准报告\n")
    ap(f"- 生成时间:{time.strftime('%Y-%m-%d %H:%M:%S')} · 总耗时 {elapsed / 60:.1f} 分钟")
    ap(f"- 地图样本:{len(maps)} 张随机地图(seed {maps[0]}~{maps[-1]});"
       f"每个实验样本均从全新部署开始(同 seed 部署确定性一致,重复间仅灾难注入不同)")
    ap(f"- 恢复判据:coverage ≥ 0.95 且 partitions = 0;"
       f"预算 M1/M2/M3 = {RECOVER_BUDGET} tick、M5 = {RECOVER_BUDGET_M5} tick")
    ap(f"- **时间折算:1 tick = {DT}s 现实秒**(time_scale=100 只是演示加速,"
       f"不改变物理;下表'现实秒'均按 tick×{DT} 折算)\n")

    # ---- M1
    ap("## M1 · 断裂单条喉道后的恢复连接时间\n")
    st = stats(m1_ok)
    ap("| 指标 | 样本数 | 恢复率 | tick 均值 | 现实秒均值 | tick 中位 | 现实秒中位 | tick p95 | 现实秒 p95 |")
    ap("|---|---|---|---|---|---|---|---|---|")
    if st["mean"] is not None:
        ap(f"| 喉道切断→全网恢复 | {len(m1)} | {len(m1_ok)}/{len(m1)}"
           f" ({len(m1_ok) / max(1, len(m1)):.0%}) | {st['mean']} | {secs(st['mean'])} | "
           f"{st['median']} | {secs(st['median'])} | {st['p95']} | {secs(st['p95'])} |")
    else:
        ap("| (无有效样本) | | | | | | | | |")
    ap("\n逐样本明细:\n")
    ap("| seed | 喉道# | 切断半宽(px) | 击杀道钉数 | 恢复 tick | 恢复现实秒 |")
    ap("|---|---|---|---|---|---|")
    for r in m1:
        t = r["recover_ticks"]
        ap(f"| {r['seed']} | {r['throat']} | {r['cut_half']} | {r.get('killed', '-')} | "
           f"{t} | {secs(t) if t > 0 else '**超时**'} |")

    # ---- M2
    ap("\n## M2 · 可承受的并发喉道断裂数\n")
    ap("同一时刻切断 k 条喉道(每图随机组合,每档 k 抽样 2 次):\n")
    ap("| 同时断裂数 k | 实验数 | 完全恢复数 | 成功率 | 平均恢复 tick | 平均现实秒 |")
    ap("|---|---|---|---|---|---|")
    for k in sorted({r["k"] for r in m2}):
        sub = [r for r in m2 if r["k"] == k]
        oks = [r["recover_ticks"] for r in sub if r["recover_ticks"] > 0]
        st = stats(oks)
        mean_s = secs(st["mean"]) if st["mean"] is not None else "-"
        ap(f"| {k} | {len(sub)} | {len(oks)} | {len(oks) / max(1, len(sub)):.0%} | "
           f"{st['mean'] if st['mean'] is not None else '-'} | {mean_s} |")
    per_map = {}
    for r in m2:
        if r["recover_ticks"] > 0:
            per_map[r["seed"]] = max(per_map.get(r["seed"], 0), r["k"])
    if per_map:
        vals = sorted(per_map.values())
        ap(f"\n- 每图最大可承受 k:{vals}(中位数 {vals[len(vals) // 2]},"
           f"均值 {sum(vals) / len(vals):.1f})")
        ap("- 注:喉道是腔室间唯一窄通道,切断 k 条即把管道分成 k+1 段;"
           "恢复依靠幸存道钉自愈移动重新桥接 + 月球车摆渡信使")

    # ---- M3
    ap("\n## M3 · 单节点孤立后的重连时间\n")
    st = stats([r["rejoin_ticks"] for r in ok3])
    ap("| 指标 | 样本数 | 重连率 | tick 均值 | 现实秒均值 | tick 中位 | 现实秒中位 | tick p95 | 现实秒 p95 |")
    ap("|---|---|---|---|---|---|---|---|---|")
    if st["mean"] is not None:
        ap(f"| 孤立道钉→重新入网 | {len(m3)} | {len(ok3)}/{len(m3)}"
           f" ({len(ok3) / max(1, len(m3)):.0%}) | {st['mean']} | {secs(st['mean'])} | "
           f"{st['median']} | {secs(st['median'])} | {st['p95']} | {secs(st['p95'])} |")
    else:
        ap("| (无有效样本) | | | | | | | | |")

    # ---- M4
    ap("\n## M4 · 链路(分区)恢复率汇总\n")
    total = len(m1) + len(m2) + len(m3) + len(m5)
    oks = len(m1_ok) + m2_ok + len(ok3) + len(m5_ok)
    ap(f"- 喉道切断(M1):{len(m1_ok)}/{len(m1)}"
       f" = {len(m1_ok) / max(1, len(m1)):.1%}")
    ap(f"- 并发喉道断裂(M2):{m2_ok}/{len(m2)} = {m2_ok / max(1, len(m2)):.1%}")
    ap(f"- 节点孤立重连(M3):{len(ok3)}/{len(m3)} = {len(ok3) / max(1, len(m3)):.1%}")
    ap(f"- 随机比例击杀(M5):{len(m5_ok)}/{len(m5)} = {len(m5_ok) / max(1, len(m5)):.1%}")
    ap(f"- **总体恢复率:{oks}/{total} = {oks / max(1, total):.1%}**")

    # ---- M5
    ap("\n## M5 · 鲁棒性 vs 节点失效比例曲线\n")
    ap("| 失效比例 p | 实验数 | 造成分区比例 | 恢复成功率 | 分区样本恢复率 | "
       "分区样本恢复 tick 均值 | 现实秒均值 | 最终覆盖率均值 |")
    ap("|---|---|---|---|---|---|---|---|")
    for p in sorted({r["p"] for r in m5}):
        sub = [r for r in m5 if r["p"] == p]
        parted = [r for r in sub if r.get("partitions_after_kill", 0) > 0]
        oks_all = [r for r in sub if r["recover_ticks"] > 0]
        oks_part = [r["recover_ticks"] for r in parted if r["recover_ticks"] > 0]
        st = stats(oks_part)
        covs = [r["final_cov"] for r in sub]
        mean_s = secs(st["mean"]) if st["mean"] is not None else "-"
        ap(f"| {p:.0%} | {len(sub)} | {len(parted) / max(1, len(sub)):.0%} | "
           f"{len(oks_all) / max(1, len(sub)):.0%} | "
           f"{len(oks_part)}/{max(1, len(parted))}"
           f" ({len(oks_part) / max(1, len(parted)):.0%}) | "
           f"{st['mean'] if st['mean'] is not None else '-'} | {mean_s} | "
           f"{sum(covs) / len(covs):.3f} |")
    ap("\n```")
    ap("恢复成功率曲线(█ = 成功样本占比,每格 2.5%):")
    for p in sorted({r["p"] for r in m5}):
        sub = [r for r in m5 if r["p"] == p]
        rate5 = sum(1 for r in sub if r["recover_ticks"] > 0) / max(1, len(sub))
        ap(f"p={p:>4.0%} |{'█' * round(rate5 * 40):<40}| {rate5:.0%}")
    ap("```\n")

    ap("## 方法学说明\n")
    ap("- 每个样本从全新 reset+部署开始(引擎确定性:同 seed 部署逐 tick 一致),"
       "样本间仅灾难注入不同;受害者抽样用独立随机源 random.Random(tag),可复现。")
    ap("- 击杀语义:节点永久宕机(alive=False,与'塌方/摧毁喉道'按钮一致);"
       "恢复 = 幸存节点经自愈移动/月球车摆渡重新全员连通基站。")
    ap("- M1 切断窗口从 ±50px 逐级加宽到 ±260px 直到确实分区(与 tests/physics_check.py 同口径);"
       "M2 每条喉道从 ±80px 起加宽。")
    ap("- M3 孤立点 = 管内安全点且与所有存活非 rover 节点距离 >1.1×RANGE(286px),"
       "确保初始无可达链路;重连 = 该节点重新持有到 BASE-00 的有效路由。")
    ap("- 时间口径:tick×0.3s 为**现实秒**(与 tests/physics_check.py 的"
       "'N tick ≈ N×0.3 真实秒'一致);若需仿真内时间再乘 time_scale=100。")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


# ------------------------------------------------------------------ 主入口

def main() -> int:
    args = sys.argv[1:]
    maps_n = 15
    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "鲁棒性基准报告.md")
    jsonl = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_results.jsonl")
    if "--maps" in args:
        maps_n = int(args[args.index("--maps") + 1])
    if "--out" in args:
        out_path = os.path.abspath(args[args.index("--out") + 1])
    maps = list(range(1, maps_n + 1))

    # 增量续跑:JSONL 里已有的样本标签跳过(同参数幂等)
    done = set()
    for r in _all_rows(jsonl):
        done.add((r["phase"], r["seed"], r.get("throat", r.get("k", r.get("victim", 0))),
                  r.get("draw", 0), r.get("p", 0)))

    fh = open(jsonl, "a", encoding="utf-8")

    def log(rec):
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()

    t0 = time.perf_counter()
    print(f"鲁棒性基准开始: maps={maps}, 输出={out_path}", flush=True)
    run_m1(maps, log, done, t0)
    render(_all_rows(jsonl), maps, out_path, time.perf_counter() - t0)
    run_m2(maps, log, done, t0)
    render(_all_rows(jsonl), maps, out_path, time.perf_counter() - t0)
    run_m3(maps, log, done, t0)
    render(_all_rows(jsonl), maps, out_path, time.perf_counter() - t0)
    run_m5(maps, log, done, t0)
    fh.close()
    render(_all_rows(jsonl), maps, out_path, time.perf_counter() - t0)
    print(f"完成,报告: {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
