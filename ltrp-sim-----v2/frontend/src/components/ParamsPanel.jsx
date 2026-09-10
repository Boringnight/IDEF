import React, { useEffect, useRef, useState } from "react";
import { api } from "../ws.js";

const slider = (label, key, min, max, step, unit, hint) =>
  ({ label, key, min, max, step, unit, hint });

// 全局协议参数: 与后端 Params.SPEC 一一对应(默认值由后端快照下发)
const ITEMS = [
  slider("信标周期 T_B", "tb", 0.3, 5.0, 0.1, "s", "调大 → 邻居发现/收敛变慢"),
  slider("建链 SNR 门限 γ", "gamma", 0, 30, 0.5, "dB", "调低 → 接入劣质链路(高 BER 区)"),
  slider("逐跳时延罚项 α", "alpha", 0, 30, 0.5, "", "调大 → 更偏向少跳长链路"),
  slider("安全路径阈值 E_DIE", "e_die", 0, 80, 1, "%", "低于此最弱 SoC → 切 max-min 能量模式"),
  slider("束队列上限 Q_CAP", "q_cap", 8, 200, 4, "个", "断连暂存容量"),
  slider("最大跳数", "hops_max", 10, 200, 5, "", "超过即丢弃"),
  slider("报文 TTL", "pkt_ttl", 30, 1200, 10, "s", "超时即丢弃"),
];

export default function ParamsPanel({ params, onClose }) {
  const [pending, setPending] = useState({});
  const throttle = useRef({ last: 0, timer: null });

  useEffect(() => setPending({}), [params]);
  useEffect(() => () => clearTimeout(throttle.current.timer), []);

  if (!params) return null;
  const val = (key) => (key in pending ? pending[key] : params[key]);

  const push = (key, v) => {
    setPending((p) => ({ ...p, [key]: v }));
    const ref = throttle.current;
    const fire = () => {
      ref.last = performance.now();
      api("/action/param", { params: { [key]: v } });
    };
    clearTimeout(ref.timer);
    if (performance.now() - ref.last > 120) fire();
    else ref.timer = setTimeout(fire, 120);
  };

  const reset = () => {
    // 恢复默认值(逐项下发)
    const defs = { tb: 0.9, gamma: 12.0, alpha: 8.0, e_die: 40.0, q_cap: 48, hops_max: 60, pkt_ttl: 240.0 };
    setPending(defs);
    api("/action/param", { params: defs });
  };

  return (
    <div className="params-panel">
      <div className="insp-head">
        <span>⚑ 协议参数(全局 · 引擎下一拍生效)</span>
        <button className="insp-close" onClick={onClose}>✕</button>
      </div>
      {ITEMS.map((it) => (
        <label key={it.key} className="insp-slider">
          <span className="sl-label">
            {it.label}
            <b className="sl-val">{val(it.key)}{it.unit}</b>
          </span>
          <input type="range" min={it.min} max={it.max} step={it.step}
                 value={val(it.key)}
                 onChange={(e) => push(it.key, parseFloat(e.target.value))} />
          <span className="sl-hint">{it.hint}</span>
        </label>
      ))}
      <button className="hud-btn pp-reset" onClick={reset}>↻ 恢复默认</button>
    </div>
  );
}
