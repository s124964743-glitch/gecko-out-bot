# -*- coding: utf-8 -*-
"""過關後的彈窗 / 活動頁處理：用模板辨識目前是哪個畫面，並回傳要點的座標。
座標皆為 1080x2448 解析度（ROG Phone 7）。其他解析度會自動等比例縮放。"""
import base64
import numpy as np
import cv2
from popup_templates import TEMPLATES

REF_W, REF_H = 1080, 2448
THRESH = 0.78

_T = {}
for _k, (_box, _b64) in TEMPLATES.items():
    _img = cv2.imdecode(np.frombuffer(base64.b64decode(_b64), np.uint8), cv2.IMREAD_GRAYSCALE)
    _T[_k] = (_box, _img)

# 畫面名稱 -> (動作, 座標)；動作 'tap' 點一下，'wait' 等待動畫
ACTIONS = {
    'welldone':      ('tap', (540, 1635)),   # 過關結算：點「40」(不看廣告的那顆)
    'newfeature':    ('tap', (540, 1757)),   # NEW FEATURE Unlocked：點青色「40」
    'island_dialog': ('tap', (984, 510)),    # Island Quest 邀請窗：右上 X
    'island_tap':    ('tap', (540, 1700)),   # Island Quest 說明頁：Tap to continue
    'island_find':   ('wait', None),         # 配對玩家中
    'ancient_board': ('tap', (540, 2265)),   # Ancient Climb 排名頁：Tap To Continue
    'ancient_list':  ('tap', (987, 495)),    # Ancient Climb 關卡列表：右上 X
    'level_dialog':  ('tap', (540, 1659)),   # Level N 選道具窗：PLAY
    'home':          ('tap', (540, 1850)),   # 主畫面：PLAY
    'searching':     ('wait', None),         # Searching for opponents
}
JOIN_EVENTS = False   # True 時 Ancient Climb 列表按 START 而不是 X


def classify(rgb):
    """rgb: HxWx3 (RGB)。回傳畫面名稱或 None。"""
    h, w = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if (w, h) != (REF_W, REF_H):
        gray = cv2.resize(gray, (REF_W, REF_H), interpolation=cv2.INTER_AREA)
    # 「Searching for opponents..」：暗藍背景上的白字（背景是會動的藤蔓，不適合用模板）
    reg = rgb if (w, h) == (REF_W, REF_H) else cv2.resize(rgb, (REF_W, REF_H), interpolation=cv2.INTER_AREA)
    r = reg[1750:1912, 292:800].astype(int)
    if (r.min(2) > 230).mean() > 0.10 and np.median(r.mean(2)) < 90:
        return 'searching'
    scores = {}
    for name, ((x0, y0, x1, y1), tpl) in _T.items():
        if name == 'searching':
            continue
        pad = 12
        X0, Y0 = max(0, x0 - pad), max(0, y0 - pad)
        X1, Y1 = min(REF_W, x1 + pad), min(REF_H, y1 + pad)
        reg = cv2.resize(gray[Y0:Y1, X0:X1], None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        if reg.shape[0] < tpl.shape[0] or reg.shape[1] < tpl.shape[1]:
            continue
        scores[name] = float(cv2.matchTemplate(reg, tpl, cv2.TM_CCOEFF_NORMED).max())
    hit = {k: v for k, v in scores.items() if v >= THRESH}
    # 「home」標籤在許多彈窗/讀取畫面下方也看得到，所以其他畫面優先
    others = {k: v for k, v in hit.items() if k != 'home'}
    if others:
        return max(others, key=others.get)
    if 'home' in hit and scores.get('searching', 0) < 0.5:
        return 'home'
    return _welldone_fallback(rgb, w, h)


def _welldone_fallback(rgb, w, h):
    """模板比對失敗時（結算畫面動畫中/有「New Feature」進度條等變體）用顏色判斷：
    上方有大片橘黃色「WELL DONE!」字，下方有青色「40」按鈕。"""
    img = rgb if (w, h) == (REF_W, REF_H) else cv2.resize(rgb, (REF_W, REF_H), interpolation=cv2.INTER_AREA)
    t = img[630:770, 40:1020].reshape(-1, 3).astype(int)
    orange = ((t[:, 0] > 230) & (t[:, 1] > 150) & (t[:, 1] < 235) & (t[:, 2] < 110)).mean()
    b = img[1560:1710, 360:720].reshape(-1, 3).astype(int)
    cyan = ((b[:, 0] < 90) & (b[:, 1] > 170) & (b[:, 2] > 200)).mean()
    if orange > 0.25 and cyan > 0.25:
        return 'welldone'
    t2 = img[330:470, 60:1020].reshape(-1, 3).astype(int)
    yel = ((t2[:, 0] > 230) & (t2[:, 1] > 150) & (t2[:, 1] < 240) & (t2[:, 2] < 120)).mean()
    b2 = img[1690:1830, 380:700].reshape(-1, 3).astype(int)
    cyan2 = ((b2[:, 0] < 90) & (b2[:, 1] > 170) & (b2[:, 2] > 200)).mean()
    if yel > 0.15 and cyan2 > 0.25:
        return 'newfeature'
    # 主畫面的「VERY HARD / HARD Level N」紅色大按鈕（模板只認得一般 PLAY）
    rb = img[1775:1960, 310:770].reshape(-1, 3).astype(int)
    red = ((rb[:, 0] > 200) & (rb[:, 1] < 90) & (rb[:, 2] < 100)).mean()
    sky = np.median(img[5:45, 300:800].reshape(-1, 3).astype(int), 0)
    if red > 0.30 and sky[0] < 140 and sky[2] > 200:
        return 'home'
    return None


def action_for(name):
    act, pos = ACTIONS[name]
    if name == 'ancient_list' and JOIN_EVENTS:
        return 'tap', (540, 1848)
    return act, pos


def scale_pos(pos, w, h):
    return int(pos[0] * w / REF_W), int(pos[1] * h / REF_H)


def tutorial_target(rgb, w, h):
    """新手教學遮罩：整個畫面被壓暗，只有要點的按鈕保持鮮豔明亮。回傳該按鈕中心 (x, y)（實際解析度），沒有教學遮罩則 None。"""
    img = rgb if (w, h) == (REF_W, REF_H) else cv2.resize(rgb, (REF_W, REF_H), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    V, S = hsv[..., 2], hsv[..., 1]
    if np.median(V[:2100]) > 100:
        return None
    m = ((V > 215) & (S > 110)).astype(np.uint8)
    m[2100:] = 0
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, _l, st, cen = cv2.connectedComponentsWithStats(m)
    best = None
    for i in range(1, n):
        if st[i, 4] > 3000 and (best is None or st[i, 4] > st[best, 4]):
            best = i
    if best is None:
        return None
    return scale_pos((float(cen[best][0]), float(cen[best][1])), w, h)
