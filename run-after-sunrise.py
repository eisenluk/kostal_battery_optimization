#!/home/eisenluk/kostal_battery_optimization/.venv/bin/python

import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from astral import Observer
from astral.sun import sunrise


# Replace these with the coordinates of the installation.
LATITUDE = 47.634782
LONGITUDE = 8.765966
TIMEZONE = ZoneInfo("Europe/Zurich")

# Paths relative to this file, so the project works in any folder.
PROJECT_DIR = Path(__file__).resolve().parent
RUN_SCRIPT = str(PROJECT_DIR / "run-kostal.sh")
LOG_FILE = PROJECT_DIR / "kostal-battery.log"


def log(message: str) -> None:
    print(message, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(message + "\n")


def main():
    now = datetime.now(TIMEZONE)

    observer = Observer(
        latitude=LATITUDE,
        longitude=LONGITUDE,
    )

    target = sunrise(
        observer,
        date=now.date(),
        tzinfo=TIMEZONE,
    ) + timedelta(minutes=30)

    delay = (target - now).total_seconds()

    if delay > 0:
        log(f"Waiting until {target.isoformat()}")
        time.sleep(delay)
    else:
        log(f"Today's target time {target.isoformat()} has already passed")
        return

    subprocess.run([RUN_SCRIPT, "--apply"], check=True)


if __name__ == "__main__":
    main()
