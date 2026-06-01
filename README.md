# ALBON PBR Remote Monitoring & Control Stack

A modular IoT software stack for ALBON's solar-powered photobioreactor (PBR) prototype at Wiley Park. Designed, built, and tested as part of the ALBON SE Design Brief 2026.

---

## Quick Start (under 5 minutes)

**Prerequisites:** Python 3.10+ (3.12 tested), pip

```bash
# 1. Clone and enter the project
git clone <repo-url> albon-pbr && cd albon-pbr

# 2. Install dependencies
pip install fastapi uvicorn websockets httpx pytest --break-system-packages

# 3. Start the server
uvicorn server.main:app --host 127.0.0.1 --port 8000

# 4. Open the web UI
# Visit http://127.0.0.1:8000 in any browser

# 5. (Optional) Run the CLI operator client
python client/operator_client.py         # interactive shell
python client/operator_client.py monitor # live telemetry stream

# 6. Run tests
pytest tests/ -v
```

That is the entire setup. No Docker, no databases, no config files required for local development.

---

## Architecture Decision: WebSocket Push vs REST Polling

Two approaches were evaluated:

| | REST + Polling | **WebSocket Push (chosen)** |
|---|---|---|
| Latency | ~500ms–2000ms (poll interval) | **<100ms on LAN** |
| Server load | High (repeated requests) | Low (push on change) |
| Connection loss detection | Implicit (missed poll) | **Explicit onclose event** |
| Safe reconnect logic | Complex to implement | **Native onopen/onclose hooks** |
| RPi4 suitability | ✓ | **✓** |
| Complexity | Low | Moderate |

**Selected: WebSocket push.** The operator safety argument is decisive — when a pump is running, an operator needs to know about a sensor alarm in under 1 second, not after the next polling cycle. WebSocket also gives us a clean connection lifecycle to enforce the actuator-confirmation requirement.

---

## System Architecture

```
┌─────────────────────┐         ┌──────────────────────────────────────┐
│  Desktop Client     │         │  Server (FastAPI + uvicorn)           │
│  operator_client.py │◄──────►│  server/main.py                       │
│                     │  HTTP   │                                        │
│  • Interactive CLI  │  REST   │  ┌─────────────┐  ┌───────────────┐  │
│  • send commands    │         │  │ REST API    │  │ WebSocket     │  │
│  • view telemetry   │◄──────►│  │ /api/*      │  │ /ws           │  │
└─────────────────────┘  WS     │  └─────────────┘  └───────────────┘  │
                                │         │                  │           │
┌─────────────────────┐         │  ┌──────▼──────────────────▼───────┐  │
│  Web UI             │◄──────►│  │ SimulatedPLCAdapter              │  │
│  ui/index.html      │  WS     │  │ (swap → RealPLCAdapter)         │  │
│                     │         │  │ • read_sensors()                 │  │
│  • Live telemetry   │         │  │ • write_actuator()               │  │
│  • Actuator toggles │         │  │ • confirm_actuator_state()       │  │
│  • Alert banner     │         │  └──────────────────────────────────┘  │
│  • Sparkline charts │         └──────────────────────────────────────┘
└─────────────────────┘
```

**Three tiers, one process, single file per tier.** No microservices, no message broker — this is a prototype for a small team.

---

## Safety & Fail-Safe Logic

1. **Connection drop** → `ws.onclose` fires immediately. UI shows red banner. All controls disabled.
2. **Reconnect** → Server sends `actuator_confirm` message on every new WebSocket connection before any control messages flow.
3. **Controls re-enable** only after `actuator_confirm` is received by the UI.
4. **Out-of-range setpoints** are rejected by Pydantic validators server-side before reaching the hardware adapter.
5. **Dry-run mode** is the default — `SimulatedPLCAdapter` runs with no PLC attached.

---

## Replacing the Simulated Adapter

To connect real hardware (Siemens LOGO! via python-snap7 or Modbus TCP):

```python
# server/adapters/real_plc.py
from snap7 import Client

class RealPLCAdapter:
    def __init__(self, host: str, rack: int = 0, slot: int = 1):
        self.client = Client()
        self.client.connect(host, rack, slot)

    def read_sensors(self) -> SensorReading:
        # Read from DB blocks — replace DB/byte offsets with actual PLC mapping
        raw = self.client.db_read(1, 0, 16)
        return SensorReading(ph=..., ...)

    def write_actuator(self, actuator, value):
        # Write coil or output register
        ...
```

