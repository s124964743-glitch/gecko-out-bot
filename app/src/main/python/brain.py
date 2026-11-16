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
        self.nosol = 0
        self.banned = set()
        self.pending = None      # (plan, states, idx, before_occ, after_occ)：上一步送出的動作
        self.level_moves = 0     # 這一關已經送出幾步（0 = 倒數還沒開始）

    def configure(self, join_events):
        popups.JOIN_EVENTS = bool(join_events)
        self.join_events = bool(join_events)
        # 每次按「開始」都當作新的開始：清掉上一輪的關卡進度/計畫（否則會沿用「已走過棋」的短時限）
        self.level_moves = 0
        self.nosol = 0
        self._plan = None
        self.pending = None
        self.prev_small = None
        self.banned = set()
        gc.GRID_CACHE.clear()

    # ------------------------------------------------------------
    @staticmethod
    def _occ_obs(b):
        return frozenset(c for c, k in b.cell.items() if k in ("gecko", "ice"))

    @staticmethod
    def _occ_state(st):
        occ = set()
        for cfg in st[0]:
            if cfg is not None:
                occ.update(cfg)
        return frozenset(occ)

    def _remember(self, b):
        """記下剛送出的這一步，下次截圖用來確認「畫面是否如預期」。"""
        plan = getattr(self, "_plan", None)
        if not plan:
            self.pending = None
            return
        idx = self._idx
        before = self._occ_obs(b)
        after = self._occ_state(self._states[idx])
        self.pending = (idx, before, after)

    def _next_cached(self, b):
        """若上一步的結果符合預期，直接取已解好的下一步；否則回傳 None（重新求解）。"""
        pend = self.pending
        plan = getattr(self, "_plan", None)
        if not pend or not plan:
            return None
        idx, before, after = pend
        obs = self._occ_obs(b)
        if obs == after:
            nxt = idx + 1
            if nxt >= len(plan):
                self._plan = None
                return None
            gi, path, kind = plan[nxt]
            self._idx = nxt
            return plan[nxt]
        if obs == before and idx < len(plan):
            # 手勢沒生效（畫面沒變）：同一步再試一次
            self._idx = idx
            return plan[idx]
        self._plan = None
        return None

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
            self.banned = set()
            self.pending = None
            self.level_moves = 0
            self.nosol = 0
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
            if scr == 'home':
                tt = popups.tutorial_target(rgb, w, h)
                if tt:     # 新手教學遮罩擋住 PLAY：改點被亮起的按鈕
                    return {"a": "tap", "x": int(tt[0]), "y": int(tt[1]), "sleep": 1.3,
                            "msg": f"[教學遮罩] 點亮起的按鈕 ({int(tt[0])},{int(tt[1])})"}
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
            if self.prev_small is not None and self.settle_wait < 6 and \
                    np.abs(small - self.prev_small).mean() > 1.2:
                self.prev_small = small
                self.settle_wait += 1
                return {"a": "wait", "sleep": 0.25, "msg": "等待動畫停止"}
            self.prev_small = small
            self.settle_wait = 0
            self.unknown = 0
            solver = gc.Solver(b, geckos, frozen, holes)
            solver.banned = self.banned
            plan = self._next_cached(b)
            if plan is None:
                # 手機上 Python 比電腦慢，且大關卡要搜尋比較久，所以給足時間；解出來後會一路照著走
                # 還沒走第一步時倒數沒開始，可以多想一下；開始倒數後，在動畫中途的畫面上不能浪費時間
                # （解不出來就放棄、馬上重拍下一張）
                # 開局解不出來通常是這張截圖剛好在動畫中；先快速放棄、重拍，連續失敗才加長上限
                if self.level_moves == 0:
                    lim = (25.0, 40.0, 60.0, 60.0)[min(self.nosol, 3)]
                else:
                    lim = 12.0
                plan = solver.solve(max_repos=(3 if self.nosol < 2 else 5), node_cap=80000, time_limit=lim)
                if plan and solver.states and len(solver.states) == len(plan):
                    self._plan, self._states, self._idx = plan, solver.states, 0
                else:
                    self._plan = None
            else:
                plan = [plan]
            if not plan:
                self.nosol += 1
                return {"a": "wait", "sleep": (1.0 if self.level_moves == 0 else 0.4), "unknown": self.nosol in (1, 4),
                        "msg": f"找不到解（壁虎{len(geckos)} 冰{len(frozen)} 洞{len(holes)}），可能是新機制，已存截圖"}
            self.nosol = 0
            gi, path, kind = plan[0]
            if getattr(solver, "partial", False):
                kind += "(部分解)"
            pts = [list(b.center(r, c)) for r, c in path]
            sig = (tuple(map(tuple, pts)), tuple(sorted(c for c, k in b.cell.items() if k == "gecko")))
            self.move_repeat = self.move_repeat + 1 if sig == self.last_move else 0
            self.last_move = sig
            if self.move_repeat >= 2:
                self.move_repeat = 0
                self.last_move = None
                self.banned.add(tuple(pts[0]) and path[0])
                return {"a": "wait", "sleep": 0.8, "unknown": True,
                        "msg": f"同一步拖了 3 次沒反應，先跳過起點 {path[0]}（可能有鎖/鑰匙等新機制）"}
            self.moves += 1
            self.level_moves += 1
            self._remember(b)
            return {"a": "drag", "pts": pts, "sleep": 0.45,
                    "msg": f"第{self.moves}步 {kind} {path[0]}→{path[-1]}"}

        # ---- 不認得的畫面（讀取中、廣告…）
        self.unknown += 1
        self.prev_small = None
        n = self.unknown
        msg = f"未知畫面 {n}"
        if self.level_moves > 0:
            # 關卡進行中（已經走過棋）看不懂畫面，多半是動畫或辨識暫時失敗：只等、不亂點。
            # （左上角是遊戲的暫停鍵，盲點會跳出 Settings 選單）
            return {"a": "wait", "sleep": 0.5, "unknown": n in (3, 12), "msg": msg + "（關卡中，只等待）"}
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


