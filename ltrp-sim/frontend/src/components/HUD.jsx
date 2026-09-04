import React from "react";
import { api } from "../ws.js";

export default function HUD({ stats, connected }) {
  if (!stats) return null;
  return (
    <div className="hud">
      <div className="hud-left">
        <div className={`dot ${connected ? "ok" : "bad"}`} />
        <span className="hud-title">{connected ? "LTRP 引擎已连接" : "连接中…"}</span>
        <Chip label="存活" v={`${stats.alive}/${stats.total}`} warn={stats.alive < stats.total} />
        <Chip label="路由覆盖" v={`${(stats.coverage * 100).toFixed(0)}%`} warn={stats.coverage < 0.9} />
        <Chip label="分区" v={stats.partitions} warn={stats.partitions > 1} />
        <Chip label="割点" v={stats.critical} />
        <Chip label="束滞留" v={stats.bundles + (stats.ferry_bundles ? ` (摆渡 ${stats.ferry_bundles})` : "")} warn={stats.bundles > 30} />
        <Chip label="平均SoC" v={`${stats.avg_soc}%`} warn={stats.avg_soc < 40} />
        <Chip label="地月链路(潮汐锁定)" v="▲ 始终可见 · 实时回传" />
        <Chip label="已交付" v={stats.delivered} />
        {stats.heat && <Chip label="热浪" v="进行中" warn />}
        {stats.sleeping > 0 && <Chip label="轮值休眠中" v={stats.sleeping} />}
      </div>
      <div className="hud-right">
        <Btn onClick={() => api("/action/disaster", { kind: "collapse" })}>🪨 塌方</Btn>
        <Btn onClick={() => api("/action/disaster", { kind: "heat" })}>🔥 热浪</Btn>
        <Btn onClick={() => api("/action/disaster", { kind: "critical" })}>⚔ 摧毁一条喉道</Btn>
        <Btn active={stats.sleep_on}
             onClick={() => api("/action/sleep", { on: !stats.sleep_on })}>
          💤 休眠调度:{stats.sleep_on ? "开" : "关"}
        </Btn>
        <Btn onClick={() => api("/action/map", {})}>🎲 随机地图</Btn>
        <Btn onClick={() => api("/action/reset", {})}>↻ 重置</Btn>
      </div>
    </div>
  );
}

function Chip({ label, v, warn }) {
  return (
    <div className={`chip ${warn ? "warn" : ""}`}>
      <span className="chip-l">{label}</span>
      <span className="chip-v">{v}</span>
    </div>
  );
}

function Btn({ children, onClick, active }) {
  return (
    <button className={`hud-btn ${active ? "active" : ""}`} onClick={onClick}>
      {children}
    </button>
  );
}
