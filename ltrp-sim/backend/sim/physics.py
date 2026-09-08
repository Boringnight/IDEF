# -*- coding: utf-8 -*-
"""物理层计算(移植自 lava-tube 工程并按本工程校准):
路径损耗 / 热噪声 / SNR / BER / 链路熔断判定 / 逐跳损伤概率。
所有量(SNR/BER/余量)都是接收端本地可测的物理量 —— 与去中心化协议天然兼容,
引擎只做"物理世界投递",不做任何全局计算。"""
import math

# 单频段 UWB(校准): 3.5GHz / 10MHz 带宽 / 1Mbps BPSK。
# 校准目标: 默认射频参数(14dBm + 双端 3dBi)下
#   d ≤ 0.8×RANGE(208m) 链路可靠闭合(余量≈7dB, SNR≈15dB);
#   d → RANGE(260m) 临界(SNR≈12.5dB, 余量≈4.5dB);
#   热浪(≈75°C)时噪声底+高温NF恶化 ≈6.7dB,足以熔断 d>205m 的劣质链路。
BAND = {
    "freq_ghz": 3.5,
    "data_rate": 1e6,
    "bandwidth_hz": 10e6,
    "modulation": "BPSK",
    "snr_req_db": 8.0,
    "max_range": 260.0,     # 与 nodes.RANGE 保持一致
}

# 月球熔岩管内: 无大气, 视距 + 洞壁散射, 路径损耗指数取 2.6
PATH_LOSS_EXPONENT = 2.6
REFERENCE_DIST_M = 1.0
K_BOLTZ = 1.38e-23
BER_MIN = 1e-12
ACK_BYTES = 14             # 每跳数据 ACK 开销(字节)


def free_space_path_loss_db(d_m: float, freq_ghz: float) -> float:
    """通用路径损耗模型: PL = FSPL(d0) + 10*gamma*log10(d/d0)
    月球熔岩管内无大气、以视距为主; 洞壁散射/多径已并入 PATH_LOSS_EXPONENT(=2.6)。"""
    if d_m < REFERENCE_DIST_M:
        d_m = REFERENCE_DIST_M
    lam = 3e8 / (freq_ghz * 1e9)
    d0 = REFERENCE_DIST_M
    fspl0 = 20 * math.log10(4 * math.pi * d0 / lam)
    return fspl0 + 10 * PATH_LOSS_EXPONENT * math.log10(d_m / d0)


def thermal_noise_floor_dbm(node, bandwidth_hz: float) -> float:
    """热噪声功率 = kTB。月球无大气吸热, 接收节点温度直接决定本征噪声底。"""
    t_k = node.temp_c + 273.15
    noise_w = K_BOLTZ * t_k * bandwidth_hz
    return 10 * math.log10(noise_w / 1e-3) + 6.0   # +6dB 接收机噪声系数


def ber_from_snr(snr_db: float) -> float:
    """BPSK BER 由 (Eb/N0) 决定, erfc(Q 函数)近似; 与 link_budget 使用同一曲线,
    供引擎在叠加阴影衰落后的实际 SNR 上重算 BER"""
    snr_lin = 10 ** (snr_db / 10.0)
    proc_gain = BAND["bandwidth_hz"] / BAND["data_rate"]
    ebn0 = snr_lin * min(proc_gain, 1e6) / 10
    ber = 0.5 * math.erfc(math.sqrt(max(ebn0, 0.0)))
    return min(1.0, max(ber, BER_MIN))


def link_budget(tx, rx) -> dict | None:
    """计算 tx -> rx 单向链路。返回 SNR/BER/余量; 物理不通返回 None。
    融合: 发射功率 + 双端天线增益 - 倾角失配惩罚 - 路径损耗 vs 有效灵敏度。"""
    if not tx.alive or not rx.alive:
        return None
    d = math.hypot(tx.x - rx.x, tx.y - rx.y)
    if d > BAND["max_range"]:
        return None

    # 天线倾角失配: cos 损失近似 -> dB 惩罚(地基沉降导致指向偏离)
    tilt_penalty = 20 * math.log10(
        1.0 / max(0.05, math.cos(math.radians(tx.tilt_deg + rx.tilt_deg) / 2))
    ) if (tx.tilt_deg + rx.tilt_deg) > 3 else 0.0

    pl = free_space_path_loss_db(d, BAND["freq_ghz"])
    prx_dbm = (tx.tx_power_dbm + tx.ant_gain_dbi + rx.ant_gain_dbi
               - pl - tilt_penalty)

    noise_dbm = thermal_noise_floor_dbm(rx, BAND["bandwidth_hz"])
    snr_db = prx_dbm - noise_dbm
    ber = ber_from_snr(snr_db)

    margin = prx_dbm - rx.effective_rx_sensitivity()
    up = margin > 0 and ber < 1e-3        # 链路熔断判定
    return {
        "distance": round(d, 1),
        "prx_dbm": round(prx_dbm, 1),
        "snr_db": round(snr_db, 1),
        "ber": ber,
        "margin_db": round(margin, 1),
        "up": up,
    }


def damage_prob(ber: float, nbytes: int) -> float:
    """nbytes 字节经 BER 信道至少错 1 比特的概率: 1-(1-ber)^(8n)"""
    p = min(max(ber, 0.0), 0.5)
    return 1.0 - (1.0 - p) ** (nbytes * 8)