def diagnose(path, w, h):
    """診斷報告（純文字）：畫面類型、棋盤格線、每隻壁虎/冰/洞的辨識結果、求解結果。
    不影響目前關卡的快取（暫時換掉格線快取，結束後還原）。"""
    import time
    out = []
    saved = dict(gc.GRID_CACHE)
    gc.GRID_CACHE.clear()
    try:
        raw = np.fromfile(path, np.uint8).reshape(h, w, 4)
        rgb = np.ascontiguousarray(raw[:, :, :3])
        out.append(f"解析度 {w}x{h}")
        scr = popups.classify(rgb)
        out.append(f"彈窗/畫面類型: {scr}")
        try:
            b, geckos, frozen, holes = gc.analyse(rgb, _Args)
        except Exception as e:
            out.append(f"analyse 失敗: {e!r}")
            return "\n".join(out)
        out.append(f"格線 {b.rows}列x{b.cols}欄 格寬{b.s:.1f}  壁虎{len(geckos)} 冰{len(frozen)} 洞{len(holes)} 冰封洞{dict(b.fhole)}")
        sym = {'floor': '.', 'gecko': 'G', 'ice': 'I', 'hole': 'O', 'wall': '#', 'void': ' '}
        for r in range(b.rows):
            out.append("  %2d " % r + "".join(sym.get(b.cell.get((r, c)), '?') for c in range(b.cols)))
        for i, g in enumerate(geckos):
            out.append(f"壁虎{i}: 頭{g['cells'][0]}→尾{g['cells'][-1]} {len(g['cells'])}格 {'雙色' if g.get('two') else ''}{(' 鎖:' + g['lock']) if g.get('lock') else ''}{(' 鑰匙:' + g['key']) if g.get('key') else ''}"
                       f" 目標{sorted(g['targets'])} 第二階段{sorted(g['targets2'])} 色{list(map(int, g['color']))}")
        for i, f in enumerate(frozen):
            out.append(f"冰{i}: 起點{f['cells'][0]} {len(f['cells'])}格 倒數{f['count']} 目標{sorted(f['targets'])}")
        out.append("洞: " + " ".join(f"{hh['cell']}{'*' if hh.get('iced') else ''}{list(map(int, hh['color']))}" for hh in holes))
        t = time.time()
        solver = gc.Solver(b, geckos, frozen, holes)
        plan = solver.solve(max_repos=3, node_cap=80000, time_limit=25.0)
        dt = time.time() - t
        if plan:
            out.append(f"求解: 找到 {len(plan)} 步，用時 {dt:.1f}s" + ("（僅部分解）" if getattr(solver, 'partial', False) else ""))
            for k, (gi, p, kind) in enumerate(plan[:30]):
                out.append(f"  {k + 1}. 壁虎{gi} {kind} {p[0]}→{p[-1]}")
        else:
            out.append(f"求解: 25 秒內找不到解（最少剩 {solver.bestrem[0]} 隻沒進洞）用時 {dt:.1f}s")
            bs = getattr(solver, 'beststate', None)
            if bs:
                st = bs[0]
                for i, c in enumerate(st[0]):
                    if c is not None:
                        out.append(f"  卡住: 壁虎/冰{i} 在 {c[0]}…{c[-1]}")
    except Exception as e:
        out.append(f"diagnose 例外: {e!r}")
    finally:
        gc.GRID_CACHE.clear(); gc.GRID_CACHE.update(saved)
    return "\n".join(out)
