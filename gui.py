from __future__ import annotations

import datetime
import math
import random
import threading
import time
import tkinter as tk
from collections.abc import Callable


class JarvisGUI:
    def __init__(
        self,
        assistant_runner: Callable[[], None] | None = None,
        stop_callback: Callable[[], None] | None = None,
    ) -> None:
        self.assistant_runner = assistant_runner
        self.stop_callback = stop_callback
        self._assistant_thread: threading.Thread | None = None

        self.root = tk.Tk()
        self.root.title("J.A.R.V.I.S. Mark VII Core Interface")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black")
        self.root.attributes("-transparentcolor", "black")
        self.root.attributes("-alpha", 0.95)
        window_width = 320
        window_height = 320
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = screen_width - window_width - 10
        y = screen_height - window_height - 20
        self.root.geometry(f"{window_width}x{window_height}+{x}+{y}")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.state = "IDLE"
        self.text_logs: list[str] = []
        self.base_color = "#00ddff"

        self.angles = [0.0] * 6
        self.pulse = 0.0

        self.update_loop()

    def _on_close(self) -> None:
        self.root.destroy()
        if self.stop_callback is not None:
            self.stop_callback()

    def _start_assistant_thread(self) -> None:
        if self.assistant_runner is None or self._assistant_thread is not None:
            return
        self._assistant_thread = threading.Thread(target=self.assistant_runner, daemon=True)
        self._assistant_thread.start()

    def mainloop(self) -> None:
        self._start_assistant_thread()
        self.root.mainloop()

    def draw_static_framework(self) -> None:
        self.canvas.create_line(550, 0, 550, 750, fill="#00162a", tags="bg")
        self.canvas.create_line(0, 375, 1100, 375, fill="#00162a", tags="bg")

        for m in [20, 25]:
            self.canvas.create_line(m, m + 60, m, m, m + 60, m, fill="#003355", width=2, tags="bg")
            self.canvas.create_line(
                1100 - m,
                m + 60,
                1100 - m,
                m,
                1100 - m - 60,
                m,
                fill="#003355",
                width=2,
                tags="bg",
            )
            self.canvas.create_line(
                m,
                750 - m - 60,
                m,
                750 - m,
                m + 60,
                750 - m,
                fill="#003355",
                width=2,
                tags="bg",
            )
            self.canvas.create_line(
                1100 - m,
                750 - m - 60,
                1100 - m,
                750 - m,
                1100 - m - 60,
                750 - m,
                fill="#003355",
                width=2,
                tags="bg",
            )

    def update_loop(self) -> None:
        self.canvas.delete("dyn")

        w = self.canvas.winfo_width() or 320
        h = self.canvas.winfo_height() or 320
        cx, cy = w / 2, h / 2
        radius = min(w, h) * 0.25

        speed_mult = 1.0
        active_color = self.base_color
        core_radii = radius * 0.52

        if self.state == "LISTENING":
            active_color = "#00ff88"
            speed_mult = 2.0
            core_radii = radius * 0.58 + math.sin(self.pulse * 2) * (radius * 0.08)
        elif self.state == "PROCESSING":
            active_color = "#ffaa00"
            speed_mult = 4.0
            core_radii = radius * 0.48 + math.sin(self.pulse * 4) * (radius * 0.05)
        elif self.state == "SPEAKING":
            active_color = "#ffffff"
            speed_mult = 1.5
            core_radii = radius * 0.52 + random.uniform(radius * 0.04, radius * 0.18)
        elif self.state == "ERROR":
            active_color = "#ff4444"
            speed_mult = 2.0
            core_radii = radius * 0.52 + math.sin(self.pulse * 4) * (radius * 0.08)
        else:
            core_radii = radius * 0.52 + math.sin(self.pulse) * (radius * 0.03)

        self.pulse += 0.1 * speed_mult

        self.angles[0] = (self.angles[0] + 1.2 * speed_mult) % 360
        self.angles[1] = (self.angles[1] - 1.8 * speed_mult) % 360
        self.angles[2] = (self.angles[2] + 0.8 * speed_mult) % 360
        self.angles[3] = (self.angles[3] + 3.0 * speed_mult) % 360
        self.angles[4] = (self.angles[4] - 0.5 * speed_mult) % 360
        self.angles[5] = (self.angles[5] + 2.0 * speed_mult) % 360

        outer_r = radius * 1.25
        self.draw_segmented_arc(cx, cy, outer_r, self.angles[4], 24, 10, "#003355", 2)

        mid_r = radius * 0.95
        self.draw_arc_line(cx, cy, mid_r, self.angles[0], 72, active_color, 2)
        self.draw_arc_line(cx, cy, mid_r, self.angles[0] + 180, 72, active_color, 2)

        inner_r = radius * 0.72
        self.canvas.create_oval(
            cx - inner_r,
            cy - inner_r,
            cx + inner_r,
            cy + inner_r,
            outline=active_color,
            dash=(6, 4),
            width=2,
            tags="dyn",
        )
        self.draw_segmented_arc(cx, cy, inner_r - 8, -self.angles[5], 48, 4, "#ffffff", 2)

        self.canvas.create_oval(
            cx - core_radii,
            cy - core_radii,
            cx + core_radii,
            cy + core_radii,
            fill="#001122",
            outline=active_color,
            width=2,
            tags="dyn",
        )

        self.canvas.create_text(cx, cy - 10, text="A.U.G.U.S.T.", fill="#005588", font=("Arial", 7, "bold"), tags="dyn")
        self.canvas.create_text(cx, cy + 10, text=self.state, fill=active_color, font=("Courier New", 11, "bold"), tags="dyn")

        self.root.after(30, self.update_loop)

    def draw_arc_line(self, cx: float, cy: float, r: float, start: float, extent: float, color: str, width: int) -> None:
        bbox = (cx - r, cy - r, cx + r, cy + r)
        self.canvas.create_arc(bbox, start=start, extent=extent, style=tk.ARC, outline=color, width=width, tags="dyn")

    def draw_segmented_arc(
        self,
        cx: float,
        cy: float,
        r: float,
        offset: float,
        extent: float,
        count: int,
        color: str,
        width: int,
    ) -> None:
        step = 360 / count
        bbox = (cx - r, cy - r, cx + r, cy + r)
        for i in range(count):
            self.canvas.create_arc(
                bbox,
                start=offset + (i * step),
                extent=extent,
                style=tk.ARC,
                outline=color,
                width=width,
                tags="dyn",
            )

    def draw_waveforms(self, w: int, h: int, active_color: str) -> None:
        wave_w = 400
        start_x = w / 2 - wave_w / 2
        base_y = h - 80

        points: list[float] = []
        for x in range(int(wave_w / 4)):
            rx = start_x + x * 4
            if self.state == "IDLE":
                ry = base_y - math.sin((x + self.pulse * 5) * 0.1) * 5
            elif self.state == "LISTENING":
                ry = base_y - math.sin((x - self.pulse * 10) * 0.2) * 15 * math.sin(x * 0.05)
            elif self.state == "SPEAKING":
                ry = base_y - random.uniform(-1, 1) * math.sin(x * 0.1) * 35
            elif self.state == "PROCESSING":
                ry = base_y - math.sin(x * 0.5 + self.pulse * 20) * 10
            elif self.state == "ERROR":
                ry = base_y - math.sin(x * 0.4 + self.pulse * 15) * 18
            else:
                ry = base_y
            points.extend([rx, ry])

        if len(points) >= 4:
            self.canvas.create_line(points, fill=active_color, width=2, smooth=True, tags="dyn")
        self.canvas.create_line(start_x, base_y + 20, start_x + wave_w, base_y + 20, fill="#003355", width=1, tags="dyn")

    def draw_side_panels(self, w: int, h: int, active_color: str) -> None:
        m = 40
        self.canvas.create_text(
            m,
            m + 60,
            text="SYS.LOG // OVERRIDE",
            fill=self.base_color,
            anchor=tk.NW,
            font=("Courier New", 10, "bold"),
            tags="dyn",
        )
        self.canvas.create_line(m, m + 80, m + 300, m + 80, fill="#004466", tags="dyn")

        y = m + 95
        for log in self.text_logs[-18:]:
            self.canvas.create_text(m, y, text=log, fill="#00aaff", anchor=tk.NW, font=("Courier New", 9), tags="dyn")
            y += 15

        self.canvas.create_text(
            w - m,
            m + 60,
            text="UPLINK.STATUS // MEMORY",
            fill=self.base_color,
            anchor=tk.NE,
            font=("Courier New", 10, "bold"),
            tags="dyn",
        )
        self.canvas.create_line(w - m - 260, m + 80, w - m, m + 80, fill="#004466", tags="dyn")

        diag_data = [
            f"CPU.CYCLES:  {random.randint(4000, 9999)} Hz",
            f"MEM.ALLOC:   {random.randint(12, 32)}.%",
            f"NET.PING:    {random.randint(1, 15)} ms",
            f"CORE.TEMP:   {random.randint(35, 55)} C",
            f"SYS.TIME:    {datetime.datetime.now().strftime('%H:%M:%S.%f')[:-4]}",
        ]
        y = m + 95
        for d in diag_data:
            self.canvas.create_text(w - m, y, text=d, fill=active_color, anchor=tk.NE, font=("Courier New", 9), tags="dyn")
            y += 20

        for px, py in [(w - m - 40, h - m - 80), (m + 40, h - m - 80)]:
            self.canvas.create_oval(px - 15, py - 15, px + 15, py + 15, outline="#004466", dash=(2, 2), tags="dyn")
            self.canvas.create_line(px - 25, py, px + 25, py, fill="#004466", tags="dyn")
            self.canvas.create_line(px, py - 25, px, py + 25, fill="#004466", tags="dyn")

    def append_log(self, text: str) -> None:
        def _add() -> None:
            for line in text.splitlines():
                if line.strip():
                    ts = time.strftime("%H:%M:%S")
                    self.text_logs.append(f"[{ts}] {line}")
            if len(self.text_logs) > 30:
                self.text_logs = self.text_logs[-30:]

        self.root.after(0, _add)

    def add_log(self, text: str) -> None:
        self.append_log(text)

    def update_state(self, state: str) -> None:
        state_map = {
            "idle": "IDLE",
            "listening": "LISTENING",
            "processing": "PROCESSING",
            "thinking": "PROCESSING",
            "speaking": "SPEAKING",
            "error": "ERROR",
        }
        mapped = state_map.get(state.strip().lower(), "IDLE")

        def _set() -> None:
            self.state = mapped

        self.root.after(0, _set)

    def set_state(self, new_state: str) -> None:
        self.update_state(new_state)
