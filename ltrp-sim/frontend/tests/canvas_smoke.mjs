// 画布层冒烟测试:用 mock 2D 上下文真实执行每个绘制函数,捕捉运行时错误
// (build 只能证明能打包,不能证明绘制路径不抛异常)。
// 运行: node tests/canvas_smoke.mjs
import { buildPipePath, drawPipe, drawBoulders, drawDeployFog } from "../src/canvas/pipe.js";
import { drawStaticLinks, drawFlows, drawFerries, drawPowerLinks } from "../src/canvas/links.js";
import { drawSignalCover, drawEnergyRings, drawNodes, drawDragGhost } from "../src/canvas/nodes.js";
import { chamberName, tubeAt, dist } from "../src/canvas/geometry.js";

// --- Path2D / CanvasRenderingContext2D 的最小替身 ---
class FakePath { moveTo() {} lineTo() {} closePath() {} }
globalThis.Path2D = FakePath;

function makeCtx() {
  const noop = () => {};
  const grad = { addColorStop: noop };
  return {
    fillStyle: "", strokeStyle: "", lineWidth: 1, font: "", textAlign: "",
    shadowColor: "", shadowBlur: 0, globalAlpha: 1,
    save: noop, restore: noop, beginPath: noop, closePath: noop,
    moveTo: noop, lineTo: noop, arc: noop, roundRect: noop,
    fill: noop, stroke: noop, fillRect: noop, clearRect: noop, clip: noop,
    setLineDash: noop, fillText: noop, translate: noop, setTransform: noop,
    createRadialGradient: () => grad, createLinearGradient: () => grad,
  };
}

// --- 合成世界与快照(字段与后端 DTO 对齐) ---
const samples = Array.from({ length: 40 }, (_, i) => [i * 80, 350 + 40 * Math.sin(i / 3), 60 + (i % 5) * 10]);
const world = {
  W: 3200, H: 760, samples,
  chambers: [{ cx: 400, hl: 180, r: 150 }, { cx: 1600, hl: 200, r: 170 }],
  throats: [[580, 1400], [1800, 2400]],
  boulders: [{ x: 700, y: 330, r: 45 }, { x: 1900, y: 380, r: 50 }],
};
const node = (id, role, x, y, extra = {}) => ({
  id, role, x, y, soc: 72, state: "NORMAL", alive: true, sleeping: false, crit: false,
  domain: 0, border: false, sos: false, moving: false, bundles: 2, pkts: 1, temp: -20,
  seu: 0, nh: "SPIKE-01", cost: 40, ms: 60, pending: false, stock: 0, pv_w: 0,
  laser_in: 0, laser_out: 0, charge_ma: 0, energy: false,
  phys: {}, nbrs: [["SPIKE-02", 18.2, 1e-9]], ...extra,
});
const snap = {
  cmd: "snap", t: 100, nodes: [
    node("BASE-00", "base", 100, 350),
    node("SPIKE-01", "spike", 300, 360, { crit: true }),
    node("SPIKE-02", "spike", 500, 340, { sleeping: true, border: true }),
    node("SPIKE-03", "spike", 700, 350, { alive: false }),
    node("PROBE-1", "probe", 900, 355, { stock: 3 }),
    node("ROVER-1", "rover", 1200, 350, { bundles: 5 }),
    node("SPIKE-04", "spike", 1400, 360, { pending: true }),
  ],
  params: {}, links: [["BASE-00", "SPIKE-01"], ["SPIKE-01", "SPIKE-02"]],
  power_links: [["BASE-00", "SPIKE-01", 120.5]],
  flows: [["BASE-00", "SPIKE-01", 0.2]], ferries: [["ROVER-1", "SPIKE-02", 1.0]],
  events: [], boulders: world.boulders,
  deploy: { front: 1500, base: 100, end: 3000, done: false },
  stats: { range: 260, signal_cover: 0.83 },
};

const view = { scale: 0.35, ox: 20, oy: 30 };
const posOf = (id) => {
  const n = snap.nodes.find((m) => m.id === id);
  return n ? [n.x, n.y] : null;
};
const mouse = { x: 400, y: 300, dragIdx: 0 };

let checks = 0;
function run(name, fn) {
  try {
    fn();
    checks++;
  } catch (e) {
    console.error(`FAIL ${name}: ${e.message}`);
    process.exitCode = 1;
  }
}

run("geometry.chamberName", () => {
  const got = [0, 25, 26, 51].map(chamberName).join(",");
  if (got !== "A,Z,AA,AZ") throw new Error(`chamberName 结果异常: ${got}`);
});
run("geometry.tubeAt", () => {
  const [y, r] = tubeAt(samples, 320);
  if (!Number.isFinite(y) || !Number.isFinite(r)) throw new Error("tubeAt 返回非数值");
  if (tubeAt([], 10)[1] !== 60) throw new Error("空 samples 兜底异常");
  if (dist(0, 0, 3, 4) !== 5) throw new Error("dist 异常");
});
run("pipe.buildPipePath", () => {
  if (!(buildPipePath(samples, view) instanceof FakePath)) throw new Error("未返回路径");
});
run("pipe.drawPipe", () => drawPipe(makeCtx(), world, view, buildPipePath(samples, view)));
run("pipe.drawBoulders", () => drawBoulders(makeCtx(), world.boulders, view, 0));
run("pipe.drawDeployFog", () => drawDeployFog(makeCtx(), snap, view, new FakePath(), 900, 600, samples));
run("links.drawStaticLinks", () => drawStaticLinks(makeCtx(), snap, view, posOf));
run("links.drawFlows", () => drawFlows(makeCtx(), snap, view, posOf, 12345));
run("links.drawFerries", () => drawFerries(makeCtx(), snap, view, posOf, 12345));
run("links.drawPowerLinks", () => drawPowerLinks(makeCtx(), snap, view, posOf, 12345));
run("nodes.drawSignalCover", () => drawSignalCover(makeCtx(), snap, view, posOf));
run("nodes.drawEnergyRings", () => drawEnergyRings(makeCtx(), snap, view, posOf));
run("nodes.drawNodes", () => drawNodes(makeCtx(), snap, view, posOf, 12345, "SPIKE-01", "SPIKE-02", false));
run("nodes.drawNodes(done)", () => drawNodes(makeCtx(), snap, view, posOf, 12345, null, null, true));
run("nodes.drawDragGhost", () => drawDragGhost(makeCtx(), view, snap, mouse, (x, y) => [x, y]));

// 边界:空世界/空快照不应抛异常
run("empty-world", () => {
  const empty = { W: 1, H: 1, samples: [], chambers: [], throats: [], boulders: [] };
  const es = { nodes: [], links: [], flows: [], ferries: [], power_links: [], boulders: [], deploy: {}, stats: {} };
  drawPipe(makeCtx(), empty, view, buildPipePath([], view));
  drawStaticLinks(makeCtx(), es, view, () => null);
  drawNodes(makeCtx(), es, view, () => null, 0, null, null, true);
  drawDeployFog(makeCtx(), es, view, new FakePath(), 10, 10, []);
});

console.log(process.exitCode ? `画布冒烟测试失败` : `画布冒烟测试通过: ${checks} 项`);
