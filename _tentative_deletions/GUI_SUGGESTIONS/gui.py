import datetime
import math
import random
import time
import tkinter as tk


class JarvisGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("J.A.R.V.I.S. Mark VII Core Interface")
        self.root.geometry("1100x750")
        self.root.configure(bg="#00050C") # Ultra deep tech blue/black
        
        self.canvas = tk.Canvas(self.root, bg="#00050C", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # GUI State
        self.state = "IDLE"
        self.text_logs = []
        self.base_color = "#00ddff" # Cyan
        
        # Motion Angles
        self.angles = [0.0] * 6
        self.pulse = 0.0
        
        # Data Streams
        self.matrix_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
        
        # Start Loop
        self.draw_static_framework()
        self.update_loop()

    def draw_static_framework(self):
        # Background Grid target reticle lines
        self.canvas.create_line(550, 0, 550, 750, fill="#00162a", tags="bg")
        self.canvas.create_line(0, 375, 1100, 375, fill="#00162a", tags="bg")
        
        # Tech brackets
        for m in [20, 25]:
            self.canvas.create_line(m, m+60, m, m, m+60, m, fill="#003355", width=2, tags="bg")
            self.canvas.create_line(1100-m, m+60, 1100-m, m, 1100-m-60, m, fill="#003355", width=2, tags="bg")
            self.canvas.create_line(m, 750-m-60, m, 750-m, m+60, 750-m, fill="#003355", width=2, tags="bg")
            self.canvas.create_line(1100-m, 750-m-60, 1100-m, 750-m, 1100-m-60, 750-m, fill="#003355", width=2, tags="bg")

    def update_loop(self):
        self.canvas.delete("dyn")
        
        w = self.canvas.winfo_width() or 1100
        h = self.canvas.winfo_height() or 750
        cx, cy = w/2, h/2
        
        # Compute colors and speed multipliers based on STATE
        speed_mult = 1.0
        active_color = self.base_color
        core_radii = 60
        
        if self.state == "LISTENING":
            active_color = "#00ff88"
            speed_mult = 2.0
            core_radii = 70 + math.sin(self.pulse * 2)*10
        elif self.state == "PROCESSING":
            active_color = "#ffaa00"
            speed_mult = 4.0
            core_radii = 50 + math.sin(self.pulse * 4)*5
        elif self.state == "SPEAKING":
            active_color = "#ffffff"
            speed_mult = 1.5
            # React wildly to simulate voice
            core_radii = 60 + random.uniform(5, 30)
        else: # IDLE
            core_radii = 60 + math.sin(self.pulse)*3
            
        self.pulse += 0.1 * speed_mult
        
        # Advance Angles
        self.angles[0] = (self.angles[0] + 1.2 * speed_mult) % 360
        self.angles[1] = (self.angles[1] - 1.8 * speed_mult) % 360
        self.angles[2] = (self.angles[2] + 0.8 * speed_mult) % 360
        self.angles[3] = (self.angles[3] + 3.0 * speed_mult) % 360
        self.angles[4] = (self.angles[4] - 0.5 * speed_mult) % 360
        self.angles[5] = (self.angles[5] + 2.0 * speed_mult) % 360
        
        # -- ARC REACTOR RINGS (Concentric from outside in) --
        
        # Ring 5 (Outer dashed framework)
        r5 = 260
        self.draw_segmented_arc(cx, cy, r5, self.angles[4], 20, 16, "#002a4d", 4)
        
        # Ring 4 (Large thick brackets)
        r4 = 230
        self.draw_arc_line(cx, cy, r4, self.angles[0], 90, active_color, 4)
        self.draw_arc_line(cx, cy, r4, self.angles[0] + 180, 90, active_color, 4)
        
        # Ring 3 (Thin dotted tracker)
        r3 = 190
        self.canvas.create_oval(cx-r3, cy-r3, cx+r3, cy+r3, outline="#004466", dash=(2, 6), width=1, tags="dyn")
        self.draw_arc_line(cx, cy, r3-5, self.angles[1], 40, active_color, 2)
        self.draw_arc_line(cx, cy, r3-5, self.angles[1] + 120, 40, active_color, 2)
        self.draw_arc_line(cx, cy, r3-5, self.angles[1] + 240, 40, active_color, 2)
        
        # Ring 2 (Bold inner blocks)
        r2 = 140
        self.draw_segmented_arc(cx, cy, r2, self.angles[2], 30, 8, active_color, 12)
        
        # Ring 1 (Reactor Core Ring)
        r1 = 100
        self.canvas.create_oval(cx-r1, cy-r1, cx+r1, cy+r1, outline=active_color, dash=(10, 5), width=3, tags="dyn")
        self.draw_segmented_arc(cx, cy, r1-15, -self.angles[5], 60, 3, "#ffffff", 4)
        
        # Central Orb Phase
        self.canvas.create_oval(cx-core_radii, cy-core_radii, cx+core_radii, cy+core_radii, fill="#001122", outline=active_color, width=2, tags="dyn")
        
        # State Text in Center
        self.canvas.create_text(cx, cy-15, text="J.A.R.V.I.S.", fill="#005588", font=("Arial", 8, "bold"), tags="dyn")
        self.canvas.create_text(cx, cy+10, text=self.state, fill=active_color, font=("Courier New", 14, "bold"), tags="dyn")

        # -- HUD DETAILS & AUDIO WAVEFORM --
        self.draw_waveforms(w, h, active_color)
        self.draw_side_panels(w, h, active_color)
        
        self.root.after(30, self.update_loop)

    def draw_arc_line(self, cx, cy, r, start, extent, color, width):
        bbox = (cx-r, cy-r, cx+r, cy+r)
        self.canvas.create_arc(bbox, start=start, extent=extent, style=tk.ARC, outline=color, width=width, tags="dyn")

    def draw_segmented_arc(self, cx, cy, r, offset, extent, count, color, width):
        step = 360 / count
        bbox = (cx-r, cy-r, cx+r, cy+r)
        for i in range(count):
            self.canvas.create_arc(bbox, start=offset + (i*step), extent=extent, style=tk.ARC, outline=color, width=width, tags="dyn")

    def draw_waveforms(self, w, h, active_color):
        # Draw tech audio wave at bottom Center
        wave_w = 400
        wave_h = 60
        start_x = w/2 - wave_w/2
        base_y = h - 80
        
        points = []
        for x in range(int(wave_w/4)): # Use step size 4 for performance
            rx = start_x + x*4
            if self.state == "IDLE":
                ry = base_y - math.sin((x + self.pulse*5)*0.1) * 5
            elif self.state == "LISTENING":
                ry = base_y - math.sin((x - self.pulse*10)*0.2) * 15 * math.sin(x*0.05)
            elif self.state == "SPEAKING":
                # Erratic spiked waveform
                ry = base_y - random.uniform(-1, 1) * math.sin(x*0.1) * 35
            elif self.state == "PROCESSING":
                ry = base_y - math.sin(x*0.5 + self.pulse*20) * 10
            points.extend([rx, ry])
            
        if len(points) >= 4:
            self.canvas.create_line(points, fill=active_color, width=2, smooth=True, tags="dyn")
            
        # Draw frame for waveform
        self.canvas.create_line(start_x, base_y+20, start_x+wave_w, base_y+20, fill="#003355", width=1, tags="dyn")

    def draw_side_panels(self, w, h, active_color):
        m = 40
        # Left Panel (Logs)
        self.canvas.create_text(m, m+60, text="SYS.LOG // OVERRIDE", fill=self.base_color, anchor=tk.NW, font=("Courier New", 10, "bold"), tags="dyn")
        self.canvas.create_line(m, m+80, m+200, m+80, fill="#004466", tags="dyn")
        
        y = m+95
        for log in self.text_logs[-18:]:
            self.canvas.create_text(m, y, text=log, fill="#00aaff", anchor=tk.NW, font=("Courier New", 9), tags="dyn")
            y += 15

        # Right Panel (Diagnostics & Matrix stream)
        self.canvas.create_text(w-m, m+60, text="UPLINK.STATUS // MEMORY", fill=self.base_color, anchor=tk.NE, font=("Courier New", 10, "bold"), tags="dyn")
        self.canvas.create_line(w-m-200, m+80, w-m, m+80, fill="#004466", tags="dyn")
        
        diag_data = [
            f"CPU.CYCLES:  {random.randint(4000, 9999)} Hz",
            f"MEM.ALLOC:   {random.randint(12, 32)}.%",
            f"NET.PING:    {random.randint(1, 15)} ms",
            f"CORE.TEMP:   {random.randint(35, 55)} °C",
            f"SYS.TIME:    {datetime.datetime.now().strftime('%H:%M:%S.%f')[:-4]}"
        ]
        y = m+95
        for d in diag_data:
            self.canvas.create_text(w-m, y, text=d, fill=active_color, anchor=tk.NE, font=("Courier New", 9), tags="dyn")
            y += 20
            
        # Draw little Iron Man Target reticles in corners
        for px, py in [(w-m-40, h-m-80), (m+40, h-m-80)]:
            self.canvas.create_oval(px-15, py-15, px+15, py+15, outline="#004466", dash=(2, 2), tags="dyn")
            self.canvas.create_line(px-25, py, px+25, py, fill="#004466", tags="dyn")
            self.canvas.create_line(px, py-25, px, py+25, fill="#004466", tags="dyn")

    # -- Interface Thread-Safe Updaters --
    def add_log(self, text):
        def _add():
            ts = time.strftime('%H:%M:%S')
            self.text_logs.append(f"[{ts}] {text}")
            if len(self.text_logs) > 30:
                self.text_logs.pop(0)
        self.root.after(0, _add)

    def set_state(self, new_state):
        def _set():
            valid_states = ["IDLE", "LISTENING", "PROCESSING", "SPEAKING", "ERROR"]
            if new_state in valid_states:
                self.state = new_state
                print(f"SYS: State switched to {new_state}")
        self.root.after(0, _set)

if __name__ == "__main__":
    app = JarvisGUI()
    app.add_log("J.A.R.V.I.S. Core booted successfully.")
    app.add_log("Awaiting visual / audio sync...")
    
    def simulate_jarvis():
        states = ["IDLE", "LISTENING", "PROCESSING", "SPEAKING"]
        app.set_state(random.choice(states))
        app.add_log("Simulating voice/action shift.")
        app.root.after(4000, simulate_jarvis)
        
    app.root.after(2000, simulate_jarvis)
    app.root.mainloop()
