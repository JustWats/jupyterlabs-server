"""Run with %run setup_lab.py from the setup notebook or another notebook."""
import json
from pathlib import Path
from labkit import configure

report = configure(Path(__file__).with_name("lab_settings.py"))
print(json.dumps(report, indent=2))
print("Saved hardware_report.json. No worker pool has been started.")
