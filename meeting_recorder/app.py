"""Small Tkinter window: pick your mic, press Record, or let it auto-record meetings."""
import json
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .audio import FORMATS, Recorder, list_devices
from .detect import MeetingWatcher

SETTINGS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "MeetingAudioRecorder" / "settings.json"
DEFAULT_OUT = Path.home() / "Documents" / "Meeting Recordings"
DEFAULT_DEVICE = "System default"
POLL_MS = 3000


def load_settings():
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2))


class App:
    def __init__(self, root):
        self.root = root
        self.recorder = None
        self.auto_started = False
        self.saving = False
        self.watcher = MeetingWatcher(self._meeting_started, self._meeting_ended)

        s = load_settings()
        self.mics, self.speakers, _, _ = list_devices()
        self.mic_var = tk.StringVar(value=self._known(s.get("mic"), self.mics))
        self.speaker_var = tk.StringVar(value=self._known(s.get("speaker"), self.speakers))
        self.out_var = tk.StringVar(value=s.get("out_dir", str(DEFAULT_OUT)))
        self.fmt_var = tk.StringVar(value=s.get("format", "mp3"))
        self.auto_var = tk.BooleanVar(value=s.get("auto", True))
        self.tracks_var = tk.BooleanVar(value=s.get("keep_tracks", False))
        self.status_var = tk.StringVar()

        root.title(f"Meeting Audio Recorder {__version__}")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._set_idle_status()
        self._tick()
        self._poll_meetings()

    @staticmethod
    def _known(name, devices):
        return name if name in [d["name"] for d in devices] else DEFAULT_DEVICE

    def _build(self):
        f = ttk.Frame(self.root, padding=14)
        f.grid()
        names = lambda devices: [DEFAULT_DEVICE] + [d["name"] for d in devices]

        ttk.Label(f, text="Microphone").grid(row=0, column=0, sticky="w")
        ttk.Combobox(f, textvariable=self.mic_var, values=names(self.mics), state="readonly",
                     width=48).grid(row=0, column=1, columnspan=2, sticky="we", pady=2)
        ttk.Label(f, text="Meeting audio from").grid(row=1, column=0, sticky="w")
        ttk.Combobox(f, textvariable=self.speaker_var, values=names(self.speakers), state="readonly",
                     width=48).grid(row=1, column=1, columnspan=2, sticky="we", pady=2)

        ttk.Label(f, text="Save to").grid(row=2, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.out_var, width=40).grid(row=2, column=1, sticky="we", pady=2)
        ttk.Button(f, text="Browse…", command=self._browse).grid(row=2, column=2, sticky="e", padx=(4, 0))

        ttk.Label(f, text="Format").grid(row=3, column=0, sticky="w")
        ttk.Combobox(f, textvariable=self.fmt_var, values=list(FORMATS), state="readonly",
                     width=8).grid(row=3, column=1, sticky="w", pady=2)

        ttk.Checkbutton(f, text="Automatically record Zoom and Teams meetings",
                        variable=self.auto_var, command=self._save).grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(f, text="Also keep separate mic and meeting tracks (.flac)",
                        variable=self.tracks_var, command=self._save).grid(row=5, column=0, columnspan=3, sticky="w")

        meters = ttk.Frame(f)
        meters.grid(row=6, column=0, columnspan=3, sticky="we", pady=(10, 0))
        self.meters = {}
        for row, (key, label) in enumerate((("meeting", "Meeting"), ("mic", "You"))):
            ttk.Label(meters, text=label, width=9).grid(row=row, column=0, sticky="w")
            bar = ttk.Progressbar(meters, length=360, maximum=1.0)
            bar.grid(row=row, column=1, pady=1)
            self.meters[key] = bar

        self.button = ttk.Button(f, text="● Start recording", command=self.toggle)
        self.button.grid(row=7, column=0, columnspan=2, sticky="we", pady=(12, 0), ipady=6)
        ttk.Button(f, text="Open folder", command=self._open_folder).grid(row=7, column=2, sticky="e", pady=(12, 0), ipady=6)
        ttk.Label(f, textvariable=self.status_var, foreground="#555").grid(row=8, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _device_index(self, name, devices):
        return next((d["index"] for d in devices if d["name"] == name), None)

    def _save(self):
        save_settings({
            "mic": self.mic_var.get(), "speaker": self.speaker_var.get(), "out_dir": self.out_var.get(),
            "format": self.fmt_var.get(), "auto": self.auto_var.get(), "keep_tracks": self.tracks_var.get(),
        })
        if not self.recorder:
            self._set_idle_status()

    def _browse(self):
        folder = filedialog.askdirectory(initialdir=self.out_var.get())
        if folder:
            self.out_var.set(folder)
            self._save()

    def _open_folder(self):
        folder = Path(self.out_var.get())
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def _set_idle_status(self):
        if self.saving:
            return
        self.status_var.set("Waiting for a Zoom or Teams meeting…" if self.auto_var.get() else "Ready.")

    # Recording ---------------------------------------------------------------

    def toggle(self):
        if self.recorder:
            self.stop()
        else:
            self.start("Recording")

    def start(self, label):
        if self.recorder or self.saving:
            return
        self._save()
        recorder = Recorder(
            self.out_var.get(),
            mic_index=self._device_index(self.mic_var.get(), self.mics),
            speaker_index=self._device_index(self.speaker_var.get(), self.speakers),
            fmt=self.fmt_var.get(),
            keep_tracks=self.tracks_var.get(),
        )
        try:
            recorder.start(label)
        except Exception as e:
            messagebox.showerror("Could not start recording", str(e))
            return
        self.recorder = recorder
        self.button.configure(text="■ Stop recording")

    def stop(self):
        recorder, self.recorder = self.recorder, None
        self.auto_started = False
        if not recorder:
            return
        self.saving = True
        self.button.configure(text="● Start recording", state="disabled")
        self.status_var.set("Saving recording…")
        result = {}

        def work():
            try:
                result["path"] = recorder.stop()
            except Exception as e:
                result["error"] = e
            self.root.after(0, done)

        def done():
            self.saving = False
            self.button.configure(state="normal")
            if "error" in result:
                messagebox.showerror("Could not save recording", str(result["error"]))
                self._set_idle_status()
            else:
                self.status_var.set(f"Saved: {result['path'].name}")
            for bar in self.meters.values():
                bar["value"] = 0

        threading.Thread(target=work, daemon=True).start()

    def _meeting_started(self, app):
        if self.auto_var.get() and not self.recorder:
            self.start(app)
            self.auto_started = self.recorder is not None

    def _meeting_ended(self, app):
        if self.auto_started:
            self.stop()

    def _poll_meetings(self):
        self.watcher.poll()
        self.root.after(POLL_MS, self._poll_meetings)

    def _tick(self):
        if self.recorder:
            seconds = int(self.recorder.elapsed)
            what = f"{self.watcher.current} meeting" if self.auto_started else "Recording"
            self.status_var.set(f"{what} — {seconds // 3600}:{seconds // 60 % 60:02}:{seconds % 60:02}")
            for key, level in self.recorder.levels().items():
                self.meters[key]["value"] = min(1.0, level * 1.5)
        self.root.after(100, self._tick)

    def close(self):
        if self.recorder or self.saving:
            if not messagebox.askyesno("Recording in progress", "Stop and save the recording, then quit?"):
                return
            self.stop()
            self._quit_when_saved()
        else:
            self.root.destroy()

    def _quit_when_saved(self):
        if self.saving:
            self.root.after(200, self._quit_when_saved)
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
