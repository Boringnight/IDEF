import React, { useRef, useEffect, useCallback, useState } from "react";

function chamberName(i) {
  // 0->A, 1->B, ..., 25->Z, 26->AA, ...(支持可变腔室数)
  let n = i, s = "";
  for (;;) {
    s = String.fromCharCode(65 + (n % 26)) + s;
    n = Math.floor(n / 26) - 1;
    if (n < 0) break;
  }
  return s;
}
const STATE_COLOR = {
  NORMAL: "#35E0C8",
  PROTECTED: "#FFC400",
  DYING: "#FF5252",
  DEAD: "#5A6270",
};
const ROLE_ZH = {
  base: "主基站", spike: "道钉", probe: "探测器", rover: "月球车",
};

function dist(ax, ay, bx, by) {
  return Math.hypot(ax - bx, ay - by);
}

export default function TopoMap2D({ world, snap, selected, onSelect, onMoveObstacle }) {
  const canvasRef = useRef(null);
  const wrapRef = useRef(null);
  const view = useRef({ scale: 0.4, ox: 0, oy: 0 });
  const mouse = useRef({ x: 0, y: 0, down: false, moved: false, mode: null, dragIdx: -1, sx: 0, sy: 0 });
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
      const t = performance.now() / 1000;
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

      // ---- 熔岩管轮廓 ----
      const S = world.samples;
      const wall = (side) => {
        ctx.beginPath();
        S.forEach(([x, y, r], i) => {
          const sx = x * v.scale + v.ox, sy = (y + side * r) * v.scale + v.oy;
          if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
        });
      };
      ctx.beginPath();
      S.forEach(([x, y, r], i) => {
        const sx = x * v.scale + v.ox, sy = (y - r) * v.scale + v.oy;
        if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
      });
      for (let i = S.length - 1; i >= 0; i--) {
        const [x, y, r] = S[i];
        ctx.lineTo(x * v.scale + v.ox, (y + r) * v.scale + v.oy);
      }
      ctx.closePath();
      ctx.fillStyle = "rgba(28,40,64,0.42)";
      ctx.fill();
      wall(1); wall(-1);
      ctx.strokeStyle = "rgba(110,160,220,0.22)";
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // ---- 腔室域着色 + 名称 ----
      world.chambers.forEach((c, i) => {
        const cx = c.cx * v.scale + v.ox, cy = 350 * v.scale + v.oy;
        const rx = c.hl * v.scale, ry = (c.r + 10) * v.scale;
        const grd = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(rx, ry));
        grd.addColorStop(0, `hsla(${190 + i * 18}, 70%, 55%, 0.05)`);
        grd.addColorStop(1, "transparent");
        ctx.fillStyle = grd;
        ctx.fillRect(cx - rx - 20, cy - ry - 20, rx * 2 + 40, ry * 2 + 40);
        ctx.fillStyle = `hsla(${190 + i * 18}, 80%, 70%, 0.5)`;
        ctx.font = "11px 'Microsoft YaHei', sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(`腔室 ${chamberName(i)} · 域${i}`, cx, cy - ry - 8);
      });
      world.throats.forEach(([a, b], i) => {
        const mx = ((a + b) / 2) * v.scale + v.ox;
        const my = 350 * v.scale + v.oy - 46 * v.scale;
        ctx.strokeStyle = "rgba(120,140,180,0.18)";
        ctx.setLineDash([4, 4]);
        [a, b].forEach((x) => {
          const sx = x * v.scale + v.ox;
          ctx.beginPath();
          ctx.moveTo(sx, (350 - 130) * v.scale + v.oy);
          ctx.lineTo(sx, (350 + 130) * v.scale + v.oy);
          ctx.stroke();
        });
        ctx.setLineDash([]);
        ctx.fillStyle = "rgba(150,170,210,0.5)";
        ctx.font = "10px 'Microsoft YaHei', sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(`喉道 ${i + 1}`, mx, my);
      });

      const nodeMap = {};
      snap.nodes.forEach((n) => { nodeMap[n.id] = n; });

      // ---- 静息链路(微亮的连接线,稳定标示节点间相连) ----
      ctx.lineWidth = 1.4;
      snap.links.forEach(([a, b]) => {
        const na = nodeMap[a], nb = nodeMap[b];
        if (!na || !nb) return;
        const pa = posOf(a), pb = posOf(b);
        if (!pa || !pb) return;
        ctx.strokeStyle = "rgba(110,200,200,0.24)";
        ctx.beginPath();
        ctx.moveTo(pa[0] * v.scale + v.ox, pa[1] * v.scale + v.oy);
        ctx.lineTo(pb[0] * v.scale + v.ox, pb[1] * v.scale + v.oy);
        ctx.stroke();
      });

      // ---- 数据流:稳定微亮的活动链路 + 沿线前行的数据包(不闪烁) ----
      snap.flows.forEach(([a, b, age]) => {
        const na = nodeMap[a], nb = nodeMap[b];
        if (!na || !nb) return;
        const k = 1 - age / 1.2;
        if (k <= 0) return;
        const pa = posOf(a), pb = posOf(b);
        if (!pa || !pb) return;
        const x1 = pa[0] * v.scale + v.ox, y1 = pa[1] * v.scale + v.oy;
        const x2 = pb[0] * v.scale + v.ox, y2 = pb[1] * v.scale + v.oy;
        // 微亮的稳定活动链路(常亮,轻微呼吸,避免明灭闪烁)
        ctx.strokeStyle = `rgba(0,255,255,${0.20 + 0.10 * k})`;
        ctx.lineWidth = 1.8;
        ctx.shadowColor = "#00FFFF";
        ctx.shadowBlur = 6;
        ctx.beginPath();
        ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
        ctx.stroke();
        ctx.shadowBlur = 0;
        // 沿 a→b 缓缓前行的白色数据包(连续循环,表现连接与方向)
        const ph = (now / 1000 * 1.1 + (a.length * 0.13 + b.length * 0.07)) % 1;
        const px = x1 + (x2 - x1) * ph, py = y1 + (y2 - y1) * ph;
        ctx.fillStyle = "rgba(225,255,255,0.95)";
        ctx.shadowColor = "#00FFFF";
        ctx.shadowBlur = 8;
        ctx.beginPath();
        ctx.arc(px, py, 2.3, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
      });

      // ---- 摆渡链路(金色虚线,稳定微亮 + 前行金色数据包) ----
      snap.ferries.forEach(([a, b, age]) => {
        const na = nodeMap[a], nb = nodeMap[b];
        if (!na || !nb) return;
        const k = 1 - age / 3;
        if (k <= 0) return;
        const pa = posOf(a), pb = posOf(b);
        if (!pa || !pb) return;
        const x1 = pa[0] * v.scale + v.ox, y1 = pa[1] * v.scale + v.oy;
        const x2 = pb[0] * v.scale + v.ox, y2 = pb[1] * v.scale + v.oy;
        ctx.strokeStyle = `rgba(255,200,87,${0.30 + 0.25 * k})`;
        ctx.lineWidth = 2.0;
        ctx.setLineDash([6, 5]);
        ctx.shadowColor = "#FFC857";
        ctx.shadowBlur = 8;
        ctx.beginPath();
        ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.shadowBlur = 0;
        const ph = (now / 1000 * 0.7 + (a.length + b.length) * 0.11) % 1;
        const px = x1 + (x2 - x1) * ph, py = y1 + (y2 - y1) * ph;
        ctx.fillStyle = "rgba(255,224,150,0.95)";
        ctx.shadowColor = "#FFC857";
        ctx.shadowBlur = 8;
        ctx.beginPath();
        ctx.arc(px, py, 2.6, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
      });

      // ---- 巨石 ----
      (snap.boulders || world.boulders).forEach((b, i) => {
        const bx = b.x * v.scale + v.ox, by = b.y * v.scale + v.oy;
        const br = b.r * v.scale;
        ctx.fillStyle = "#1B2438";
        ctx.strokeStyle = "#3A4E75";
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        for (let k = 0; k < 9; k++) {
          const ang = (k / 9) * Math.PI * 2 + 0.35 * Math.sin(k * 2.1);
          const rr = br * (0.82 + 0.18 * Math.sin(k * 3.7 + 1));
          const px = bx + rr * Math.cos(ang), py = by + rr * Math.sin(ang);
          if (k === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        ctx.strokeStyle = "rgba(90,110,150,0.4)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(bx - br * 0.4, by - br * 0.3);
        ctx.lineTo(bx + br * 0.1, by + br * 0.1);
        ctx.lineTo(bx + br * 0.45, by - br * 0.2);
        ctx.stroke();
        if (mouse.current.dragIdx === i) {
          ctx.strokeStyle = "rgba(0,255,255,0.8)";
          ctx.setLineDash([4, 4]);
          ctx.beginPath();
          ctx.arc(bx, by, br + 5, 0, Math.PI * 2);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      });

      // ---- 节点 ----
      snap.nodes.forEach((n) => {
        const pp = posOf(n.id) || [n.x, n.y];
        const x = pp[0] * v.scale + v.ox, y = pp[1] * v.scale + v.oy;
        const s = Math.max(0.85, v.scale * 2.2);
        if (!n.alive) {
          ctx.strokeStyle = "#4A5262";
          ctx.lineWidth = 1.6;
          ctx.beginPath();
          ctx.moveTo(x - 5, y - 5); ctx.lineTo(x + 5, y + 5);
          ctx.moveTo(x + 5, y - 5); ctx.lineTo(x - 5, y + 5);
          ctx.stroke();
          return;
        }
        const col = STATE_COLOR[n.state] || "#35E0C8";
        const alpha = n.sleeping ? 0.35 : 1;
        ctx.globalAlpha = alpha;
        const breath = n.state === "NORMAL" && !n.sleeping
          ? 0.5 + 0.5 * Math.sin(t * 2 + n.x) : 1;

        if (n.crit) {
          ctx.strokeStyle = `rgba(255,80,80,${0.35 + 0.3 * Math.sin(t * 3)})`;
          ctx.lineWidth = 1.6;
          ctx.beginPath();
          ctx.arc(x, y, 10 * s + 2, 0, Math.PI * 2);
          ctx.stroke();
        }
        // SOS 孤立求救(节点自愈移动中)
        if (n.sos) {
          ctx.strokeStyle = `rgba(255,120,40,${0.45 + 0.35 * Math.sin(t * 4)})`;
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.arc(x, y, 14 * s + 3, 0, Math.PI * 2);
          ctx.stroke();
          ctx.fillStyle = "rgba(255,150,60,0.9)";
          ctx.font = "bold 10px 'Microsoft YaHei', sans-serif";
          ctx.textAlign = "center";
          ctx.fillText("SOS", x, y - 18 * s - 4);
        }
        if (n.role === "base") {
          ctx.save();
          ctx.translate(x, y);
          ctx.beginPath();
          for (let k = 0; k < 6; k++) {
            const a = (k / 6) * Math.PI * 2 - Math.PI / 2;
            const px = 12 * s * Math.cos(a), py = 12 * s * Math.sin(a);
            if (k === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
          }
          ctx.closePath();
          ctx.fillStyle = "rgba(0,229,255,0.18)";
          ctx.fill();
          ctx.strokeStyle = col;
          ctx.lineWidth = 2;
          ctx.shadowColor = "#00E5FF";
          ctx.shadowBlur = 12;
          ctx.stroke();
          ctx.shadowBlur = 0;
          ctx.restore();
        } else if (n.role === "spike") {
          ctx.fillStyle = n.border ? "rgba(120,220,255,0.9)" : col;
          ctx.shadowColor = col;
          ctx.shadowBlur = n.sleeping ? 0 : 6 * breath;
          ctx.beginPath();
          ctx.arc(x, y, (n.border ? 6 : 4.6) * s, 0, Math.PI * 2);
          ctx.fill();
          ctx.shadowBlur = 0;
          if (n.border) {
            ctx.strokeStyle = "rgba(120,220,255,0.5)";
            ctx.lineWidth = 1;
            ctx.setLineDash([3, 3]);
            ctx.beginPath();
            ctx.arc(x, y, 9 * s, 0, Math.PI * 2);
            ctx.stroke();
            ctx.setLineDash([]);
          }
        } else if (n.role === "probe") {
          ctx.fillStyle = col;
          ctx.shadowColor = col;
          ctx.shadowBlur = 8 * breath;
          ctx.beginPath();
          ctx.moveTo(x, y - 7 * s); ctx.lineTo(x + 6 * s, y);
          ctx.lineTo(x, y + 7 * s); ctx.lineTo(x - 6 * s, y);
          ctx.closePath();
          ctx.fill();
          ctx.shadowBlur = 0;
        } else if (n.role === "rover") {
          ctx.fillStyle = "#FFD166";
          ctx.strokeStyle = "#FFE29A";
          ctx.lineWidth = 1.5;
          const rw = 13 * s, rh = 8 * s;
          ctx.beginPath();
          ctx.roundRect(x - rw / 2, y - rh / 2, rw, rh, 3);
          ctx.fill();
          ctx.stroke();
          ctx.fillStyle = "#0A0F1A";
          ctx.beginPath();
          ctx.arc(x + rw / 2 - 3 * s, y, 1.6 * s, 0, Math.PI * 2);
          ctx.fill();
        }
        // 束队列徽标
        if (n.bundles > 0) {
          ctx.fillStyle = n.role === "rover" ? "#FFC857" : "#FF9E42";
          ctx.font = "bold 10px 'Consolas', monospace";
          ctx.textAlign = "left";
          ctx.fillText(`⬒${n.bundles}`, x + 8 * s, y - 8 * s);
        }
        if (n.sleeping) {
          ctx.fillStyle = "rgba(180,200,255,0.7)";
          ctx.font = "9px 'Microsoft YaHei'";
          ctx.textAlign = "center";
          ctx.fillText("zZ", x + 9 * s, y + 10 * s);
        }
        ctx.globalAlpha = 1;
        // 标签
        if (v.scale > 0.28) {
          ctx.fillStyle = selected === n.id ? "rgba(255,255,255,0.95)" : "rgba(255,255,255,0.45)";
          ctx.font = "9px 'Consolas', monospace";
          ctx.textAlign = "center";
          ctx.fillText(n.id, x, y + 16 * s + 6);
        }
        if (selected === n.id) {
          ctx.strokeStyle = "rgba(255,255,255,0.9)";
          ctx.lineWidth = 1.6;
          ctx.beginPath();
          ctx.arc(x, y, 13 * s + 3, 0, Math.PI * 2);
          ctx.stroke();
        }
        if (hover === n.id) {
          ctx.strokeStyle = "rgba(0,255,255,0.6)";
          ctx.lineWidth = 1.2;
          ctx.beginPath();
          ctx.arc(x, y, 11 * s + 2, 0, Math.PI * 2);
          ctx.stroke();
        }
      });

      // 拖拽中的巨石幽灵
      if (mouse.current.dragIdx >= 0) {
        const [wx, wy] = toWorld(mouse.current.x, mouse.current.y);
        ctx.strokeStyle = "rgba(0,255,255,0.5)";
        ctx.setLineDash([5, 5]);
        ctx.beginPath();
        ctx.arc(wx * v.scale + v.ox, wy * v.scale + v.oy,
          (snap.boulders[mouse.current.dragIdx]?.r || 40) * v.scale, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    };
    rafRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(rafRef.current);
  }, [world, snap, selected, hover, toWorld]);

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
  // 统一改用 clientX/clientY 减去 canvas 边界矩形,得到 canvas 本地坐标(与 onWheel 一致,兼顾悬停/选中/拖拽)。
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
  // 邻居富信息:把 nbrs[[id, snr]] 与 snap.nodes 交叉引用,取出 角色/电量/状态/位置
  const hoverNbrs = (() => {
    if (!hoverNode || !snap) return [];
    const byId = {};
    snap.nodes.forEach((n) => { byId[n.id] = n; });
    return (hoverNode.nbrs || []).map(([id, snr]) => {
      const n = byId[id];
      return n ? { ...n, snr: snr ?? 0 } : { id, snr: snr ?? 0, role: "?", state: "UNKNOWN", sleeping: false, soc: null };
    });
  })();
  // 工具提示几何:靠近右/下边缘时向内收缩,避免溢出画布
  const ttW = 320, ttH = 300;
  const ttLeft = Math.min(mouse.current.x + 16, (wrapRef.current?.clientWidth || 800) - ttW - 8);
  const ttTop = Math.min(mouse.current.y + 14, (wrapRef.current?.clientHeight || 600) - ttH - 8);

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
      {hoverNode && (() => {
        const soc = Math.max(0, Math.min(100, hoverNode.soc ?? 0));
        const socColor = soc < 20 ? "#FF5252" : soc < 40 ? "#FF9E42" : "#2EFF9E";
        const stColor = STATE_COLOR[hoverNode.state] || "#9FD8FF";
        return (
          <div className="tooltip" style={{ left: ttLeft, top: ttTop }}>
            <div className="tt-title">{hoverNode.id} · {ROLE_ZH[hoverNode.role]}</div>

            <div className="tt-row">
              <span className="k">电量 (SoC)</span>
              <span className="v" style={{ color: socColor }}>{soc.toFixed(1)}%</span>
            </div>
            <div className="tt-soc"><i style={{ width: soc + "%", background: socColor }} /></div>

            <div className="tt-row">
              <span className="k">运行状态</span>
              <span className="v" style={{ color: stColor }}>
                {stateZh(hoverNode.state)}
                {hoverNode.sleeping ? " · 休眠" : ""}
                {hoverNode.moving ? " · 移动" : ""}
                {hoverNode.sos ? " · 求救" : ""}
              </span>
            </div>
            <div className="tt-row">
              <span className="k">位置信息</span>
              <span className="v tt-pos">({hoverNode.x} , {hoverNode.y})</span>
            </div>
            <div className="tt-row">
              <span className="k">所属腔室</span>
              <span className="v">腔室 {chamberName(hoverNode.domain)}{hoverNode.border ? " · 喉道边界" : ""}</span>
            </div>

            {hoverNode.role !== "base" && (
              hoverNode.nh
                ? <div className="tt-row">
                    <span className="k">→ 基站路由</span>
                    <span className="v">经 {hoverNode.nh} · 代价 {hoverNode.cost} · 最弱SoC {hoverNode.ms}%</span>
                  </div>
                : <div className="tt-row"><span className="k">→ 基站路由</span><span className="v tt-bad">✕ 无实时路由(束模式)</span></div>
            )}

            <div className="tt-row">
              <span className="k">载荷</span>
              <span className="v">{hoverNode.bundles > 0 ? `⬒ 束 ${hoverNode.bundles} · 待发 ${hoverNode.pkts}` : `待发包 ${hoverNode.pkts}`}</span>
            </div>
            {hoverNode.crit && <div className="tt-bad tt-note">⚠ 关键割点 · 2 跳视图自识别(锁定常开)</div>}
            {hoverNode.anchor && <div className="tt-info tt-note">◆ 域锚节点</div>}

            <div className="tt-sec">邻居节点 ({hoverNbrs.length})</div>
            {hoverNbrs.length === 0 && <div className="dim">无(等待信标)</div>}
            <div className="tt-nbr-grid">
              {hoverNbrs.map((nb) => {
                const cls = [];
                if (nb.state === "DEAD") cls.push("dead");
                if (nb.sleeping) cls.push("sleeping");
                if (nb.soc != null && nb.soc < 25) cls.push("lowbat");
                const lowSoc = nb.soc != null && nb.soc < 25;
                return (
                  <span key={nb.id} className={`tt-nbr ${cls.join(" ")}`}>
                    <span className="nid">{nb.id}</span>
                    <span className="nrole">{ROLE_ZH[nb.role] || nb.role}</span>
                    <span className="nsnr">{nb.snr.toFixed(1)}dB</span>
                    {nb.soc != null && <span className={`nsoc ${lowSoc ? "low" : ""}`}>{nb.soc.toFixed(0)}%</span>}
                  </span>
                );
              })}
            </div>
          </div>
        );
      })()}
    </div>
  );
}

function stateZh(s) {
  return { NORMAL: "正常", PROTECTED: "保护态(退出中继)", DYING: "垂危", DEAD: "宕机" }[s] || s;
}
