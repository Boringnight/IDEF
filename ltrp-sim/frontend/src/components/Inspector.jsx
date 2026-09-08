import React, { useEffect, useRef, useState } from "react";
import { api } from "../ws.js";

const STATE_ZH = {
  NORMAL: "正常(可中继)", PROTECTED: "保护态(退出中继,只收不发)",
  DYING: "垂危(强制休眠)", DEAD: "宕机",
};

const slider = (label, key, min, max, step, unit) => ({ label, key, min, max, step, unit });

// 节点物理参数面板: 与后端 Node.MUTABLE 白名单一一对应
const PARAM_GROUPS = [
  {
    title: "能源参数 (Power)",
    items: [
      slider("电池剩余容量", "battery_mah", 0, 12000, 100, "mAh"),
      slider("发射电流", "i_tx", 50, 900, 10, "mA"),
      slider("超级电容", "supercap_pct", 0, 100, 1, "%"),
    ],
  },
  {
    title: "射频与天线 (RF)",
    items: [
      slider("发射功率 TX Power", "tx_power_dbm", -10, 24, 0.5, "dBm"),
      slider("接收灵敏度 RX Sens", "rx_sensitivity_dbm", -120, -70, 0.5, "dBm"),
      slider("天线增益", "ant_gain_dbi", 0, 12, 0.5, "dBi"),
      slider("天线倾角(沉降)", "tilt_deg", 0, 60, 1, "°"),
    ],
  },
  {
    title: "环境 (Environment)",
    items: [
      slider("节点温度", "temp_c", -80, 120, 1, "°C"),
      slider("累积辐射剂量", "radiation_rad", 0, 40000, 100, "rad"),
    ],
  },
];

export default function Inspector({ node, onClose }) {
  const [pending, setPending] = useState({});
  const throttle = useRef({ last: 0, timer: null });

  // 后端每帧回传最新值, 未在拖动中的滑块跟随刷新
  useEffect(() => setPending({}), [node?.id]);
  useEffect(() => () => clearTimeout(throttle.current.timer), []);

  if (!node) return null;
  const val = (key, fallback) =>
    key in pending ? pending[key] : (node[key] ?? fallback);
  const raw = (key) =>
    key in pending ? pending[key] : (node.phys?.[key] ?? null);

  const push = (key, v) => {
    setPending((p) => ({ ...p, [key]: v }));   // 本地立即回显
    // 提交节流: 拖动时 ≤8 次/秒下发, 尾值 120ms 后必达 —— 防参数风暴
    const ref = throttle.current;
    const fire = () => {
      ref.last = performance.now();
      api("/action/param", { node: node.id, params: { [key]: v } });
    };
    clearTimeout(ref.timer);
    if (performance.now() - ref.last > 120) fire();
    else ref.timer = setTimeout(fire, 120);
  };

  return (
    <div className="inspector">
      <div className="insp-head">
        <span>{node.id}</span>
        <button className="insp-close" onClick={onClose}>✕</button>
      </div>
      <Row k="角色" v={roleZh(node.role)} />
      <Row k="所属域" v={`腔室 ${chamberName(node.domain)}${node.border ? "(喉道边界)" : ""}`} />
      <Row k="剩余电量" v={`${node.soc}% · ${STATE_ZH[node.state] || node.state}`} />
      {node.temp != null && (
        <Row k="温度" v={`${node.temp}°C${node.temp >= 45 ? "(热降额中)" : ""}`}
             warn={node.temp >= 45} />
      )}
      {node.seu != null && node.seu > 0 && <Row k="单粒子翻转" v={`${node.seu} 次`} warn />}
      <Row k="割点自识别" v={node.crit ? "⚠ 是(2 跳视图判定,锁定常开)" : "否"} />
      {node.role !== "base" && (
        node.nh
          ? <Row k="→ 基站路由" v={`下一跳 ${node.nh} · 代价 ${node.cost} · 路径最弱SoC ${node.ms}%`} />
          : <Row k="→ 基站路由" v="✕ 无实时路由(数据进入束存储/等待摆渡)" warn />
      )}
      <Row k="滞留数据束" v={node.bundles} warn={node.bundles > 0} />
      <Row k="待发数据包" v={node.pkts} />

      <div className="insp-sec">邻居表(信标发现 · EWMA SNR / BER)</div>
      <div className="insp-nbrs">
        {node.nbrs.length === 0 && <span className="dim">无(等待信标)</span>}
        {node.nbrs.map(([id, snr, ber]) => (
          <span key={id} className={`nbr ${snr < 12 ? "bad" : ""}`}>
            {id} · {snr.toFixed(1)}dB · {fmtBer(ber)}
          </span>
        ))}
      </div>

      {node.role !== "base" && (
        <div className="insp-god">
          <div className="insp-sec">⚙ 上帝模式 · 物理参数(实时生效)</div>
          {PARAM_GROUPS.map((g) => (
            <div key={g.title} className="insp-group">
              <div className="insp-gtitle">{g.title}</div>
              {g.items.map((it) => (
                <label key={it.key} className="insp-slider">
                  <span className="sl-label">
                    {it.label}
                    <b className="sl-val">{fmtVal(raw(it.key))}{it.unit}</b>
                  </span>
                  <input type="range" min={it.min} max={it.max} step={it.step}
                         value={val(it.key)}
                         onChange={(e) => push(it.key, parseFloat(e.target.value))} />
                </label>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function fmtBer(b) {
  if (b == null) return "";
  if (b <= 1e-11) return "BER~0";
  return `BER${b.toExponential(0)}`;
}

function fmtVal(v) {
  return v == null ? "--" : Math.round(v * 10) / 10;
}

function roleZh(r) {
  return { base: "主基站(汇聚点)", spike: "道钉中继", probe: "深处探测器", rover: "月球车(接触计划载体)" }[r] || r;
}

function chamberName(i) {
  let n = i, s = "";
  for (;;) {
    s = String.fromCharCode(65 + (n % 26)) + s;
    n = Math.floor(n / 26) - 1;
    if (n < 0) break;
  }
  return s;
}

function Row({ k, v, warn }) {
  return (
    <div className={`insp-row ${warn ? "warn" : ""}`}>
      <span className="k">{k}</span>
      <span className="v">{v}</span>
    </div>
  );
}
