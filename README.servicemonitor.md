# Service Monitor

Web dashboard for monitoring and managing systemd services on a Raspberry Pi.

## Screenshot

![Service Monitor Dashboard](static/screenshot.png)

## Tech Stack

`Python 3.12, Flask, systemd/systemctl, vanilla JS, Inter (Google Fonts)`

## Architecture

```mermaid
flowchart LR
    subgraph RaspberryPi
        subgraph systemd
            SVC1[projects_*.service]
        end
        subgraph App
            Flask[Flask :5001]
        end
    end
    subgraph External
        CF[Cloudflared Tunnel]
        Browser[Browser]
    end

    SVC1 -->|systemctl status/list-units| Flask
    Flask -->|systemctl restart| SVC1
    Flask <-->|HTTP| CF
    CF <-->|HTTPS| Browser
```

**Data Flow:**
1. Flask queries systemd for services matching `projects_*` pattern
2. Parses status output for uptime, memory, CPU, errors
3. Renders dashboard; sidebar details loaded async via `/api/services/sidebar-details`
4. Live logs streamed via SSE at `/logs/stream` (journalctl -f)
5. Restart commands sent via `sudo systemctl restart`
6. Failed services trigger Telegram alerts (once per 6 AM–6 AM window)
7. Dashboard home polls `/api/system-info` every 10s for host vitals (temp, CPU, memory, disk, uptime)

## Prerequisites

- Python 3.12+
- uv (Python package manager)
- systemd (Linux)
- `sudo` access for service restarts
- Cloudflared (for external access)

## Installation

1. Clone and enter the repo:
   ```bash
   cd ~/service-monitor
   ```

2. Copy and fill out the env file:
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

3. Run the install script:
   ```bash
   cd install
   ./install.sh
   ```

4. Configure sudoers (required for restart functionality):
   ```bash
   # Add to /etc/sudoers.d/service-monitor
   mnalavadi ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart projects_*
   ```

## Running

**Via systemd (production):**
```bash
sudo systemctl start projects_service-monitor.service
```

**Manual (development):**
```bash
uv run src/app.py
```

**Default URL:** `http://localhost:5001`
**External URL:** `https://service-monitor.mnalavadi.org` (via Cloudflared)

## Project Structure

```
service-monitor/
├── src/
│   ├── app.py          # Flask app — all routes and request handling
│   ├── services.py     # systemd querying, ServiceStatus parsing, CI status
│   ├── scheduler.py    # Background health check thread + Telegram alerting
│   ├── telegram.py     # Telegram error notifications
│   ├── canned_info.py  # Static website links + canned ServiceStatus fixtures for dev/testing
│   ├── values.py       # Loads secrets from .env (python-dotenv)
│   └── config.py       # CLI tool that reads pyproject.toml config values
├── templates/
│   └── index.html      # Main dashboard template (Jinja2)
├── static/
│   ├── app.css         # Full stylesheet (custom CSS, design tokens)
│   ├── main.js         # Module bootstrap
│   ├── ui-shell.js     # Sidebar open/close, hamburger, keyboard nav
│   ├── services-list.js # Service list: search, auto-refresh, project colors
│   ├── log-stream.js   # SSE log streaming, filtering (time/count/severity/text), spike chart, traceback grouping + highlight
│   ├── sidebar-details.js # Async sidebar status/CI enrichment
│   ├── system-info.js  # Dashboard home: polls /api/system-info, renders vitals grid
│   └── notifications.js # ARIA live region announcements
├── tests/
│   ├── test_app.py
│   ├── test_services.py
│   └── test_config.py
├── install/
│   ├── install.sh
│   └── projects_service-monitor.service
├── .env.example        # Template for required environment variables
├── pyproject.toml
└── cloudflared/
    └── config.yml
```

## Environment Variables

