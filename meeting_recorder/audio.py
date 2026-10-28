"""Record what you hear (the meeting) and your microphone, then mix them into one file.

Windows only: the meeting side is captured with WASAPI loopback, which taps the
audio going to your speakers/headset, so it works with Zoom, Teams or anything else.
"""
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyaudiowpatch as pyaudio
import soundfile as sf
import soxr

BUFFER_SECONDS = 0.05
MIX_BLOCK = 48000  # frames read per step while mixing

FORMATS = {
    "mp3": {"format": "MP3", "subtype": "MPEG_LAYER_III"},
    "wav": {"format": "WAV", "subtype": "PCM_16"},
    "flac": {"format": "FLAC", "subtype": "PCM_16"},
}


def list_devices():
    """Return (microphones, speakers, default_mic_index, default_speaker_index)."""
    p = pyaudio.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        mics, speakers = [], []
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] != wasapi["index"] or d.get("isLoopbackDevice"):
                continue
            entry = {"index": d["index"], "name": d["name"]}
            if d["maxInputChannels"] > 0:
                mics.append(entry)
            elif d["maxOutputChannels"] > 0:
                speakers.append(entry)
        return mics, speakers, wasapi["defaultInputDevice"], wasapi["defaultOutputDevice"]
    finally:
        p.terminate()


@dataclass
class Recording:
    """A finished recording: the mixed file plus the separate meeting and mic tracks."""
    audio: Path
    meeting_track: Path
    mic_track: Path

    def discard_tracks(self):
        for path in (self.meeting_track, self.mic_track):
            path.unlink(missing_ok=True)


class _Track:
    """One input stream, written to a FLAC file by a background thread."""

    def __init__(self, p, device, path):
        self.path = path
        self.rate = int(device["defaultSampleRate"])
        self.channels = device["maxInputChannels"]
        self.level = 0.0
        self.started_at = None
        self._queue = queue.Queue()
        self._file = sf.SoundFile(path, "w", self.rate, self.channels, format="FLAC", subtype="PCM_16")
        self._writer = threading.Thread(target=self._write, daemon=True)
        self._writer.start()
        self._stream = p.open(
            format=pyaudio.paFloat32,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=device["index"],
            frames_per_buffer=int(self.rate * BUFFER_SECONDS),
            stream_callback=self._callback,
        )

    def _callback(self, data, frames, info, status):
        if self.started_at is None:
            self.started_at = time.monotonic() - frames / self.rate
        block = np.frombuffer(data, dtype=np.float32).reshape(-1, self.channels)
        self.level = float(np.abs(block).max()) if block.size else 0.0
        self._queue.put(block.copy())
        return None, pyaudio.paContinue

    def _write(self):
        while (block := self._queue.get()) is not None:
            self._file.write(block)

    def close(self):
        self._stream.stop_stream()
        self._stream.close()
        self._queue.put(None)
        self._writer.join()
        self._file.close()


class _Source:
    """Reads a recorded track back as stereo float32 at a target sample rate."""

    def __init__(self, path, rate, delay_seconds, mono):
        self._file = sf.SoundFile(path)
        self._mono = mono
        self._resampler = None
        if self._file.samplerate != rate:
            self._resampler = soxr.ResampleStream(self._file.samplerate, rate, 2, dtype="float32")
        self._buf = np.zeros((int(delay_seconds * rate), 2), np.float32)
        self._eof = False
        self.last_real = 0  # frames of the last take() that were real audio, not padding

    @property
    def exhausted(self):
        return self._eof and len(self._buf) == 0

    def _stereo(self, block):
        if self._mono or block.shape[1] == 1:
            mono = block.mean(axis=1, keepdims=True)
            return np.repeat(mono, 2, axis=1)
        return block[:, :2]

    def take(self, n):
        while len(self._buf) < n and not self._eof:
            block = self._file.read(MIX_BLOCK, dtype="float32", always_2d=True)
            self._eof = len(block) < MIX_BLOCK
            block = self._stereo(block)
            if self._resampler:
                block = self._resampler.resample_chunk(block, last=self._eof)
            self._buf = np.concatenate([self._buf, block])
        out, self._buf = self._buf[:n], self._buf[n:]
        self.last_real = len(out)
        if len(out) < n:
            out = np.pad(out, ((0, n - len(out)), (0, 0)))
        return out

    def close(self):
        self._file.close()


