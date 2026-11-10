# net-availability-monitor

Cross-platform availability monitor for **YouTube** and **Telegram** with a local dashboard,
menu-bar/tray indicators and an Android app. Each check is split into stages
**DNS → TCP → TLS → HTTP**, so the app can tell “the service is down” from
“the connection is being filtered/blocked” (timeouts, resets, DNS failures) while the
control channel proves the internet itself is working.

[Русская версия](README.ru.md)

[![CI](https://github.com/bogatkin82-jpg/net-availability-monitor/actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

![Dashboard](docs/screenshots/desktop.png)

## What it does

- checks **YouTube**, **Telegram** and a **control internet channel** every N seconds;
- classifies each service as `ok` / `degraded` / `blocked` / `down` / `unknown`;
- shows the failing stage and reason (e.g. “TLS handshake timed out — looks like DPI filtering”);
- keeps history, computes 1 h / 24 h uptime and opens **incidents** after 2 failed polls;
- provides a searchable incident log and JSON export;
- runs on macOS, Windows and Android, all driven by one `config.json`.

## Platforms

| Platform | What you get | Install |
|---|---|---|
| **macOS** | Python server + browser dashboard + native menu-bar dot (Swift/AppKit), autostart via LaunchAgents | `./install.sh` |
| **Windows** | Python server + dashboard + system-tray dot, single `.exe` (PyInstaller), autostart via HKCU Run | `NetMonitor-windows-x64.zip` from Releases |
| **Android** | Native Kotlin/Compose app with its own checks, incident log, settings and notifications | `net-availability-monitor-android-<tag>.apk` from Releases |
| **Linux** | Python server + dashboard (tray optional, `tray.py`) | `python3 server.py` |

All platforms share the same [`config.json`](config.json): services, endpoints, timeouts,
polling interval, incident thresholds, status labels/colors and notification rules.

## macOS quick start

```bash
git clone https://github.com/bogatkin82-jpg/net-availability-monitor.git
cd net-availability-monitor
./install.sh
open http://127.0.0.1:8099/
```

`install.sh` builds the menu-bar app, installs it to `~/Applications/NetBar.app`, copies the
runtime to `~/Library/Application Support/netmon` (outside TCC-protected folders) and registers
two LaunchAgents so the server and the indicator start at login and recover after a crash.

Menu-bar indicator modes (set in `config.json` → `display.macos_menubar.mode`):

- `overlay` — floating colored dot in the top-right corner, always visible above other windows;
- `dot` — compact dot inside the menu bar;
- `text` — `YT✓ TG✕`.

Left click opens the status menu, right click opens the dashboard, dragging moves the dot.

## Windows

Download `NetMonitor-windows-x64.zip` from the latest Release, unzip, run
`install.ps1` (PowerShell) or `NetMonitor.exe`. The app starts the local server, shows a
tray dot and opens the dashboard on demand. Autostart is a single HKCU `Run` entry; run
`uninstall.ps1` to remove it. See [docs/windows.md](docs/windows.md) for details,
including the SmartScreen note (the build is unsigned).

## Android

Install the APK (`net-availability-monitor-android-<tag>.apk`) from the latest Release (allow installation from unknown sources),
open the app and grant the notification permission. The phone performs checks itself —
no PC required. A foreground service keeps polling in the background and notifies you when
YouTube/Telegram become blocked or unreachable. Settings (interval, thresholds, enabled
services, notifications) are editable in the app and default to `config.json`.
See [docs/android.md](docs/android.md).

## Configuration

`config.json` is the single source of truth. Schema: [`docs/config.schema.json`](docs/config.schema.json).

```jsonc
{
  "server": { "host": "127.0.0.1", "port": 8099 },
  "polling": { "interval_sec": 20, "check_timeout_sec": 8, "dns_timeout_sec": 4 },
  "incidents": { "open_after_failures": 2, "close_after_successes": 1, "max_stored": 500 },
  "history": { "max_age_hours": 24, "max_points": 8640, "series_points": 180 },
  "status_labels": { "ok": "Доступен", "blocked": "Похоже на блокировку", "...": "..." },
  "status_colors": { "ok": "#34c759", "degraded": "#ff9500", "blocked": "#ff3b30", "...": "..." },
  "services": [
    {
      "id": "youtube", "name": "YouTube", "accent": "#ff3040",
      "checks": [
        { "id": "yt_https", "kind": "https", "host": "www.youtube.com",
          "port": 443, "path": "/generate_204", "expect": [204],
          "core": true, "timeout": 8 }
      ]
    }
  ]
}
```

Validation and cross-platform parity:

```bash
python3 tools/check_config.py            # validate schema + Android asset parity
python3 tools/check_config.py --sync     # copy config.json into the Android assets
```

Server-side overrides: `--config PATH`, `--port`, `--interval`; env `NETMON_CONFIG`.

## How statuses are computed

| Situation | Status |
|---|---|
| all core checks pass | `ok` |
| some core checks fail | `degraded` |
| all core fail, internet alive, TCP/TLS timeouts/resets or DNS failure | `blocked` — looks like filtering |
| all core fail with connection refused / HTTP error | `down` |
| control channel is down | `down` for every service |
| max successful core latency > 4 s | `degraded` |

Error codes are identical across platforms: `dns_error`, `dns_timeout`, `connect_timeout`,
`connect_refused`, `connect_unreachable`, `tls_handshake_timeout`, `tls_reset`,
`tls_cert_error`, `tls_error`, `read_timeout`, `http_status_unexpected`, `http_error`,
`unknown_error`.

## HTTP API

The Python server exposes a small JSON API (`/api/state`, `/api/incidents`, `/api/check`,
`/api/export`, `/api/health`) and serves the dashboard from `static/`.
Full contract: [docs/api-contract.md](docs/api-contract.md).

```bash
curl -s http://127.0.0.1:8099/api/state | python3 -m json.tool | head -40
curl -s 'http://127.0.0.1:8099/api/incidents?q=таймаут&service=telegram&status=active'
```

## Text UI, screenshots

| macOS overlay dot | Menu-bar dot | Dashboard | Mobile layout |
|---|---|---|---|
| [overlay](docs/screenshots/overlay.png) | [menubar](docs/screenshots/menubar.png) | [desktop](docs/screenshots/desktop.png) | [mobile](docs/screenshots/mobile.png) |

## Repository layout

```
config.json                 # shared settings for every platform
server.py                   # Python backend: checks, incidents, API, dashboard
static/                     # dashboard (vanilla HTML/CSS/JS, no build step)
tray.py                     # cross-platform tray indicator (Windows/Linux) with --selftest
menubar/NetBar.swift        # native macOS menu-bar app (Swift/AppKit)
windows/                    # PyInstaller entry, spec, install.ps1, build script
android/                    # Kotlin + Jetpack Compose application
tests/                      # Python tests (unit + API)
tools/check_config.py       # config validation and parity checks
docs/                       # API contract, architecture, per-platform guides, screenshots
.github/workflows/          # CI: config, Python, Swift, Windows exe, Android APK
```

## Development

```bash
python3 server.py --config config.json --port 8099     # run backend + dashboard
python3 tools/check_config.py                          # config lint
python3 -m pytest -q tests                             # Python tests

cd menubar && ./build.sh                               # macOS app (swiftc, no dependencies)
cd windows && pyinstaller netmon.spec                  # Windows exe (build on Windows)
cd android && ./gradlew test assembleDebug              # Android (JDK 17 + Android SDK)
```

CI builds every artifact and attaches them to GitHub Releases on `v*` tags:
`NetMonitor-windows-x64.zip`, `net-availability-monitor-android-<tag>.apk`.

## Verification

- `docs/qa-report-macos.md` — macOS acceptance report (API, incidents, UI via Chrome/CDP).
- `QA-REPORT-FINAL.md` — cross-platform parity and build verification before release.

## Disclaimer

`blocked` is a **heuristic** based on network symptoms (timeouts, resets, DNS failures).
The same symptoms can be caused by a broken server, Wi-Fi issue or VPN; that is why the
control channel is always checked. The project does not prove censorship and does not
circumvent it.

## License

MIT — see [LICENSE](LICENSE).
