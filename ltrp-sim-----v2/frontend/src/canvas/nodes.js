// 节点层绘制:信号覆盖圈 / 电量环 / 节点本体(按角色) / 徽标与标签。
// 全部为无状态纯绘制;posOf(id) 提供含帧间插值的坐标,由 TopoMap2D 注入。
import { STATE_COLOR } from "./geometry.js";

// 信号覆盖范围:每个存活节点画一个通信半径圆(底层)
export function drawSignalCover(ctx, snap, view, posOf) {
  const rr = (snap.stats?.range ?? 260) * view.scale;
  ctx.save();
  ctx.fillStyle = "rgba(27,122,209,0.07)";
  ctx.strokeStyle = "rgba(27,122,209,0.38)";
  ctx.lineWidth = 1;
  for (const n of snap.nodes) {
    if (n.pending || !n.alive || n.role === "rover") continue;
    const pp = posOf(n.id) || [n.x, n.y];
    ctx.beginPath();
    ctx.arc(pp[0] * view.scale + view.ox, pp[1] * view.scale + view.oy, rr, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
  const spct = Math.round((snap.stats?.signal_cover ?? 0) * 100);
  ctx.save();
  ctx.fillStyle = "rgba(28,78,138,0.95)";
  ctx.font = "11px Consolas, monospace";
  ctx.textAlign = "left";
  ctx.fillText(`⭕ 信号覆盖洞穴 ${spct}%（每个节点通信半径 = ${snap.stats?.range ?? 260}m）`, 12, 20);
  ctx.restore();
}

// 整体电量显示:每个节点按 soc 画色环 + 百分比
export function drawEnergyRings(ctx, snap, view, posOf) {
  const s0 = Math.max(0.85, view.scale * 2.2);
  ctx.save();
  ctx.font = `${Math.max(7, 8 * s0)}px Consolas, monospace`;
  ctx.textAlign = "center";
  for (const n of snap.nodes) {
    if (n.pending || !n.alive || n.role === "rover") continue;
    const pp = posOf(n.id) || [n.x, n.y];
    const cx = pp[0] * view.scale + view.ox, cy = pp[1] * view.scale + view.oy;
    const soc = n.soc ?? 0;
    ctx.strokeStyle = `hsl(${(soc * 1.2).toFixed(0)},68%,42%)`;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(cx, cy, (s0 + 4) * 1.5, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * soc / 100);
    ctx.stroke();
    ctx.fillStyle = "rgba(30,66,108,0.95)";
    ctx.fillText(Math.round(soc), cx, cy + 3 * s0);
  }
  ctx.restore();
}

// 单个节点本体(菱形/圆点/车辆等)与状态标记
function drawOne(ctx, n, x, y, s, t, selected, hover, showLabel) {
  if (!n.alive) {
    ctx.strokeStyle = "#8A94A6";
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(x - 5, y - 5); ctx.lineTo(x + 5, y + 5);
    ctx.moveTo(x + 5, y - 5); ctx.lineTo(x - 5, y + 5);
    ctx.stroke();
    return;
  }
  const col = STATE_COLOR[n.state] || "#35E0C8";
  ctx.globalAlpha = n.sleeping ? 0.35 : 1;
  const breath = n.state === "NORMAL" && !n.sleeping
    ? 0.5 + 0.5 * Math.sin(t * 2 + n.x) : 1;

  if (n.crit) {                       // 关键割点:红色呼吸环
    ctx.strokeStyle = `rgba(255,80,80,${0.35 + 0.3 * Math.sin(t * 3)})`;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.arc(x, y, 10 * s + 2, 0, Math.PI * 2);
    ctx.stroke();
  }
  if (n.sos) {                        // 自愈移动中:SOS 环 + 文字
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
    ctx.fillStyle = "rgba(11,114,196,0.16)";
    ctx.fill();
    ctx.strokeStyle = col;
    ctx.lineWidth = 2;
    ctx.shadowColor = "rgba(11,114,196,0.8)";
    ctx.shadowBlur = 12;
    ctx.stroke();
    ctx.shadowBlur = 0;
    ctx.restore();
  } else if (n.role === "spike") {
    ctx.fillStyle = n.border ? "#4C93D2" : col;
    ctx.shadowColor = col;
    ctx.shadowBlur = n.sleeping ? 0 : 6 * breath;
    ctx.beginPath();
    ctx.arc(x, y, (n.border ? 6 : 4.6) * s, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;
    if (n.border) {                   // 喉道边界道钉:虚线环
      ctx.strokeStyle = "rgba(27,122,209,0.55)";
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.arc(x, y, 9 * s, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  } else if (n.role === "probe") {    // 面包屑撒布载体:菱形 + 扫描环 + 载货数
    ctx.strokeStyle = "rgba(27,122,209,0.5)";
    ctx.lineWidth = 1.1;
    ctx.beginPath();
    ctx.arc(x, y, 9 * s, 0, Math.PI * 2);
    ctx.stroke();
    const gl = (performance.now() / 1000 * 1.3 + n.x * 0.01) % 1;
    ctx.strokeStyle = `rgba(21,116,201,${(0.9 * (1 - gl)).toFixed(3)})`;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 6 + gl * 12, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = col;
    ctx.shadowColor = col;
    ctx.shadowBlur = 10 * breath;
    ctx.beginPath();
    ctx.moveTo(x, y - 8 * s); ctx.lineTo(x + 7 * s, y);
    ctx.lineTo(x, y + 8 * s); ctx.lineTo(x - 7 * s, y);
    ctx.closePath();
    ctx.fill();
    ctx.shadowBlur = 0;
    if (n.stock > 0) {
      ctx.fillStyle = "rgba(181,122,0,0.95)";
      ctx.font = "bold 9px Consolas, monospace";
      ctx.textAlign = "left";
      ctx.fillText(`⬒${n.stock}`, x + 8 * s, y - 8 * s);
    }
  } else if (n.role === "rover") {
    ctx.fillStyle = "#F2B93B";
    ctx.strokeStyle = "#D99A12";
    ctx.lineWidth = 1.5;
    const rw = 13 * s, rh = 8 * s;
    ctx.beginPath();
    ctx.roundRect(x - rw / 2, y - rh / 2, rw, rh, 3);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#3A4656";
    ctx.beginPath();
    ctx.arc(x + rw / 2 - 3 * s, y, 1.6 * s, 0, Math.PI * 2);
    ctx.fill();
  }
  if (n.bundles > 0) {                // 束队列徽标
    ctx.fillStyle = n.role === "rover" ? "#C9860D" : "#D96F0E";
    ctx.font = "bold 10px 'Consolas', monospace";
    ctx.textAlign = "left";
    ctx.fillText(`⬒${n.bundles}`, x + 8 * s, y - 8 * s);
  }
  if (n.sleeping) {
    ctx.fillStyle = "rgba(74,99,132,0.85)";
    ctx.font = "9px 'Microsoft YaHei'";
    ctx.textAlign = "center";
    ctx.fillText("zZ", x + 9 * s, y + 10 * s);
  }
  ctx.globalAlpha = 1;
  if (showLabel) {                    // 标签(缩得太小时省略)
    ctx.fillStyle = selected === n.id ? "rgba(18,44,78,0.98)" : "rgba(40,66,100,0.7)";
    ctx.font = "9px 'Consolas', monospace";
    ctx.textAlign = "center";
    ctx.fillText(n.id, x, y + 16 * s + 6);
  }
  if (selected === n.id) {
    ctx.strokeStyle = "rgba(20,60,110,0.85)";
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.arc(x, y, 13 * s + 3, 0, Math.PI * 2);
    ctx.stroke();
  }
  if (hover === n.id) {
    ctx.strokeStyle = "rgba(11,114,196,0.65)";
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(x, y, 11 * s + 2, 0, Math.PI * 2);
    ctx.stroke();
  }
}

// 全部节点:未撒布的不画;部署完成前不画月球车
export function drawNodes(ctx, snap, view, posOf, now, selected, hover, deployDone) {
  const t = now / 1000;
  const showLabel = view.scale > 0.28;
  snap.nodes.forEach((n) => {
    if (n.pending) return;
    if (n.role === "rover" && !deployDone) return;
    const pp = posOf(n.id) || [n.x, n.y];
    const x = pp[0] * view.scale + view.ox, y = pp[1] * view.scale + view.oy;
    const s = Math.max(0.85, view.scale * 2.2);
    drawOne(ctx, n, x, y, s, t, selected, hover, showLabel);
  });
}

// 拖拽中的巨石幽灵圆
export function drawDragGhost(ctx, view, snap, mouse, toWorld) {
  if (mouse.dragIdx < 0) return;
  const [wx, wy] = toWorld(mouse.x, mouse.y);
  ctx.strokeStyle = "rgba(11,114,196,0.55)";
  ctx.setLineDash([5, 5]);
  ctx.beginPath();
  ctx.arc(wx * view.scale + view.ox, wy * view.scale + view.oy,
    (snap.boulders[mouse.dragIdx]?.r || 40) * view.scale, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([]);
}
