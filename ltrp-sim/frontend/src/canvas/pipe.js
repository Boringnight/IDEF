// 熔岩管静态几何绘制:管道轮廓 / 腔室辉光与标注 / 喉道界线 / 巨石 / 部署迷雾。
// 所有函数均为无状态纯绘制(ctx + 数据 + 视口变换),由 TopoMap2D 的渲染循环调用。
import { chamberName, tubeAt } from "./geometry.js";

// 管道轮廓路径:先上壁再下壁,闭合成可填充的管道形状
export function buildPipePath(samples, view) {
  const p = new Path2D();
  samples.forEach(([x, y, r], i) => {
    const sx = x * view.scale + view.ox, sy = (y - r) * view.scale + view.oy;
    if (i === 0) p.moveTo(sx, sy); else p.lineTo(sx, sy);
  });
  for (let i = samples.length - 1; i >= 0; i--) {
    const [x, y, r] = samples[i];
    p.lineTo(x * view.scale + view.ox, (y + r) * view.scale + view.oy);
  }
  p.closePath();
  return p;
}

// 管道填充 + 洞壁描边 + 腔室辉光/标注 + 喉道分界虚线
export function drawPipe(ctx, world, view, pipePath) {
  const S = world.samples;
  ctx.fillStyle = "rgba(28,40,64,0.42)";
  ctx.fill(pipePath);
  // 直接描边闭合轮廓(上下两壁都描到;原实现连续 beginPath 只描了下壁)
  ctx.strokeStyle = "rgba(110,160,220,0.22)";
  ctx.lineWidth = 1.5;
  ctx.stroke(pipePath);
  // 腔室域着色 + 名称(中心线 y 由几何插值得到,不再假设固定 350)
  world.chambers.forEach((c, i) => {
    const [cyc] = tubeAt(S, c.cx);
    const cx = c.cx * view.scale + view.ox, cy = cyc * view.scale + view.oy;
    const rx = c.hl * view.scale, ry = (c.r + 10) * view.scale;
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
  // 喉道分界:按该处中心线/半径画竖线,并标注喉道编号
  world.throats.forEach(([a, b], i) => {
    const [my, mr] = tubeAt(S, (a + b) / 2);
    const mx = ((a + b) / 2) * view.scale + view.ox;
    ctx.strokeStyle = "rgba(120,140,180,0.18)";
    ctx.setLineDash([4, 4]);
    [a, b].forEach((x) => {
      const [yy, rr] = tubeAt(S, x);
      const sx = x * view.scale + view.ox;
      ctx.beginPath();
      ctx.moveTo(sx, (yy - rr - 20) * view.scale + view.oy);
      ctx.lineTo(sx, (yy + rr + 20) * view.scale + view.oy);
      ctx.stroke();
    });
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(150,170,210,0.5)";
    ctx.font = "10px 'Microsoft YaHei', sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(`喉道 ${i + 1}`, mx, (my - mr - 30) * view.scale + view.oy);
  });
}

// 巨石:不规则多边形 + 内部裂纹;拖拽中的巨石额外画虚线高亮环
export function drawBoulders(ctx, boulders, view, dragIdx) {
  (boulders || []).forEach((b, i) => {
    const bx = b.x * view.scale + view.ox, by = b.y * view.scale + view.oy;
    const br = b.r * view.scale;
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
    if (dragIdx === i) {
      ctx.strokeStyle = "rgba(0,255,255,0.8)";
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.arc(bx, by, br + 5, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  });
}

// 首次部署迷雾:前沿以右(未探明)区域盖灰 + 探索进度百分比
export function drawDeployFog(ctx, snap, view, pipePath, W, H, samples) {
  const dep = snap.deploy || { front: 1e9, end: 1e9, done: true };
  if (!(dep.front < dep.end - 1)) return;
  const fx = dep.front * view.scale + view.ox;
  ctx.save();
  ctx.clip(pipePath);
  const grd = ctx.createLinearGradient(fx - 90, 0, fx + 50, 0);
  grd.addColorStop(0, "rgba(96,108,128,0)");
  grd.addColorStop(1, "rgba(96,108,128,0.86)");
  ctx.fillStyle = grd;
  ctx.fillRect(fx - 90, 0, W - fx + 90, H);
  ctx.restore();
  const pct = Math.min(100, Math.max(0,
    Math.round((dep.front - dep.base) / (dep.end - dep.base) * 100)));
  if (pct < 100 && samples) {
    const [fy, fr] = tubeAt(samples, dep.front);
    ctx.fillStyle = "rgba(160,210,255,0.75)";
    ctx.font = "10px Consolas, monospace";
    ctx.textAlign = "center";
    ctx.fillText(`探索 ${pct}%`, fx, fy * view.scale + view.oy - fr * view.scale - 12);
  }
}
