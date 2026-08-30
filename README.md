# KOSTAL Battery Optimization

Optimize the daily battery charging release time of a KOSTAL PLENTICORE G3
inverter using Solcast PV forecasts.

The script is intended for systems where the battery should reach its target
state of charge near the end of the solar-production day instead of charging to
100% early in the morning and remaining full for many hours.

## Features

- Authenticates against the local KOSTAL PLENTICORE REST API.
- Reads the current battery state of charge (SoC).
- Downloads forecasts for two Solcast rooftop arrays.
- Combines north- and south-facing PV forecasts.
- Applies a configurable forecast safety reduction.
- Accounts for a configurable base load.
- Calculates sunrise and sunset for a fixed location and time zone.
- Calculates the latest safe charging release time on a 15-minute grid.
- Updates only the schedule of the current weekday.
- Skips the API write when the calculated schedule is already active.
- Never enables grid charging.
- Supports a manual charging release time as an alternative to forecast mode.
- Returns a machine-readable JSON result for Node-RED or other automation tools.

## How it works

In automatic mode, the script performs the following calculation:

1. Determine today's sunrise and sunset.
2. Read the current battery SoC from the inverter.
3. Calculate the battery energy required to reach the target SoC.
4. Download the 30-minute Solcast forecasts for both PV orientations.
5. Combine the forecasts and limit their total output to the configured inverter
   AC capacity.
6. Apply the forecast safety reduction and subtract the configured base load.
7. Define the completion deadline as sunset minus the configured buffer.
8. Search backwards from that deadline for the latest 15-minute release time
   that provides enough forecast surplus energy.
9. Write only today's KOSTAL schedule if it differs from the existing value.

If the conservative forecast does not contain enough energy to reach the target
SoC, charging is released from sunrise. The script does not use grid charging,
so reaching 100% cannot be guaranteed on unexpectedly poor-weather days.

## Requirements

- Python 3.9 or newer
- A KOSTAL PLENTICORE G3 inverter reachable over the local network and a local
  inverter user account with permission to read and change battery settings
- A battery connected to the inverter
- A [Solcast](https://www.solcast.com/) Home User account with one or two
  configured rooftop PV sites

## Installation

Clone the repository and create a virtual environment:

```bash
git clone https://github.com/eisenluk/kostal_battery_optimization.git
cd kostal_battery_optimization

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Credentials

The script reads credentials from environment variables. Do not store passwords
or API keys in the source file.

```bash
export KOSTAL_PASSWORD='your-kostal-password'
export SOLCAST_API_KEY='your-solcast-api-key'
```

For unattended execution, store the values in a protected environment file or
systemd service configuration and restrict access to that file.

## Automatic forecast mode

### Solcast resource IDs

`YOUR_SOUTH_RESOURCE_ID` and `YOUR_NORTH_RESOURCE_ID` are the resource IDs of
the rooftop PV sites configured in your Solcast account. They are not KOSTAL
device IDs.

Create a separate Solcast rooftop site for each materially different array
orientation. For this installation, one site represents all south-facing PV
modules and the other represents all north-facing PV modules. Configure each
site with its corresponding total module capacity, inverter AC capacity,
azimuth, tilt, location, and efficiency factor.

Open the rooftop site in the Solcast portal after creating it. The resource ID
is shown in the site summary and is also part of the forecast API URL:

```text
https://api.solcast.com.au/rooftop_sites/RESOURCE_ID/forecasts?format=json
```

For example, if the URL contains
`/rooftop_sites/abcd-1234-efgh-5678/forecasts`, use
`abcd-1234-efgh-5678` as the command-line value. Do not include the full URL.

If the system only has one relevant array orientation, the script currently
still expects both resource-ID arguments. Configure the Solcast sites according
to the actual PV installation rather than reusing an unrelated resource ID.

Always run the script without `--apply` first. This produces a preview and does
not change the inverter configuration:

```bash
python kostal_battery_optimization.py \
  --host 192.168.1.100 \
  --solar-auto \
  --sunset-buffer-minutes 60 \
  --base-load-w 750 \
  --battery-capacity-kwh 22.1 \
  --south-resource-id YOUR_SOUTH_RESOURCE_ID \
  --north-resource-id YOUR_NORTH_RESOURCE_ID
```

Add `--apply` after verifying the result:

```bash
python kostal_battery_optimization.py \
  --host 192.168.1.100 \
  --solar-auto \
  --sunset-buffer-minutes 60 \
  --base-load-w 750 \
  --battery-capacity-kwh 22.1 \
  --south-resource-id YOUR_SOUTH_RESOURCE_ID \
  --north-resource-id YOUR_NORTH_RESOURCE_ID \
  --apply
```

The current defaults are configured for:

| Setting | Default |
| --- | ---: |
| Latitude | `47.634782` |
| Longitude | `8.765966` |
| Time zone | `Europe/Zurich` |
| Battery capacity | `22.1 kWh` |
| Target SoC | `100%` |
| Charging efficiency | `94%` |
| Sunset buffer | `60 minutes` |
| Forecast safety reduction | `25%` |
| Base load | `750 W` |
| Combined AC limit | `22,000 W` |

All values can be overridden with command-line arguments. Run the following for
the complete list:

```bash
python kostal_battery_optimization.py --help
```

## Manual mode

To allow charging from 14:00 on selected weekdays:

```bash
python kostal_battery_optimization.py \
  --host 192.168.1.100 \
  --from-time 14:00 \
  --days mon tue wed thu fri
```

Add `--apply` to write the manual schedule.

## Automation

Changing the KOSTAL time-controlled battery settings may restart MPPT tracking
temporarily. For that reason, this script is designed to run once per day before
PV production begins, for example 30 minutes before sunrise.

The script can be triggered by Node-RED, cron, or a systemd timer. When using
Node-RED, execute the script on the Linux host through SSH or another remote
execution mechanism. Avoid installing and running Python inside the Node-RED
container unless that setup is intentionally maintained.

Each automatic run makes two Solcast API requests: one request for each rooftop
site. Keep the account's daily request limit in mind when testing.

## Output

The script prints a JSON object such as:

```json
{
  "status": "changed",
  "applied": false,
  "weekday": "sat",
  "current_soc_percent": 41.0,
  "required_surplus_kwh": 13.871,
  "conservative_surplus_available_kwh": 25.468,
  "charge_allowed_from": "2026-08-29T15:15:00+02:00"
}
```

Important status values include:

- `changed`: the calculated schedule differs from the existing schedule.
- `unchanged`: no settings write is necessary.
- `insufficient_forecast_energy`: the forecast cannot meet the target; charging
  is released from sunrise.

The `applied` field is `true` only when `--apply` was supplied and the inverter
schedule was actually written.

## Security and operational notes

- Keep `KOSTAL_PASSWORD` and `SOLCAST_API_KEY` out of Git.
- Use a fixed IP address or reliable local DNS name for the inverter.
- Test without `--apply` before enabling unattended execution.
- Incorrect inverter settings may affect system operation. Use this software at
  your own risk.
- The local KOSTAL REST API is used by the inverter's web interface but is not a
  stable public API contract; firmware updates may change its behavior.

## Disclaimer

This project is not affiliated with or endorsed by KOSTAL Solar Electric or
Solcast. It is provided without warranty.

