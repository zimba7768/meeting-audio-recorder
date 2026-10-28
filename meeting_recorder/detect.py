"""Detect when a Zoom or Teams meeting is in progress.

Windows keeps a per-app record of microphone use (the same data behind the
"app is using your microphone" icon in the taskbar). While an app holds the mic
open its LastUsedTimeStop value is 0. Zoom and Teams keep the mic open for the
whole meeting, even while you are muted, so this is a reliable meeting signal.
"""
import time
import winreg

MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"

MEETING_APPS = {
    "Zoom": ("zoom",),
    "Teams": ("teams",),  # new Teams (MSTeams_...), classic Teams.exe, ms-teams.exe
}


def _subkeys(key):
    i = 0
    while True:
        try:
            yield winreg.EnumKey(key, i)
        except OSError:
            return
        i += 1


def _mic_in_use(key):
    try:
        start, _ = winreg.QueryValueEx(key, "LastUsedTimeStart")
        stop, _ = winreg.QueryValueEx(key, "LastUsedTimeStop")
    except OSError:
        return False
    return start > 0 and stop == 0


def _apps_using_mic():
    """Yield registry names of every app currently holding the microphone."""
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, MIC_KEY)
    except OSError:
        return
    with root:
        for name in _subkeys(root):
            with winreg.OpenKey(root, name) as key:
                if name == "NonPackaged":
                    for exe in _subkeys(key):
                        with winreg.OpenKey(key, exe) as sub:
                            if _mic_in_use(sub):
                                yield exe
                elif _mic_in_use(key):
                    yield name


def active_meeting_app():
    """Return 'Zoom' or 'Teams' if one of them is in a meeting right now, else None."""
    for name in _apps_using_mic():
        lowered = name.lower()
        for app, patterns in MEETING_APPS.items():
            if any(p in lowered for p in patterns):
                return app
    return None


class MeetingWatcher:
    """Call poll() every few seconds; it reports meeting start/end transitions.

    A meeting is considered over only after the app has released the mic for
    `grace_seconds`, so a brief audio-device switch doesn't split a recording.
    """

    def __init__(self, on_start, on_end, grace_seconds=15):
        self.on_start = on_start
        self.on_end = on_end
        self.grace_seconds = grace_seconds
        self.current = None
        self._last_seen = 0.0

    def poll(self):
        app = active_meeting_app()
        now = time.monotonic()
        if app:
            self._last_seen = now
            if self.current is None:
                self.current = app
                self.on_start(app)
        elif self.current and now - self._last_seen >= self.grace_seconds:
            app, self.current = self.current, None
            self.on_end(app)
