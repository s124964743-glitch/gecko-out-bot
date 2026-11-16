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
    '6': "AAP8AD/+AH/+Af/+A//+B//8D/+AH/wAH/AAP+AAP8AAP4AAf4AAf/8Af//gf//4f//4///8///+/4H+/wD//wD/fwD/fwD/f4D+f8P+P//+H//8H//4D//wA//AAH4A",
    '7': "f//+f//+///////////+f//+B//+AAP8AAP8AAP8AAf4AAfwAA/wAB/gAD/gAD/AAH+AAH+AAP8AAP4AAP4AAf4AA/wAA/gAA/gAB/gAB/AAB/AAB/AAB/AAB+AAB+AA",
    '1': "AAB/AAH/AAP/AA//AB//AH//AP//AP//AH//AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB+AAAAAAAAAAAAAAAAAAAA8AAA+AAAcAAA",
    '4': "AwAIB+B/D+B/D+B/D+B/D+B/H8B/H8B/H8B/P8B/P4B/P4B/P4B/f4B/fwB/fwB//8D/////////////////////P///AAD/AAB/AAB/AAB/AAB/AAB/AAB/AAB/AAB+",
    '3': "AH8AAf/AB//wD//4H//8H//8P/f8P4D+PwD+HwD+BAD+AAH+AD/8AD/8AD/wAD/4AD/4AD/8AAH+AAD+AAB/fAB//gB/fwB/f4D/f//+P//+P//8H//4D//wA//AAH8A",
    '2': "AHwAA/+AB//AH//gH//wP//4f//8f4P8fwH8/gD+/gD+/gD+/gD+/gH+eAH+AAP8AAf8AB/4AH/wAP/wA//AB/+AH/8AP/wAf/wAf//+////////////////f///P//+",
    '8': "AH8AA//AB//gD//4H//8P//8P8P+f4H+f4D+fwD+f4D+P8H+P+f8H//4D//4D//4H//8P//+f8H+fwD//wB//wB//wB/fwB/f4D/f+P+P//+H//8H//4B//wA//AADwA",
    '5': "H//+H//+P//+P//+f//+f//+f+AAf4AAf4AAf4AAf8AAf/4Af//A///w///4///8///+///+PwH/BgD/AAD/AAD/AAD/EAD/fgH////+///+///8///4P//wH//AAf8A",
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
    # 用「洞中心落在格線上」微調格子大小（洞多時很準，避免誤差累積）
    if len(holes) >= 5:
        pts = np.array([[h[0][0], h[0][1] - 0.10 * 1.0 * s] for h in holes])
        best = None
        for sc in np.arange(s * 0.9, s * 1.06, 0.25):
            err = 0.0
            for k in (0, 1):
                ang = 2 * np.pi * pts[:, k] / sc
                m = np.hypot(np.cos(ang).mean(), np.sin(ang).mean())
                err += 1 - m
            if best is None or err < best[0] - 1e-9:
                best = (err, sc)
        if abs(best[1] - s) / s > 0.025:    # 差距小就沿用原估計（避免相位跳動）
            s = float(best[1])
    # 相位：以洞中心對齊 (洞的黑色中心略偏下)
    # 垂直格距有時和水平不同（列數多的高棋盤，約差 1~2%，累積 12 列會差 0.2 格）：只用洞的 y 座標單獨估
    sy = s
    if len(holes) >= 5:
        yy_ = np.array([h[0][1] - 0.10 * s for h in holes])
        if yy_.max() - yy_.min() > 6 * s:
            def _coh(sc):
                ang = 2 * np.pi * yy_ / sc
                return float(np.hypot(np.cos(ang).mean(), np.sin(ang).mean()))
            cands = np.arange(0.95 * s, 1.035 * s, 0.1)
            sb = float(cands[int(np.argmax([_coh(c) for c in cands]))])
            if abs(sb - s) / s > 0.008 and _coh(sb) > _coh(s) + 0.02:
                sy = sb
    hx = float(np.median([h[0][0] % s for h in holes]))
    hy = float(np.median([(h[0][1] - 0.10 * s) % sy for h in holes]))
    # 修正 s：由寬度估 cols
    W = xmax - xmin + 1
    px = (hx - s / 2) % s
    py = (hy - sy / 2) % sy
    c0 = int(round((xmin - px) / s)); c1 = int(round((xmax - px) / s))
    r0 = int(round((ymin - py) / sy)); r1 = int(round((ymax - py) / sy))
    cols = force_cols or (c1 - c0)
    rows = force_rows or (r1 - r0)
    x0_, y0_ = px + c0 * s, py + r0 * sy
    ycen = None
    try:
        ycen = _perspective_rows(fl, xmin, xmax, ymin, ymax, holes, sy, y0_)
    except Exception:
        ycen = None
    if ycen is None and sy != s:
        ycen = [y0_ + (r + .5) * sy for r in range(rows)]
    if ycen is None:
        # 透視：上半部列距比下半部小（約 108px vs 113px）。固定格距在上方會偏 0.2 格 -> 用洞的實際位置擬合各列中心
        try:
            ycen = _hole_rows(holes, s, y0_, sy, rows) or ycen
        except Exception:
            pass
    if ycen is not None and not force_rows:
        rows = len(ycen)
    return x0_, y0_, s, cols, rows, ycen