Copy `.env.example` to `.env` and fill in values:

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_API_TOKEN` | Yes | Telegram bot token for failure alerts |
| `TELEGRAM_CHAT_ID` | Yes | Telegram chat ID to send alerts to |
| `GITHUB_TOKEN` | No | GitHub PAT for CI status; unauthenticated rate limit applies if omitted |
| `INSPECTOR_DETECTOR_UV_PATH` | No | Path to `uv` binary on Pi (default: `/home/mnalavadi/.local/bin/uv`) |
| `INSPECTOR_DETECTOR_CWD` | No | Working directory for inspector-detector check (default: `/home/mnalavadi/inspector_detector`) |

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Dashboard view, lists all `projects_*` services |
| `/?service=<name>` | GET | Dashboard with status header + live log stream for selected service |
| `/restart` | POST | Restart a service (validated against known services) |
| `/logs/stream` | GET (SSE) | Server-sent events stream of journalctl output for a service |
| `/api/services/sidebar-details` | GET | JSON: enriched status + CI for all services (loaded async after first paint) |
| `/api/system-info` | GET | JSON: host (Pi) vitals — temperature, CPU, memory, disk, uptime |
| `/inspector-detector/check` | POST | Run Inspector Detector inspection check (service-specific) |

### POST `/restart`

**Request:**
```
Content-Type: application/x-www-form-urlencoded
service=projects_example.service
```
**Validation:** Service name must exist in `systemctl list-units projects_*`. Returns 400 for unknown services.
**Response:** Redirects to `/?service=<name>` on success, 400/500 on error.

### GET `/logs/stream`

SSE stream. Each event is a JSON-encoded log line string:
```
data: "2026-05-04T14:52:57+0200 hostname service[pid]: log line here"
```
Client reconnects automatically on disconnect with exponential backoff.

### GET `/api/services/sidebar-details`

Returns:
```json
{
  "services": [
    {
      "name": "projects_foo.service",
      "is_active": true,
      "is_failed": false,
      "uptime": "2d 3h",
      "memory": "123.4M",
      "cpu": "2min 15s",
      "last_error": null,
      "ci_status": "success"
    }
  ]
}
```

### GET `/api/system-info`

Host vitals read live from `/proc` and `/sys` (stdlib only, no extra deps). Polled by the
dashboard home view every 10s. Any field is `null` when its source is unavailable (e.g. running
off-Pi); in dev mode the route returns `canned_system_info`.

```json
{
  "hostname": "raspberrypi",
  "uptime": "6d 14h",
  "temperature_c": 52.6,
  "cpu_percent": 12.4,
  "load_avg": 0.42,
  "cpu_count": 4,
  "memory_used_mb": 1840,
  "memory_total_mb": 3886,
  "memory_used_pct": 47.3,
  "disk_used_pct": 38.0,
  "disk_used_gb": 44.7,
  "disk_total_gb": 117.6
}
```

Sources: `temperature_c` from `/sys/class/thermal/thermal_zone0/temp`; `cpu_percent` sampled over
~100ms from `/proc/stat`; `memory_*` from `/proc/meminfo`; `uptime` from `/proc/uptime`;
`load_avg`/`cpu_count` and `disk_*` via stdlib (`os`, `shutil`), so they populate cross-platform.

## Key Concepts

| Concept | Description |
|---|---|
| `projects_*` | Naming convention for monitored services; only services matching this pattern are displayed |
| `ServiceStatus` | Dataclass holding parsed service info: name, is_active, is_failed, uptime, memory, cpu, last_error, ci_status |
| Status indicators | Green = active (running), Red = failed, Gray = inactive |
| Project groups | Services sharing the same base name (e.g. `projects_energy-monitor_*`) are visually grouped in the sidebar |
| CI status | Fetched from GitHub Actions API for services without a suffix; cached 60s per repo |

## Data Models

```
ServiceStatus
├── name: str              # Full service name (e.g. "projects_foo.service")
├── is_active: bool        # True if "active (running)" in systemctl status
├── is_failed: bool        # True if "active: failed" in systemctl status
├── uptime: str | None     # Parsed from "Active: ... since ...; X ago"
├── memory: str | None     # Parsed from "Memory: X"
├── cpu: str | None        # Parsed from "CPU: X"
├── last_error: str | None # Parsed from "Error: X"
├── full_status: str       # Raw systemctl status output
├── project_group: str     # Base name parsed from service name
├── suffix: str | None     # Sub-service suffix (e.g. "data-backup-scheduler")
└── ci_status: str | None  # "success" | "failure" | "error" | None
```

## Storage / Persistence

- No database. All state is read live from systemd.
- Service list cached in-process for 5 seconds.
- CI status cached in-process for 60 seconds per repo.
- Alert deduplication tracked in-memory; resets each day at 6 AM.

## Configuration

| Variable | Location | Default | Description |
|---|---|---|---|
| `host` | `src/app.py` | `0.0.0.0` | Bind address |
| `port` | `src/app.py` | `5001` | HTTP port |
| `service_pattern` | `src/services.py` | `projects_*` | systemctl filter pattern |
| `ALERT_RESET_HOUR` | `src/scheduler.py` | `6` | Hour (local time) at which daily alert deduplication resets |

## Deployment

**systemd unit file:** `install/projects_service-monitor.service`

```ini
[Unit]
Description=Service Monitor
After=multi-user.target

[Service]
WorkingDirectory=/home/mnalavadi/service-monitor
Type=idle
ExecStart=/home/mnalavadi/.local/bin/uv run src/app.py
User=mnalavadi

[Install]
WantedBy=multi-user.target
```

**Cloudflared:** Configured via `add_cloudflared_service.sh` to expose on `service-monitor.mnalavadi.org`.

## External Dependencies

| Service | Purpose | Auth |
|---|---|---|
| systemd | Service management | Local system |
| Cloudflared | HTTPS tunnel | Cloudflare account |
| Telegram Bot API | Failure alerts | Bot token in `.env` |
| GitHub Actions API | CI status badges | PAT in `.env` (optional) |

## Known Limitations

- Inspector Detector check endpoint is hardcoded to a specific service name and configured via `INSPECTOR_DETECTOR_*` env vars
- No authentication on web interface
- Requires sudo for restart functionality (must configure sudoers)
- Only monitors services matching `projects_*` pattern
- Log streaming only works on Linux (journalctl); dev mode shows placeholder
