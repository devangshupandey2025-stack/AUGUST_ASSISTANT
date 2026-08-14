"""
J.A.R.V.I.S.-style Desktop Assistant GUI
=========================================
A Python Tkinter GUI inspired by the Iron Man movies: dark background,
a glowing animated "arc reactor" style circle, scrolling log console,
and a command bar. Supports typed commands, and optional voice
input/output if the required libraries are installed.

--------------------------------------------------------------------
REQUIREMENTS
--------------------------------------------------------------------
Core GUI (always required):
    Tkinter ships with most Python installs. On Linux you may need:
        sudo apt-get install python3-tk

Optional voice features (the app works fine without these -- it will
just fall back to text-only mode):
    pip install pyttsx3 SpeechRecognition pyaudio

Run:
    python jarvis_gui.py
--------------------------------------------------------------------
"""

import datetime
import math
import random
import threading
import tkinter as tk
import webbrowser
from tkinter import font as tkfont

# ----------------------------------------------------------------------
# Optional voice engines (loaded lazily / safely so the GUI never crashes
# if these packages aren't installed)
# ----------------------------------------------------------------------
TTS_AVAILABLE = False
STT_AVAILABLE = False

try:
    import pyttsx3
    TTS_AVAILABLE = True
except ImportError:
    pass

try:
    import speech_recognition as sr
    STT_AVAILABLE = True
except ImportError:
    pass


# ========================================================================
# COLOR THEME (fiery gold particle-burst HUD palette)
# ========================================================================
BG_COLOR = "#060301"
PANEL_COLOR = "#0d0704"
ACCENT = "#ff8c1a"
ACCENT_DIM = "#5c3410"
ACCENT_BRIGHT = "#ffd27a"
ACCENT_CORE = "#fff2d0"
TEXT_COLOR = "#ffe2b8"
WARN_COLOR = "#ff3b3b"


class JarvisGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("J.A.R.V.I.S.")
        self.root.geometry("900x650")
        self.root.configure(bg=BG_COLOR)
        self.root.minsize(700, 500)

        self.angle = 0
        self.pulse = 0
        self.listening = False
        self._init_particles()

        # Text-to-speech engine (optional)
        self.tts_engine = None
        if TTS_AVAILABLE:
            try:
                self.tts_engine = pyttsx3.init()
                self.tts_engine.setProperty("rate", 175)
            except Exception:
                self.tts_engine = None

        self._build_layout()
        self._animate_reactor()
        self.log("J.A.R.V.I.S. online. All systems nominal.")
        self.speak("All systems online. Good day, sir.")

    # --------------------------------------------------------------
    # UI CONSTRUCTION
    # --------------------------------------------------------------
    def _build_layout(self):
        title_font = tkfont.Font(family="Consolas", size=20, weight="bold")
        mono_font = tkfont.Font(family="Consolas", size=11)
        small_font = tkfont.Font(family="Consolas", size=9)

        # --- Header ---
        header = tk.Frame(self.root, bg=BG_COLOR)
        header.pack(fill="x", pady=(10, 0))
        tk.Label(
            header, text="J . A . R . V . I . S .",
            fg=ACCENT, bg=BG_COLOR, font=title_font
        ).pack(side="left", padx=20)
        self.clock_label = tk.Label(
            header, text="", fg=ACCENT_DIM, bg=BG_COLOR, font=mono_font
        )
        self.clock_label.pack(side="right", padx=20)
        self._update_clock()

        # --- Main body: reactor canvas (left) + console (right) ---
        body = tk.Frame(self.root, bg=BG_COLOR)
        body.pack(fill="both", expand=True, padx=15, pady=10)

        # Arc reactor canvas
        self.canvas = tk.Canvas(
            body, width=380, height=380, bg=BG_COLOR, highlightthickness=0
        )
        self.canvas.pack(side="left", padx=10, pady=10)

        # Console / log panel
        console_frame = tk.Frame(body, bg=PANEL_COLOR, highlightbackground=ACCENT_DIM,
                                  highlightthickness=1)
        console_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        tk.Label(
            console_frame, text="SYSTEM LOG", fg=ACCENT_DIM, bg=PANEL_COLOR,
            font=small_font, anchor="w"
        ).pack(fill="x", padx=8, pady=(6, 0))

        self.log_box = tk.Text(
            console_frame, bg=PANEL_COLOR, fg=TEXT_COLOR, font=mono_font,
            insertbackground=ACCENT, wrap="word", borderwidth=0,
            highlightthickness=0
        )
        self.log_box.pack(fill="both", expand=True, padx=8, pady=8)
        self.log_box.config(state="disabled")

        # --- Status bar ---
        self.status_label = tk.Label(
            self.root, text="STATUS: IDLE", fg=ACCENT, bg=BG_COLOR, font=small_font
        )
        self.status_label.pack(anchor="w", padx=25)

        # --- Command bar ---
        cmd_frame = tk.Frame(self.root, bg=BG_COLOR)
        cmd_frame.pack(fill="x", padx=15, pady=(5, 15))

        self.entry = tk.Entry(
            cmd_frame, bg=PANEL_COLOR, fg=ACCENT_BRIGHT, insertbackground=ACCENT,
            font=mono_font, highlightbackground=ACCENT_DIM, highlightthickness=1,
            relief="flat"
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 10))
        self.entry.bind("<Return>", lambda e: self.handle_command(self.entry.get()))
        self.entry.focus_set()

        send_btn = tk.Button(
            cmd_frame, text="SEND", command=lambda: self.handle_command(self.entry.get()),
            bg=ACCENT_DIM, fg=BG_COLOR, activebackground=ACCENT, font=small_font,
            relief="flat", padx=15
        )
        send_btn.pack(side="left")

        if STT_AVAILABLE:
            mic_btn = tk.Button(
                cmd_frame, text="🎤 LISTEN", command=self.listen_voice,
                bg=ACCENT_DIM, fg=BG_COLOR, activebackground=ACCENT, font=small_font,
                relief="flat", padx=15
            )
            mic_btn.pack(side="left", padx=(10, 0))

    # --------------------------------------------------------------
    # PARTICLE FIELD SETUP (fiery burst core)
    # --------------------------------------------------------------
    def _init_particles(self):
        """Pre-generate a fixed random particle field so the burst has a
        stable shape and only rotates/flickers, rather than reshuffling
        every frame."""
        random.seed(42)
        self.particles = []
        # Dense inner spray + sparser long outer spikes, like the reference
        for _ in range(90):
            angle = random.uniform(0, 360)
            length = random.uniform(14, 55)
            width = random.uniform(1, 2.2)
            self.particles.append([angle, length, width, "inner"])
        for _ in range(55):
            angle = random.uniform(0, 360)
            length = random.uniform(55, 130)
            width = random.uniform(0.6, 1.6)
            self.particles.append([angle, length, width, "outer"])
        random.seed()  # restore randomness for jokes etc.

    # --------------------------------------------------------------
    # ARC REACTOR ANIMATION (fiery gold particle-burst HUD)
    # --------------------------------------------------------------
    def _animate_reactor(self):
        c = self.canvas
        c.delete("all")
        cx, cy = 190, 190
        glow = ACCENT_BRIGHT if self.listening else ACCENT

        # --- outer square HUD frame with corner brackets (top-left style) ---
        fx0, fy0, fx1, fy1 = 15, 15, 365, 365
        bl = 26  # bracket leg length
        for (x0, y0, dx, dy) in [
            (fx0, fy0, 1, 1), (fx1, fy0, -1, 1),
            (fx0, fy1, 1, -1), (fx1, fy1, -1, -1),
        ]:
            c.create_line(x0, y0, x0 + dx * bl, y0, fill=ACCENT_DIM, width=2)
            c.create_line(x0, y0, x0, y0 + dy * bl, fill=ACCENT_DIM, width=2)
        # top ticks (like the two short bars above the frame in the ref image)
        c.create_line(fx0 + 20, fy0 - 10, fx0 + 70, fy0 - 10, fill=ACCENT_DIM, width=3)
        c.create_line(fx0 + 80, fy0 - 10, fx0 + 95, fy0 - 10, fill=ACCENT_DIM, width=3)

        # --- small rotating gear/dial, bottom-right corner ---
        gx, gy, gr = fx1 - 6, fy1 - 6, 10
        gear_angle = self.angle * 2.5
        for i in range(8):
            a = math.radians(gear_angle + i * 45)
            x1, y1 = gx + gr * math.cos(a), gy + gr * math.sin(a)
            x2, y2 = gx + (gr + 5) * math.cos(a), gy + (gr + 5) * math.sin(a)
            c.create_line(x1, y1, x2, y2, fill=ACCENT_DIM, width=2)
        c.create_oval(gx - gr, gy - gr, gx + gr, gy + gr, outline=ACCENT_DIM, width=1)

        # --- large faint dashed containment ring, slightly off-center ---
        big_r = 150
        c.create_oval(
            cx - big_r, cy - big_r + 6, cx + big_r, cy + big_r + 6,
            outline=ACCENT_DIM, width=1
        )
        # a thicker partial arc riding on that ring (top-left accent, as in ref)
        c.create_arc(
            cx - big_r, cy - big_r + 6, cx + big_r, cy + big_r + 6,
            start=(self.angle * 0.6) % 360, extent=70,
            style="arc", outline=glow, width=3
        )

        # --- mid rotating dashed rings ---
        for radius, width, speed, dash in [
            (118, 1, 1.0, (4, 8)),
            (100, 2, -1.4, (2, 5)),
        ]:
            start = (self.angle * speed) % 360
            c.create_arc(
                cx - radius, cy - radius, cx + radius, cy + radius,
                start=start, extent=300, style="arc",
                outline=glow, width=width, dash=dash
            )

        # --- fiery particle burst core ---
        flicker = 0.85 + 0.15 * math.sin(self.pulse * 3)
        for p in self.particles:
            base_angle, length, width, kind = p
            a = math.radians(base_angle + self.angle * (0.4 if kind == "outer" else 0.15))
            length_f = length * flicker
            x1 = cx + 6 * math.cos(a)
            y1 = cy + 6 * math.sin(a)
            x2 = cx + length_f * math.cos(a)
            y2 = cy + length_f * math.sin(a)
            color = ACCENT_BRIGHT if kind == "inner" else ACCENT
            c.create_line(x1, y1, x2, y2, fill=color, width=width)

        # --- blazing white-gold center ---
        core_r = 16 + 3 * math.sin(self.pulse)
        c.create_oval(
            cx - core_r * 1.8, cy - core_r * 1.8, cx + core_r * 1.8, cy + core_r * 1.8,
            fill=ACCENT, outline=""
        )
        c.create_oval(
            cx - core_r, cy - core_r, cx + core_r, cy + core_r,
            fill=ACCENT_CORE, outline=""
        )

        self.angle = (self.angle + 1.1) % 360
        self.pulse += 0.12
        self.root.after(40, self._animate_reactor)

    # --------------------------------------------------------------
    # CLOCK
    # --------------------------------------------------------------
    def _update_clock(self):
        now = datetime.datetime.now().strftime("%A, %d %b %Y  |  %H:%M:%S")
        self.clock_label.config(text=now)
        self.root.after(1000, self._update_clock)

    # --------------------------------------------------------------
    # LOGGING / OUTPUT
    # --------------------------------------------------------------
    def log(self, message, tag="jarvis"):
        self.log_box.config(state="normal")
        prefix = "> " if tag == "user" else "  JARVIS: "
        self.log_box.insert("end", f"{prefix}{message}\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def speak(self, text):
        self.log(text)
        if self.tts_engine:
            def _run():
                try:
                    self.tts_engine.say(text)
                    self.tts_engine.runAndWait()
                except Exception:
                    pass
            threading.Thread(target=_run, daemon=True).start()

    # --------------------------------------------------------------
    # VOICE INPUT (optional)
    # --------------------------------------------------------------
    def listen_voice(self):
        if not STT_AVAILABLE:
            self.log("Voice recognition library not installed.")
            return

        def _run():
            self.listening = True
            self.status_label.config(text="STATUS: LISTENING...", fg=ACCENT_BRIGHT)
            recognizer = sr.Recognizer()
            try:
                with sr.Microphone() as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.4)
                    audio = recognizer.listen(source, timeout=5, phrase_time_limit=6)
                text = recognizer.recognize_google(audio)
                self.root.after(0, lambda: self.handle_command(text))
            except Exception:
                self.root.after(0, lambda: self.log(f"Voice error: {e}"))
            finally:
                self.listening = False
                self.root.after(0, lambda: self.status_label.config(
                    text="STATUS: IDLE", fg=ACCENT))

        threading.Thread(target=_run, daemon=True).start()

    # --------------------------------------------------------------
    # COMMAND HANDLING
    # --------------------------------------------------------------
    def handle_command(self, text):
        text = text.strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.log(text, tag="user")
        self.status_label.config(text="STATUS: PROCESSING...", fg=ACCENT_BRIGHT)
        self.root.after(150, lambda: self.process_command(text))

    def process_command(self, text):
        lower = text.lower()

        if any(w in lower for w in ["hello", "hi jarvis", "hey jarvis"]):
            response = "Hello, sir. How can I assist you?"

        elif "time" in lower:
            response = f"The current time is {datetime.datetime.now().strftime('%H:%M:%S')}."

        elif "date" in lower:
            response = f"Today's date is {datetime.datetime.now().strftime('%A, %d %B %Y')}."

        elif "your name" in lower:
            response = "I am J.A.R.V.I.S. — Just A Rather Very Intelligent System."

        elif "joke" in lower:
            jokes = [
                "Why do programmers prefer dark mode? Because light attracts bugs.",
                "I would tell you a UDP joke, but you might not get it.",
                "Sir, I calculated the odds of that plan succeeding. It's best not to know.",
            ]
            response = random.choice(jokes)

        elif lower.startswith("open "):
            target = lower.replace("open ", "").strip()
            url = target if target.startswith("http") else f"https://{target}.com"
            try:
                webbrowser.open(url)
                response = f"Opening {target}, sir."
            except Exception:
                response = f"I was unable to open {target}."

        elif "how are you" in lower:
            response = "All systems are functioning within normal parameters."

        elif any(w in lower for w in ["exit", "quit", "shutdown", "power down"]):
            response = "Shutting down. Goodbye, sir."
            self.speak(response)
            self.status_label.config(text="STATUS: OFFLINE", fg=WARN_COLOR)
            self.root.after(1500, self.root.destroy)
            return

        else:
            response = f'I heard "{text}", but I have no protocol configured for that yet.'

        self.speak(response)
        self.status_label.config(text="STATUS: IDLE", fg=ACCENT)


def main():
    root = tk.Tk()
    app = JarvisGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()