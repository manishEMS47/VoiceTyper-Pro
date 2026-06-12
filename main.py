import customtkinter as ctk
import threading
from pynput import keyboard
import codecs
import time
import pyaudio
import wave
import os
from playsound import playsound
from datetime import datetime
from deepgram import Deepgram
from dotenv import load_dotenv
from PIL import Image
import asyncio
import pystray
import json
import requests
from deepgram.errors import DeepgramSetupError

# Set theme and color scheme
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ---------------------------------------------------------------------------
# Speech-to-text providers
#
# Every provider exposes the same contract -- transcribe(audio_file) -> str --
# so the rest of the app (recording, typing, logging) never has to know which
# backend is active. Add a new provider by subclassing STTProvider and
# registering it in VoiceTyperApp.build_provider().
# ---------------------------------------------------------------------------
class STTProvider:
    """Common interface for all transcription backends."""
    name = "base"

    def transcribe(self, audio_file):
        """Transcribe a WAV file on disk and return the plain transcript text."""
        raise NotImplementedError


class DeepgramProvider(STTProvider):
    """Deepgram pre-recorded (batch) transcription via the v2 SDK."""
    name = "deepgram"

    def __init__(self, api_key):
        # Deepgram() validates the key format synchronously and raises
        # DeepgramSetupError if it is malformed.
        self.client = Deepgram(api_key)

    def transcribe(self, audio_file):
        return asyncio.run(self._transcribe(audio_file))

    async def _transcribe(self, audio_file):
        with open(audio_file, 'rb') as audio:
            source = {'buffer': audio, 'mimetype': 'audio/wav'}
            options = {
                'punctuate': True,
                'language': 'en',
                'model': 'general',
            }
            response = await self.client.transcription.prerecorded(source, options)
            return response['results']['channels'][0]['alternatives'][0]['transcript']


class SixtyDBProvider(STTProvider):
    """60db speech-to-text via the REST API (https://api.60db.ai/stt)."""
    name = "60db"
    STT_URL = "https://api.60db.ai/stt"

    def __init__(self, api_key):
        if not api_key or not api_key.strip():
            # Mirror Deepgram's "bad key at setup" behaviour so the UI can
            # surface the API-key dialog. The key itself is only verified
            # against the server on the first transcription request.
            raise DeepgramSetupError("Missing 60db API key")
        self.api_key = api_key.strip()

    def transcribe(self, audio_file):
        headers = {'Authorization': f'Bearer {self.api_key}'}
        with open(audio_file, 'rb') as audio:
            files = {'file': (os.path.basename(audio_file), audio, 'audio/wav')}
            data = {'language': 'en'}
            response = requests.post(
                self.STT_URL,
                headers=headers,
                files=files,
                data=data,
                timeout=60,
            )
        response.raise_for_status()
        return response.json().get('text', '')

