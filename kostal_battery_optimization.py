#!/usr/bin/env python3
"""Set a PLENTICORE charge-release time manually or from Solcast forecasts.

Secrets are read from KOSTAL_PASSWORD and SOLCAST_API_KEY. By default the
program previews its decision; --apply permits one settings write when needed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from astral import LocationInfo
from astral.sun import sun
from Cryptodome.Cipher import AES


DAY_IDS = {
    "mon": "Battery:TimeControl:ConfMon",
    "tue": "Battery:TimeControl:ConfTue",
    "wed": "Battery:TimeControl:ConfWed",
    "thu": "Battery:TimeControl:ConfThu",
    "fri": "Battery:TimeControl:ConfFri",
    "sat": "Battery:TimeControl:ConfSat",
    "sun": "Battery:TimeControl:ConfSun",
}
WEEKDAYS = tuple(DAY_IDS)


def quarters_until(value: str) -> int:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Time must use the HH:MM format") from exc
    if parsed.minute not in (0, 15, 30, 45):
        raise argparse.ArgumentTypeError("Time must be aligned to a 15-minute interval")
    return parsed.hour * 4 + parsed.minute // 15


def make_day(charge_allowed_from: int) -> str:
    # 1 = charging blocked, discharging for home demand allowed; 0 = no restriction
    return "1" * charge_allowed_from + "0" * (96 - charge_allowed_from)


class KostalApi:
    def __init__(self, host: str, timeout: int = 15):
        self.base = f"http://{host.rstrip('/')}/api/v1"
        self.timeout = timeout
        self.http = requests.Session()
        self.http.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    def post(self, path: str, payload=None):
        response = self.http.post(self.base + path, json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json() if response.content else None

    def put(self, path: str, payload=None):
        response = self.http.put(self.base + path, json=payload, timeout=self.timeout)
        if not response.ok:
            raise RuntimeError(
                f"PUT {path} failed: HTTP {response.status_code}: {response.text}"
            )
        return response.json() if response.content else None

    def get(self, path: str):
        response = self.http.get(self.base + path, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def login(self, password: str) -> None:
        client_nonce = base64.b64encode(secrets.token_bytes(12)).decode()
        start = self.post("/auth/start", {"username": "user", "nonce": client_nonce})
        server_nonce = start["nonce"]
        transaction_id = start["transactionId"]
        salt_b64 = start["salt"]
        salted = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(start["rounds"])
        )
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
        stored_key = hashlib.sha256(client_key).digest()
        auth_message = (
            f"n=user,r={client_nonce},r={server_nonce},s={salt_b64},i={start['rounds']},c=biws,r={server_nonce}"
        ).encode()
        client_signature = hmac.new(stored_key, auth_message, hashlib.sha256).digest()
        proof = bytes(a ^ b for a, b in zip(client_key, client_signature))
        finish = self.post(
            "/auth/finish",
            {"transactionId": transaction_id, "proof": base64.b64encode(proof).decode()},
        )

        # Derive the protocol key used to encrypt the temporary session token.
        protocol = hmac.new(stored_key, b"Session Key", hashlib.sha256)
        protocol.update(auth_message)
        protocol.update(client_key)
        protocol_key = protocol.digest()
        iv = secrets.token_bytes(16)
        cipher = AES.new(protocol_key, AES.MODE_GCM, nonce=iv)
        encrypted, tag = cipher.encrypt_and_digest(finish["token"].encode())
        created = self.post(
            "/auth/create_session",
            {
                "transactionId": transaction_id,
                "iv": base64.b64encode(iv).decode(),
                "tag": base64.b64encode(tag).decode(),
                "payload": base64.b64encode(encrypted).decode(),
            },
        )
        self.http.headers["Authorization"] = "Session " + created["sessionId"]
        if not self.get("/auth/me").get("authenticated"):
            raise RuntimeError("The inverter did not confirm authentication")

    def read_schedule(self):
        # G3 firmware expects settings reads as POST /settings. The older
        # path-style GET can return HTTP 500 even after a successful login.
        setting_ids = ["Battery:TimeControl:Enable", *DAY_IDS.values()]
        return self.post(
            "/settings",
            [{"moduleid": "devices:local", "settingids": setting_ids}],
        )

    def write_schedule(self, values: dict[str, str]):
        settings = [{"id": "Battery:TimeControl:Enable", "value": "1"}]
        settings.extend({"id": DAY_IDS[day], "value": value} for day, value in values.items())
        return self.put("/settings", [{"moduleid": "devices:local", "settings": settings}])

    def read_soc(self) -> float:
        data = self.post(
            "/processdata",
            [{"moduleid": "devices:local:battery", "processdataids": ["SoC"]}],
        )
        try:
            return float(data[0]["processdata"][0]["value"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Unable to read the battery SoC: {data!r}") from exc


def schedule_values(raw) -> dict[str, str]:
    try:
        settings = raw[0]["settings"]
        return {item["id"]: str(item["value"]) for item in settings}
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected schedule response: {raw!r}") from exc


def parse_solcast_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_solcast(resource_id: str, api_key: str, timezone: ZoneInfo) -> dict[datetime, float]:
    url = f"https://api.solcast.com.au/rooftop_sites/{resource_id}/forecasts"
    response = requests.get(
        url,
        params={"format": "json"},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            f"Solcast {resource_id} request failed: HTTP {response.status_code}: {response.text}"
        )
    forecasts = response.json().get("forecasts", [])
    if not forecasts:
        raise RuntimeError(f"Solcast {resource_id} returned no forecast intervals")
    result: dict[datetime, float] = {}
    for item in forecasts:
        end = parse_solcast_time(item["period_end"]).astimezone(timezone)
        result[end] = float(item["pv_estimate"])
    return result


def combined_intervals(
    south: dict[datetime, float],
    north: dict[datetime, float],
    ac_limit_kw: float,
    safety_percent: float,
    base_load_kw: float,
) -> list[tuple[datetime, datetime, float]]:
    intervals = []
    for end in sorted(set(south) & set(north)):
        # Hobbyist forecasts use PT30M intervals ending at period_end.
        start = end - timedelta(minutes=30)
        pv_kw = min(south[end] + north[end], ac_limit_kw)
        usable_surplus_kw = max(0.0, pv_kw * (1.0 - safety_percent / 100.0) - base_load_kw)
        intervals.append((start, end, usable_surplus_kw))
    if not intervals:
        raise RuntimeError("The north and south forecasts have no matching intervals")
    return intervals


def energy_between(
    intervals: list[tuple[datetime, datetime, float]], start: datetime, end: datetime
) -> float:
    total_kwh = 0.0
    for interval_start, interval_end, power_kw in intervals:
        overlap_start = max(start, interval_start)
        overlap_end = min(end, interval_end)
        if overlap_end > overlap_start:
            total_kwh += power_kw * (overlap_end - overlap_start).total_seconds() / 3600.0
    return total_kwh


def floor_quarter(value: datetime) -> datetime:
    return value.replace(minute=(value.minute // 15) * 15, second=0, microsecond=0)


def latest_safe_start(
    intervals: list[tuple[datetime, datetime, float]],
    earliest: datetime,
    deadline: datetime,
    required_kwh: float,
) -> tuple[datetime, float, bool]:
    earliest = floor_quarter(earliest)
    deadline = floor_quarter(deadline)
    available = energy_between(intervals, earliest, deadline)
    if available + 1e-9 < required_kwh:
        return earliest, available, False

    candidate = deadline
    while candidate >= earliest:
        if energy_between(intervals, candidate, deadline) + 1e-9 >= required_kwh:
            return candidate, available, True
        candidate -= timedelta(minutes=15)
    return earliest, available, False


def parse_args():
    parser = argparse.ArgumentParser(
        description="KOSTAL: Set the battery charging release time manually or from Solcast"
    )
    parser.add_argument("--host", required=True, help="IP/Hostname von WR1")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--from-time", type=quarters_until, metavar="HH:MM",
        help="Manual mode: allow battery charging from this time",
    )
    mode.add_argument(
        "--solar-auto", action="store_true",
        help="Calculate the release time from SoC, solar times, and Solcast",
    )
    parser.add_argument(
        "--days", nargs="+", choices=DAY_IDS, default=list(DAY_IDS),
        help="Weekdays to change; default: all days",
    )
    parser.add_argument("--apply", action="store_true", help="Write the calculated change")
    parser.add_argument("--latitude", type=float, default=47.634782)
    parser.add_argument("--longitude", type=float, default=8.765966)
    parser.add_argument("--timezone", default="Europe/Zurich")
    parser.add_argument("--south-resource-id", default="c0f0-2f3d-2107-648b")
    parser.add_argument("--north-resource-id", default="30f4-9257-92b0-6819")
    parser.add_argument("--battery-capacity-kwh", type=float, default=22.1)
    parser.add_argument("--target-soc", type=float, default=100.0)
    parser.add_argument("--charge-efficiency", type=float, default=0.94)
    parser.add_argument("--sunset-buffer-minutes", type=int, default=60)
    parser.add_argument("--forecast-safety-percent", type=float, default=25.0)
    parser.add_argument("--base-load-w", type=float, default=750.0)
    parser.add_argument("--combined-ac-limit-w", type=float, default=22000.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    password = os.environ.get("KOSTAL_PASSWORD")
    if args.solar_auto and not password:
        raise SystemExit("The KOSTAL_PASSWORD environment variable is missing")

    if args.from_time is not None:
        values = {day: make_day(args.from_time) for day in args.days}
        result = {"status": "preview" if not args.apply else "changed", "values": values}
        if args.apply:
            if not password:
                raise SystemExit("The KOSTAL_PASSWORD environment variable is missing")
            api = KostalApi(args.host)
            api.login(password)
            api.write_schedule(values)
        print(json.dumps(result, indent=2))
        return

    solcast_key = os.environ.get("SOLCAST_API_KEY")
    if not solcast_key:
        raise SystemExit("The SOLCAST_API_KEY environment variable is missing")
    if not 0 <= args.target_soc <= 100:
        raise SystemExit("--target-soc must be between 0 and 100")
    if not 0 < args.charge_efficiency <= 1:
        raise SystemExit("--charge-efficiency must be between 0 and 1")
    if not 0 <= args.forecast_safety_percent < 100:
        raise SystemExit("--forecast-safety-percent must be between 0 and 100")

    tz = ZoneInfo(args.timezone)
    now = datetime.now(tz)
    location = LocationInfo(latitude=args.latitude, longitude=args.longitude, timezone=args.timezone)
    solar = sun(location.observer, date=now.date(), tzinfo=tz)
    sunrise = solar["sunrise"]
    sunset = solar["sunset"]
    deadline = sunset - timedelta(minutes=args.sunset_buffer_minutes)

    api = KostalApi(args.host)
    api.login(password)
    soc = api.read_soc()
    before_raw = api.read_schedule()
    before = schedule_values(before_raw)

    south = fetch_solcast(args.south_resource_id, solcast_key, tz)
    north = fetch_solcast(args.north_resource_id, solcast_key, tz)
    intervals = combined_intervals(
        south,
        north,
        args.combined_ac_limit_w / 1000.0,
        args.forecast_safety_percent,
        args.base_load_w / 1000.0,
    )
    missing_battery_kwh = max(
        0.0, args.battery_capacity_kwh * (args.target_soc - soc) / 100.0
    )
    required_surplus_kwh = missing_battery_kwh / args.charge_efficiency
    charge_start, available_kwh, sufficient = latest_safe_start(
        intervals, sunrise, deadline, required_surplus_kwh
    )

    day = WEEKDAYS[now.weekday()]
    new_value = make_day(charge_start.hour * 4 + charge_start.minute // 15)
    old_value = before.get(DAY_IDS[day])
    changed = old_value != new_value
    status = "insufficient_forecast_energy" if not sufficient else ("changed" if changed else "unchanged")
    wrote = False
    if args.apply and changed:
        api.write_schedule({day: new_value})
        wrote = True

    result = {
        "status": status,
        "applied": wrote,
        "date": now.date().isoformat(),
        "weekday": day,
        "sunrise": sunrise.isoformat(),
        "sunset": sunset.isoformat(),
        "target_full_time": deadline.isoformat(),
        "sunset_buffer_minutes": args.sunset_buffer_minutes,
        "current_soc_percent": round(soc, 2),
        "target_soc_percent": args.target_soc,
        "battery_energy_missing_kwh": round(missing_battery_kwh, 3),
        "required_surplus_kwh": round(required_surplus_kwh, 3),
        "conservative_surplus_available_kwh": round(available_kwh, 3),
        "forecast_safety_percent": args.forecast_safety_percent,
        "base_load_w": args.base_load_w,
        "charge_allowed_from": charge_start.isoformat(),
        "old_schedule": old_value,
        "new_schedule": new_value,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