def _hole_rows(holes, s, y0, sy, rows):
    if len(holes) < 5:
        return None
    pts = []
    for h in holes:
        tc = float(h[0][1] - 0.10 * s)
        r = int(round((tc - (y0 + .5 * sy)) / sy))
        if 0 <= r < rows:
            pts.append((r, tc))
    rs = sorted({p[0] for p in pts})
    if len(rs) < 4:
        return None
    R = np.array([p[0] for p in pts], float); Y = np.array([p[1] for p in pts])
    uni = np.abs(y0 + (R + .5) * sy - Y).max()
    if uni < 0.10 * s:
        return None          # 固定格距已經夠準，不動
    deg = 2 if len(rs) >= 6 else 1
    co = np.polyfit(R, Y, deg)
    res = np.abs(np.polyval(co, R) - Y).max()
    if res > 0.6 * uni or res > 0.10 * s:
        return None
    return [float(np.polyval(co, r)) for r in range(rows)]


def _perspective_rows(fl, xmin, xmax, ymin, ymax, holes, s, y0):
    """遊戲畫面有輕微透視：越下面的列越高（約 105px → 117px）。列數多的關卡用固定格距會讓
    上半部整個錯位。這裡用「地板格線」量出每列的真實位置；量不到或固定格距就夠準時回傳 None。"""
    sub = fl[:, xmin:xmax + 1].sum(1).astype(float)
    k5 = np.ones(5) / 5.0
    sm = np.convolve(sub, k5, 'same')
    base = float(np.median(sm[ymin + 10:ymax - 10])) if ymax - ymin > 40 else 0.0
    if base <= 0:
        return None
    # 從棋盤上緣往下一條一條找格線(局部最小值)，間距需落在 0.8~1.15 格
    edges = [float(ymin)]
    pitch = 0.92 * s
    while True:
        lo = int(edges[-1] + 0.80 * pitch); hi = int(edges[-1] + 1.20 * pitch)
        if hi >= ymax - 5 or lo >= len(sm):
            break
        seg = sm[lo:hi]
        j = int(np.argmin(seg))
        # 必須是明顯的凹谷：比兩側的最大值低，且比一般地板列少很多
        if seg[j] > 0.80 * base or j in (0, len(seg) - 1):
            break
        edges.append(float(lo + j))
        pitch = edges[-1] - edges[-2]
    if len(edges) < 5:
        return None
    # 固定格距若已經對得上，就沿用舊方法（避免動到已經正常的關卡）
    err = max(abs(e - (y0 + ((round((e - y0) / s))) * s)) for e in edges)
    if err < 0.12 * s:
        return None
    n_top = len(edges) - 1
    rr = np.arange(len(edges), dtype=float)
    # 先用上緣格線擬合 (二次)，外推出洞所在的列，再把洞也納入重新擬合
    deg = 2 if len(edges) >= 6 else 1
    ce = np.polyfit(rr, np.array(edges), deg)
    cen_r = [(r + 0.5, float((np.polyval(ce, r) + np.polyval(ce, r + 1)) / 2)) for r in range(n_top)]
    pts = list(cen_r)
    for h in holes:
        tc = float(h[0][1] - 0.10 * s)
        best = None
        for r in range(0, 40):
            c_ = float((np.polyval(ce, r) + np.polyval(ce, r + 1)) / 2)
            if best is None or abs(c_ - tc) < best[0]:
                best = (abs(c_ - tc), r)
        if best[0] < 0.30 * s:
            pts.append((best[1] + 0.5, tc))
    xs_ = np.array([p[0] for p in pts]); ys_ = np.array([p[1] for p in pts])
    cc = np.polyfit(xs_, ys_, deg)          # 列「中心」隨列號 (r+0.5) 的函數
    res = np.abs(np.polyval(cc, xs_) - ys_)
    if res.max() > 0.18 * s:
        return None
    # 列數：由棋盤下緣 (最後一列的下邊界) 決定
    def lower_edge(r):
        half = (np.polyval(cc, r + 1.5) - np.polyval(cc, r - 0.5)) / 4.0
        return float(np.polyval(cc, r + 0.5) + half)
    rows = min(range(1, 40), key=lambda n: abs(lower_edge(n - 1) - ymax))
    return [float(np.polyval(cc, r + 0.5)) for r in range(rows)]


def ring_color(rgb, cx, cy, s, frozen=False, ret_n=False):
    """洞外圈顏色：環形取樣的中位數（排除地板、黑洞心、白字、冰藍）"""
    pts = []
    for ang in range(0, 360, 15):
        for rr in (0.33, 0.38, 0.43):
            x = int(cx + rr * s * np.cos(np.radians(ang)))
            y = int(cy + rr * s * np.sin(np.radians(ang)))
            if not (0 <= y < rgb.shape[0] and 0 <= x < rgb.shape[1]):
                continue
            c = med(rgb, x, y, 3).astype(int)
            if abs(c - FLOOR).max() < 14 or c.max() < 70 and not frozen or c.min() > 225:
                continue
            if frozen and (c[2] > c[0] + 60 and c[2] > 170):   # 冰藍
                continue
            pts.append(c)
    if len(pts) < 5:
        return (np.array([255, 255, 255]), len(pts)) if ret_n else np.array([255, 255, 255])
    return (np.median(pts, 0), len(pts)) if ret_n else np.median(pts, 0)


def _central_white(white, r):
    """只保留與格子中央重疊的白色連通區（數字本體）；冰霜/反光的白點在邊緣，會被濾掉。"""
    n, lab_, st, _c = cv2.connectedComponentsWithStats((white > 0).astype(np.uint8), connectivity=8)
    q = max(3, int(0.45 * r))
    out = np.zeros_like(white)
    for i in range(1, n):
        x, y, w_, h_, a = st[i]
        if a < 12:
            continue
        if x < r + q and x + w_ > r - q and y < r + q and y + h_ > r - q:
            out[lab_ == i] = 255
    return out if out.any() else white


