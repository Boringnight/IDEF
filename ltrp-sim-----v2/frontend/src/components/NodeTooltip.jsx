// 节点悬停科技感 Tooltip:电量/状态/位置/温度/路由/载荷/邻居表。
// 纯展示组件;邻居富信息由 TopoMap2D 交叉引用快照后传入。
import React from "react";
import { chamberName, ROLE_ZH, STATE_COLOR, stateZh } from "../canvas/geometry.js";

export default function NodeTooltip({ node, nbrs, left, top }) {
  if (!node) return null;
  const soc = Math.max(0, Math.min(100, node.soc ?? 0));
  const socColor = soc < 20 ? "#FF5252" : soc < 40 ? "#FF9E42" : "#2EFF9E";
  const stColor = STATE_COLOR[node.state] || "#9FD8FF";
  return (
    <div className="tooltip" style={{ left, top }}>
      <div className="tt-title">{node.id} · {ROLE_ZH[node.role]}</div>

      <div className="tt-row">
        <span className="k">电量 (SoC)</span>
        <span className="v" style={{ color: socColor }}>{soc.toFixed(1)}%</span>
      </div>
      <div className="tt-soc"><i style={{ width: soc + "%", background: socColor }} /></div>

      <div className="tt-row">
        <span className="k">运行状态</span>
        <span className="v" style={{ color: stColor }}>
          {stateZh(node.state)}
          {node.sleeping ? " · 休眠" : ""}
          {node.moving ? " · 移动" : ""}
          {node.sos ? " · 求救" : ""}
        </span>
      </div>
      <div className="tt-row">
        <span className="k">位置信息</span>
        <span className="v tt-pos">({node.x} , {node.y})</span>
      </div>
      <div className="tt-row">
        <span className="k">温度 / 辐射</span>
        <span className="v tt-pos">
          {(node.temp ?? 0).toFixed(0)}°C · SEU {node.seu ?? 0} 次
        </span>
      </div>
      <div className="tt-row">
        <span className="k">所属腔室</span>
        <span className="v">腔室 {chamberName(node.domain)}{node.border ? " · 喉道边界" : ""}</span>
      </div>

      {node.role !== "base" && (
        node.nh
          ? <div className="tt-row">
              <span className="k">→ 基站路由</span>
              <span className="v">经 {node.nh} · 代价 {node.cost} · 最弱SoC {node.ms}%</span>
            </div>
          : <div className="tt-row"><span className="k">→ 基站路由</span><span className="v tt-bad">✕ 无实时路由(束模式)</span></div>
      )}

      <div className="tt-row">
        <span className="k">载荷</span>
        <span className="v">{node.bundles > 0 ? `⬒ 束 ${node.bundles} · 待发 ${node.pkts}` : `待发包 ${node.pkts}`}</span>
      </div>
      {node.crit && <div className="tt-bad tt-note">⚠ 关键割点 · 2 跳视图自识别(锁定常开)</div>}

      <div className="tt-sec">邻居节点 ({nbrs.length})</div>
      {nbrs.length === 0 && <div className="dim">无(等待信标)</div>}
      <div className="tt-nbr-grid">
        {nbrs.map((nb) => {
          const cls = [];
          if (nb.state === "DEAD") cls.push("dead");
          if (nb.sleeping) cls.push("sleeping");
          const lowSoc = nb.soc != null && nb.soc < 25;
          if (lowSoc) cls.push("lowbat");
          return (
            <span key={nb.id} className={`tt-nbr ${cls.join(" ")}`}>
              <span className="nid">{nb.id}</span>
              <span className="nrole">{ROLE_ZH[nb.role] || nb.role}</span>
              <span className="nsnr">{nb.snr.toFixed(1)}dB</span>
              {nb.ber != null && nb.ber > 1e-9 && (
                <span className="nber">e{nb.ber.toExponential(0).replace("e-", "-")}</span>
              )}
              {nb.soc != null && <span className={`nsoc ${lowSoc ? "low" : ""}`}>{nb.soc.toFixed(0)}%</span>}
            </span>
          );
        })}
      </div>
    </div>
  );
}
