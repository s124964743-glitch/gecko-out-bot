# -*- coding: utf-8 -*-
"""Gecko Out 機器人的大腦：每次給一張截圖，回傳下一個動作(JSON 字串)。

動作：
  {"a":"tap","x":..,"y":..,"sleep":秒,"msg":..}
  {"a":"drag","pts":[[x,y],...],"sleep":秒,"msg":..}
  {"a":"back","sleep":秒,"msg":..}
  {"a":"wait","sleep":秒,"msg":..}
"""
import json
import numpy as np
import cv2

import gecko_core as gc
import popups


class _Args:
    cols = None
    rows = None


class Brain:
    def __init__(self):
        self.join_events = False
        self.last_screen = None
        self.same_count = 0
        self.unknown = 0
        self.prev_small = None
        self.settle_wait = 0
        self.last_move = None
        self.move_repeat = 0
        self.moves = 0
        self.levels = 0

    def configure(self, join_events):
        popups.JOIN_EVENTS = bool(join_events)
        self.join_events = bool(join_events)

    # ------------------------------------------------------------
    def step(self, path, w, h):
        raw = np.fromfile(path, np.uint8).reshape(h, w, 4)
        rgb = np.ascontiguousarray(raw[:, :, :3])
        out = self._decide(rgb, w, h)
        return json.dumps(out, ensure_ascii=False)

    def _decide(self, rgb, w, h):
        scr = popups.classify(rgb)
        if scr:
            self.unknown = 0
            gc.GRID_CACHE.clear()          # 新關卡棋盤可能不同
            self.prev_small = None
            self.last_move = None
            if scr == 'welldone' and self.last_screen != 'welldone':
                self.levels += 1
            self.same_count = self.same_count + 1 if scr == self.last_screen else 0
            self.last_screen = scr
            act, pos = popups.action_for(scr)
            if act == 'wait':
                return {"a": "wait", "sleep": 1.0, "msg": f"[{scr}] 等待"}
            if self.same_count >= 5:
                # 點了好幾次畫面還沒換 -> 改按返回鍵
                self.same_count = 0
                return {"a": "back", "sleep": 1.2, "msg": f"[{scr}] 卡住，按返回鍵"}
            x, y = popups.scale_pos(pos, w, h)
            return {"a": "tap", "x": x, "y": y, "sleep": 1.3, "msg": f"[{scr}] 點 ({x},{y})"}
        self.last_screen = None
        self.same_count = 0

        # ---- 遊戲畫面？
        try:
            b, geckos, frozen, holes = gc.analyse(rgb, _Args)
        except Exception as e:
            b = None
        if b is not None and (geckos or frozen) and holes:
            # 等動畫停下來
            small = cv2.resize(rgb, (w // 8, h // 8), interpolation=cv2.INTER_AREA).astype(np.int16)
            if self.prev_small is not None and self.settle_wait < 4 and \
                    np.abs(small - self.prev_small).mean() > 1.2:
                self.prev_small = small
                self.settle_wait += 1
                return {"a": "wait", "sleep": 0.5, "msg": "等待動畫停止"}
            self.prev_small = small
            self.settle_wait = 0
            self.unknown = 0
            plan = gc.Solver(b, geckos, frozen, holes).solve()
            if not plan:
                return {"a": "wait", "sleep": 1.5, "msg": "找不到解，重新辨識中"}
            gi, path, kind = plan[0]
            pts = [list(b.center(r, c)) for r, c in path]
            sig = (tuple(map(tuple, pts)), tuple(sorted(c for c, k in b.cell.items() if k == "gecko")))
            self.move_repeat = self.move_repeat + 1 if sig == self.last_move else 0
            self.last_move = sig
            if self.move_repeat >= 3:
                self.move_repeat = 0
                self.last_move = None
                return {"a": "back", "sleep": 1.0, "msg": "同一步重複 4 次沒反應，按返回鍵"}
            self.moves += 1
            return {"a": "drag", "pts": pts, "sleep": 1.7,
                    "msg": f"第{self.moves}步 {kind} {path[0]}→{path[-1]}"}

        # ---- 不認得的畫面（讀取中、廣告…）
        self.unknown += 1
        self.prev_small = None
        n = self.unknown
        msg = f"未知畫面 {n}"
        if n in (10, 20, 30):
            return {"a": "back", "sleep": 1.0, "msg": msg + "：按返回鍵", "unknown": True}
        if n in (14, 24, 34):
            x, y = popups.scale_pos((1015, 120), w, h)
            return {"a": "tap", "x": x, "y": y, "sleep": 1.0, "msg": msg + "：點右上角關閉", "unknown": True}
        if n in (16, 26, 36):
            x, y = popups.scale_pos((70, 120), w, h)
            return {"a": "tap", "x": x, "y": y, "sleep": 1.0, "msg": msg + "：點左上角關閉", "unknown": True}
        return {"a": "wait", "sleep": 1.0, "msg": msg, "unknown": n in (3, 12)}


_brain = Brain()


def configure(join_events):
    _brain.configure(join_events)


def step(path, w, h):
    return _brain.step(path, w, h)


def stats():
    return json.dumps({"moves": _brain.moves, "levels": _brain.levels})
