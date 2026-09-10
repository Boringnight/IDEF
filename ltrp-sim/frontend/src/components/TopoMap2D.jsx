// 2D 沙盘主组件:视口/缩放/平移/拾取/拖巨石 的 React 胶水层。
// 具体绘制拆到 ../canvas/{pipe,links,nodes}.js,本文件只负责状态、事件与渲染循环调度。
import React, { useRef, useEffect, useCallback, useState } from "react";
import { dist } from "../canvas/geometry.js";
import { buildPipePath, drawPipe, drawBoulders, drawDeployFog } from "../canvas/pipe.js";
import { drawStaticLinks, drawFlows, drawFerries, drawPowerLinks } from "../canvas/links.js";
import { drawSignalCover, drawEnergyRings, drawNodes, drawDragGhost } from "../canvas/nodes.js";
import NodeTooltip from "./NodeTooltip.jsx";

const TOOLTIP_W = 320;   // 悬停卡片宽度(用于边缘避让)
const TOOLTIP_H = 300;   // 悬停卡片高度(用于边缘避让)

export default function TopoMap2D({ world, snap, selected, onSelect, onMoveObstacle,
                                    signalCover, energyShow }) {
  const canvasRef = useRef(null);
  const wrapRef = useRef(null);
  const view = useRef({ scale: 0.4, ox: 0, oy: 0 });
  const mouse = useRef({ x: 0, y: 0, down: false, moved: false, mode: null,
                         dragIdx: -1, sx: 0, sy: 0 });
  const [hover, setHover] = useState(null);
  const rafRef = useRef(0);
  // 平滑插值:记录前后两帧快照,按时间插值节点位置(月球车等移动更流畅)
  const prevNodes = useRef({});
  const curNodes = useRef({});
  const snapT = useRef(0);

  const toWorld = useCallback((sx, sy) => {
    const v = view.current;
    return [(sx - v.ox) / v.scale, (sy - v.oy) / v.scale];
  }, []);

  const resize = useCallback(() => {
    const cv = canvasRef.current, wrap = wrapRef.current;
    if (!cv || !wrap) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = wrap.clientWidth * dpr;
    cv.height = wrap.clientHeight * dpr;
    cv.style.width = `${wrap.clientWidth}px`;
    cv.style.height = `${wrap.clientHeight}px`;
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }, []);

  // 初始视口:适配整条熔岩管
  useEffect(() => {
    if (!world || !canvasRef.current) return;
    resize();
    const cv = canvasRef.current;
    const w = cv.clientWidth, h = cv.clientHeight;
    const s = Math.min(w / (world.W * 1.02), h / (world.H * 1.25));
    view.current.scale = s;
    view.current.ox = (w - world.W * s) / 2;
    view.current.oy = (h - world.H * s) / 2;
  }, [world]); // eslint-disable-line

  // 每当收到新快照,记录上一帧节点位置用于插值(平滑)
  useEffect(() => {
    prevNodes.current = curNodes.current;
    const m = {};
    if (snap) snap.nodes.forEach((n) => { m[n.id] = n; });
    curNodes.current = m;
    snapT.current = performance.now();
  }, [snap]);

  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    return () => ro.disconnect();
  }, [resize]);

  // ---------------- render loop ----------------
  useEffect(() => {
    const draw = () => {
      rafRef.current = requestAnimationFrame(draw);
      const cv = canvasRef.current;
      if (!cv || !world || !snap) return;
      const ctx = cv.getContext("2d");
      const W = cv.clientWidth, H = cv.clientHeight;
      const v = view.current;
      const now = performance.now();
      // 视口保护:若视图变换损坏,重置为适配整图,避免滚轮后白屏/黑屏
      if (!Number.isFinite(v.scale) || v.scale <= 0 ||
          !Number.isFinite(v.ox) || !Number.isFinite(v.oy)) {
        const fit = Math.min(W / (world.W * 1.02), H / (world.H * 1.25));
        if (fit > 0 && Number.isFinite(fit)) {
          v.scale = fit; v.ox = (W - world.W * fit) / 2; v.oy = (H - world.H * fit) / 2;
        }
      }
      // 快照插值:按时间在前后两帧节点位置之间平滑过渡
      const alpha = Math.min(1, Math.max(0, (now - snapT.current) / 300));
      const cMap = curNodes.current, pMap = prevNodes.current;
      const posOf = (id) => {
        const c = cMap[id], p = pMap[id];
        if (!c) return null;
        if (!p) return [c.x, c.y];
        return [p.x + (c.x - p.x) * alpha, p.y + (c.y - p.y) * alpha];
      };

      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = "#0A0F1A";
      ctx.fillRect(0, 0, W, H);

      const pipePath = buildPipePath(world.samples, v);
      drawPipe(ctx, world, v, pipePath);
      if (signalCover) drawSignalCover(ctx, snap, v, posOf);
      drawPowerLinks(ctx, snap, v, posOf, now);
      if (energyShow) drawEnergyRings(ctx, snap, v, posOf);
      drawStaticLinks(ctx, snap, v, posOf);
      drawFlows(ctx, snap, v, posOf, now);
      drawFerries(ctx, snap, v, posOf, now);
      drawBoulders(ctx, snap.boulders || world.boulders, v, mouse.current.dragIdx);
      drawNodes(ctx, snap, v, posOf, now, selected, hover, snap.deploy?.done);
      drawDeployFog(ctx, snap, v, pipePath, W, H, world.samples);
      drawDragGhost(ctx, v, snap, mouse.current, toWorld);
    };
    rafRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(rafRef.current);
  }, [world, snap, selected, hover, toWorld, signalCover, energyShow]);

  // ---------------- interactions ----------------
  const hitNode = useCallback((wx, wy) => {
    if (!snap) return null;
    let best = null, bd = 16 / view.current.scale;
    snap.nodes.forEach((n) => {
      const d = dist(wx, wy, n.x, n.y);
      if (d < bd) { bd = d; best = n; }
    });
    return best;
  }, [snap]);

  const hitBoulder = useCallback((wx, wy) => {
    const bs = snap ? snap.boulders : (world ? world.boulders : []);
    for (let i = bs.length - 1; i >= 0; i--) {
      if (dist(wx, wy, bs[i].x, bs[i].y) < bs[i].r + 8) return i;
    }
    return -1;
  }, [snap, world]);

  const onWheel = useCallback((e) => {
    e.preventDefault();
    const cv = canvasRef.current; if (!cv) return;
    const rect = cv.getBoundingClientRect();
    const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
    const v = view.current;
    const wx = (cx - v.ox) / v.scale, wy = (cy - v.oy) / v.scale;
    const k = e.deltaY < 0 ? 1.1 : 0.9;
    const ns = Math.min(4, Math.max(0.06, v.scale * k));
    if (!Number.isFinite(ns) || !Number.isFinite(wx) || !Number.isFinite(wy)) return;
    v.ox = cx - wx * ns;
    v.oy = cy - wy * ns;
    v.scale = ns;
  }, []);

  // React 合成 MouseEvent 不暴露 offsetX/offsetY(恒为 undefined),会导致坐标算出 NaN、命中永远落空。
  // 统一改用 clientX/clientY 减去 canvas 边界矩形,得到 canvas 本地坐标(与 onWheel 一致)。
  const local = useCallback((e) => {
    let ox = e.clientX, oy = e.clientY;
    const t = e.currentTarget;
    if (t && t.getBoundingClientRect) {
      const r = t.getBoundingClientRect();
      ox -= r.left; oy -= r.top;
    }
    return [ox, oy];
  }, []);

  const onDown = useCallback((e) => {
    const m = mouse.current;
    const [ox, oy] = local(e);
    m.down = true; m.moved = false;
    m.sx = ox; m.sy = oy;
    const [wx, wy] = toWorld(ox, oy);
    const bi = hitBoulder(wx, wy);
    if (bi >= 0) { m.mode = "boulder"; m.dragIdx = bi; }
    else m.mode = "pan";
  }, [toWorld, hitBoulder, local]);

  const onMove = useCallback((e) => {
    const m = mouse.current;
    const [ox, oy] = local(e);
    m.x = ox; m.y = oy;
    const [wx, wy] = toWorld(ox, oy);
    if (m.down) {
      if (Math.abs(ox - m.sx) + Math.abs(oy - m.sy) > 4) m.moved = true;
      if (m.mode === "pan") {
        const v = view.current;
        v.ox += e.movementX; v.oy += e.movementY;
      }
      return;
    }
    const n = hitNode(wx, wy);
    setHover(n ? n.id : null);
  }, [toWorld, hitNode, local]);

  const onUp = useCallback((e) => {
    const m = mouse.current;
    const [ox, oy] = local(e);
    const [wx, wy] = toWorld(ox, oy);
    if (m.down && m.mode === "boulder" && m.moved) {
      onMoveObstacle(m.dragIdx, Math.round(wx), Math.round(wy));
    } else if (!m.moved) {
      const n = hitNode(wx, wy);
      onSelect(n ? n.id : null);
    }
    m.down = false; m.mode = null; m.dragIdx = -1;
  }, [toWorld, hitNode, onSelect, onMoveObstacle, local]);

  const hoverNode = hover && snap ? snap.nodes.find((n) => n.id === hover) : null;
  // 邻居富信息:把 nbrs[[id, snr, ber]] 与 snap.nodes 交叉引用,取出 角色/电量/状态
  const hoverNbrs = (() => {
    if (!hoverNode || !snap) return [];
    const byId = {};
    snap.nodes.forEach((n) => { byId[n.id] = n; });
    return (hoverNode.nbrs || []).map(([id, snr, ber]) => {
      const n = byId[id];
      return { ...n, snr: snr ?? 0, ber: ber ?? null };
    });
  })();
  // 工具提示几何:靠近右/下边缘时向内收缩,避免溢出画布
  const ttLeft = Math.min(mouse.current.x + 16, (wrapRef.current?.clientWidth || 800) - TOOLTIP_W - 8);
  const ttTop = Math.min(mouse.current.y + 14, (wrapRef.current?.clientHeight || 600) - TOOLTIP_H - 8);

  return (
    <div className="topo-wrap" ref={wrapRef}>
      <canvas
        ref={canvasRef}
        onWheel={onWheel}
        onMouseDown={onDown}
        onMouseMove={onMove}
        onMouseUp={onUp}
        onMouseLeave={() => { setHover(null); mouse.current.down = false; }}
      />
      {hoverNode && (
        <NodeTooltip node={hoverNode} nbrs={hoverNbrs} left={ttLeft} top={ttTop} />
      )}
    </div>
  );
}
