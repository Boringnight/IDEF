# -*- coding: utf-8 -*-
"""熔岩管几何世界:多腔室 + 窄喉道 + 巨石(复杂内部结构,支持随机种子生成)"""
import math
import random


def letter(i: int) -> str:
    """0->A, 1->B, ..., 25->Z, 26->AA, ...(腔室命名)"""
    s = ""
    n = i
    while True:
        s = chr(65 + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


class World:
    def __init__(self, seed=7):
        self.seed = seed
        rng = random.Random(seed)
        # ---- 总体尺寸与管道起伏 ----
        self.n_chambers = rng.randint(4, 6)
        self.frac = rng.uniform(0.78, 0.92)       # 有腔室覆盖的 x 比例(其余为两端引道)
        self.W = rng.randint(2800, 3600)
        self.base_r = rng.randint(48, 68)         # 喉道(窄)半径
        self.amp = rng.uniform(40, 88)            # 中心线摆动幅度
        self.wl = rng.uniform(180, 300)           # 主波长
        self.phase = rng.uniform(0, 2 * math.pi)
        self.harm = rng.uniform(0.12, 0.36)       # 二次谐波占幅比例
        # ---- 腔室排布:等间距,半径/半长随机,喉道长度随机 ----
        hl = [rng.randint(150, 230) for _ in range(self.n_chambers)]
        cr = [rng.randint(135, 185) for _ in range(self.n_chambers)]
        throat_len = rng.randint(90, 180)
        span = sum(2 * h for h in hl) + throat_len * (self.n_chambers - 1)
        inner = self.W * self.frac
        x0 = (self.W - inner) / 2
        scale = min(1.0, inner / max(1.0, span))  # 压缩以适配总长
        x = x0
        self.chambers = []
        for i in range(self.n_chambers):
            h = hl[i] * scale
            cx = x + h
            self.chambers.append({"cx": round(cx, 1), "hl": round(h, 1), "r": cr[i]})
            x = cx + h + throat_len * scale
        max_r = max(c["r"] for c in self.chambers)
        self.H = int(2 * (self.amp * (1 + self.harm) + max_r) + 240)
        # ---- 巨石:每腔 2~4 块,大小/位置随机,远离喉道避免封死通路 ----
        self.boulders = []
        for ci, ch in enumerate(self.chambers):
            placed, tries = 0, 0
            want = rng.randint(2, 4)
            while placed < want and tries < 400:
                tries += 1
                x = rng.uniform(ch["cx"] - ch["hl"] * 0.5, ch["cx"] + ch["hl"] * 0.5)
                side = rng.choice([-1, 1])
                y = self.yc(x) + side * rng.uniform(8, max(8.0, self.r_at(x) - 42))
                r = rng.uniform(34, 56)
                b = {"x": round(x, 1), "y": round(y, 1), "r": round(r, 1)}
                if self._boulder_ok(b):
                    self.boulders.append(b)
                    placed += 1

    # ---- 几何 ----
    def yc(self, x: float) -> float:
        # 主正弦 + 二次谐波,管道蜿蜒起伏更复杂
        return self.H / 2 \
            + self.amp * math.sin(2 * math.pi * x / self.wl + self.phase) \
            + self.amp * self.harm * math.sin(2 * math.pi * x / (self.wl * 0.41) + self.phase * 2.7)

    def r_at(self, x: float) -> float:
        # 腔室处增宽(按该腔自己的半径),喉道收窄到 base_r
        r = self.base_r
        for ch in self.chambers:
            t = max(0.0, 1.0 - ((x - ch["cx"]) / ch["hl"]) ** 2)
            r = max(r, ch["r"] * t + self.base_r * (1 - t))
        return r

    def inside(self, x: float, y: float, margin: float = 6.0) -> bool:
        return abs(y - self.yc(x)) <= self.r_at(x) - margin

    def _boulder_ok(self, b) -> bool:
        if not self.inside(b["x"], b["y"], b["r"] + 8):
            return False
        for o in self.boulders:
            if math.hypot(o["x"] - b["x"], o["y"] - b["y"]) < o["r"] + b["r"] + 55:
                return False
        return True

    def los(self, p1, p2) -> bool:
        """视线:线段全程在管内且不穿巨石"""
        # 1) 管内采样:洞壁形状非线性,沿线取点判断是否穿出岩壁
        d = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        n = max(2, int(d / 14))
        for k in range(n + 1):
            t = k / n
            if not self.inside(p1[0] + (p2[0] - p1[0]) * t,
                               p1[1] + (p2[1] - p1[1]) * t, 8):
                return False
        # 2) 巨石遮挡:直接算"线段到圆心的最近距离",一次判定(比逐点采样更准更快)
        for b in self.boulders:
            if self._seg_circle_hit(p1, p2, b["x"], b["y"], b["r"] + 5):
                return False
        return True

    @staticmethod
    def _seg_circle_hit(p1, p2, cx, cy, r) -> bool:
        """线段与圆是否相交:线段上离圆心最近的点到圆心距离 ≤ r"""
        ax, ay = p1
        dx, dy = p2[0] - ax, p2[1] - ay
        l2 = dx * dx + dy * dy
        if l2 == 0.0:
            return (ax - cx) ** 2 + (ay - cy) ** 2 < r * r
        t = max(0.0, min(1.0, ((cx - ax) * dx + (cy - ay) * dy) / l2))
        px, py = ax + t * dx, ay + t * dy
        return (px - cx) ** 2 + (py - cy) ** 2 < r * r

    def domain_of(self, x: float):
        """返回 (域编号, 是否喉道边界节点)"""
        for i, ch in enumerate(self.chambers):
            if abs(x - ch["cx"]) <= ch["hl"]:
                return i, False
        for i in range(len(self.chambers) - 1):
            a = self.chambers[i]["cx"] + self.chambers[i]["hl"]
            b = self.chambers[i + 1]["cx"] - self.chambers[i + 1]["hl"]
            if a <= x <= b:
                return i, True
        return 0, False

    def throats(self):
        out = []
        for i in range(len(self.chambers) - 1):
            a = self.chambers[i]["cx"] + self.chambers[i]["hl"]
            b = self.chambers[i + 1]["cx"] - self.chambers[i + 1]["hl"]
            out.append((a, b))
        return out

    def centerline_samples(self, step: int = 20):
        return [(x, round(self.yc(x), 1), round(self.r_at(x), 1))
                for x in range(int(0.05 * self.W), int(0.95 * self.W) + 1, step)]

    def rover_bounds(self):
        """月球车可巡逻的 x 范围(左右端点)"""
        return (0.05 * self.W, 0.95 * self.W)

    def export(self) -> dict:
        return {
            "W": self.W, "H": self.H,
            "samples": self.centerline_samples(),
            "chambers": [dict(c) for c in self.chambers],
            "throats": self.throats(),
            "boulders": [dict(b) for b in self.boulders],
        }
