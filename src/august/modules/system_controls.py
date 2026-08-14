import os
from ctypes import POINTER, cast

import screen_brightness_control as sbc
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

from august.core.tts import speak
from august.utils.logger import get_logger

logger = get_logger("SystemControls")

def _get_volume_interface():
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))

def handle_system_command(entity):
    action = entity.get('action')
    try:
        if action == 'sleep':
            speak("Going to sleep now.")
            os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
        elif action == 'shutdown' or action == 'turn off':
            speak("Shutting down the system. Goodbye.")
            os.system("shutdown /s /t 1")
        elif action == 'restart':
            speak("Restarting the system.")
            os.system("shutdown /r /t 1")
        elif action == 'volume':
            param = entity.get('param')
            volume = _get_volume_interface()
            if param == 'up':
                speak("Increasing volume.")
                current_vol = volume.GetMasterVolumeLevelScalar()
                volume.SetMasterVolumeLevelScalar(min(1.0, current_vol + 0.1), None)
            elif param == 'down':
                speak("Decreasing volume.")
                current_vol = volume.GetMasterVolumeLevelScalar()
                volume.SetMasterVolumeLevelScalar(max(0.0, current_vol - 0.1), None)
            elif param == 'mute':
                speak("Muting volume.")
                volume.SetMute(1, None)
        elif action == 'brightness':
            speak("Adjusting brightness")
            try:
                current = sbc.get_brightness()[0]
                sbc.set_brightness(min(100, current + 20))
            except Exception as e:
                logger.error(f"Could not change brightness: {e}")
                speak("My apologies, I cannot change the brightness on this display.")
            
    except Exception as e:
        logger.error(f"Failed to execute system command: {e}")
        speak("I encountered an error executing that system command.")
