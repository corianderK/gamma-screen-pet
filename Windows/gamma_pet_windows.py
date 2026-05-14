from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from pathlib import Path
from typing import Callable

from PIL import Image, ImageTk

# Near-black chroma key — invisible if it bleeds at window edges,
# and unlikely to appear in the sprite palette.
APP_WIDTH = 192
APP_HEIGHT = 208
TRANSPARENT_COLOR = "#010101"

ANIMATION_SPECS: dict[str, list[int]] = {
    "idle": [280, 110, 110, 140, 140, 320],
    "running-right": [120, 120, 120, 120, 120, 120, 120, 220],
    "running-left": [120, 120, 120, 120, 120, 120, 120, 220],
    "waving": [140, 140, 140, 280],
    "jumping": [140, 140, 140, 140, 280],
    "failed": [140, 140, 140, 140, 140, 140, 140, 240],
    "waiting": [150, 150, 150, 150, 150, 260],
    "running": [120, 120, 120, 120, 120, 220],
    "review": [150, 150, 150, 150, 150, 280],
}

ONE_SHOT_STATES = {"waving", "jumping", "failed", "review"}

VISUAL_SCALE_BY_STATE: dict[str, float] = {
    "idle": 0.62,
    "running-right": 1.0,
    "running-left": 1.0,
    "waving": 0.63,
    "jumping": 0.61,
    "failed": 0.97,
    "waiting": 0.72,
    "running": 0.61,
    "review": 0.63,
}


def app_root() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[1]