def read_digit(rgb, cx, cy, s):
    """讀冰塊上的數字 (1~9)。找得到 pytesseract 就用，否則回傳 None (視為未知)。"""
    r = int(s * 0.42)
    crop = rgb[cy - r:cy + r, cx - r:cx + r]
    white = ((crop.min(2) > 225)).astype(np.uint8) * 255
    white = _central_white(white, r)
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


def _metal(b, rgb, center, s):
    """鑰匙 / 鎖鏈：銀色(藍白)與金色(黃)像素比例。被鎖鏈蓋住的格子，顏色改取「扣掉金屬」後的身體色，
    這樣鎖鏈不會把壁虎切成好幾段。"""
    h = int(.34 * s)
    b.sil, b.gold = {}, {}
    fix = {}
    for cell, k in b.cell.items():
        if k != "gecko":
            continue
        cx, cy = center(*cell)
        p = rgb[max(0, cy - h):cy + h, max(0, cx - h):cx + h].reshape(-1, 3).astype(int)
        if len(p) == 0:
            continue
        R, G, B = p[:, 0], p[:, 1], p[:, 2]
        sil = (B >= 232) & (R >= 165) & (R <= 228) & (G >= 195) & (B - R >= 18)
        gold = (R > 225) & (G > 175) & (G < 240) & (B < 110)
        b.sil[cell] = float(sil.mean()); b.gold[cell] = float(gold.mean())
        mx, mn = p.max(1), p.min(1)
        sat = (mx - mn) > 0.45 * np.maximum(mx, 1)
        body = sat & (mx > 110)
        if b.sil[cell] >= 0.03:
            ok = body & ~sil
        elif 0.08 <= b.gold[cell] < 0.9 and b.ccol[cell][1] < 190:
            ok = body & ~gold
        else:
            continue
        if ok.mean() >= 0.25:
            q = p[ok]
            hh = cv2.cvtColor(q.astype(np.uint8).reshape(-1, 1, 3), cv2.COLOR_RGB2HSV).reshape(-1, 3)[:, 0].astype(int)
            # 鏈條的橘褐色邊緣會污染中位數：取「色相出現最多」的那一群
            binid = ((hh + 6) % 180) // 12
            top = np.bincount(binid, minlength=15).argmax()
            sel = binid == top
            fix[cell] = np.median(q[sel], 0)
        else:
            fix[cell] = None
    for cell, col in fix.items():
        if col is not None:
            b.ccol[cell] = col
    for cell, col in fix.items():
        if col is None:       # 幾乎被鎖頭蓋住：借用相鄰（同樣被鎖鏈蓋住或最近）的身體色
            nb = [(cell[0] + d[0], cell[1] + d[1]) for d in DIRS]
            nb = [x for x in nb if b.cell.get(x) == "gecko" and fix.get(x, 0) is not None]
            nb.sort(key=lambda x: (x not in fix))
            if nb:
                b.ccol[cell] = b.ccol[nb[0]]


