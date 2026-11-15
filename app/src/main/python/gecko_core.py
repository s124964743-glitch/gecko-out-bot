#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gecko Out 自動玩家 (辨色 + 路徑分析 + 自動拖曳)
------------------------------------------------
流程：adb 截圖 -> 解析棋盤(格線/壁/洞/壁虎/冰塊) -> 求解 -> 用 adb 拖曳壁虎頭部到同色洞 -> 重新截圖，重複。

用法：
  python gecko_bot.py                 # 自動玩到過關
  python gecko_bot.py --once          # 只走一步（建議第一次先用這個確認拖曳正確）
  python gecko_bot.py --dry-run       # 只解析+求解，不操作手機
  python gecko_bot.py --image a.png   # 離線測試某張截圖（會輸出 debug_*.png）
需求： pip install opencv-python numpy scipy ；電腦裝好 adb，手機開 USB/無線偵錯。
"""
import argparse, subprocess, sys, time, itertools
from collections import deque
import numpy as np
import cv2

FLOOR = np.array([135, 160, 184])
VOID = np.array([215, 227, 235])
_TPL_B64 = {  # 由實機截圖取樣的數字模板 (24x32)，其餘數字退回 pytesseract
    '1': "AAB/AAH/AAP/AA//AB//AH//AP//AP//AH//AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB+AAAAAAAAAAAAAAAAAAAA8AAA+AAAcAAA",
    '4': "AwAIB+B/D+B/D+B/D+B/D+B/H8B/H8B/H8B/P8B/P4B/P4B/P4B/f4B/fwB/fwB//8D/////////////////////P///AAD/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB+",
    '3': "AH8AAf/AB//wD//4H//8H//8P/f8P4D+PwD+HwD+BAD+AAH+AD/8AD/8AD/wAD/4AD/4AD/8AAH+AAD+AAB/fAB//gB/fwB/f4D/f//+P//+P//8H//4D//wA//AAH8A",
    '2': "AHwAA/+AB//AH//gH//wP//4f//8f4P8fwH8/gD+/gD+/gD+/gD+/gH+eAH+AAP8AAf8AB/4AH/wAP/wA//AB/+AH/8AP/wAf/wAf//+////////////////f///P//+",
}
import base64
DIGIT_TPL = {k: np.unpackbits(np.frombuffer(base64.b64decode(v), np.uint8))[:24 * 32].reshape(32, 24).astype(bool)
             for k, v in _TPL_B64.items()}


# ---------------------------------------------------------------- ADB
class Adb:
    def __init__(self, serial=None):
        self.base = ["adb"] + (["-s", serial] if serial else [])

    def screencap(self):
        out = subprocess.run(self.base + ["exec-out", "screencap", "-p"],
                             capture_output=True, check=True).stdout
        img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("截圖失敗，請確認 adb 已連線 (adb devices)")
        return img

    def drag(self, pts, step_ms=0.0):
        """沿著多個點拖曳 (需要 Android 12+ 的 input motionevent)。"""
        cmds = []
        x, y = pts[0]
        cmds.append(f"input motionevent DOWN {x} {y}")
        for x, y in pts[1:]:
            cmds.append(f"input motionevent MOVE {x} {y}")
        x, y = pts[-1]
        cmds.append(f"input motionevent UP {x} {y}")
        subprocess.run(self.base + ["shell", "; ".join(cmds)], check=True)


# ---------------------------------------------------------------- 視覺
def lab(c):
    a = np.uint8([[list(map(int, c))]])
    return cv2.cvtColor(a, cv2.COLOR_RGB2LAB)[0, 0].astype(float)


def cdist(a, b):
    return float(np.linalg.norm(lab(a) - lab(b)))


def med(rgb, x, y, r):
    h, w = rgb.shape[:2]
    p = rgb[max(0, y - r):min(h, y + r), max(0, x - r):min(w, x + r)].reshape(-1, 3)
    return np.median(p, 0).astype(int) if len(p) else np.zeros(3, int)


class Board:
    pass


def detect_grid(rgb, force_cols=None, force_rows=None):
    """用地板色範圍 + 洞的大小推得格子大小與原點。回傳 (x0, y0, s, cols, rows)。"""
    fl = (np.abs(rgb.astype(int) - FLOOR).max(2) < 10).astype(np.uint8)
    fl = cv2.morphologyEx(fl, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    colc = fl.sum(0); rowc = fl.sum(1)
    xs_ = np.where(colc > 60)[0]; ys_ = np.where(rowc > 60)[0]
    if len(xs_) == 0 or len(ys_) == 0:
        raise RuntimeError("找不到棋盤（不是遊戲畫面？）")
    ys, xs = ys_, xs_
    xmin, xmax, ymin, ymax = xs.min(), xs.max(), ys.min(), ys.max()
    dk = (rgb.max(2) < 60).astype(np.uint8)
    n, lab_, st, cen = cv2.connectedComponentsWithStats(dk)
    holes = [(cen[i], st[i]) for i in range(1, n) if 1500 < st[i, 4] < 8000
             and 0.6 < st[i, 2] / max(1, st[i, 3]) < 1.8
             and ymin - 40 < cen[i][1] < ymax + 40]
    if not holes:
        raise RuntimeError("畫面上已沒有洞（關卡可能已完成）")
    bw = float(np.median([h[1][2] for h in holes]))
    s = bw / 0.51
    # 相位：以洞中心對齊 (洞的黑色中心略偏下)
    hx = float(np.median([h[0][0] % s for h in holes]))
    hy = float(np.median([(h[0][1] - 0.10 * s) % s for h in holes]))
    # 修正 s：由寬度估 cols
    W = xmax - xmin + 1
    px = (hx - s / 2) % s
    py = (hy - s / 2) % s
    c0 = int(round((xmin - px) / s)); c1 = int(round((xmax - px) / s))
    r0 = int(round((ymin - py) / s)); r1 = int(round((ymax - py) / s))
    cols = force_cols or (c1 - c0)
    rows = force_rows or (r1 - r0)
    return px + c0 * s, py + r0 * s, s, cols, rows


def ring_color(rgb, cx, cy, s):
    best, bs = None, -1
    for ang in range(0, 360, 30):
        x = int(cx + 0.38 * s * np.cos(np.radians(ang)))
        y = int(cy + 0.38 * s * np.sin(np.radians(ang)))
        c = med(rgb, x, y, 4)
        sat = int(c.max()) - int(c.min())
        if sat > bs and not (abs(c - FLOOR).max() < 12):
            best, bs = c, sat
    return best if best is not None else np.array([255, 255, 255])


def read_digit(rgb, cx, cy, s):
    """讀冰塊上的數字 (1~9)。找得到 pytesseract 就用，否則回傳 None (視為未知)。"""
    r = int(s * 0.42)
    crop = rgb[cy - r:cy + r, cx - r:cx + r]
    white = ((crop.min(2) > 225)).astype(np.uint8) * 255
    x, y, ww, hh = cv2.boundingRect(white)
    if ww > 4 and hh > 4:
        w = cv2.resize(white[y:y + hh, x:x + ww], (24, 32), interpolation=cv2.INTER_AREA) > 127
        best = min(DIGIT_TPL.items(), key=lambda kv: np.mean(kv[1] != w))
        if np.mean(best[1] != w) < 0.12:
            return int(best[0])
    try:
        import pytesseract
        big = cv2.resize(255 - white, None, fx=2, fy=2)
        t = pytesseract.image_to_string(big, config="--psm 10 -c tessedit_char_whitelist=123456789").strip()
        return int(t[0]) if t else None
    except Exception:
        return None


GRID_CACHE = {}


def parse(rgb, force_cols=None, force_rows=None):
    if 'g' in GRID_CACHE:
        x0, y0, s, cols, rows = GRID_CACHE['g']
    else:
        x0, y0, s, cols, rows = detect_grid(rgb, force_cols, force_rows)
        GRID_CACHE['g'] = (x0, y0, s, cols, rows)
    b = Board()
    b.x0, b.y0, b.s, b.cols, b.rows = x0, y0, s, cols, rows
    b.cell = {}   # (r,c) -> kind
    b.ccol = {}   # (r,c) -> rgb
    b.hole = {}   # (r,c) -> rgb ring color
    b.digit = {}  # (r,c) -> 數字 or None
    cs = int(0.14 * s)

    def center(r, c):
        return int(x0 + (c + .5) * s), int(y0 + (r + .5) * s)

    b.center = center
    for r in range(rows):
        for c in range(cols):
            cx, cy = center(r, c)
            m = med(rgb, cx, cy, int(.17 * s))
            yy, xx = np.ogrid[:rgb.shape[0], :rgb.shape[1]]
            patch = rgb[cy + int(.10 * s) - int(.13 * s):cy + int(.10 * s) + int(.13 * s), max(0, cx - int(.15 * s)):cx + int(.15 * s)]
            dark_frac = float((patch.max(2) < 60).mean())
            corners = np.median([med(rgb, int(cx + dx * .40 * s), int(cy + dy * .40 * s), 5)
                                 for dx in (-1, 1) for dy in (-1, 1)], 0)
            if dark_frac > 0.5:
                b.cell[(r, c)] = "hole"
                b.hole[(r, c)] = ring_color(rgb, cx, cy, s)
            elif cdist(corners, VOID) < 8 and cdist(m, VOID) < 8:
                b.cell[(r, c)] = "void"
            elif corners[2] > 225 and corners[1] > 170 and corners[0] < 150 and cdist(corners, FLOOR) > 25:
                b.cell[(r, c)] = "ice"
                white = (rgb[cy - cs:cy + cs, cx - cs:cx + cs].min(2) > 225).mean()
                if white > 0.12:
                    b.digit[(r, c)] = read_digit(rgb, cx, cy, s)
            elif m.min() > 235 and cdist(corners, FLOOR) > 25:
                b.cell[(r, c)] = "wall"
            elif cdist(m, FLOOR) < 9:
                b.cell[(r, c)] = "floor"
            elif cdist(m, VOID) < 8:
                b.cell[(r, c)] = "void"
            else:
                b.cell[(r, c)] = "gecko"
                b.ccol[(r, c)] = m
    b.rgb = rgb
    return b


def components(cells, adj_ok):
    cells = set(cells)
    seen, out = set(), []
    for c in cells:
        if c in seen:
            continue
        comp, dq = [], deque([c])
        seen.add(c)
        while dq:
            u = dq.popleft(); comp.append(u)
            for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                v = (u[0] + d[0], u[1] + d[1])
                if v in cells and v not in seen and adj_ok(u, v):
                    seen.add(v); dq.append(v)
        out.append(comp)
    return out


def ham_paths(comp, start, limit=50):
    """從 start 出發、走遍 comp 所有格的路徑（壁虎身體）。"""
    cs = set(comp); res = []
    def dfs(path, seen):
        if len(res) >= limit:
            return
        if len(path) == len(comp):
            res.append(tuple(path)); return
        u = path[-1]
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            v = (u[0] + d[0], u[1] + d[1])
            if v in cs and v not in seen:
                seen.add(v); path.append(v)
                dfs(path, seen)
                path.pop(); seen.discard(v)
    dfs([start], {start})
    return res


def eye_score(b, cell):
    """眼睛 = 白色像素，旁邊有黑色瞳孔，且被壁虎同色皮膚包圍。"""
    s = b.s; cx, cy = b.center(*cell); r = int(.40 * s)
    crop = b.rgb[max(0, cy - r):cy + r, max(0, cx - r):cx + r].astype(int)
    base = b.ccol[cell]
    white = (crop.min(2) > 235).astype(np.uint8)
    dark = (crop.max(2) < 70).astype(np.uint8)
    skin = (np.abs(crop - base).sum(2) < 60).astype(np.uint8)
    k = np.ones((13, 13), np.uint8)
    ok = cv2.dilate(dark, k) & cv2.dilate(skin, k)
    return int((white & ok).sum())


def arrow_score(b, cell):
    s = b.s; cx, cy = b.center(*cell); r = int(.3 * s)
    crop = b.rgb[cy - r:cy + r, cx - r:cx + r].reshape(-1, 3).astype(int)
    base = b.ccol.get(cell, np.median(crop, 0))
    dkr = (crop.sum(1) < 0.80 * base.sum()) & (crop.max(1) > 40)
    return int(dkr.sum())


def build_state(b):
    """回傳 dict: geckos(list), holes, static_block, frozen(list)"""
    geckos, frozen = [], []
    gcells = [c for c, k in b.cell.items() if k == "gecko"]

    def same(u, v):
        return cdist(b.ccol[u], b.ccol[v]) < 22
    for comp in components(gcells, same):
        if len(comp) < 2:
            continue
        head = max(comp, key=lambda c: eye_score(b, c))
        paths = ham_paths(comp, head)
        if not paths:
            # 找不到走遍的路徑 -> 放棄此元件
            continue
        if len(paths) > 1:
            paths.sort(key=lambda p: -arrow_score(b, p[-1]))
        col = np.median([b.ccol[c] for c in comp], 0).astype(int)
        geckos.append(dict(cells=paths[0], color=col))
    icec = [c for c, k in b.cell.items() if k == "ice"]
    for comp in components(icec, lambda u, v: True):
        dig = [c for c in comp if c in b.digit]
        head = dig[0] if dig else comp[0]
        paths = ham_paths(comp, head)
        cells = paths[0] if paths else tuple(comp)
        nd = [c for c in comp if c not in b.digit] or comp
        tint = np.mean([med(b.rgb, *b.center(*c)[:2], int(.15 * b.s)) for c in nd], 0)
        frozen.append(dict(cells=cells, count=b.digit.get(head), color=None, tint=tint))
    holes = [dict(cell=c, color=col) for c, col in b.hole.items()]
    return geckos, frozen, holes


def match_targets(geckos, frozen, holes):
    """每隻壁虎配對同色洞（同色多個洞可互換）。回傳每隻壁虎可進入的洞格集合。"""
    used = set()
    hc = [(h["cell"], h["color"]) for h in holes]
    for g in geckos:
        ds = sorted(hc, key=lambda h: cdist(g["color"], h[1]))
        thr = cdist(ds[0][1], g["color"]) + 18
        g["targets"] = {h[0] for h in ds if cdist(h[1], g["color"]) <= thr and cdist(h[1], g["color"]) < 55}
    # 冰凍壁虎：把「還沒被自由壁虎分配到」的洞，用顏色相近度分配
    # 同色群：把洞分群
    groups = []
    for h, col in hc:
        for gr in groups:
            if cdist(gr["color"], col) < 20:
                gr["cells"].add(h); break
        else:
            groups.append(dict(color=col, cells={h}))
    need = {id(gr): len(gr["cells"]) for gr in groups}
    for g in geckos:
        for gr in groups:
            if g["targets"] and next(iter(g["targets"])) in gr["cells"]:
                need[id(gr)] -= 1
    ICE = np.array([40, 214, 253])
    pairs = []
    for fi, f in enumerate(frozen):
        fc = f.get("tint")
        for gr in groups:
            if need[id(gr)] <= 0:
                continue
            pred = 0.75 * gr["color"] + 0.25 * ICE
            pairs.append((cdist(fc, pred) if fc is not None else 0.0, fi, id(gr)))
    pairs.sort(key=lambda p: p[0])
    assigned = set()
    for dist, fi, gid in pairs:
        if fi in assigned or need[gid] <= 0:
            continue
        gr = next(g_ for g_ in groups if id(g_) == gid)
        frozen[fi]["targets"] = set(gr["cells"]); need[gid] -= 1; assigned.add(fi)
    for fi, f in enumerate(frozen):
        f.setdefault("targets", set())
    return geckos, frozen


# ---------------------------------------------------------------- 求解
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def canon(cfg):
    cfg = tuple(cfg)
    return min(cfg, cfg[::-1])


class Solver:
    def __init__(self, b, geckos, frozen, holes):
        self.b = b
        self.floorlike = {c for c, k in b.cell.items() if k in ("floor", "gecko", "ice", "hole")}
        self.holes = {h["cell"] for h in holes}
        self.geckos = geckos
        self.frozen = frozen
        self.banned = set()   # 已證實拖了沒反應的起點格，不再嘗試

    def moves_bfs(self, cfg, blocked, targets, used=frozenset(), maxn=4000):
        """單隻壁虎 BFS。回傳 (到洞的最短頭部路徑 or None, {config: 頭部路徑})"""
        start = tuple(cfg)
        prev = {start: None}
        dq = deque([start])
        goal = None
        while dq:
            cur = dq.popleft()
            head, tail = cur[0], cur[-1]
            for d in DIRS:
                n = (head[0] + d[0], head[1] + d[1])
                if n not in self.floorlike:
                    continue
                if n in targets and n not in used:
                    if goal is None:
                        goal = (cur, n)
                    continue
                if (n in self.holes and n not in used) or n in blocked:
                    continue
                if n in cur[:-1]:
                    continue
                nxt = (n,) + cur[:-1]
                if nxt in prev:
                    continue
                prev[nxt] = cur
                dq.append(nxt)
                if len(prev) > maxn:
                    break
            if goal and False:
                break
        return goal, prev

    @staticmethod
    def head_path(prev, cur, extra=None):
        seq = []
        while cur is not None:
            seq.append(cur[0]); cur = prev[cur]
        seq.reverse()
        if extra:
            seq.append(extra)
        return seq

    def blocked_for(self, state, idx):
        """state: (tuple cfg per gecko or None=removed, tuple frozen counters)"""
        blk = set()
        for j, cfg in enumerate(state[0]):
            if cfg is not None and j != idx:
                blk.update(cfg)
        for k, f in enumerate(self.frozen):
            if state[1][k] is not None:    # 還沒解凍
                blk.update(f["cells"])
        return blk

    def tick(self, state):
        cnt = list(state[1]); geck = list(state[0]); used = state[2]
        for k, v in enumerate(cnt):
            if v is None:
                continue
            v = v - 1
            cnt[k] = None if v <= 0 else v
        return tuple(geck), tuple(cnt), used

    def initial(self):
        cfgs = tuple(canon(g["cells"]) for g in self.geckos) + tuple(None for _ in self.frozen)
        cnt = tuple((f["count"] if f["count"] else 99) for f in self.frozen)
        return cfgs, cnt, frozenset()

    # 把冰凍壁虎也當成一般壁虎 (index 偏移)，解凍後才可動
    def all_targets(self, i):
        n = len(self.geckos)
        return (self.geckos[i]["targets"] if i < n else self.frozen[i - n]["targets"])

    def mobile(self, state, i):
        n = len(self.geckos)
        if state[0][i] is None:
            return False
        if i < n:
            return True
        return state[1][i - n] is None

    def frozen_cfg(self, i):
        return tuple(self.frozen[i - len(self.geckos)]["cells"])

    def blocked(self, state, idx):
        blk = set()
        n = len(self.geckos)
        for j, cfg in enumerate(state[0]):
            if j == idx or cfg is None:
                continue
            blk.update(cfg)
        for k, f in enumerate(self.frozen):
            if state[1][k] is not None:
                blk.update(f["cells"])
        return blk

    def goto_hole(self, state):
        """回傳可直接進洞的 (idx, path) 清單。任一端都可以當領頭(拖曳的那一端)。"""
        res = []
        for i, cfg in enumerate(state[0]):
            if not self.mobile(state, i):
                continue
            best = None
            for ori in (tuple(cfg), tuple(cfg)[::-1]):
                goal, prev = self.moves_bfs(ori, self.blocked(state, i), self.all_targets(i), state[2])
                if goal:
                    p = self.head_path(prev, goal[0], goal[1])
                    if p[0] in self.banned:
                        continue
                    if best is None or len(p) < len(best):
                        best = p
            if best:
                res.append((i, best))
        return res

    def apply_remove(self, state, i, hole=None):
        cfgs = list(state[0]); cfgs[i] = None
        used = state[2] | ({hole} if hole else set())
        return self.tick((tuple(cfgs), state[1], used))

    def apply_reposition(self, state, i, cfg):
        cfgs = list(state[0]); cfgs[i] = canon(cfg)
        return (tuple(cfgs), state[1], state[2])   # 冰塊只在「有壁虎進洞」時倒數

    def normalize(self, state):
        """解凍中的壁虎：counter 變 None 時，配置設為冰塊所在位置。"""
        cfgs = list(state[0]); n = len(self.geckos)
        for k, v in enumerate(state[1]):
            if v is None and cfgs[n + k] is None and self.frozen[k]["cells"] and not self._done.get(k):
                pass
        return state

    def solve(self, max_repos=3, node_cap=20000, time_limit=8.0):
        """回傳第一步的動作序列 [(idx, head_path, kind)]，kind='goal'/'repos'。"""
        n = len(self.geckos)
        init = list(self.initial())
        cfgs = list(init[0])
        for k, f in enumerate(self.frozen):
            cfgs[n + k] = canon(f["cells"])
        init = (tuple(cfgs), init[1], init[2])
        seen = set()
        nodes = [0]
        t_end = time.time() + time_limit

        def done(st):
            return all(c is None for c in st[0])

        def key(st):
            return st

        def dfs(st, repos_left, plan):
            nodes[0] += 1
            if done(st):
                return plan
            if nodes[0] > node_cap or time.time() > t_end:
                return None
            if (st, repos_left) in seen:
                return None
            seen.add((st, repos_left))
            gl = self.goto_hole(st)
            if gl:
                # 進洞一律是好事（只會讓出空間）；取第一個
                i, path = gl[0]
                ns = self.apply_remove(st, i, path[-1])
                r = dfs(ns, repos_left, plan + [(i, path, "goal")])
                if r is not None:
                    return r
                return None
            if repos_left == 0:
                return None
            # 無法進洞 -> 嘗試把某隻可動壁虎移到別處 (也會讓冰塊倒數)
            cand = []
            for i, cfg in enumerate(st[0]):
                if not self.mobile(st, i):
                    continue
                for ori in (tuple(cfg), tuple(cfg)[::-1]):
                    goal, prev = self.moves_bfs(ori, self.blocked(st, i), set(), st[2])
                    for c in prev:
                        if canon(c) == canon(cfg):
                            continue
                        cand.append((i, c, prev))
            def plen(prev, c):
                k = 0
                while c is not None:
                    c = prev[c]; k += 1
                return k
            uniq = {}
            for i, c, prev in cand:
                kf = (i, canon(c))
                L = plen(prev, c)
                if kf not in uniq or L < uniq[kf][0]:
                    uniq[kf] = (L, i, c, prev)
            good, other = [], []
            for kf, (L, i, c, prev) in uniq.items():
                ns = self.apply_reposition(st, i, c)
                (good if self.goto_hole(ns) else other).append((L, i, c, prev, ns))
            good.sort(key=lambda t_: t_[0]); other.sort(key=lambda t_: t_[0])
            for L, i, c, prev, ns in good + (other[:40] if repos_left > 1 else []):
                r = dfs(ns, repos_left - 1, plan + [(i, self.head_path(prev, c), "repos")])
                if r is not None:
                    return r
            return None

        self.partial = False
        for rl in range(0, max_repos + 1):
            seen.clear(); nodes[0] = 0
            r = dfs(init, rl, [])
            if r is not None:
                return r
        # 找不到完整解（例如有尚未支援的機制）：至少做「現在就能進洞」的安全步驟
        gl = self.goto_hole(init)
        if gl:
            self.partial = True
            i, path = gl[0]
            return [(i, path, "goal")]
        return None


# ---------------------------------------------------------------- 主流程
def to_px(b, cells):
    return [b.center(r, c) for r, c in cells]


def draw_debug(b, geckos, frozen, plan, name):
    img = cv2.cvtColor(b.rgb, cv2.COLOR_RGB2BGR).copy()
    for (r, c), k in b.cell.items():
        cx, cy = b.center(r, c)
        col = {"hole": (0, 0, 255), "ice": (255, 200, 0), "wall": (255, 255, 255), "void": (90, 90, 90),
               "floor": (60, 60, 60), "gecko": (0, 200, 0)}[k]
        cv2.rectangle(img, (int(cx - b.s / 2 + 3), int(cy - b.s / 2 + 3)), (int(cx + b.s / 2 - 3), int(cy + b.s / 2 - 3)), col, 1)
    for gi, g in enumerate(geckos):
        pts = to_px(b, g["cells"])
        for a, c in zip(pts, pts[1:]):
            cv2.line(img, a, c, (255, 0, 255), 4)
        cv2.circle(img, pts[0], 16, (0, 255, 255), 3)   # 頭
        cv2.putText(img, f"G{gi}", pts[0], cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
        for t in g.get("targets", []):
            cv2.line(img, pts[0], b.center(*t), (0, 255, 255), 1)
    for f in frozen:
        pts = to_px(b, f["cells"])
        cv2.putText(img, f"ice{f['count']}", pts[0], cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
    if plan:
        i, path, kind = plan[0]
        pts = to_px(b, path)
        for a, c in zip(pts, pts[1:]):
            cv2.arrowedLine(img, a, c, (0, 0, 255), 6)
    cv2.imwrite(name, img)


def analyse(rgb, args):
    b = parse(rgb, args.cols, args.rows)
    geckos, frozen, holes = build_state(b)
    match_targets(geckos, frozen, holes)
    return b, geckos, frozen, holes


def is_result_screen(rgb):
    h, w = rgb.shape[:2]
    top = rgb[int(h * .12):int(h * .5), :].reshape(-1, 3).mean(0)
    return top[2] > 150 and top[0] < 90 and top[1] < 90    # 藍紫色結算畫面


def stable_shot(adb, wait=0.6):
    a = adb.screencap(); time.sleep(wait)
    for _ in range(6):
        b = adb.screencap()
        if np.abs(a.astype(int) - b.astype(int)).mean() < 1.0:
            return cv2.cvtColor(b, cv2.COLOR_BGR2RGB)
        a = b; time.sleep(0.4)
    return cv2.cvtColor(b, cv2.COLOR_BGR2RGB)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial"); ap.add_argument("--image")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--once", action="store_true")
    ap.add_argument("--cols", type=int); ap.add_argument("--rows", type=int)
    ap.add_argument("--settle", type=float, default=1.6, help="每次拖曳後等待動畫秒數")
    ap.add_argument("--max-moves", type=int, default=60)
    args = ap.parse_args()

    if args.image:
        rgb = cv2.cvtColor(cv2.imread(args.image), cv2.COLOR_BGR2RGB)
        b, geckos, frozen, holes = analyse(rgb, args)
        print(f"格子 {b.rows}x{b.cols}, 格寬 {b.s:.1f}px, 壁虎 {len(geckos)}, 冰凍 {len(frozen)}, 洞 {len(holes)}")
        for i, g in enumerate(geckos):
            print(f" G{i} head={g['cells'][0]} cells={g['cells']} targets={sorted(g['targets'])}")
        for f in frozen:
            print(f" ICE cells={f['cells']} count={f['count']} targets={sorted(f['targets'])}")
        plan = Solver(b, geckos, frozen, holes).solve()
        print("解:", None if plan is None else [(p[0], p[2], p[1]) for p in plan])
        draw_debug(b, geckos, frozen, plan, "debug_" + args.image.split("/")[-1])
        return

    adb = Adb(args.serial)
    fails = 0
    last, repeat = None, 0
    for step in range(args.max_moves):
        rgb = stable_shot(adb)
        if is_result_screen(rgb):
            print("偵測到結算畫面，關卡完成！請手動按下一關。"); return
        try:
            b, geckos, frozen, holes = analyse(rgb, args)
        except Exception as e:
            print("解析失敗:", e); time.sleep(1); fails += 1
            if fails > 5: return
            continue
        if not geckos and not frozen:
            print("棋盤上沒有壁虎了（或尚未進入關卡）。"); return
        plan = Solver(b, geckos, frozen, holes).solve()
        draw_debug(b, geckos, frozen, plan, "debug_step.png")
        if not plan:
            print("找不到解（請看 debug_step.png 檢查辨識）"); return
        gi, path, kind = plan[0]
        pts = to_px(b, path)
        sig = (gi, tuple(path), tuple(sorted(c for c, k in b.cell.items() if k == 'gecko')))
        repeat = repeat + 1 if sig == last else 0
        last = sig
        if repeat >= 2:
            print('同一步重複 3 次畫面沒變化：拖曳可能沒被遊戲接受，請看 debug_step.png / 改用 --once 檢查。'); return
        print(f"[{step}] {kind} G{gi} {path[0]} -> {path[-1]}  ({len(path)} 格)")
        if args.dry_run:
            return
        adb.drag(pts)
        time.sleep(args.settle)
        if args.once:
            return


if __name__ == "__main__":
    main()
