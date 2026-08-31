#!/home/eisenluk/kostal_battery_optimization/.venv/bin/python

import subprocess
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from astral import Observer
from astral.sun import sunrise


# Replace these with the coordinates of the installation.
LATITUDE = 47.634782
LONGITUDE = 8.765966
TIMEZONE = ZoneInfo("Europe/Zurich")

RUN_SCRIPT = "/home/eisenluk/kostal_battery_optimization/run-kostal.sh"


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
        print(f"Waiting until {target.isoformat()}", flush=True)
        time.sleep(delay)
    else:
        print(
            f"Today's target time {target.isoformat()} has already passed",
            flush=True,
        )
        return

    subprocess.run([RUN_SCRIPT, "--apply"], check=True)


if __name__ == "__main__":
    main()
