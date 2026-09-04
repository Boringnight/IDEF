import React from "react";

const STATE_ZH = {
  NORMAL: "正常(可中继)", PROTECTED: "保护态(退出中继,只收不发)",
  DYING: "垂危(强制休眠)", DEAD: "宕机",
};

export default function Inspector({ node, onClose }) {
  if (!node) return null;
  return (
    <div className="inspector">
      <div className="insp-head">
        <span>{node.id}</span>
        <button className="insp-close" onClick={onClose}>✕</button>
      </div>
      <Row k="角色" v={roleZh(node.role)} />
      <Row k="所属域" v={`腔室 ${chamberName(node.domain)}${node.border ? "(喉道边界)" : ""}`} />
      <Row k="剩余电量" v={`${node.soc}% · ${STATE_ZH[node.state] || node.state}`} />
      <Row k="割点自识别" v={node.crit ? "⚠ 是(2 跳视图判定,锁定常开)" : "否"} />
      {node.role !== "base" && (
        node.nh
          ? <Row k="→ 基站路由" v={`下一跳 ${node.nh} · 代价 ${node.cost} · 路径最弱SoC ${node.ms}%`} />
          : <Row k="→ 基站路由" v="✕ 无实时路由(数据进入束存储/等待摆渡)" warn />
      )}
      <Row k="滞留数据束" v={node.bundles} warn={node.bundles > 0} />
      <Row k="待发数据包" v={node.pkts} />
      <div className="insp-sec">邻居表(信标发现 · EWMA SNR)</div>
      <div className="insp-nbrs">
        {node.nbrs.length === 0 && <span className="dim">无(等待信标)</span>}
        {node.nbrs.map(([id, snr]) => (
          <span key={id} className={`nbr ${snr < 12 ? "bad" : ""}`}>
            {id} · {snr.toFixed(1)}dB
          </span>
        ))}
      </div>
    </div>
  );
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
