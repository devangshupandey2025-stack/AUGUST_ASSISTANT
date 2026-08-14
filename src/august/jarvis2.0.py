import platform
import queue
import subprocess
import time
import webbrowser

import numpy as np
import pyautogui
import pyttsx3
import sounddevice as sd
import whisper

# ------------------- SETTINGS -------------------
WHISPER_MODEL = "small"     # tiny / base / small / medium
WAKE_WORDS = ["computer", "hey computer"]
COMMAND_LISTEN_TIME = 5      # seconds
SAMPLE_RATE = 16000
BLOCK_SIZE = 8000
# -------------------------------------------------

# Load whisper model
print("Loading Whisper model... (takes a few seconds)")
model = whisper.load_model(WHISPER_MODEL)

# TTS
tts = pyttsx3.init()
tts.setProperty("rate", 170)

def speak(text):
    tts.say(text)
    tts.runAndWait()

# Audio queue
audio_q = queue.Queue()

def audio_callback(indata, frames, time, status):
    if status:
        print("Audio status:", status)
    audio_q.put(indata.copy())

# Recording stream
stream = sd.InputStream(
    channels=1,
    callback=audio_callback,
    samplerate=SAMPLE_RATE,
    blocksize=BLOCK_SIZE
)

# -----------------------------------------
# SPEECH TO TEXT USING WHISPER
# -----------------------------------------
def transcribe_chunk(seconds=5):
    frames = []
    start = time.time()

    while time.time() - start < seconds:
        try:
            frames.append(audio_q.get(timeout=seconds))
        except queue.Empty:
            break

    if not frames:
        return ""

    audio = np.concatenate(frames, axis=0).flatten().astype(np.float32)
    audio = audio / np.max(np.abs(audio) + 1e-9)

    result = model.transcribe(audio, fp16=False)
    text = result["text"].lower().strip()
    return text

# -----------------------------------------
# INTENT PARSER
# -----------------------------------------
def parse_intent(text):
    if "open" in text:
        app = text.split("open")[-1].strip()
        return ("open_app", app)
    if "search" in text or "google" in text:
        query = text.replace("search", "").replace("google", "").strip()
        return ("search", query)
    if "volume up" in text:
        return ("volume_up", None)
    if "volume down" in text:
        return ("volume_down", None)
    if "mute" in text:
        return ("mute", None)
    if "type" in text:
        msg = text.split("type")[-1].strip()
        return ("type", msg)
    
    return ("unknown", text)

# -----------------------------------------
# EXECUTE COMMAND
# -----------------------------------------
def execute(intent, value):
    osname = platform.system().lower()

    # ---- OPEN APP ----
    if intent == "open_app":
        speak(f"Opening {value}")

        app = value.lower()

        # Basic cross-OS app mapping
        app_map = {
            "chrome": {
                "win": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                "linux": "google-chrome",
                "darwin": "Google Chrome"
            },
            "notepad": {
                "win": "notepad",
                "linux": "gedit",
                "darwin": "TextEdit"
            },
            "calculator": {
                "win": "calc",
                "linux": "gnome-calculator",
                "darwin": "Calculator"
            }
        }

        if app in app_map:
            if osname.startswith("win"):
                subprocess.Popen([app_map[app]["win"]])
            elif osname.startswith("linux"):
                subprocess.Popen([app_map[app]["linux"]])
            else:
                subprocess.Popen(["open", "-a", app_map[app]["darwin"]])
        else:
            speak("App not mapped.")
        return

    # ---- SEARCH WEB ----
    if intent == "search":
        speak(f"Searching for {value}")
        url = "https://www.google.com/search?q=" + value.replace(" ", "+")
        webbrowser.open(url)
        return

    # ---- VOLUME ----
    if intent == "volume_up":
        speak("Volume up")
        pyautogui.press("volumeup")
        return

    if intent == "volume_down":
        speak("Volume down")
        pyautogui.press("volumedown")
        return

    if intent == "mute":
        speak("Muting")
        pyautogui.press("volumemute")
        return

    # ---- TYPE ----
    if intent == "type":
        speak("Typing")
        pyautogui.write(value, interval=0.02)
        return

    speak("I didn't understand that.")

# -----------------------------------------
# MAIN LOOP
# -----------------------------------------
def main():
    speak("Whisper voice control active.")
    print("Say: 'Computer' to activate.")

    with stream:
        while True:
            text = transcribe_chunk(seconds=3)

            if any(w in text for w in WAKE_WORDS):
                print("Wake word detected!")
                speak("Yes?")

                # Listen for command
                command = transcribe_chunk(seconds=COMMAND_LISTEN_TIME)
                print("Command:", command)

                if command.strip() == "":
                    speak("I didn't hear anything.")
                    continue

                intent, value = parse_intent(command)
                print("Intent:", intent, value)

                execute(intent, value)


if __name__ == "__main__":
    main()
