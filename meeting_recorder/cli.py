"""Command-line mode: python -m meeting_recorder --cli [options]"""
import argparse
import time

from .audio import FORMATS, Recorder, list_devices
from .detect import MeetingWatcher

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

    def new_recorder():
        return Recorder(args.out, args.mic, args.speaker, args.format, args.keep_tracks)

    if not args.auto:
        recorder = new_recorder().start("Recording")
        print("Recording… press Ctrl+C to stop.")
        try:
            while args.duration is None or recorder.elapsed < args.duration:
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        print("Saving…")
        print(f"Saved {recorder.stop()}")
        return

    state = {"recorder": None}

    def started(app):
        print(f"{app} meeting started — recording.")
        state["recorder"] = new_recorder().start(app)

    def ended(app):
        print(f"{app} meeting ended — saving…")
        print(f"Saved {state['recorder'].stop()}")
        state["recorder"] = None

    watcher = MeetingWatcher(started, ended)
    print("Waiting for a Zoom or Teams meeting… press Ctrl+C to quit.")
    try:
        while True:
            watcher.poll()
            time.sleep(3)
    except KeyboardInterrupt:
        if state["recorder"]:
            print("Saving…")
            print(f"Saved {state['recorder'].stop()}")
