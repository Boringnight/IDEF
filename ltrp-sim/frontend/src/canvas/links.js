// 链路层绘制:静息链路 / 数据流 / 摆渡链路 / 激光充能链路。
// 全部为无状态纯绘制;posOf(id) 提供节点坐标(含帧间插值),由 TopoMap2D 注入。

// 静息链路:节点间相连的微亮线
export function drawStaticLinks(ctx, snap, view, posOf) {
  ctx.lineWidth = 1.4;
  snap.links.forEach(([a, b]) => {
    const pa = posOf(a), pb = posOf(b);
    if (!pa || !pb) return;
    ctx.strokeStyle = "rgba(110,200,200,0.24)";
    ctx.beginPath();
    ctx.moveTo(pa[0] * view.scale + view.ox, pa[1] * view.scale + view.oy);
    ctx.lineTo(pb[0] * view.scale + view.ox, pb[1] * view.scale + view.oy);
    ctx.stroke();
  });
}

// 数据流:稳定微亮的活动链路 + 沿线前行的数据包(不闪烁)
export function drawFlows(ctx, snap, view, posOf, now) {
  snap.flows.forEach(([a, b, age]) => {
    const k = 1 - age / 1.2;
    if (k <= 0) return;
    const pa = posOf(a), pb = posOf(b);
    if (!pa || !pb) return;
    const x1 = pa[0] * view.scale + view.ox, y1 = pa[1] * view.scale + view.oy;
    const x2 = pb[0] * view.scale + view.ox, y2 = pb[1] * view.scale + view.oy;
    ctx.strokeStyle = `rgba(0,255,255,${0.20 + 0.10 * k})`;
    ctx.lineWidth = 1.8;
    ctx.shadowColor = "#00FFFF";
    ctx.shadowBlur = 6;
    ctx.beginPath();
    ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
    ctx.stroke();
    ctx.shadowBlur = 0;
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
}

// 摆渡链路(金色虚线,稳定微亮 + 前行金色数据包)
export function drawFerries(ctx, snap, view, posOf, now) {
  snap.ferries.forEach(([a, b, age]) => {
    const k = 1 - age / 3;
    if (k <= 0) return;
    const pa = posOf(a), pb = posOf(b);
    if (!pa || !pb) return;
    const x1 = pa[0] * view.scale + view.ox, y1 = pa[1] * view.scale + view.oy;
    const x2 = pb[0] * view.scale + view.ox, y2 = pb[1] * view.scale + view.oy;
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
}

// 激光充能:蓝信号线上叠加红色能量脉(流动点=功率),并标注每跳传输功率
export function drawPowerLinks(ctx, snap, view, posOf, now) {
  if (!snap.power_links?.length) return;
  ctx.save();
  ctx.lineWidth = 2;
  const tt = now / 1000;
  for (const [a, b, pw] of snap.power_links) {
    const na = posOf(a), nb = posOf(b);
    if (!na || !nb) continue;
    const ax = na[0] * view.scale + view.ox, ay = na[1] * view.scale + view.oy;
    const bx = nb[0] * view.scale + view.ox, by = nb[1] * view.scale + view.oy;
    ctx.strokeStyle = "rgba(255,64,64,0.5)";
    ctx.setLineDash([6, 4]);
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
    ctx.setLineDash([]);
    const flow = (tt * 1.6) % 1;
    ctx.fillStyle = "#ff6a6a";
    ctx.shadowColor = "#ff6a6a"; ctx.shadowBlur = 9;
    ctx.beginPath();
    ctx.arc(ax + (bx - ax) * flow, ay + (by - ay) * flow, 3.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.fillStyle = "rgba(255,150,150,0.9)";
    ctx.font = "9px Consolas, monospace"; ctx.textAlign = "center";
    ctx.fillText(`${pw}W`, (ax + bx) / 2, (ay + by) / 2 - 5);
  }
  ctx.restore();
}