Then in `server/main.py`, replace:
```python
adapter = SimulatedPLCAdapter()
# with:
adapter = RealPLCAdapter(host="192.168.1.100")
```

**No other files change.** The UI, API, and tests are all adapter-agnostic.

---

## API Reference

### `GET /api/health`
Returns server status and uptime.

### `GET /api/sensors/latest`
Returns the most recent sensor reading (REST fallback).

### `GET /api/actuators`
Returns current actuator state.

### `POST /api/actuators/command`
Send an actuator command.
```json
{ "actuator": "pump_feed", "value": true, "operator": "cli-operator" }
```
Returns `202 Accepted` + confirmed state, or `422 Unprocessable Entity` on validation failure.

Allowed actuators: `pump_feed`, `pump_recirculation`, `co2_valve`, `led_intensity` (0–100)

### `GET /api/metrics`
Returns uptime, message rate, error count, connected clients.

### `WS /ws`
WebSocket endpoint. Server pushes:
- `telemetry` — sensor readings + actuator state + alerts at 1 Hz
- `actuator_confirm` — on every new connection (safety requirement)
- `actuator_update` — after every successful command

---

## Deploying to Raspberry Pi 4

```bash
# On RPi4 running Pi OS Bookworm (Python 3.11+)
git clone <repo-url> albon-pbr && cd albon-pbr
pip install fastapi uvicorn websockets httpx --break-system-packages

# Bind to LAN IP (replace with actual RPi IP)
uvicorn server.main:app --host 0.0.0.0 --port 8000

# Optional: run as a systemd service for auto-start
```

**Containerised deployment (path to production):**
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install fastapi uvicorn websockets httpx
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
```
```bash
docker build -t albon-pbr . && docker run -p 8000:8000 albon-pbr
```

---

## Troubleshooting Checklist (Top 3 Live-Site Failures)

**1. UI connects but shows stale/no data**
- Check `uvicorn` process is running: `ps aux | grep uvicorn`
- Check `GET /api/health` returns 200 from the browser
- Check firewall allows port 8000: `sudo ufw allow 8000`
- Check browser console for WebSocket errors — ensure `ws://` not `wss://` on LAN

**2. Actuator command rejected with 422**
- Value is out of allowed range (LED must be 0–100)
- Actuator name is misspelled — check exact field names in `/api/actuators`
- JSON payload malformed — use Content-Type: application/json

**3. High latency or dropped messages on RPi4**
- Check WiFi signal strength: `iwconfig wlan0`
- Switch to wired Ethernet — WiFi is unreliable outdoors at Wiley Park
- Check CPU load: `htop` — uvicorn async loop should idle under 5% CPU
- Reduce sensor loop to 2 Hz if needed: change `asyncio.sleep(1.0)` to `asyncio.sleep(0.5)`

---

## Security Notes

**Local LAN (current):** Server binds to `127.0.0.1` by default. Safe for development.

**Hardening for WAN/production:**
- Add JWT bearer token authentication to all API endpoints
- Switch to HTTPS/WSS via nginx reverse proxy with Let's Encrypt
- Restrict `allow_origins` in CORS middleware to specific client IPs
- Add rate limiting (e.g. `slowapi`) to prevent command flooding
- Role separation: read-only token vs write token; log all write operations

---

## Code Style Guide

- **Python:** PEP 8, type hints throughout, docstrings on all public methods
- **Async:** `async/await` throughout server; no blocking calls on the event loop
- **Logging:** structured JSON logs with `ts`, `level`, `module`, `msg` fields
- **Models:** Pydantic for all data boundaries — never raw dicts across layer boundaries
- **Naming:** `snake_case` for all Python; `camelCase` for JavaScript; descriptive over terse
- **Comments:** explain *why*, not *what* — the code shows what, comments explain intent and trade-offs
- **Tests:** one test class per component; happy path + at least one failure mode per function

## Project Structure

```
albon-pbr/
├── server/
│   └── main.py          # FastAPI server, WebSocket manager, device adapter
├── client/
│   └── operator_client.py  # CLI operator client (monitor + interactive shell)
├── ui/
│   └── index.html       # Web UI served by FastAPI
├── tests/
│   └── test_server.py   # 19 unit + integration tests
└── README.md
```
