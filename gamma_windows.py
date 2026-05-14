"""
Gamma Screen Pet — Windows port
Requirements: Python 3.8+, Pillow
Install:      pip install Pillow
Run:          python gamma_windows.py
"""

import os
import sys
import time
import random
import ctypes
import tkinter as tk
from PIL import Image, ImageTk

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# Magenta is the window chroma key — transparent pixels must match this color.
# Cyan (#00FFFF) is keyed out from sprite frames when no alpha channel exists.
TRANS        = '#FF00FF'
CHROMA_SRC   = (0, 255, 255)
CHROMA_T     = 96.0

WINDOW_W, WINDOW_H = 192, 208

# Per-frame durations in seconds, sourced from the original Swift AnimationSpec values
DURATIONS = {
    'idle':          [0.28, 0.11, 0.11, 0.14, 0.14, 0.32],
    'running-right': [0.12, 0.12, 0.12, 0.12, 0.12, 0.12, 0.12, 0.22],
    'running-left':  [0.12, 0.12, 0.12, 0.12, 0.12, 0.12, 0.12, 0.22],
    'waving':        [0.14, 0.14, 0.14, 0.28],
    'jumping':       [0.14, 0.14, 0.14, 0.14, 0.28],
    'failed':        [0.14, 0.14, 0.14, 0.14, 0.14, 0.14, 0.14, 0.24],
    'waiting':       [0.15, 0.15, 0.15, 0.15, 0.15, 0.26],
    'running':       [0.12, 0.12, 0.12, 0.12, 0.12, 0.22],
    'review':        [0.15, 0.15, 0.15, 0.15, 0.15, 0.28],
}

# Visual scale factors from the original visualScaleByState dictionary
VISUAL_SCALES = {
    'idle': 0.62, 'running-right': 1.0, 'running-left': 1.0,
    'waving': 0.63, 'jumping': 0.61, 'failed': 0.97,
    'waiting': 0.72, 'running': 0.61, 'review': 0.63,
}

ONE_SHOTS = {'waving', 'jumping', 'failed', 'review'}

# Vertical bob offset per frame step during patrol walking
BOB_PATTERN = [0, 2, 1, 0, 2, 1, 0, -1]


