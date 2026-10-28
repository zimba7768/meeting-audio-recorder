# Builds dist\MeetingAudioRecorder.exe (a single file, no console window).
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name MeetingAudioRecorder `
    --collect-binaries soundfile --collect-data soundfile run.py
