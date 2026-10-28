"""Small Tkinter window: pick your mic, press Record, or let it auto-record meetings."""
import json
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .audio import FORMATS, Recorder, list_devices
from .detect import MeetingWatcher
from .transcribe import DEFAULT_MODEL, MODELS, transcribe_file, transcribe_recording

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
        # Transcripts are made one at a time on a background thread, so a new
        # meeting can be recorded while the last one is still being transcribed.
        self.jobs = queue.Queue()
        self.jobs_pending = 0
        self.transcribe_status = ""
        threading.Thread(target=self._transcribe_worker, daemon=True).start()

        s = load_settings()
        self.mics, self.speakers, _, _ = list_devices()
        self.mic_var = tk.StringVar(value=self._known(s.get("mic"), self.mics))
        self.speaker_var = tk.StringVar(value=self._known(s.get("speaker"), self.speakers))
        self.out_var = tk.StringVar(value=s.get("out_dir", str(DEFAULT_OUT)))
        self.fmt_var = tk.StringVar(value=s.get("format", "mp3"))
        self.auto_var = tk.BooleanVar(value=s.get("auto", True))
        self.tracks_var = tk.BooleanVar(value=s.get("keep_tracks", False))
        self.transcribe_var = tk.BooleanVar(value=s.get("transcribe", True))
        self.model_var = tk.StringVar(value=s.get("model") if s.get("model") in MODELS else DEFAULT_MODEL)
        self.status_var = tk.StringVar()
        self.transcribe_status_var = tk.StringVar()

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

        transcribe = ttk.Frame(f)
        transcribe.grid(row=6, column=0, columnspan=3, sticky="we")
        ttk.Checkbutton(transcribe, text="Transcribe to a text file (.txt)  —  accuracy:",
                        variable=self.transcribe_var, command=self._save).grid(row=0, column=0, sticky="w")
        model = ttk.Combobox(transcribe, textvariable=self.model_var, values=list(MODELS), state="readonly", width=8)
        model.grid(row=0, column=1, padx=(4, 0))
        model.bind("<<ComboboxSelected>>", lambda _e: self._save())

        meters = ttk.Frame(f)
        meters.grid(row=7, column=0, columnspan=3, sticky="we", pady=(10, 0))
        self.meters = {}
        for row, (key, label) in enumerate((("meeting", "Meeting"), ("mic", "You"))):
            ttk.Label(meters, text=label, width=9).grid(row=row, column=0, sticky="w")
            bar = ttk.Progressbar(meters, length=360, maximum=1.0)
            bar.grid(row=row, column=1, pady=1)
            self.meters[key] = bar

        self.button = ttk.Button(f, text="● Start recording", command=self.toggle)
        self.button.grid(row=8, column=0, columnspan=2, sticky="we", pady=(12, 0), ipady=6)
        ttk.Button(f, text="Open folder", command=self._open_folder).grid(row=8, column=2, sticky="e", pady=(12, 0), ipady=6)
        ttk.Label(f, textvariable=self.status_var, foreground="#555").grid(row=9, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(f, text="Transcribe a file…", command=self._transcribe_file).grid(row=9, column=2, sticky="e", pady=(8, 0))
        ttk.Label(f, textvariable=self.transcribe_status_var, foreground="#555").grid(row=10, column=0, columnspan=3, sticky="w")

    def _device_index(self, name, devices):
        return next((d["index"] for d in devices if d["name"] == name), None)

    def _save(self):
        save_settings({
            "mic": self.mic_var.get(), "speaker": self.speaker_var.get(), "out_dir": self.out_var.get(),
            "format": self.fmt_var.get(), "auto": self.auto_var.get(), "keep_tracks": self.tracks_var.get(),
            "transcribe": self.transcribe_var.get(), "model": self.model_var.get(),
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
        keep_tracks, transcribe, model = self.tracks_var.get(), self.transcribe_var.get(), self.model_var.get()

        def work():
            try:
                recording = recorder.stop()
                result["path"] = recording.audio
                if transcribe:
                    self._queue_transcript(recording.audio.name, lambda progress, status: transcribe_recording(
                        recording, model, keep_tracks, progress=progress, status=status))
                elif not keep_tracks:
                    recording.discard_tracks()
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

    # Transcription -----------------------------------------------------------

    def _queue_transcript(self, name, job):
        self.jobs_pending += 1
        self.transcribe_status = f"Waiting to transcribe {name}…"
        self.jobs.put((name, job))

    def _transcribe_worker(self):
        while True:
            name, job = self.jobs.get()

            def status(msg):
                self.transcribe_status = msg

            def progress(fraction):
                self.transcribe_status = f"Transcribing {name}… {int(fraction * 100)}%"

            try:
                path = job(progress, status)
                self.transcribe_status = f"Transcript saved: {path.name}"
            except Exception as e:
                self.transcribe_status = f"Transcription failed: {e}"
            self.jobs_pending -= 1

    def _transcribe_file(self):
        path = filedialog.askopenfilename(
            initialdir=self.out_var.get(), title="Choose a recording to transcribe",
            filetypes=[("Audio", "*.mp3 *.wav *.flac *.ogg"), ("All files", "*.*")])
        if path:
            model = self.model_var.get()
            self._queue_transcript(Path(path).name, lambda progress, status: transcribe_file(
                path, model_name=model, progress=progress, status=status))

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
        if self.transcribe_status_var.get() != self.transcribe_status:
            self.transcribe_status_var.set(self.transcribe_status)
        self.root.after(100, self._tick)

    def close(self):
        if self.recorder or self.saving:
            if not messagebox.askyesno("Recording in progress", "Stop and save the recording, then quit?"):
                return
            self.stop()
            self._quit_when_saved()
        elif self.jobs_pending:
            if messagebox.askyesno("Transcription in progress",
                                   "A transcript is still being made. Quit anyway?\n\n"
                                   "The audio is already saved. You can transcribe it later "
                                   "with “Transcribe a file…”."):
                self.root.destroy()
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