class GammaPet:
    def __init__(self, root: tk.Tk, frames_root: Path) -> None:
        self.root = root
        self.frames_root = frames_root
        self.frames: dict[str, list[ImageTk.PhotoImage]] = {}
        self.current_state = "idle"
        self.frame_index = 0
        self.frame_job: str | None = None
        self.patrol_job: str | None = None
        self.pounce_job: str | None = None
        self.lock_current_state = False
        self.is_idle_patrolling = False
        self.idle_patrol_direction = -1
        self.idle_patrol_target_x: int | None = None
        self.idle_patrol_ground_y: int | None = None
        self.drag_start_pointer: tuple[int, int] | None = None
        self.drag_start_window: tuple[int, int] | None = None
        self.last_drag_x: int | None = None
        self.did_drag = False
        self.pounce_cooldown_until = 0

        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT_COLOR)
        try:
            self.root.wm_attributes("-transparentcolor", TRANSPARENT_COLOR)
        except tk.TclError:
            pass

        # Disable Windows 11 rounded corners — rounded edges produce pixels that
        # don't match the chroma key exactly, causing a visible colored border.
        self.root.update_idletasks()
        try:
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_DONOTROUND = 1
            hwnd = self.root.winfo_id()
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(ctypes.c_int(DWMWCP_DONOTROUND)),
                ctypes.sizeof(ctypes.c_int),
            )
        except Exception:
            pass

        self.canvas = tk.Canvas(
            self.root,
            width=APP_WIDTH,
            height=APP_HEIGHT,
            bg=TRANSPARENT_COLOR,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack()
        self.sprite_id = self.canvas.create_image(
            APP_WIDTH // 2,
            APP_HEIGHT // 2,
            anchor="center",
        )

        self.menu = tk.Menu(self.root, tearoff=False)
        for label, state in [
            ("Wave", "waving"),
            ("Jump", "jumping"),
            ("Work", "running"),
            ("Review", "review"),
            ("Nap", "waiting"),
            ("Oops", "failed"),
            ("Idle", "idle"),
        ]:
            self.menu.add_command(label=label, command=lambda s=state: self.play(s, locked=True))
        self.menu.add_separator()
        self.menu.add_command(label="Quit Gamma", command=self._quit)

        self._load_frames()
        self._place_near_cursor()
        self._bind_events()
        self.play("idle")
        self._poll_cursor_for_pounce()

    def _load_frames(self) -> None:
        tr = int(TRANSPARENT_COLOR[1:3], 16)
        tg = int(TRANSPARENT_COLOR[3:5], 16)
        tb = int(TRANSPARENT_COLOR[5:7], 16)
        for state in ANIMATION_SPECS:
            state_dir = self.frames_root / state
            loaded: list[ImageTk.PhotoImage] = []
            for path in sorted(state_dir.glob("*.png"), key=lambda item: int(item.stem)):
                image = Image.open(path).convert("RGBA")
                scale = VISUAL_SCALE_BY_STATE.get(state, 1.0)
                if scale != 1.0:
                    width = max(1, round(image.width * scale))
                    height = max(1, round(image.height * scale))
                    image = image.resize((width, height), Image.Resampling.NEAREST)
                # Composite onto the transparent color so semi-transparent edge pixels
                # blend to exactly the chroma key, preventing a colored border halo.
                bg = Image.new("RGBA", image.size, (tr, tg, tb, 255))
                image = Image.alpha_composite(bg, image).convert("RGB")
                loaded.append(ImageTk.PhotoImage(image))
            if loaded:
                self.frames[state] = loaded

    def _place_near_cursor(self) -> None:
        self.root.update_idletasks()
        x = max(24, self.root.winfo_pointerx() - APP_WIDTH // 2)
        y = max(36, self.root.winfo_pointery() - APP_HEIGHT - 120)
        self.root.geometry(f"{APP_WIDTH}x{APP_HEIGHT}+{x}+{y}")

    def _bind_events(self) -> None:
        for widget in (self.root, self.canvas):
            widget.bind("<ButtonPress-1>", self._mouse_down)
            widget.bind("<B1-Motion>", self._mouse_dragged)
            widget.bind("<ButtonRelease-1>", self._mouse_up)
            widget.bind("<Double-Button-1>", self._double_click)
            widget.bind("<Key>", self._key_down)
        self.canvas.bind("<Button-3>", self._show_menu)
        self.root.bind("<Escape>", lambda _event: self._quit())
        self.root.focus_force()

    def _window_position(self) -> tuple[int, int]:
        return self.root.winfo_x(), self.root.winfo_y()

    def _set_window_position(self, x: int, y: int) -> None:
        self.root.geometry(f"{APP_WIDTH}x{APP_HEIGHT}+{x}+{y}")

    def _screen_bounds(self) -> tuple[int, int]:
        return self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _cancel_after(self, job_attr: str) -> None:
        job = getattr(self, job_attr)
        if job is not None:
            self.root.after_cancel(job)
            setattr(self, job_attr, None)

    def play(self, state: str, locked: bool = False) -> None:
        if state not in self.frames:
            return
        if state == "idle":
            self.lock_current_state = locked
            self._start_idle_patrol()
            return

        self._stop_idle_patrol()
        self.current_state = state
        self.frame_index = 0
        self.lock_current_state = locked
        self._schedule_next_frame(0)

    def _start_idle_patrol(self) -> None:
        self.is_idle_patrolling = True
        self.idle_patrol_direction *= -1
        self._advance_idle_patrol()

    def _stop_idle_patrol(self) -> None:
        self.is_idle_patrolling = False
        self._cancel_after("patrol_job")
        if self.idle_patrol_ground_y is not None:
            x, _y = self._window_position()
            self._set_window_position(x, self.idle_patrol_ground_y)
        self.idle_patrol_target_x = None
        self.idle_patrol_ground_y = None

    def _advance_idle_patrol(self) -> None:
        if not self.is_idle_patrolling:
            return

        self.idle_patrol_direction *= -1
        self.current_state = "running-left" if self.idle_patrol_direction < 0 else "running-right"
        self.frame_index = 0
        self._schedule_next_frame(0)

        x, y = self._window_position()
        screen_width, _screen_height = self._screen_bounds()
        distance = 180
        target_x = x + self.idle_patrol_direction * distance
        target_x = min(max(12, target_x), screen_width - APP_WIDTH - 12)
        self.idle_patrol_target_x = round(target_x)
        self.idle_patrol_ground_y = y
        self._cancel_after("patrol_job")
        self.patrol_job = self.root.after(2500, self._advance_idle_patrol)

    def _step_idle_patrol(self, frame_duration_ms: int) -> None:
        if not self.is_idle_patrolling or self.idle_patrol_target_x is None or self.idle_patrol_ground_y is None:
            return
        if self.current_state not in {"running-left", "running-right"}:
            return

        speed = 72
        step = self.idle_patrol_direction * speed * frame_duration_ms / 1000
        x, _y = self._window_position()
        if self.idle_patrol_direction < 0:
            next_x = max(self.idle_patrol_target_x, x + step)
        else:
            next_x = min(self.idle_patrol_target_x, x + step)

        bob_pattern = [0, 2, 1, 0, 2, 1, 0, -1]
        bob = bob_pattern[self.frame_index % len(bob_pattern)]
        self._set_window_position(round(next_x), self.idle_patrol_ground_y + bob)

    def _schedule_next_frame(self, delay_ms: int) -> None:
        self._cancel_after("frame_job")
        self.frame_job = self.root.after(delay_ms, self._advance_frame)

    def _advance_frame(self) -> None:
        images = self.frames.get(self.current_state, [])
        if not images:
            return

        image = images[self.frame_index % len(images)]
        self.canvas.itemconfigure(self.sprite_id, image=image)
        durations = ANIMATION_SPECS[self.current_state]
        duration = durations[self.frame_index % len(durations)]
        self.frame_index += 1

        if self.frame_index >= len(images):
            if self.current_state in ONE_SHOT_STATES and not self.lock_current_state:
                self.play("idle")
                return
            if self.current_state == "waiting" and not self.lock_current_state:
                self.play("idle")
                return

        self._step_idle_patrol(duration)
        self._schedule_next_frame(duration)

    def _mouse_down(self, event: tk.Event) -> None:
        self.root.focus_force()
        self._stop_idle_patrol()
        self.drag_start_pointer = (event.x_root, event.y_root)
        self.drag_start_window = self._window_position()
        self.last_drag_x = event.x_root
        self.did_drag = False

    def _mouse_dragged(self, event: tk.Event) -> None:
        if self.drag_start_pointer is None or self.drag_start_window is None:
            return

        pointer_x, pointer_y = self.drag_start_pointer
        window_x, window_y = self.drag_start_window
        dx = event.x_root - pointer_x
        dy = event.y_root - pointer_y
        self._set_window_position(window_x + dx, window_y + dy)
        self.did_drag = self.did_drag or abs(dx) > 3 or abs(dy) > 3

        if self.last_drag_x is not None:
            direction = event.x_root - self.last_drag_x
            if direction > 2 and self.current_state != "running-right":
                self.play("running-right", locked=True)
            elif direction < -2 and self.current_state != "running-left":
                self.play("running-left", locked=True)
        self.last_drag_x = event.x_root

    def _mouse_up(self, _event: tk.Event) -> None:
        self.drag_start_pointer = None
        self.drag_start_window = None
        self.last_drag_x = None
        if self.did_drag:
            self.did_drag = False
            return
        self.play("waving", locked=True)

    def _double_click(self, _event: tk.Event) -> None:
        self.play("jumping", locked=True)

    def _show_menu(self, event: tk.Event) -> None:
        self.root.focus_force()
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _key_down(self, event: tk.Event) -> None:
        key = event.keysym.lower()
        actions: dict[str, Callable[[], None]] = {
            "w": lambda: self.play("waving", locked=True),
            "j": lambda: self.play("jumping", locked=True),
            "r": lambda: self.play("running", locked=True),
            "i": lambda: self.play("idle", locked=True),
        }
        action = actions.get(key)
        if action:
            action()

    def _quit(self) -> None:
        self.root.withdraw()
        self.root.update()
        self.root.destroy()

    def _poll_cursor_for_pounce(self) -> None:
        if not self.is_idle_patrolling and not self.lock_current_state and self.current_state in {"idle", "waiting"}:
            now = self.root.tk.call("clock", "milliseconds")
            if now >= self.pounce_cooldown_until:
                mouse_x = self.root.winfo_pointerx()
                mouse_y = self.root.winfo_pointery()
                x, y = self._window_position()
                is_above = y - 280 < mouse_y < y + 16
                is_near = x - 150 < mouse_x < x + APP_WIDTH + 150
                if is_above and is_near:
                    self.pounce_cooldown_until = now + 1600
                    self.play("jumping")
                    self._set_window_position(x + max(-34, min(34, mouse_x - (x + APP_WIDTH // 2))), y - 44)
                    self.root.after(260, lambda: self._set_window_position(x, y))
        self.pounce_job = self.root.after(80, self._poll_cursor_for_pounce)


def main() -> int:
    root_dir = app_root()
    frames_root = root_dir / "Assets" / "frames"
    if not frames_root.exists():
        print(f"Could not find frames at {frames_root}", file=sys.stderr)
        return 1

    root = tk.Tk()
    root.title("Gamma")
    GammaPet(root, frames_root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
