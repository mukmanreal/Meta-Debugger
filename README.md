Head Set Debugger
=================

A small PyQt6-based GUI to interact with an Android/Meta headset over ADB.

Features
- Dark/neon-styled UI inspired by the provided reference
- Device connection indicator (green = connected, red = disconnected)
- Live `adb logcat` viewer
- Run arbitrary `adb` commands (per-device using `-s <id>`)

Prerequisites
- Python 3.10+
- `adb` (Android platform-tools) installed and available in `PATH`
- Install Python deps:

```bash
pip install -r requirements.txt
```

Run

```bash
python HSDB.py
```

Notes
- The app calls the `adb` binary via subprocess — ensure your device is authorized for ADB.
- This is a starting skeleton; I can add features like command history, file transfer, or theming.