class GammaPet:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.wm_attributes('-topmost', True)
        self.root.wm_attributes('-transparentcolor', TRANS)
        self.root.configure(bg=TRANS)

        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Assets', 'frames')
        self.frames = self._load_all(base)
        if not self.frames:
            print('ERROR: No animation frames found under', base, file=sys.stderr)
            sys.exit(1)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.px = float((sw - WINDOW_W) // 2)
        self.py = float(sh - WINDOW_H - 48)
        self.ground_y = self.py

        self.root.geometry(f'{WINDOW_W}x{WINDOW_H}+{int(self.px)}+{int(self.py)}')

        self.canvas = tk.Canvas(self.root, width=WINDOW_W, height=WINDOW_H,
                                bg=TRANS, highlightthickness=0)
        self.canvas.pack()
        self.img_item = self.canvas.create_image(WINDOW_W // 2, WINDOW_H // 2, anchor='center')

        # Animation state
        self.state = 'idle'
        self.frame_idx = 0
        self.anim_gen = 0       # incremented on every state change to cancel stale loops
        self.locked = False     # True when user manually triggered a state via menu/key
        self.idle_cycles = 0

        # Patrol state
        self.is_patrolling = False
        self.patrol_gen = 0
        self.patrol_dir = -1.0  # will flip to +1 on first patrol start
        self.bob_idx = 0

        # Pounce state
        self.pounce_cooldown = 0.0
        self._inhibit_wave = False  # suppress wave-on-release after double-click

        # Drag state
        self.dragging = False
        self._drag_ox = self._drag_oy = self._prev_mx = 0.0

        # Bind events
        self.canvas.bind('<ButtonPress-1>',   self._on_press)
        self.canvas.bind('<B1-Motion>',        self._on_drag)
        self.canvas.bind('<ButtonRelease-1>',  self._on_release)
        self.canvas.bind('<Double-Button-1>',  self._on_double_click)
        self.canvas.bind('<Button-3>',         self._on_right_click)
        self.root.bind('<KeyPress>',           self._on_key)
        self.root.focus_force()

        self._play('idle')
        self._patrol_tick()
        self._pounce_tick()

    # ── Asset loading ─────────────────────────────────────────────────────────

    def _load_all(self, base):
        tr = int(TRANS[1:3], 16)
        tg = int(TRANS[3:5], 16)
        tb = int(TRANS[5:7], 16)
        result = {}
        for state, durs in DURATIONS.items():
            path = os.path.join(base, state)
            if not os.path.isdir(path):
                continue
            pngs = sorted(f for f in os.listdir(path) if f.lower().endswith('.png'))
            loaded = []
            for fname in pngs:
                img = Image.open(os.path.join(path, fname)).convert('RGBA')
                img = self._apply_chroma(img)

                # Scale sprite to fill window, then apply state's visual scale
                w, h = img.size
                vis = VISUAL_SCALES.get(state, 1.0)
                fit = min(WINDOW_W / w, WINDOW_H / h) * vis
                nw = max(1, round(w * fit))
                nh = max(1, round(h * fit))
                img = img.resize((nw, nh), Image.NEAREST)

                # Composite centered on transparent-color canvas
                canvas_img = Image.new('RGBA', (WINDOW_W, WINDOW_H), (tr, tg, tb, 255))
                ox = (WINDOW_W - nw) // 2
                oy = (WINDOW_H - nh) // 2
                canvas_img.paste(img, (ox, oy), img)
                loaded.append(ImageTk.PhotoImage(canvas_img.convert('RGB')))

            if loaded:
                result[state] = loaded
        return result

    def _apply_chroma(self, img):
        """Remove cyan (#00FFFF) background when no real alpha channel is present."""
        w, h = img.size
        cr, cg, cb = CHROMA_SRC
        corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
        is_cyan_bg = any(
            img.getpixel(c)[3] == 255
            and ((img.getpixel(c)[0] - cr) ** 2
               + (img.getpixel(c)[1] - cg) ** 2
               + (img.getpixel(c)[2] - cb) ** 2) ** 0.5 < CHROMA_T
            for c in corners
        )
        if not is_cyan_bg:
            return img
        pixels = list(img.getdata())
        new_px = []
        for r, g, b, a in pixels:
            dist = ((r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2) ** 0.5
            new_px.append((r, g, b, 0 if dist < CHROMA_T else a))
        img.putdata(new_px)
        return img

    # ── State machine ─────────────────────────────────────────────────────────

    def _play(self, state, locked=False):
        if state not in self.frames:
            return
        if state == 'idle':
            self.locked = locked
            self._start_patrol()
            return
        self._stop_patrol()
        self.locked = locked
        self.idle_cycles = 0
        self._change_state(state)

    def _change_state(self, state):
        if state not in self.frames:
            return
        self.state = state
        self.frame_idx = 0
        self.anim_gen += 1
        self._anim_step(self.anim_gen)

    def _anim_step(self, gen):
        if gen != self.anim_gen:
            return  # stale loop — a newer state has taken over

        state = self.state
        frames = self.frames.get(state, [])
        durs = DURATIONS.get(state, [0.2])
        if not frames:
            return

        n = len(frames)
        idx = self.frame_idx

        # Display the current frame
        self.canvas.itemconfig(self.img_item, image=frames[idx % n])
        dur_ms = int(durs[idx % len(durs)] * 1000)

        self.frame_idx = idx + 1

        # End-of-cycle handling
        if self.frame_idx >= n:
            self.frame_idx = 0
            if state in ONE_SHOTS and not self.locked:
                # Show the last frame for its duration, then return to idle
                self.root.after(dur_ms, lambda: self._on_oneshot_done())
                return
            if state == 'waiting' and not self.locked and random.random() < 1 / 6:
                self.root.after(dur_ms, lambda: self._play('idle'))
                return
            if state == 'idle' and not self.locked:
                self.idle_cycles += 1
                if self.idle_cycles >= random.randint(4, 7):
                    self.idle_cycles = 0
                    next_s = 'idle' if random.random() < 0.5 else 'waiting'
                    if next_s == 'waiting':
                        self.root.after(dur_ms, lambda: self._change_state('waiting'))
                        return

        self.root.after(dur_ms, lambda: self._anim_step(gen))

    def _on_oneshot_done(self):
        self._play('idle')

    # ── Idle patrol ───────────────────────────────────────────────────────────

    def _start_patrol(self):
        self._stop_patrol()
        self.ground_y = self.py
        self.is_patrolling = True
        self.patrol_dir *= -1
        self.bob_idx = 0
        walk = 'running-right' if self.patrol_dir > 0 else 'running-left'
        self._change_state(walk)
        self.patrol_gen += 1
        gen = self.patrol_gen
        self.root.after(2500, lambda: self._advance_patrol(gen))

    def _stop_patrol(self):
        self.is_patrolling = False
        self.patrol_gen += 1
        # Snap back to ground level
        self.py = self.ground_y

    def _advance_patrol(self, gen):
        if gen != self.patrol_gen or not self.is_patrolling:
            return
        self.patrol_dir *= -1
        walk = 'running-right' if self.patrol_dir > 0 else 'running-left'
        self._change_state(walk)
        self.patrol_gen += 1
        gen = self.patrol_gen
        self.root.after(2500, lambda: self._advance_patrol(gen))

    def _patrol_tick(self):
        if self.is_patrolling and not self.dragging:
            sw = self.root.winfo_screenwidth()
            step = 72.0 * 0.04  # 72 px/s at 40 ms intervals
            self.px += self.patrol_dir * step

            if self.px <= 0:
                self.px = 0.0
                if self.patrol_dir < 0:
                    self.patrol_dir = 1.0
                    self._change_state('running-right')
            elif self.px + WINDOW_W >= sw:
                self.px = float(sw - WINDOW_W)
                if self.patrol_dir > 0:
                    self.patrol_dir = -1.0
                    self._change_state('running-left')

            bob = BOB_PATTERN[self.bob_idx % len(BOB_PATTERN)]
            self.bob_idx += 1
            self.root.geometry(f'{WINDOW_W}x{WINDOW_H}+{int(self.px)}+{int(self.ground_y + bob)}')

        self.root.after(40, self._patrol_tick)

    # ── Pounce ────────────────────────────────────────────────────────────────

    def _pounce_tick(self):
        if (not self.locked and not self.dragging and not self.is_patrolling
                and self.state in ('idle', 'waiting')
                and time.time() >= self.pounce_cooldown):
            mx = self.root.winfo_pointerx()
            my = self.root.winfo_pointery()
            # Mouse within 280px above the window top and 150px horizontal padding
            above = self.py - 280 < my < self.py + 16
            near_h = self.px - 150 < mx < self.px + WINDOW_W + 150
            if above and near_h:
                self._do_pounce(mx)
        self.root.after(80, self._pounce_tick)

    def _do_pounce(self, mx):
        self.pounce_cooldown = time.time() + 1.6
        orig_x, orig_y = self.px, self.py
        shift_x = max(-34.0, min(34.0, mx - (self.px + WINDOW_W / 2)))
        tgt_x, tgt_y = orig_x + shift_x, orig_y - 44.0
        self._play('jumping')
        self._tween(tgt_x, tgt_y, 160,
                    on_done=lambda: self._tween(orig_x, orig_y, 220,
                                                on_done=self._after_pounce))

    def _after_pounce(self):
        if self.state == 'jumping' and not self.locked:
            self._play('idle')

    def _tween(self, tx, ty, dur_ms, on_done=None):
        sx, sy = self.px, self.py
        steps = max(1, dur_ms // 16)
        step_ms = dur_ms // steps

        def do_step(n):
            if n >= steps:
                self.px, self.py = float(tx), float(ty)
                if self.is_patrolling:
                    self.ground_y = self.py
                self.root.geometry(f'{WINDOW_W}x{WINDOW_H}+{int(tx)}+{int(ty)}')
                if on_done:
                    on_done()
                return
            t = n / steps
            x = sx + (tx - sx) * t
            y = sy + (ty - sy) * t
            self.px, self.py = x, y
            self.root.geometry(f'{WINDOW_W}x{WINDOW_H}+{int(x)}+{int(y)}')
            self.root.after(step_ms, lambda: do_step(n + 1))

        do_step(0)

    # ── Mouse / keyboard events ───────────────────────────────────────────────

    def _on_press(self, e):
        self._drag_ox = e.x_root - self.px
        self._drag_oy = e.y_root - self.py
        self._prev_mx = float(e.x_root)
        self.dragging = False
        self._inhibit_wave = False
        self._stop_patrol()
        self.root.focus_force()

    def _on_drag(self, e):
        self.dragging = True
        self.px = e.x_root - self._drag_ox
        self.py = e.y_root - self._drag_oy
        self.ground_y = self.py
        self.root.geometry(f'{WINDOW_W}x{WINDOW_H}+{int(self.px)}+{int(self.py)}')
        dx = e.x_root - self._prev_mx
        if abs(dx) > 2:
            new_s = 'running-right' if dx > 0 else 'running-left'
            if self.state != new_s:
                self._change_state(new_s)
        self._prev_mx = float(e.x_root)

    def _on_release(self, e):
        was_dragging = self.dragging
        self.dragging = False
        if was_dragging:
            self._play('idle', locked=False)
            return
        if not self._inhibit_wave:
            self._play('waving', locked=True)
        self._inhibit_wave = False

    def _on_double_click(self, e):
        self._inhibit_wave = True
        self._play('jumping', locked=True)

    def _on_right_click(self, e):
        m = tk.Menu(self.root, tearoff=0)
        entries = [
            ('Wave',   'waving'),
            ('Jump',   'jumping'),
            ('Work',   'running'),
            ('Review', 'review'),
            ('Nap',    'waiting'),
            ('Oops',   'failed'),
            ('Idle',   'idle'),
        ]
        for label, s in entries:
            m.add_command(label=label, command=lambda st=s: self._play(st, locked=True))
        m.add_separator()
        m.add_command(label='Quit Gamma', command=self.root.destroy)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _on_key(self, e):
        mapping = {'w': 'waving', 'j': 'jumping', 'r': 'running', 'i': 'idle'}
        ch = e.char.lower() if e.char else ''
        if ch in mapping:
            self._play(mapping[ch], locked=True)
        elif e.keysym == 'Escape':
            self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    GammaPet().run()
