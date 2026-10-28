"""Turn recordings into a .txt transcript with Whisper, running on this PC (faster-whisper).

The mic and meeting tracks are transcribed separately, so each line can be labelled
"You" or "Others". The first run downloads the chosen speech model from Hugging Face.
"""
import difflib
import os
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

WHISPER_RATE = 16000
PAUSE_SECONDS = 1.5
MODELS = {  # name: approximate download size
    "tiny": "75 MB",
    "base": "145 MB",
    "small": "465 MB",
    "medium": "1.5 GB",
}
DEFAULT_MODEL = "base"

_models = {}


def load_model(name, status=lambda msg: None):
    if name not in _models:
        status(f"Loading speech model '{name}' (first time downloads {MODELS.get(name, 'it')})…")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from faster_whisper import WhisperModel  # heavy import, only when needed
        _models[name] = WhisperModel(name, device="cpu", compute_type="int8",
                                     cpu_threads=max(1, (os.cpu_count() or 2) // 2))
    return _models[name]


def _load_audio(path):
    """Read any audio file as 16 kHz mono float32, which is what Whisper expects."""
    with sf.SoundFile(path) as f:
        resampler = soxr.ResampleStream(f.samplerate, WHISPER_RATE, 1, dtype="float32")
        chunks = []
        for block in f.blocks(blocksize=f.samplerate * 10, dtype="float32", always_2d=True):
            chunks.append(resampler.resample_chunk(block.mean(axis=1)))
        chunks.append(resampler.resample_chunk(np.zeros(0, np.float32), last=True))
    return np.concatenate(chunks)


def _segments(model, path, language, progress):
    segments, info = model.transcribe(_load_audio(path), language=language, vad_filter=True,
                                      condition_on_previous_text=False, word_timestamps=True)
    found = []
    for s in segments:
        # Whisper can glue together speech either side of a long silence (when the
        # other person was talking), so split wherever the words pause.
        piece = []
        for word in s.words or []:
            if piece and word.start - piece[-1].end > PAUSE_SECONDS:
                found.append(_piece(piece))
                piece = []
            piece.append(word)
        if piece:
            found.append(_piece(piece))
        if info.duration:
            progress(min(1.0, s.end / info.duration))
    progress(1.0)
    return [p for p in found if p[2]], info


def _piece(words):
    return words[0].start, words[-1].end, "".join(w.word for w in words).strip()


def _is_echo(seg, others):
    """True if a mic segment is just the meeting audio picked up from your speakers."""
    start, end, text = seg
    for o_start, o_end, o_text in others:
        if o_start - 1 <= end and start <= o_end + 1:
            if difflib.SequenceMatcher(None, text.lower(), o_text.lower()).ratio() > 0.6:
                return True
    return False


def _stamp(seconds):
    s = int(seconds)
    return f"{s // 3600:02}:{s // 60 % 60:02}:{s % 60:02}"


def _write(path, title, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{title}\n\n")
        if not lines:
            f.write("(No speech was detected.)\n")
        for start, speaker, text in lines:
            f.write(f"[{_stamp(start)}] {speaker + ': ' if speaker else ''}{text}\n\n")


def transcribe_meeting(meeting_track, mic_track, out_path, model_name=DEFAULT_MODEL, title="",
                       progress=lambda fraction: None, status=lambda msg: None):
    """Transcribe both tracks of a recording into one transcript labelled You / Others."""
    model = load_model(model_name, status)
    status("Transcribing…")
    others, info = _segments(model, meeting_track, None, lambda f: progress(f / 2))
    language = info.language if others and info.language_probability > 0.5 else None
    mine, _ = _segments(model, mic_track, language, lambda f: progress(0.5 + f / 2))
    mine = [s for s in mine if not _is_echo(s, others)]

    tagged = sorted([(s, "Others") for s in others] + [(s, "You") for s in mine], key=lambda x: x[0][0])
    lines = []  # (start, speaker, text), joining consecutive lines from the same speaker
    for (start, _, text), speaker in tagged:
        if lines and lines[-1][1] == speaker:
            lines[-1] = (lines[-1][0], speaker, f"{lines[-1][2]} {text}")
        else:
            lines.append((start, speaker, text))
    _write(out_path, title or Path(out_path).stem, lines)
    return Path(out_path)


def transcribe_file(audio_path, out_path=None, model_name=DEFAULT_MODEL,
                    progress=lambda fraction: None, status=lambda msg: None):
    """Transcribe any audio file (no speaker labels). Saves next to it as .txt by default."""
    out_path = Path(out_path or Path(audio_path).with_suffix(".txt"))
    model = load_model(model_name, status)
    status("Transcribing…")
    segments, _ = _segments(model, audio_path, None, progress)
    _write(out_path, Path(audio_path).stem, [(start, "", text) for start, _, text in segments])
    return out_path


def transcribe_recording(recording, model_name=DEFAULT_MODEL, keep_tracks=False, title="",
                         progress=lambda fraction: None, status=lambda msg: None):
    """Write <recording>.txt next to the audio, then remove the tracks unless asked to keep them."""
    try:
        return transcribe_meeting(recording.meeting_track, recording.mic_track,
                                  recording.audio.with_suffix(".txt"), model_name, title, progress, status)
    finally:
        if not keep_tracks:
            recording.discard_tracks()
