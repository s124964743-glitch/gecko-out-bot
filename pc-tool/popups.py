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
    return None


def action_for(name):
    act, pos = ACTIONS[name]
    if name == 'ancient_list' and JOIN_EVENTS:
        return 'tap', (540, 1848)
    return act, pos


def scale_pos(pos, w, h):
    return int(pos[0] * w / REF_W), int(pos[1] * h / REF_H)
