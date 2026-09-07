import React, { useEffect, useState, useCallback } from "react";
import { connectWS, api } from "./ws.js";
import TopoMap2D from "./components/TopoMap2D.jsx";
import HUD from "./components/HUD.jsx";
import EventLog from "./components/EventLog.jsx";
import Inspector from "./components/Inspector.jsx";

export default function App() {
  const [world, setWorld] = useState(null);
  const [snap, setSnap] = useState(null);
  const [events, setEvents] = useState([]);
  const [connected, setConnected] = useState(false);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    const handle = connectWS({
      onInit: (m) => {
        setWorld(m.world);
        setSnap(m.snapshot);
        setEvents(m.snapshot.events || []);
        setConnected(true);
      },
      onSnap: (m) => {
        setSnap(m);
        setConnected(true);
        if (m.events && m.events.length) {
          setEvents((old) => [...old, ...m.events].slice(-400));
        }
      },
    });
    return () => handle.close();
  }, []);

  const onMoveObstacle = useCallback((idx, x, y) => {
    api("/action/obstacle", { idx, x, y });
  }, []);

  const narration = [...events].reverse().find((e) => e.narr);

  const selNode = snap && selected
    ? snap.nodes.find((n) => n.id === selected) : null;

  if (!snap) {
    return (
      <div className="boot-screen">
        <div className="boot-title">LTRP · 月球熔岩管去中心化路由保持协议</div>
        <div className="boot-sub">正在连接仿真引擎(127.0.0.1:5000)…</div>
        <div className="boot-sub dim">请先启动后端:cd backend && python main.py</div>
      </div>
    );
  }

  return (
    <div className="app">
      <HUD stats={snap.stats} connected={connected} />
      <div className="main">
        <div className="canvas-col">
          <TopoMap2D
            world={world}
            snap={snap}
            selected={selected}
            onSelect={setSelected}
            onMoveObstacle={onMoveObstacle}
          />
          <div className="legend">
            <span>⬡ 基站</span><span>● 道钉</span><span>◇ 探测器</span>
            <span>▮ 月球车</span><span className="lg-cyan">━ 数据流</span>
            <span className="lg-gold">╌ 摆渡(存储-携带-转发)</span>
            <span className="lg-red">◯ 关键割点</span><span className="lg-orange">⬒ 滞留束</span>
            <span className="dim">拖巨石可改变视距(压到节点会使其直接宕机) · 滚轮缩放 · 拖拽平移</span>
          </div>
          {narration && (
            <div className="narration">
              <span className="narr-t">t{narration.t}</span>
              {narration.msg}
            </div>
          )}
        </div>
        <div className="side-col">
          <Inspector node={selNode} onClose={() => setSelected(null)} />
          <EventLog events={events} />
        </div>
      </div>
    </div>
  );
}