class SettingsDialog:
    PROVIDERS = ["60db", "deepgram"]

    def __init__(self, parent, app=None):
        self.app = app
        self.dialog = ctk.CTkToplevel(parent)
        self.dialog.title("Settings")
        self.dialog.geometry("400x320")
        self.dialog.transient(parent)
        self.dialog.resizable(False, False)

        # Load current settings
        try:
            with open('settings.json', 'r') as f:
                self.settings = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.settings = {}

        # Per-provider keys (migrate the legacy single api_key onto Deepgram)
        self.keys = {
            "deepgram": self.settings.get('deepgram_api_key', self.settings.get('api_key', '')),
            "60db": self.settings.get('60db_api_key', ''),
        }
        self.current_provider = self.settings.get('provider', '60db')
        if self.current_provider not in self.PROVIDERS:
            self.current_provider = '60db'

        self.frame = ctk.CTkFrame(self.dialog)
        self.frame.pack(fill="x", padx=20, pady=20)

        # Provider selector
        self.provider_label = ctk.CTkLabel(
            self.frame,
            text="Transcription Provider:",
            font=ctk.CTkFont(size=14)
        )
        self.provider_label.pack(anchor="w", pady=(5, 0))

        self.provider_var = ctk.StringVar(value=self.current_provider)
        self.provider_menu = ctk.CTkOptionMenu(
            self.frame,
            values=self.PROVIDERS,
            variable=self.provider_var,
            command=self.on_provider_change,
            width=300
        )
        self.provider_menu.pack(pady=5)

        # API key input (contents follow the selected provider)
        self.api_label = ctk.CTkLabel(
            self.frame,
            text="API Key:",
            font=ctk.CTkFont(size=14)
        )
        self.api_label.pack(anchor="w", pady=(10, 0))

        self.api_entry = ctk.CTkEntry(
            self.frame,
            width=300,
            font=ctk.CTkFont(size=14)
        )
        self.api_entry.pack(pady=5)
        self.api_entry.insert(0, self.keys.get(self.current_provider, ''))
        self._update_key_label()

        # Save button
        self.save_btn = ctk.CTkButton(
            self.dialog,
            text="Save",
            command=self.save_settings,
            width=100
        )
        self.save_btn.pack(pady=20)

    def _update_key_label(self):
        self.api_label.configure(text=f"{self.current_provider} API Key:")

    def on_provider_change(self, value):
        # Stash whatever is currently typed before swapping the field contents.
        self.keys[self.current_provider] = self.api_entry.get()
        self.current_provider = value
        self.api_entry.delete(0, 'end')
        self.api_entry.insert(0, self.keys.get(value, ''))
        self._update_key_label()

    def save_settings(self):
        self.keys[self.current_provider] = self.api_entry.get()
        self.settings['provider'] = self.current_provider
        self.settings['deepgram_api_key'] = self.keys['deepgram']
        self.settings['60db_api_key'] = self.keys['60db']
        # Keep the legacy field in sync for backward compatibility.
        self.settings['api_key'] = self.keys['deepgram']
        with open('settings.json', 'w') as f:
            json.dump(self.settings, f, indent=2)
        self.dialog.destroy()
        if self.app is not None:
            self.app.reload_provider()

