"""Command-line mode: python -m meeting_recorder --cli [options]"""
import argparse
import time

from .audio import FORMATS, Recorder, list_devices
from .detect import MeetingWatcher
from .transcribe import DEFAULT_MODEL, MODELS, transcribe_file, transcribe_recording

DEFAULT_OUT = "Meeting Recordings"


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="meeting-recorder", description="Record meeting audio and your mic.")
    ap.add_argument("--cli", action="store_true", help="run in the terminal instead of opening the window")
    ap.add_argument("--list-devices", action="store_true", help="show microphones and speakers, then exit")
    ap.add_argument("--auto", action="store_true", help="wait for Zoom/Teams meetings and record each one")
    ap.add_argument("--mic", type=int, help="microphone device index (default: Windows default)")
    ap.add_argument("--speaker", type=int, help="speaker/headset device index (default: Windows default)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="folder to save recordings in")
    ap.add_argument("--format", choices=list(FORMATS), default="mp3")
    ap.add_argument("--keep-tracks", action="store_true", help="also keep separate mic/meeting .flac files")
    ap.add_argument("--duration", type=float, help="stop after this many seconds")
    ap.add_argument("--transcribe", action="store_true", help="also save a .txt transcript of each recording")
    ap.add_argument("--model", choices=list(MODELS), default=DEFAULT_MODEL,
                    help="speech model for transcripts: bigger is more accurate but slower")
    ap.add_argument("--vocabulary", default="", metavar="TERMS",
                    help='names and terms to expect in transcripts, e.g. "CUI, DFARS, CMMC"')
    ap.add_argument("--transcribe-file", metavar="AUDIO", help="transcribe an existing audio file, then exit")
    return ap.parse_args(argv)


def run(args):
    if args.list_devices:
        mics, speakers, default_mic, default_speaker = list_devices()
        print("Microphones:")
        for d in mics:
            print(f"  {d['index']:>3}  {d['name']}{'  (default)' if d['index'] == default_mic else ''}")
        print("Speakers / headsets:")
        for d in speakers:
            print(f"  {d['index']:>3}  {d['name']}{'  (default)' if d['index'] == default_speaker else ''}")
        return

    if args.transcribe_file:
        print(f"Saved {transcribe_file(args.transcribe_file, model_name=args.model, vocabulary=args.vocabulary, status=print)}")
        return

    def new_recorder():
        return Recorder(args.out, args.mic, args.speaker, args.format)

    def save(recorder):
        print("Saving…")
        recording = recorder.stop()
        print(f"Saved {recording.audio}")
        if args.transcribe:
            print(f"Saved {transcribe_recording(recording, args.model, args.keep_tracks, vocabulary=args.vocabulary, status=print)}")
        elif not args.keep_tracks:
            recording.discard_tracks()

    if not args.auto:
        recorder = new_recorder().start("Recording")
        print("Recording… press Ctrl+C to stop.")
        try:
            while args.duration is None or recorder.elapsed < args.duration:
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        save(recorder)
        return

    state = {"recorder": None}

    def started(app):
        print(f"{app} meeting started — recording.")
        state["recorder"] = new_recorder().start(app)

    def ended(app):
        print(f"{app} meeting ended.")
        save(state["recorder"])
        state["recorder"] = None

    watcher = MeetingWatcher(started, ended)
    print("Waiting for a Zoom or Teams meeting… press Ctrl+C to quit.")
    try:
        while True:
            watcher.poll()
            time.sleep(3)
    except KeyboardInterrupt:
        if state["recorder"]:
            save(state["recorder"])
