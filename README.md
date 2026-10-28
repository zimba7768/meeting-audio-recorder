# Meeting Audio Recorder

Record **audio only** from your Zoom and Microsoft Teams meetings on Windows: what the other people say *and* what you say into your microphone. Both are mixed into one MP3 per meeting.

- **Auto-record:** starts when a Zoom or Teams meeting starts, saves when it ends.
- **Works with any USB mic or headset.** Pick it from a list.
- **No meeting bots, plugins or accounts.** Everything stays on your PC.
- **Small files:** about 1 MB per minute as MP3. WAV and FLAC are also available.
- Free and open source (MIT).

![screenshot](docs/screenshot.png)

## Download

Get `MeetingAudioRecorder.exe` from the [latest release](../../releases/latest) and run it. You don't need to install anything.

> Windows may show a "Windows protected your PC" warning because the app isn't code-signed. Click **More info → Run anyway**. If you'd rather not, you can [run it from source](#run-from-source) instead.

## How to use it

1. Open **Meeting Audio Recorder**.
2. Choose your **Microphone** (your USB mic) and the **Meeting audio from** device (the speakers or headset you hear the meeting through). Leave either on *System default* to follow Windows.
3. Leave **Automatically record Zoom and Teams meetings** ticked and keep the window open (minimizing it is fine).
4. Join a meeting. The status line switches to *"Zoom meeting — 0:00:12"*, and the level bars move for **Meeting** and **You**.
5. When you leave the meeting, the recording is saved to `Documents\Meeting Recordings` as e.g. `2026-10-28 14-30 Teams.mp3`.

You can also press **Start recording** / **Stop recording** at any time to record by hand.

**Tip:** to check that everything is set up before a real meeting, use Zoom's test meeting at <https://zoom.us/test> or Teams' **Settings → Devices → Make a test call**.

## How it works

| Part | How |
|---|---|
| Other people's voices | Windows **WASAPI loopback**: a copy of the audio going to your speakers/headset. Works no matter which meeting app plays it. |
| Your voice | Recorded straight from your chosen microphone. |
| Mixing | Both tracks are saved while recording, then lined up, resampled if needed and mixed into one file when you stop. |
| Meeting detection | Windows logs which app is using the microphone (the same info behind the mic icon in the taskbar). Zoom and Teams hold the mic for the whole meeting, even while you are muted, so "Zoom/Teams is using the mic" = "you're in a meeting". The recording is saved 15 seconds after they let go of it. |

### Things to know

- **The meeting track is everything you hear.** Notification sounds, music or videos playing during the meeting get recorded too, so mute them.
- **Pick your headset if you switch devices mid-call.** The recorder captures the device that was chosen (or the Windows default) when recording started.
- **Browser meetings** (Teams or Zoom in Chrome/Edge) aren't detected automatically. Use the **Start recording** button for those.
- Windows 10 or 11 only.

## Please get consent

Recording laws differ by country and state. Many places require **everyone** in the call to agree to being recorded. Tell participants you're recording, and follow your workplace's rules.

## Run from source

Requires Python 3.10+ on Windows.

```bash
git clone https://github.com/zimba7768/meeting-audio-recorder.git
cd meeting-audio-recorder
pip install -r requirements.txt
python -m meeting_recorder
```

### Command line

```bash
python -m meeting_recorder --list-devices           # show mics and speakers
python -m meeting_recorder --cli                    # record now, Ctrl+C to stop
python -m meeting_recorder --auto --mic 30          # wait for meetings, use mic #30
python -m meeting_recorder --cli --format flac --keep-tracks --out D:\Recordings
```

### Build the .exe yourself

```powershell
.\build.ps1        # creates dist\MeetingAudioRecorder.exe
```

Pushing a tag like `v1.0.1` makes GitHub Actions build the exe and attach it to a release.

## Contributing

Issues and pull requests are welcome. Ideas that would make it better:

- Record only the meeting app's audio (Windows process loopback), so other sounds are left out
- Tray icon and start-with-Windows option
- Automatic transcription (e.g. with Whisper)
- Detection of meetings in the browser

## License

[MIT](LICENSE)
