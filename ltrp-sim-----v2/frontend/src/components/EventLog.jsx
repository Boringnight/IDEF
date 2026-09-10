import React, { useEffect, useRef } from "react";

const SEV_CLS = { good: "good", warn: "warn", bad: "bad", info: "info" };

export default function EventLog({ events }) {
  const listRef = useRef(null);
  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = 0;
  }, [events]);
  const shown = events.slice(-120).reverse();
  return (
    <div className="eventlog">
      <div className="el-title">⟡ 协议过程时间线(去中心化,逐节点本地决策)</div>
      <div className="el-list" ref={listRef}>
        {shown.map((ev) => (
          <div key={ev.i} className={`el-item ${SEV_CLS[ev.sev] || "info"}`}>
            <span className="el-t">t{ev.t}</span>
            <span className="el-tag">{tagZh(ev.type)}</span>
            <span>{ev.msg}</span>
          </div>
        ))}
        {shown.length === 0 && <div className="el-item info">等待协议事件…</div>}
      </div>
    </div>
  );
}

function tagZh(t) {
  return {
    boot: "[启动]", crit: "[割点]", ferry: "[摆渡]", earth: "[地月]",
    disaster: "[灾害]", heal: "[自愈]", dead: "[宕机]", sleep: "[休眠]", geo: "[几何]",
    seu: "[翻转]", msg: "[误码]", override: "[调参]",
  }[t] || "[事件]";
}
