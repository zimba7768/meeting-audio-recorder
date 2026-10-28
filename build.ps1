# Builds dist\MeetingAudioRecorder.exe (a single file, no console window).
python -m pip install -r requirements.txt pyinstaller
# The excludes keep optional extras (model converters, plotting, GUI toolkits) out of the exe
# if they happen to be installed in this Python.
python -m PyInstaller --noconfirm --onefile --windowed --name MeetingAudioRecorder `
    --collect-binaries soundfile --collect-data soundfile `
    --collect-all faster_whisper --collect-binaries ctranslate2 --collect-binaries onnxruntime `
    --exclude-module torch --exclude-module transformers --exclude-module tensorflow `
    --exclude-module matplotlib --exclude-module pandas --exclude-module scipy `
    --exclude-module PySide6 --exclude-module PyQt5 --exclude-module cv2 --exclude-module IPython `
    run.py