class VoiceTyperApp:
    def __init__(self):
        self.root = ctk.CTk()
        self.root.title("Voice Typer Pro")
        self.root.geometry("400x250")
        self.root.minsize(400, 250)
        
        # Initialize variables
        self.is_recording = False
        self.file_ready_counter = 0
        self.stop_recording = False
        self.pykeyboard = keyboard.Controller()
        self.recording_animation_active = False

        # Build the UI before anything that might report status/errors into it.
        self.setup_ui()

        # Initialize system tray
        self.setup_system_tray()

        # Track if log section is expanded
        self.log_expanded = False  # Start with log collapsed

        # Global keyboard listener
        self.keyboard_listener = keyboard.Listener(
            on_press=self.on_key_press,
            on_release=self.on_key_release
        )
        self.keyboard_listener.start()

        # Load settings and initialize the selected transcription provider.
        try:
            self.load_settings()
        except DeepgramSetupError:
            # Show the API-key dialog immediately if the key is missing/invalid.
            self.show_api_key_error()
        except Exception as e:
            self.show_error(f"Error: {str(e)}")

        self.start_transcription_thread()
        
    def show_api_key_error(self):
        provider_name = self.settings.get('provider', '60db')

        error_dialog = ctk.CTkToplevel(self.root)
        error_dialog.title("API Key Error")
        error_dialog.geometry("400x200")
        error_dialog.transient(self.root)
        error_dialog.resizable(False, False)

        # Center the dialog
        error_dialog.geometry("+%d+%d" % (
            self.root.winfo_x() + (self.root.winfo_width() - 400) // 2,
            self.root.winfo_y() + (self.root.winfo_height() - 200) // 2
        ))

        # Error message
        message = ctk.CTkLabel(
            error_dialog,
            text=f"Missing or invalid {provider_name} API Key.\nPlease enter a valid API key to continue.",
            font=ctk.CTkFont(size=14),
            wraplength=350
        )
        message.pack(pady=20)

        # API Key input
        api_entry = ctk.CTkEntry(
            error_dialog,
            width=300,
            font=ctk.CTkFont(size=14)
        )
        api_entry.pack(pady=10)
        api_entry.insert(0, self.get_key_for(provider_name))

        def save_and_retry():
            new_key = api_entry.get()
            try:
                # Try to initialize the active provider with the new key.
                self.provider = self.build_provider(provider_name, new_key)
                # If successful, persist the key for that provider.
                if provider_name == 'deepgram':
                    self.settings['deepgram_api_key'] = new_key
                    self.settings['api_key'] = new_key
                else:
                    self.settings[f'{provider_name}_api_key'] = new_key
                with open('settings.json', 'w') as f:
                    json.dump(self.settings, f, indent=2)
                error_dialog.destroy()
                self.status_label.configure(text="API Key updated successfully!")
            except DeepgramSetupError:
                message.configure(text="Invalid API Key. Please try again.", text_color="red")
        
        # Save button
        save_btn = ctk.CTkButton(
            error_dialog,
            text="Save & Retry",
            command=save_and_retry,
            width=120
        )
        save_btn.pack(pady=20)
        
    def show_error(self, error_message):
        self.status_label.configure(
            text=error_message,
            text_color="red"
        )
        
    def load_settings(self):
        try:
            with open('settings.json', 'r') as f:
                self.settings = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.settings = {}

        # Migrate the legacy single-key format onto Deepgram.
        if 'api_key' in self.settings and 'deepgram_api_key' not in self.settings:
            self.settings['deepgram_api_key'] = self.settings['api_key']
        self.settings.setdefault('provider', '60db')

        provider_name = self.settings['provider']
        try:
            self.provider = self.build_provider(provider_name, self.get_key_for(provider_name))
        except DeepgramSetupError:
            raise
        except Exception as e:
            raise Exception(f"Failed to initialize {provider_name}: {str(e)}")

    def get_key_for(self, provider_name):
        """Return the stored API key for a provider from self.settings."""
        if provider_name == 'deepgram':
            return self.settings.get('deepgram_api_key', self.settings.get('api_key', ''))
        return self.settings.get(f'{provider_name}_api_key', '')

    def build_provider(self, provider_name, api_key):
        """Construct an STTProvider instance for the given backend name."""
        if provider_name == 'deepgram':
            return DeepgramProvider(api_key)
        elif provider_name == '60db':
            return SixtyDBProvider(api_key)
        raise ValueError(f"Unknown provider: {provider_name}")

    def reload_provider(self):
        """Rebuild the active provider after settings change (called from the dialog)."""
        try:
            self.load_settings()
            self.status_label.configure(
                text=f"Ready to record... ({self.settings['provider']})",
                text_color=("gray10", "gray90")
            )
        except DeepgramSetupError:
            self.show_api_key_error()
        except Exception as e:
            self.show_error(f"Error: {str(e)}")
        
    def setup_system_tray(self):
        # Create system tray icon
        self.icon_image = Image.new('RGB', (64, 64), color='blue')
        self.tray_icon = pystray.Icon(
            "Voice Typer",
            self.icon_image,
            menu=pystray.Menu(
                pystray.MenuItem("Show", self.show_window),
                pystray.MenuItem("Exit", self.quit_app)
            )
        )
        
    def setup_ui(self):
        # Main container with padding
        self.main_frame = ctk.CTkFrame(self.root)
        self.main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Header frame with title and settings button
        self.header_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.header_frame.pack(fill="x", pady=5)
        
        # Title
        self.title_label = ctk.CTkLabel(
            self.header_frame,
            text="Voice Typer Pro",
            font=ctk.CTkFont(size=20, weight="bold")
        )
        self.title_label.pack(side="left", padx=10)
        
        # Settings button
        self.settings_btn = ctk.CTkButton(
            self.header_frame,
            text="⚙️",
            width=40,
            command=self.open_settings
        )
        self.settings_btn.pack(side="right", padx=10)
        
        # Record button and indicator in one frame
        self.control_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.control_frame.pack(fill="x", pady=10)
        
        self.record_button = ctk.CTkButton(
            self.control_frame,
            text="Start Recording (F2)",
            command=self.toggle_recording,
            height=40,
            corner_radius=20
        )
        self.record_button.pack(pady=5)
        
        self.recording_indicator = ctk.CTkProgressBar(
            self.control_frame,
            width=200,
            height=6
        )
        self.recording_indicator.pack(pady=5)
        self.recording_indicator.set(0)
        
        # Status label
        self.status_label = ctk.CTkLabel(
            self.main_frame,
            text="Ready to record...",
            font=ctk.CTkFont(size=12)
        )
        self.status_label.pack(pady=5)
        
        # Create a container frame for log section
        self.log_container = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.log_container.pack(fill="x", expand=False)
        
        # Log controls in container
        self.toggle_log_btn = ctk.CTkButton(
            self.log_container,
            text="▶ Show Log",
            command=self.toggle_log_section,
            width=100,
            height=28,
            fg_color=["#2B2B2B", "#333333"],
            hover_color=["#333333", "#404040"]
        )
        self.toggle_log_btn.pack(side="left", padx=5)
        
        self.clear_log_btn = ctk.CTkButton(
            self.log_container,
            text="Clear",
            command=self.clear_logs,
            width=60,
            height=28,
            fg_color="#c93434",
            hover_color="#a82a2a"
        )
        self.clear_log_btn.pack(side="right", padx=5)
        
        # Log frame and content
        self.log_frame = ctk.CTkFrame(self.main_frame)
        self.transcription_text = ctk.CTkTextbox(
            self.log_frame,
            height=200,
            font=ctk.CTkFont(size=12)
        )
        self.transcription_text.pack(fill="both", expand=True, pady=5)
        
    def animate_recording(self):
        if self.recording_animation_active:
            current = self.recording_indicator.get()
            if current >= 1:
                self.recording_indicator.set(0)
            else:
                self.recording_indicator.set(current + 0.05)  # Smoother animation
            self.root.after(50, self.animate_recording)  # Faster updates
            
            # Pulse effect on record button
            current_color = self.record_button.cget("fg_color")
            if current_color == "#c93434":
                self.record_button.configure(fg_color="#a82a2a")
            else:
                self.record_button.configure(fg_color="#c93434")
    
    def toggle_recording(self):
        if not hasattr(self, 'provider'):
            self.show_api_key_error()
            return
            
        if not self.is_recording:
            self.start_recording()
            # Start animation with pulsing effect
            self.recording_animation_active = True
            self.record_button.configure(
                fg_color="#c93434",
                text="■ Stop Recording (F2)"  # Square stop symbol
            )
            self.animate_recording()
        else:
            self.stop_recording = True
            # Stop animation
            self.recording_animation_active = False
            self.record_button.configure(
                fg_color=["#3B8ED0", "#1F6AA5"],
                text="● Start Recording (F2)"  # Circle record symbol
            )
            self.recording_indicator.set(0)
    
    def on_key_press(self, key):
        try:
            if key == keyboard.Key.f2:
                self.root.after(0, self.toggle_recording)
        except AttributeError:
            pass
    
    def on_key_release(self, key):
        pass
            
    def start_recording(self):
        threading.Thread(target=self.record_speech, daemon=True).start()
        
    def record_speech(self):
        self.is_recording = True
        chunk = 1024
        sample_format = pyaudio.paInt16
        channels = 2
        fs = 44100
        
        p = pyaudio.PyAudio()
        stream = p.open(
            format=sample_format,
            channels=channels,
            rate=fs,
            frames_per_buffer=chunk,
            input=True
        )
        
        frames = []
        playsound("assets/on.wav")
        
        while not self.stop_recording:
            data = stream.read(chunk)
            frames.append(data)
            
        stream.stop_stream()
        stream.close()
        p.terminate()
        playsound("assets/off.wav")
        
        # Save recording
        wf = wave.open(f"test{self.file_ready_counter+1}.wav", 'wb')
        wf.setnchannels(channels)
        wf.setsampwidth(p.get_sample_size(sample_format))
        wf.setframerate(fs)
        wf.writeframes(b''.join(frames))
        wf.close()
        
        self.stop_recording = False
        self.is_recording = False
        self.file_ready_counter += 1
        
        self.status_label.configure(text="Processing transcription...")
        
    def start_transcription_thread(self):
        threading.Thread(target=self.transcribe_speech).start()
        
    def transcribe_speech(self):
        i = 1
        
        while True:
            while self.file_ready_counter < i:
                time.sleep(0.01)
                
            audio_file = f"test{i}.wav"
            try:
                transcript = self.provider.transcribe(audio_file)
                
                # Update GUI
                self.transcription_text.insert('1.0', f"{datetime.now().strftime('%H:%M:%S')}: {transcript}\n\n")
                self.status_label.configure(text="Ready to record...")
                
                # Log transcription
                with codecs.open('transcribe.log', 'a', encoding='utf-8') as f:
                    f.write(f"{datetime.now()}: {transcript}\n")
                    
                # Type the text
                for element in transcript:
                    try:
                        self.pykeyboard.type(element)
                        time.sleep(0.0025)
                    except:
                        print("empty or unknown symbol")
                        
                os.remove(audio_file)
                i += 1
                
            except Exception as e:
                self.status_label.configure(text=f"Error: {str(e)}")
                i += 1

    def __del__(self):
        # Clean up keyboard listener
        if hasattr(self, 'keyboard_listener'):
            self.keyboard_listener.stop()

    def animate_window_resize(self, target_height, current_height=None, step=0):
        total_steps = 15  # Move this outside the if statement
        
        if current_height is None:
            current_height = self.root.winfo_height()
            height_diff = target_height - current_height
            self.height_step = height_diff / total_steps
        
        if step < total_steps:
            new_height = int(current_height + self.height_step)
            self.root.geometry(f"400x{new_height}")
            self.root.after(10, lambda: self.animate_window_resize(target_height, new_height, step + 1))
        else:
            self.root.geometry(f"400x{target_height}")
            # Ensure proper packing of log frame after animation
            if self.log_expanded:
                self.log_frame.pack(fill="both", expand=True, pady=5)
            else:
                self.log_frame.pack_forget()

    def toggle_log_section(self):
        if not hasattr(self, 'log_expanded'):
            self.log_expanded = False
            
        self.log_expanded = not self.log_expanded
        
        if self.log_expanded:
            self.toggle_log_btn.configure(
                text="▼ Hide Log",
                fg_color="#c93434",
                hover_color="#a82a2a"
            )
            self.log_frame.pack(fill="both", expand=True, pady=5)
            self.animate_window_resize(600)
        else:
            self.toggle_log_btn.configure(
                text="▶ Show Log",
                fg_color=["#2B2B2B", "#333333"],
                hover_color=["#333333", "#404040"]
            )
            self.log_frame.pack_forget()
            self.animate_window_resize(250)
            
    def clear_logs(self):
        self.transcription_text.delete('1.0', 'end')
        # Also clear the log file
        with open('transcribe.log', 'w', encoding='utf-8') as f:
            f.write('')
            
    def minimize_to_tray(self):
        self.root.withdraw()  # Hide the window
        if not self.tray_icon.visible:
            # Start system tray icon in a separate thread
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
            
    def show_window(self):
        self.tray_icon.stop()
        self.root.after(0, self.root.deiconify)
        
    def quit_app(self):
        self.tray_icon.stop()
        self.root.quit()

    def open_settings(self):
        SettingsDialog(self.root, self)

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = VoiceTyperApp()
    app.run() 