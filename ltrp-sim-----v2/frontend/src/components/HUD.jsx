import React from "react";
import { api } from "../ws.js";

export default function HUD({ stats, connected, onParams, signalCover, onToggleSignal, energyShow, onToggleEnergy }) {
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
        <Chip label="信号覆盖洞穴" v={`${((stats.signal_cover ?? 0) * 100).toFixed(0)}%`} />
        <Chip label="已交付" v={stats.delivered} />
        {stats.retries > 0 && <Chip label="误码重传" v={stats.retries} warn={stats.damaged_drops > 0} />}
        {stats.heat && <Chip label="温度浪潮" v={`${stats.avg_temp}°C`} warn />}
        {stats.sleeping > 0 && <Chip label="轮值休眠中" v={stats.sleeping} />}
      </div>
      <div className="hud-right">
        <Btn active={signalCover} onClick={onToggleSignal}>⭕ 信号覆盖范围</Btn>
        <Btn active={energyShow} onClick={onToggleEnergy}>🔋 整体电量</Btn>
        <Btn onClick={onParams}>⚑ 协议参数</Btn>
        <Btn onClick={() => api("/action/disaster", { kind: "collapse" })}>🪨 塌方</Btn>
        <Btn onClick={() => api("/action/disaster", { kind: "heat" })}>🔥 温度浪潮</Btn>
        <Btn onClick={() => api("/action/disaster", { kind: "critical" })}>⚔ 摧毁一条喉道</Btn>
        <Btn active={stats.sleep_on}
             onClick={() => api("/action/sleep", { on: !stats.sleep_on })}>
          💤 休眠调度:{stats.sleep_on ? "开" : "关"}
        </Btn>
        <Btn onClick={() => api("/action/map", {})}>🎲 随机地图</Btn>
        <Btn onClick={() => api("/action/reset", {})}>↻ 重置</Btn>
        <div className="hud-time">
          <span className="ht-label">⏱ 时间缩放</span>
          <input type="range" min={1} max={1000} step={1}
            value={Math.round(stats.time_scale ?? 60)}
            onChange={(e) => api("/action/time_scale", { scale: +e.target.value })} />
          <span className="ht-val">{Math.round(stats.time_scale ?? 60)}x</span>
        </div>
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
