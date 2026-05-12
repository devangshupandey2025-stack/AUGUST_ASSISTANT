import threading
import time
from gui import JarvisGUI

def start_assistant_logic(gui):
    """
    This runs your JARVIS main logic in a separate thread.
    You can trigger GUI updates from here by modifying the gui state!
    """
    time.sleep(2)
    gui.add_log("Starting audio listener...")
    
    # Example pseudo-loop hooking up to the GUI
    while True:
        time.sleep(3)
        gui.set_state("LISTENING")
        gui.add_log("User speaking: 'what is the time'")
        
        time.sleep(2)
        gui.set_state("PROCESSING")
        time.sleep(1)
        
        gui.set_state("SPEAKING")
        gui.add_log("Executing: tell_time")
        time.sleep(3)
        
        gui.set_state("IDLE")


if __name__ == "__main__":
    # 1. Create the GUI main loop on the primary thread
    app = JarvisGUI()
    app.add_log("Porcupine Assistant V4 Boot Sequence...")
    
    # 2. Run backend (assistant logic) on a background thread
    assistant_thread = threading.Thread(target=start_assistant_logic, args=(app,), daemon=True)
    assistant_thread.start()
    
    # 3. Start Tkinter event loop
    app.root.mainloop()

