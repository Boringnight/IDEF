// 画布几何与枚举工具:纯函数/常量,不持有任何状态。
// 被 pipe/links/nodes/TopoMap2D 共同引用,避免各文件重复定义颜色与插值逻辑。

export const STATE_COLOR = {
  NORMAL: "#35E0C8",
  PROTECTED: "#FFC400",
  DYING: "#FF5252",
  DEAD: "#5A6270",
};

export const ROLE_ZH = {
  base: "主基站", spike: "道钉", probe: "探测器", rover: "月球车",
};

export function stateZh(s) {
  return { NORMAL: "正常", PROTECTED: "保护态(退出中继)", DYING: "垂危", DEAD: "宕机" }[s] || s;
}

export function chamberName(i) {
  // 0->A, 1->B, ..., 25->Z, 26->AA, ...(支持可变腔室数)
  let n = i, s = "";
  for (;;) {
    s = String.fromCharCode(65 + (n % 26)) + s;
    n = Math.floor(n / 26) - 1;
    if (n < 0) break;
  }
  return s;
}

export function dist(ax, ay, bx, by) {
  return Math.hypot(ax - bx, ay - by);
}

// 管道几何插值:给定 x 返回该处中心线 y 与半径 r
export function tubeAt(samples, x) {
  if (!samples || !samples.length) return [0, 60];
  const s = samples;
  if (x <= s[0][0]) return [s[0][1], s[0][2]];
  const last = s[s.length - 1];
  if (x >= last[0]) return [last[1], last[2]];
  for (let i = 0; i < s.length - 1; i++) {
    const a = s[i], b = s[i + 1];
    if (a[0] <= x && x <= b[0]) {
      const span = b[0] - a[0] || 1;
      const t = (x - a[0]) / span;
      return [a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
    }
  }
  return [last[1], last[2]];
}
