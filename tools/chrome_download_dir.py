"""Where does this Chrome save downloads? Ask the profile instead of guessing."""
import json
import os
from pathlib import Path

base = Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"
for profile in sorted(base.glob("*")):
    prefs = profile / "Preferences"
    if not prefs.is_file():
        continue
    try:
        data = json.loads(prefs.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        continue
    download = data.get("download", {})
    directory = download.get("default_directory")
    print(f"{profile.name}: default_directory={directory!r} "
          f"prompt={download.get('prompt_for_download')}")