class Recorder:
    """Records meeting audio + microphone. Call start(), then stop() to get the saved Recording."""

    def __init__(self, out_dir, mic_index=None, speaker_index=None, fmt="mp3",
                 own_folder=True, mic_gain=1.0, meeting_gain=1.0):
        self.out_dir = Path(out_dir)
        self.mic_index = mic_index
        self.speaker_index = speaker_index
        self.fmt = fmt
        self.own_folder = own_folder  # put each recording in its own "<date> <time> <label>" folder
        self.mic_gain = mic_gain
        self.meeting_gain = meeting_gain
        self.started_at = None
        self._p = None
        self._tracks = {}
        self._keepalive = None

    @property
    def recording(self):
        return self._p is not None

    @property
    def elapsed(self):
        return time.monotonic() - self.started_at if self.started_at else 0.0

    def levels(self):
        """Peak level (0..1) of the latest buffer for 'meeting' and 'mic'."""
        return {name: t.level for name, t in self._tracks.items()}

    def _new_base(self, label):
        """Path (without extension) for a new recording, e.g. .../2026-10-28 14-30 Teams/2026-10-28 14-30 Teams.

        Adds " (2)", " (3)"… rather than overwrite an earlier recording from the same minute.
        """
        stamp = f"{time.strftime('%Y-%m-%d %H-%M')} {label}"
        n = 1
        while True:
            name = stamp if n == 1 else f"{stamp} ({n})"
            folder = self.out_dir / name if self.own_folder else self.out_dir
            taken = folder.exists() if self.own_folder else Path(f"{folder / name}.{self.fmt}").exists()
            if not taken:
                folder.mkdir(parents=True, exist_ok=True)
                return folder / name
            n += 1

    def start(self, label="Meeting"):
        self._base = self._new_base(label)
        self._p = pyaudio.PyAudio()
        try:
            wasapi = self._p.get_host_api_info_by_type(pyaudio.paWASAPI)
            speaker = self._p.get_device_info_by_index(
                wasapi["defaultOutputDevice"] if self.speaker_index is None else self.speaker_index)
            mic = self._p.get_device_info_by_index(
                wasapi["defaultInputDevice"] if self.mic_index is None else self.mic_index)
            loopback = self._p.get_wasapi_loopback_analogue_by_dict(speaker)

            # Loopback only delivers data while something is playing. Playing silence
            # keeps it flowing so the meeting track stays in sync with the mic track.
            silence = bytes(4 * int(speaker["maxOutputChannels"]) * int(speaker["defaultSampleRate"]))
            self._keepalive = self._p.open(
                format=pyaudio.paFloat32,
                channels=speaker["maxOutputChannels"],
                rate=int(speaker["defaultSampleRate"]),
                output=True,
                output_device_index=speaker["index"],
                stream_callback=lambda _d, frames, _i, _s: (silence[: frames * 4 * speaker["maxOutputChannels"]],
                                                            pyaudio.paContinue),
            )
            self._tracks["meeting"] = _Track(self._p, loopback, f"{self._base} - meeting.flac")
            self._tracks["mic"] = _Track(self._p, mic, f"{self._base} - mic.flac")
        except Exception:
            self._close_streams()
            for track in ("meeting", "mic"):
                Path(f"{self._base} - {track}.flac").unlink(missing_ok=True)
            if self.own_folder and not any(self._base.parent.iterdir()):
                self._base.parent.rmdir()
            raise
        self.started_at = time.monotonic()
        return self

    def _close_streams(self):
        for track in self._tracks.values():
            track.close()
        if self._keepalive:
            self._keepalive.stop_stream()
            self._keepalive.close()
            self._keepalive = None
        if self._p:
            self._p.terminate()
            self._p = None

    def stop(self):
        """Stop recording and mix both tracks into one file.

        The separate tracks stay on disk; call discard_tracks() on the result when
        you no longer need them (they are used for transcription).
        """
        if not self.recording:
            return None
        tracks = dict(self._tracks)
        self._close_streams()
        self._tracks = {}
        self.started_at = None

        out_path = Path(f"{self._base}.{self.fmt}")
        _mix(tracks, out_path, self.fmt, {"meeting": self.meeting_gain, "mic": self.mic_gain})
        return Recording(out_path, Path(tracks["meeting"].path), Path(tracks["mic"].path))


def _mix(tracks, out_path, fmt, gains):
    rate = tracks["meeting"].rate
    first = min(t.started_at or 0 for t in tracks.values())
    sources = {
        name: _Source(t.path, rate, (t.started_at or first) - first, mono=(name == "mic"))
        for name, t in tracks.items()
    }
    try:
        with sf.SoundFile(out_path, "w", rate, 2, **FORMATS[fmt]) as out:
            while not all(s.exhausted for s in sources.values()):
                mixed = sum(s.take(MIX_BLOCK) * gains[name] for name, s in sources.items())
                real = max(s.last_real for s in sources.values())
                out.write(np.clip(mixed[:real], -1.0, 1.0))
    finally:
        for s in sources.values():
            s.close()