def parse(rgb, force_cols=None, force_rows=None):
    if 'g' in GRID_CACHE:
        x0, y0, s, cols, rows, ycen = GRID_CACHE['g']
    else:
        x0, y0, s, cols, rows, ycen = detect_grid(rgb, force_cols, force_rows)
        GRID_CACHE['g'] = (x0, y0, s, cols, rows, ycen)
    b = Board()
    b.x0, b.y0, b.s, b.cols, b.rows = x0, y0, s, cols, rows
    b.cell = {}   # (r,c) -> kind
    b.ccol = {}   # (r,c) -> rgb
    b.hole = {}   # (r,c) -> rgb ring color
    b.digit = {}  # (r,c) -> 數字 or None
    cs = int(0.14 * s)

    def center(r, c):
        if ycen is not None and 0 <= r < len(ycen):
            return int(x0 + (c + .5) * s), int(ycen[r])
        if ycen is not None:   # 超出範圍：用最後的格距外推
            d = (ycen[-1] - ycen[-2]) if len(ycen) > 1 else s
            return int(x0 + (c + .5) * s), int(ycen[-1] + (r - len(ycen) + 1) * d) if r >= len(ycen) else int(ycen[0] + r * d)
        return int(x0 + (c + .5) * s), int(y0 + (r + .5) * s)

    b.center = center
    # 洞 = 近似圓形的深色塊（黑色外框的壁虎是細長的，排除）
    dk_all = (rgb.max(2) < 60).astype(np.uint8)
    nn, _l, st_, cen_ = cv2.connectedComponentsWithStats(dk_all)
    hole_cells = set()
    for i in range(1, nn):
        w_, h_, a_ = st_[i, 2], st_[i, 3], st_[i, 4]
        if 0.40 * s < w_ < 0.66 * s and 0.36 * s < h_ < 0.62 * s and a_ > 0.62 * w_ * h_ and 0.75 < w_ / h_ < 1.4:
            hc = int((cen_[i][0] - x0) // s)
            if ycen is not None:
                hr = int(np.argmin(np.abs(np.array(ycen) - (cen_[i][1] - 0.10 * s))))
            else:
                hr = int((cen_[i][1] - 0.10 * s - y0) // s)
            hole_cells.add((hr, hc))
    for r in range(rows):
        for c in range(cols):
            cx, cy = center(r, c)
            m = med(rgb, cx, cy, int(.17 * s))
            yy, xx = np.ogrid[:rgb.shape[0], :rgb.shape[1]]
            patch = rgb[cy + int(.10 * s) - int(.13 * s):cy + int(.10 * s) + int(.13 * s), max(0, cx - int(.15 * s)):cx + int(.15 * s)]
            dark_frac = float((patch.max(2) < 60).mean())
            corners = np.median([med(rgb, int(cx + dx * .40 * s), int(cy + dy * .40 * s), 5)
                                 for dx in (-1, 1) for dy in (-1, 1)], 0)
            if (r, c) in hole_cells:
                b.cell[(r, c)] = "hole"
                b.hole[(r, c)] = ring_color(rgb, cx, cy, s)
            elif cdist(corners, VOID) < 8 and cdist(m, VOID) < 8:
                b.cell[(r, c)] = "void"
            elif corners[2] > 210 and corners[1] > 170 and corners[0] < 150 and cdist(corners, FLOOR) > 17 \
                    and cdist(m, FLOOR) > 14 \
                    and (m.min() > 70 or (rgb[cy - cs:cy + cs, cx - cs:cx + cs].min(2) > 225).mean() > 0.12):
                # (青色外框的壁虎在轉彎處，四角會是青色而被誤認成冰；但它的中心是飽和的黃/青，最小色階很低，
                #  冰格的中心則是淡色或有白色數字)
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
    _metal(b, rgb, center, s)
    b.fhole = {}
    ICE_ = np.array([40, 214, 253])

    def _tint(r, c):
        cx, cy = center(r, c); h = int(.42 * s)
        p = rgb[cy - h:cy + h, cx - h:cx + h].reshape(-1, 3).astype(int)
        icep = (p[:, 2] > 200) & (p[:, 2] > p[:, 0] + 45)
        keep = ~(icep | (p.min(1) > 215) | (p.max(1) < 90))
        if keep.sum() < 30:
            return None
        return np.median(p[keep], 0)
    tints = {c: _tint(*c) for c, k in b.cell.items() if k == "ice"}
    for c, dg in list(b.digit.items()):
        if b.cell.get(c) != "ice" or tints.get(c) is None:
            continue
        sim = False
        for d in DIRS:
            nb = (c[0] + d[0], c[1] + d[1])
            if b.cell.get(nb) == "ice" and tints.get(nb) is not None and cdist(tints[nb], tints[c]) < 26:
                sim = True
        if not sim:   # 沒有同色的冰格相連 -> 這是「冰封的洞」，不是冰凍壁虎
            cx_, cy_ = center(*c)
            rc, npt = ring_color(rgb, cx_, cy_, s, frozen=True, ret_n=True)
            if npt < 25:
                continue
            rc = rc.astype(float)
            col = np.clip((rc - 0.25 * ICE_) / 0.75, 0, 255)
            b.cell[c] = "hole"; b.hole[c] = col; b.fhole[c] = dg
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


def _outline(b, comp):
    """身體格子外緣（沒有同隻鄰格的那一側）的顏色，用來判斷是不是雙色壁虎"""
    pts = []
    cs = set(comp)
    for (r, c) in comp:
        cx, cy = b.center(r, c)
        for d in DIRS:
            if (r + d[0], c + d[1]) in cs:
                continue
            for f_ in (.28, .32, .36):
                p_ = med(b.rgb, int(cx + d[1] * f_ * b.s), int(cy + d[0] * f_ * b.s), 2)
                if cdist(p_, FLOOR) > 14:
                    pts.append(p_)
    return np.median(pts, 0) if pts else None


def _merge_blink(b, comps):
    """頭部眨眼/表情動畫會讓端點格顏色偏暗，被切成獨立單格：
    把「顏色接近、且正好接在某條壁虎端點」的單格併回去（雙色壁虎的外框色差很大，不受影響）。"""
    comps = [list(c) for c in comps]
    for sc in [c for c in comps if len(c) == 1]:
        cell = sc[0]
        best = None
        for big in comps:
            if big is sc or len(big) < 2:
                continue
            cs = set(big)
            for d in DIRS:
                nb = (cell[0] + d[0], cell[1] + d[1])
                if nb in cs and sum(1 for e in DIRS if (nb[0] + e[0], nb[1] + e[1]) in cs) == 1:
                    # 只比色相(Lab 的 a,b)：眨眼只會變暗，色相差約 15；雙色壁虎的外框色色相差 >= 43
                    la = lab(b.ccol[cell]); lb = lab(np.median([b.ccol[c] for c in big], 0))
                    dist = float(np.linalg.norm(la[1:] - lb[1:]))
                    if dist < 24 and (best is None or dist < best[0]):
                        best = (dist, big)
        if best:
            best[1].append(cell); sc.clear()
    return [c for c in comps if c]


def _lock_key(b, cells):
    """回傳 (lock, key)：'silver'/'gold'/None。鎖 = 身上有 >=2 格被鏈條(金屬色)蓋住；鑰匙 = 只有 1 格小面積金屬。"""
    sil = [b.sil.get(c, 0) for c in cells]
    gold = [b.gold.get(c, 0) for c in cells]
    col = np.mean([b.ccol[c] for c in cells], 0)
    if (sum(1 for v in sil if v >= 0.10) >= 2) or (sum(1 for v in sil if v >= 0.05) >= 2 and max(sil) >= 0.15):
        return "silver", None
    if col[1] < 185:     # 金色只在「非黃色」壁虎上判斷（黃色壁虎本身就是金色）
        if sum(1 for v in gold if v >= 0.10) >= 2:
            return "gold", None
    if any(v >= 0.03 for v in sil):
        return None, "silver"
    if col[1] < 185 and any(v >= 0.08 for v in gold):
        return None, "gold"
    return None, None


def _split_caps(b, comps):
    """雙色壁虎的頭/尾外框色有時佔 2 格（在彎角）而被併成 2 格的小壁虎：
    若這個 2 格小塊接在某條 >=3 格壁虎的端點、色相差很大，且那條壁虎的另一端也接著顏色相近的小塊，就拆成單格，
    交給後面的雙色壁虎邏輯處理。"""
    def hue_d(c1, c2):
        return float(np.linalg.norm(lab(c1)[1:] - lab(c2)[1:]))
    def endpoints(big):
        cs = set(big)
        return [c for c in big if sum(1 for d in DIRS if (c[0] + d[0], c[1] + d[1]) in cs) == 1]
    out = [list(c) for c in comps]
    for sm in [c for c in out if len(c) == 2]:
        scol = np.median([b.ccol[c] for c in sm], 0)
        for big in out:
            if big is sm or len(big) < 2:
                continue
            bcol = np.median([b.ccol[c] for c in big], 0)
            if hue_d(scol, bcol) < 28:
                continue
            eps = endpoints(big)
            if len(eps) != 2:
                continue
            touch = [e for e in eps if any((e[0] + d[0], e[1] + d[1]) in sm for d in DIRS)]
            if len(touch) != 1:
                continue
            other = [e for e in eps if e != touch[0]][0]
            ok = False
            for o in out:
                if o is big or o is sm or len(o) > 2:
                    continue
                if any((other[0] + d[0], other[1] + d[1]) in o for d in DIRS) and \
                        hue_d(np.median([b.ccol[c] for c in o], 0), scol) < 45:
                    ok = True
            if ok:
                out.remove(sm)
                out.extend([[c] for c in sm])
                break
    return out


def _split_locked(b, comps, same):
    """兩隻同色壁虎並排、其中一隻被鎖鏈蓋住時，中間的縫被鏈條遮住而併成一塊（出現分岔/2x2）。
    有鏈條的格子排成一直線 -> 那條直線上連續的格子是上鎖的壁虎，其餘的另成一塊。"""
    out = []
    for comp in comps:
        cs = set(comp)
        deg = {c: sum(1 for d in DIRS if (c[0] + d[0], c[1] + d[1]) in cs) for c in comp}
        block = any((r + 1, c) in cs and (r, c + 1) in cs and (r + 1, c + 1) in cs for (r, c) in comp)
        if len(comp) < 5 or not (block or max(deg.values()) >= 3):
            out.append(comp); continue
        fl = [c for c in comp if b.sil.get(c, 0) >= 0.05 or b.gold.get(c, 0) >= 0.10]
        if len(fl) < 2:
            out.append(comp); continue
        run = None
        if len({c[1] for c in fl}) == 1:
            col = fl[0][1]
            r0 = min(c[0] for c in fl); r1 = max(c[0] for c in fl)
            while (r0 - 1, col) in cs: r0 -= 1
            while (r1 + 1, col) in cs: r1 += 1
            run = [(r, col) for r in range(r0, r1 + 1)]
        elif len({c[0] for c in fl}) == 1:
            row = fl[0][0]
            c0 = min(c[1] for c in fl); c1 = max(c[1] for c in fl)
            while (row, c0 - 1) in cs: c0 -= 1
            while (row, c1 + 1) in cs: c1 += 1
            run = [(row, c) for c in range(c0, c1 + 1)]
        if not run or len(run) < 2 or len(run) >= len(comp):
            out.append(comp); continue
        out.append(run)
        rest = [c for c in comp if c not in set(run)]
        # 剩下的用「不看縫隙」的相鄰關係重新分塊
        seen = set()
        for c in rest:
            if c in seen:
                continue
            piece, st = [], [c]; seen.add(c)
            while st:
                u = st.pop(); piece.append(u)
                for d in DIRS:
                    v = (u[0] + d[0], u[1] + d[1])
                    if v in rest and v not in seen:
                        seen.add(v); st.append(v)
            out.append(piece)
    return out


def _linked(b, u, v):
    """相鄰兩格的中心連線上若露出地板（壁虎身體比格子細，兩隻同色壁虎並排時中間會有縫），就不是同一隻。"""
    (x1, y1), (x2, y2) = b.center(*u), b.center(*v)
    gap = 0
    for k in range(1, 10):
        f = k / 10.0
        px = med(b.rgb, int(x1 + (x2 - x1) * f), int(y1 + (y2 - y1) * f), 2)
        if cdist(px, FLOOR) < 9:
            gap += 1
    return gap < 2


def build_state(b):
    """回傳 dict: geckos(list), holes, static_block, frozen(list)"""
    geckos, frozen = [], []
    gcells = [c for c, k in b.cell.items() if k == "gecko"]

    def same(u, v):
        return cdist(b.ccol[u], b.ccol[v]) < 22 and _linked(b, u, v)
    comps = _split_locked(b, components(gcells, same), same)
    comps = _merge_blink(b, comps)
    comps = _split_caps(b, comps)
    singles = [list(c)[0] for c in comps if len(c) == 1]
    big = [c for c in comps if len(c) >= 2]
    taken = set()
    # 雙色壁虎：身體外緣顏色與身體不同 -> 兩端各有一個「外框色」的端點格
    info = []
    for comp in big:
        body = np.median([b.ccol[c] for c in comp], 0)
        ol = _outline(b, comp)
        two = ol is not None and cdist(ol, body) > 45
        yel = two and ol[0] > 200 and ol[1] > 185 and ol[2] < 120      # 黃色外框：金色像素是外框，不是鎖鏈
        if two and not yel and _lock_key(b, comp)[0] is not None:
            two = False
        info.append([comp, body, ol, two, [], set()])
    # 補強：外框色沒抓準時，改看「路徑兩端各接一個顏色明顯不同、彼此相近的單格」
    for it in info:
        comp, body, ol, two = it[0], it[1], it[2], it[3]
        if two or len(comp) < 2:
            continue
        deg = {c: sum(1 for d in DIRS if (c[0] + d[0], c[1] + d[1]) in comp) for c in comp}
        endpts = [c for c in comp if deg[c] == 1]
        if len(endpts) != 2:
            continue
        found = []
        for e in endpts:
            cand = [(e[0] + d[0], e[1] + d[1]) for d in DIRS]
            cand = [x for x in cand if x in singles and cdist(b.ccol[x], body) > 35]
            if len(cand) == 1:
                found.append(cand[0])
        if len(found) == 2 and found[0] != found[1] and cdist(b.ccol[found[0]], b.ccol[found[1]]) < 40:
            it[3] = True
            it[2] = np.median([b.ccol[x] for x in found], 0)
    for it in info:
        comp, body, ol, two, caps, extra = it
        if not two:
            continue
        ends = set()
        for (r, c) in comp:
            for d in DIRS:
                nb = (r + d[0], c + d[1])
                if nb in singles and nb not in taken:
                    ends.add(nb)
        for nb in sorted(ends, key=lambda nb: cdist(b.ccol[nb], ol)):
            if (cdist(b.ccol[nb], ol) < 60 or cdist(b.ccol[nb], body) > 60) and len(caps) < 2:
                caps.append(nb)
        taken.update(caps)
        # 端點在「彎角」時，外框色可能連續佔 2 格：把緊接在端點後、同色的單格併進來，端點改成最外那格
        new_caps = []
        for cp in caps:
            end, chain = cp, []
            for _ in range(2):
                nxt = [(end[0] + d[0], end[1] + d[1]) for d in DIRS]
                nxt = [x for x in nxt if x in singles and x not in taken and x not in chain and x not in comp
                       and cdist(b.ccol[x], b.ccol[cp]) < 45]
                if len(nxt) != 1:
                    break
                chain.append(nxt[0]); end = nxt[0]; taken.add(end)
            if chain:
                extra.update([cp] + chain[:-1]); new_caps.append(chain[-1])
            else:
                new_caps.append(cp)
        caps[:] = new_caps
    for comp, body, ol, two, caps, extra in info:
        full = set(comp) | set(caps) | extra
        if len(full) < 2:
            continue
        if caps:
            paths = []
            for st in caps:
                paths += [p for p in ham_paths(full, st) if p[-1] in caps]
            paths = [p for p in paths if p[0] in caps and p[-1] in caps]
            paths = paths[:1] if paths else ham_paths(full, caps[0])
            head = caps[0]
        else:
            paths = []
            for st_ in sorted(full, key=lambda c: -eye_score(b, c)):
                paths = ham_paths(full, st_)
                if paths:
                    break
        if not paths:
            continue
        if len(paths) > 1 and not caps:
            paths.sort(key=lambda p: -arrow_score(b, p[-1]))
        col = np.median([b.ccol[c] for c in comp], 0).astype(int)
        g = dict(cells=paths[0], color=col)
        g["lock"], g["key"] = _lock_key(b, list(full))
        if caps:
            g["color_a"] = np.median([b.ccol[c] for c in caps], 0).astype(int)
            g["two"] = True
            ca = g["color_a"]
            if ca[0] > 200 and ca[1] > 185 and ca[2] < 120:    # 黃色外框
                if g["lock"] == "gold": g["lock"] = None
                if g["key"] == "gold": g["key"] = None
        geckos.append(g)
    icec = [c for c, k in b.cell.items() if k == "ice"]
    for comp in components(icec, lambda u, v: True):
        dig = [c for c in comp if c in b.digit]
        head = dig[0] if dig else comp[0]
        paths = ham_paths(comp, head)
        cells = paths[0] if paths else tuple(comp)
        nd = [c for c in comp if c not in b.digit] or comp
        tint = np.mean([med(b.rgb, *b.center(*c)[:2], int(.15 * b.s)) for c in nd], 0)
        frozen.append(dict(cells=cells, count=b.digit.get(head), color=None, tint=tint))
    holes = [dict(cell=c, color=col, iced=(c in b.fhole)) for c, col in b.hole.items()]
    return geckos, frozen, holes


def match_targets(geckos, frozen, holes):
    """每隻壁虎配對同色洞（同色多個洞可互換）。回傳每隻壁虎可進入的洞格集合。"""
    hc = [(h["cell"], h["color"]) for h in holes]
    iced = {h["cell"] for h in holes if h.get("iced")}
    ICED_TOL = 32   # 冰封的洞顏色是「扣掉冰色」估出來的，誤差較大，容許度放寬

    def tg(col):
        ds = sorted(hc, key=lambda h: cdist(col, h[1]))
        thr = cdist(ds[0][1], col) + 18
        out = {h[0] for h in ds if cdist(h[1], col) <= thr and cdist(h[1], col) < 75}
        for h in ds:
            if h[0] in iced and cdist(h[1], col) < ICED_TOL:
                out.add(h[0])
        return out
    for g in geckos:
        if g.get("two"):
            g["targets"] = tg(g["color_a"]); g["targets2"] = tg(g["color"])
        else:
            g["targets"] = tg(g["color"]); g["targets2"] = set()
    # 洞分組：一般洞先依顏色分組；冰封洞（顏色估計較不準）併入「最近的」一般洞組，
    # 但若離某隻壁虎的顏色更近、就自成一組（例如粉紅冰封洞不該併進紫色組）
    gcols = []
    for g in geckos:
        gcols.append(g["color"])
        if g.get("color_a") is not None:
            gcols.append(g["color_a"])
    groups = []
    for h, col in [x for x in hc if x[0] not in iced]:
        for gr in groups:
            if cdist(gr["color"], col) < 20:
                gr["cells"].add(h); break
        else:
            groups.append(dict(color=col, cells={h}))
    for h, col in [x for x in hc if x[0] in iced]:
        dg = [(cdist(gr["color"], col), gr) for gr in groups]
        dgk = min((cdist(gc_, col) for gc_ in gcols), default=999)
        best = min(dg, key=lambda t_: t_[0]) if dg else None
        if best is not None and best[0] < ICED_TOL and best[0] <= dgk + 6:
            best[1]["cells"].add(h)
        else:
            groups.append(dict(color=col, cells={h}))
    # 同一隻壁虎（同一階段）的目標若跨了好幾組，代表它們其實是同色 -> 合併
    for g in geckos:
        for key in ("targets", "targets2"):
            tset = g[key]
            hit = [gr for gr in groups if gr["cells"] & tset]
            if len(hit) > 1:
                keep = hit[0]
                for gr in hit[1:]:
                    keep["cells"] |= gr["cells"]
                    groups[:] = [x for x in groups if x is not gr]   # 用身分比較（字典裡有 numpy 陣列，不能用 ==）
    need = {id(gr): len(gr["cells"]) for gr in groups}
    for g in geckos:
        for key in ("targets", "targets2"):
            if g[key]:
                for gr in groups:
                    if g[key] & gr["cells"]:
                        need[id(gr)] -= 1
                        break
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
        self.fl = sorted(getattr(b, "fhole", {}).items())   # 冰封的洞 [(cell, count)]

    def any_goto(self, state):
        """有沒有任何壁虎現在能直接進洞？(只回答是/否，找到就立刻返回，比 goto_hole 快很多)"""
        for i, cfg in enumerate(state[0]):
            if not self.mobile(state, i):
                continue
            blocked = self.blocked(state, i)
            targets = self.all_targets(i, state)
            for ori in (tuple(cfg), tuple(cfg)[::-1]):
                if self._reaches(ori, blocked, targets, state[2]):
                    return True
        return False

    def _flood_ok(self, head, blocked, targets, used):
        """粗篩：把壁虎當成一個點，從頭部走得到目標洞嗎？走不到就一定進不去（不必做昂貴的整條身體搜尋）。"""
        seen = {head}
        st = [head]
        while st:
            cr, cc = st.pop()
            for d in DIRS:
                n = (cr + d[0], cc + d[1])
                if n in seen or n not in self.floorlike or n in blocked:
                    continue
                if n in targets and n not in used:
                    return True
                if n in self.holes and n not in used:
                    continue
                seen.add(n); st.append(n)
        return False

    def _reaches(self, cfg, blocked, targets, used):
        if not self._flood_ok(tuple(cfg)[0], blocked, targets, used):
            return False
        start = tuple(cfg)
        seen = {start}
        dq = deque([start])
        while dq:
            cur = dq.popleft()
            head = cur[0]
            for d in DIRS:
                n = (head[0] + d[0], head[1] + d[1])
                if n not in self.floorlike or n in blocked:
                    continue
                if n in targets and n not in used:
                    return True
                if n in self.holes and n not in used:
                    continue
                if n in cur[:-1]:
                    continue
                nxt = (n,) + cur[:-1]
                if nxt in seen:
                    continue
                seen.add(nxt)
                if len(seen) > 4000:
                    break
                dq.append(nxt)
        return False

    def moves_bfs(self, cfg, blocked, targets, used=frozenset(), maxn=4000):
        return self._moves_bfs(cfg, blocked, targets, used, maxn)

    def _moves_bfs(self, cfg, blocked, targets, used=frozenset(), maxn=4000):
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
                if n in blocked:
                    continue
                if n in targets and n not in used:
                    if goal is None:
                        goal = (cur, n)
                    continue
                if n in self.holes and n not in used:
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
        cnt = tuple((f["count"] if f["count"] else 99) for f in self.frozen) + \
            tuple((c if c else 99) for _cell, c in self.fl)
        return cfgs, cnt, frozenset()

    # 把冰凍壁虎也當成一般壁虎 (index 偏移)，解凍後才可動
    def all_targets(self, i, state=None):
        n = len(self.geckos)
        if i < n:
            g = self.geckos[i]
            if g.get("two") and state is not None and ("p", i) in state[2]:
                return g["targets2"]
            return g["targets"]
        return self.frozen[i - n]["targets"]

    def is_phase0(self, state, i):
        return i < len(self.geckos) and self.geckos[i].get("two") and ("p", i) not in state[2]

    def mobile(self, state, i):
        n = len(self.geckos)
        if state[0][i] is None:
            return False
        if i < n:
            lk = self.geckos[i].get("lock")
            if lk:
                need = sum(1 for g in self.geckos if g.get("key") == lk)   # 實機驗證：一把鑰匙不夠，保守起見要全部同色鑰匙壁虎進洞
                got = sum(1 for u in state[2] if isinstance(u, tuple) and len(u) == 3 and u[0] == "u" and u[1] == lk)
                if got < need:
                    return False      # 上鎖
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
        nf = len(self.frozen)
        for k, (cell, _cnt) in enumerate(self.fl):
            if state[1][nf + k] is not None:      # 冰封的洞：倒數到 0 才能進
                blk.add(cell)
        return blk

    def static_blocked(self, state):
        blk = set()
        for k, f in enumerate(self.frozen):
            if state[1][k] is not None:
                blk.update(f["cells"])
        nf = len(self.frozen)
        for k, (cell, _c) in enumerate(self.fl):
            if state[1][nf + k] is not None:
                blk.add(cell)
        return blk

    def goto_hole(self, state):
        """回傳可直接進洞的 (idx, path) 清單。任一端都可以當領頭(拖曳的那一端)。"""
        res = []
        for i, cfg in enumerate(state[0]):
            if not self.mobile(state, i):
                continue
            best = None
            for ori in (tuple(cfg), tuple(cfg)[::-1]):
                goal, prev = self.moves_bfs(ori, self.blocked(state, i), self.all_targets(i, state), state[2])
                if goal:
                    p = self.head_path(prev, goal[0], goal[1])
                    if p[0] in self.banned:
                        continue
                    if best is None or len(p) < len(best[0]):
                        best = (p, goal[0])
            if best:
                res.append((i, best[0], best[1]))
        return res

    def apply_remove(self, state, i, hole=None, endcfg=None):
        cfgs = list(state[0])
        used = state[2] | ({hole} if hole else set())
        if i < len(self.geckos) and self.geckos[i].get("key") and not (self.is_phase0(state, i) and endcfg is not None):
            used = used | {("u", self.geckos[i]["key"], i)}      # 鑰匙壁虎完全進洞 -> 計入解鎖
        if self.is_phase0(state, i) and endcfg is not None:
            cfgs[i] = canon(endcfg)          # 外層進洞後，內層壁虎留在原地繼續
            used = used | {("p", i)}
        else:
            cfgs[i] = None
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
        seg = [0]    # 每一段(兩次進洞之間)可用的移位次數
        t_end = time.time() + time_limit

        def done(st):
            return all(c is None for c in st[0])

        def key(st):
            return st

        def dfs(st, repos_left, plan, sts):
            nodes[0] += 1
            rem = sum(1 for c in st[0] if c is not None) + sum(1 for g_ in self.geckos if False)
            if rem < self.bestrem[0]:
                self.bestrem[0] = rem
                self.beststate = (st, list(plan))
            if done(st):
                return (plan, sts)
            if nodes[0] > node_cap or time.time() > t_end:
                return None
            if (st, repos_left) in seen:
                return None
            seen.add((st, repos_left))
            gl = self.goto_hole(st)
            if gl:
                normal = [x for x in gl if not self.is_phase0(st, x[0])]
                if normal:
                    # 一般壁虎進洞一律是好事（只會讓出空間）；取第一個
                    i, path, ec = normal[0]
                    ns = self.apply_remove(st, i, path[-1], ec)
                    r = dfs(ns, seg[0], plan + [(i, path, "goal")], sts + [ns])
                    return r
                # 雙色壁虎的外層進洞：壁虎還留在場上，要分支嘗試
                for i, path, ec in gl:
                    ns = self.apply_remove(st, i, path[-1], ec)
                    r = dfs(ns, seg[0], plan + [(i, path, "goal")], sts + [ns])
                    if r is not None:
                        return r
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
            # 聚焦：每隻「想進洞卻被擋住」的壁虎，找出擋路的壁虎，只挑能讓出那條路的位置
            focus = set()
            for i in range(len(st[0])):
                if not self.mobile(st, i):
                    continue
                cfg = st[0][i]
                best_p = None
                for ori in (tuple(cfg), tuple(cfg)[::-1]):
                    goal, prev0 = self.moves_bfs(ori, self.static_blocked(st), self.all_targets(i, st), st[2])
                    if goal:
                        p0 = self.head_path(prev0, goal[0], goal[1])
                        if best_p is None or len(p0) < len(best_p):
                            best_p = p0
                if not best_p:
                    continue
                route = set(best_p)
                for j, cj in enumerate(st[0]):
                    if j == i or cj is None or not self.mobile(st, j) or not (set(cj) & route):
                        continue
                    ks = [(v[0], kf) for kf, v in uniq.items() if kf[0] == j and not (set(kf[1]) & route)]
                    ks.sort()
                    for _L, kf in ks[:14]:
                        focus.add(kf)
            good, other, foc = [], [], []
            for kf, (L, i, c, prev) in uniq.items():
                ns = self.apply_reposition(st, i, c)
                if kf in focus:
                    foc.append((L, i, c, prev, ns))
                else:
                    (good if self.any_goto(ns) else other).append((L, i, c, prev, ns))
            good.sort(key=lambda t_: t_[0]); other.sort(key=lambda t_: t_[0]); foc.sort(key=lambda t_: t_[0])
            for L, i, c, prev, ns in good + foc + (other[:20] if repos_left > 1 else []):
                r = dfs(ns, repos_left - 1, plan + [(i, self.head_path(prev, c), "repos")], sts + [ns])
                if r is not None:
                    return r
            return None

        self.partial = False
        self.states = []
        self.bestrem = [99]
        for rl in range(0, max_repos + 1):
            seen.clear(); nodes[0] = 0; seg[0] = rl
            r = dfs(init, rl, [], [])
            if r is not None:
                self.states = r[1]
                return r[0]
        # 找不到完整解（例如有尚未支援的機制）：至少做「現在就能進洞」的安全步驟
        gl = self.goto_hole(init)
        if gl:
            self.partial = True
            i, path, _ec = gl[0]
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
