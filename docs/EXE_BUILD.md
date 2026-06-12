# Building the Windows .exe (desktop app)

Build on a Windows machine (PyInstaller is not a cross-compiler).

```powershell
# inside the project venv
pip install pyinstaller

pyinstaller --noconfirm --onefile --windowed `
  --name OptionMoneyAI `
  --add-data ".env.example;." `
  --hidden-import SmartApi `
  --hidden-import pyttsx3.drivers `
  --hidden-import pyttsx3.drivers.sapi5 `
  --hidden-import plyer.platforms.win.notification `
  --collect-submodules sklearn `
  main.py
```

Output: `dist/OptionMoneyAI.exe`.

Notes:
- Place a filled-in `.env` next to the .exe (config is read from the working
  directory's `.env`).
- `--windowed` hides the console; drop it while debugging to see tracebacks.
- First start is slow (one-file extraction); use `--onedir` for faster startup.
- The exe launches the **desktop dashboard** by default
  (`python main.py` → `desktop`). To ship the headless engine instead, build
  with `--console` and pass `engine` as the first argument in a shortcut.
- If Windows Defender flags the file (common for PyInstaller), sign the binary
  or add an exclusion.
