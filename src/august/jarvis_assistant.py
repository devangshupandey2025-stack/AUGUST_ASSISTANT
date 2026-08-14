# Jarvis Assistant – Full Code (Part 1/5)
import os

import google.generativeai as genai
import pyttsx3

# ---------------- CONFIG ----------------
STT_MODE = "gemini"
MODEL_PATH = "vosk-model-small-en-us-0.15"
SAMPLE_RATE = 16000
BLOCKSIZE = 8000
WAKE_WORDS = ["jarvis", "hey jarvis", "okay jarvis"]

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
ENABLE_GEMINI = GEMINI_KEY is not None
if ENABLE_GEMINI:
    genai.configure(api_key=GEMINI_KEY)

engine = pyttsx3.init()

def speak(text):
    engine.say(text)
    engine.runAndWait()
