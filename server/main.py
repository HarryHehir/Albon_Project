import asyncio
import json
import logging
import time
import random
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, validator
#Note here also there are some lines of codes that have lines through them when looking on VSCode, this is cause im using Pydantic v1 but in future will change to use v2.

#just the basic for it should be logged in the terminal, makes easier to read.
logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","module":"%(module)s","msg":%(message)s}',
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("pbr")


# this just uses the pydantic BaseModel which means it defines the data into specific types so if later on one of them randomly
# change it will throw and error. (can catch it)
class SensorReading(BaseModel):
    timestamp: str
    ph: float
    dissolved_oxygen: float   # mg/L
    temperature: float        # °C
    turbidity: float          # NTU

class ActuatorState(BaseModel):
    pump_feed: bool = False
    pump_recirculation: bool = False
    led_intensity: int = 0    # 0–100 %
    co2_valve: bool = False

#this the class that controls when someone wants to actually send something. it checks to see if this is a valid command. 
class CommandRequest(BaseModel):
    actuator: str
    value: bool | int
    operator: str = "anonymous"

# @validator is just a safety check that runs automatically when a command arrives.

# this one here tests if the actuator name is allowed
    @validator("actuator")
    def actuator_must_be_known(cls, v):
        allowed = {"pump_feed", "pump_recirculation", "led_intensity", "co2_valve"}
        if v not in allowed:
            raise ValueError(f"Unknown actuator '{v}'. Allowed: {allowed}")
        return v
# this one tests that the intensity is a value between 0 - 100
    @validator("value")
    def value_in_range(cls, v, values):
        if values.get("actuator") == "led_intensity":
            if type(v) is not int or not (0 <= v <= 100):
                raise ValueError("led_intensity must be integer 0–100")
        return v

class SystemStatus(BaseModel):
    connected: bool
    sensors: Optional[SensorReading]
    actuators: ActuatorState
    alerts: list[str]
    server_uptime_s: float
    message_rate_per_min: float


class SimulatedPLCAdapter:
    def __init__(self):
        self._actuators = ActuatorState()
        self._base_ph = 7.2
        self._base_do = 8.5
        self._base_temp = 22.0
        self._base_turbidity = 12.0

    def read_sensors(self) -> SensorReading:
        self._base_ph += random.uniform(-0.005, 0.005)
        self._base_do += random.uniform(-0.02, 0.02)
        self._base_temp += random.uniform(-0.01, 0.01)
        self._base_turbidity += random.uniform(-0.1, 0.1)

        self._base_ph = max(5.5, min(9.0, self._base_ph))
        self._base_do = max(0.0, min(20.0, self._base_do))
        self._base_temp = max(10.0, min(40.0, self._base_temp))
        self._base_turbidity = max(0.0, min(200.0, self._base_turbidity))

        return SensorReading(
            timestamp=datetime.now(timezone.utc).isoformat(),
            ph=round(self._base_ph + random.gauss(0, 0.01), 3),
            dissolved_oxygen=round(self._base_do + random.gauss(0, 0.05), 3),
            temperature=round(self._base_temp + random.gauss(0, 0.1), 2),
            turbidity=round(self._base_turbidity + random.gauss(0, 0.2), 2),
        )

    def write_actuator(self, actuator: str, value: bool | int) -> None:
        log.info(f'"actuator_write" actuator="{actuator}" value={value}')
        setattr(self._actuators, actuator, value) #note: this the same as writing self._actuators.actuator = value, it allows no hardcoding the name.
    def get_actuators(self) -> ActuatorState:
        return self._actuators.copy()

    def confirm_actuator_state(self) -> ActuatorState:
        log.info('"actuator_confirm" msg="Re-confirming actuator states after reconnect"')
        return self.get_actuators()


ALERT_THRESHOLDS = {
    "ph":               (6.0, 8.5),
    "dissolved_oxygen": (2.0, 15.0),
    "temperature":      (15.0, 35.0),
    "turbidity":        (0.0, 100.0),
    # these are just made up but in the real environment would change for specific specifications for the system.
}

def check_alerts(sensors: SensorReading) -> list[str]:
    alerts = []
    for field, (lo, hi) in ALERT_THRESHOLDS.items():
        val = getattr(sensors, field)
        if val < lo:
            alerts.append(f"{field.upper()} LOW: {val} (min {lo})")
        elif val > hi:
            alerts.append(f"{field.upper()} HIGH: {val} (max {hi})")
    return alerts


class ConnectionManager:
    def __init__(self):
        self._connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.add(ws)
        log.info(f'"ws_connect" clients={len(self._connections)}')

    def disconnect(self, ws: WebSocket):
        self._connections.discard(ws)
        log.info(f'"ws_disconnect" clients={len(self._connections)}')

    async def broadcast(self, data: dict):
        dead = set()
        for ws in self._connections:
            try:
                await ws.send_text(json.dumps(data))
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def count(self) -> int:
        return len(self._connections)


app = FastAPI(title="ALBON PBR Control API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_headers=["*"],
)
#NOTE: here you would change to the exact origins you want.

adapter = SimulatedPLCAdapter()
manager = ConnectionManager()

_start_time = time.monotonic()
_msg_count = 0
_err_count = 0
_last_sensors: Optional[SensorReading] = None
# NOTE: _last_sensors is a global variable safe for single-worker deployment (Raspberry Pi prototype).
# If scaled to multiple uvicorn workers (--workers 4), each process holds its own isolated copy,
# destroying the single source of truth. Fix for multi-worker: replace with a shared cache (e.g. Redis)
# or use a message broker (e.g. MQTT) as the authoritative state store.


async def sensor_loop():
    global _msg_count, _last_sensors
    while True:
        sensors = adapter.read_sensors()
        _last_sensors = sensors
        alerts = check_alerts(sensors)

        payload = {
            "type": "telemetry",
            "sensors": sensors.dict(),
            "actuators": adapter.get_actuators().dict(),
            "alerts": alerts,
            "server_uptime_s": round(time.monotonic() - _start_time, 1),
        }
        if manager.count > 0:
            await manager.broadcast(payload)
            _msg_count += 1

        await asyncio.sleep(1.0)  
@app.on_event("startup")
async def startup():
    asyncio.create_task(sensor_loop())
    log.info('"startup" msg="PBR server started, sensor loop running"')



@app.get("/api/health")
def health():
    uptime = round(time.monotonic() - _start_time, 1)
    return {"status": "ok", "uptime_s": uptime, "clients": manager.count}

@app.get("/api/sensors/latest")
def sensors_latest():
    if _last_sensors is None:
        raise HTTPException(status_code=503, detail="No sensor data yet")
    return _last_sensors

@app.get("/api/actuators")
def actuators_get():
    return adapter.get_actuators()

@app.post("/api/actuators/command", status_code=status.HTTP_202_ACCEPTED)
async def actuators_command(cmd: CommandRequest):

    global _err_count
    try:
        # NOTE: SimulatedPLCAdapter is safe to call directly as it executes in-memory.
        # If replaced with a real synchronous adapter (e.g. pyModbusTCP, python-snap7),
        # this call must be offloaded to a thread pool to avoid blocking the async event
        # loop and freezing the sensor broadcast:
        #   await asyncio.get_running_loop().run_in_executor(None, adapter.write_actuator, cmd.actuator, cmd.value)
        adapter.write_actuator(cmd.actuator, cmd.value)
        confirmed = adapter.get_actuators()
        log.info(f'"command_accepted" operator="{cmd.operator}" actuator="{cmd.actuator}" value={cmd.value}')

        await manager.broadcast({
            "type": "actuator_update",
            "actuators": confirmed.dict(),
            "operator": cmd.operator,
        })
        return {"status": "accepted", "confirmed": confirmed}
    except Exception as e:
        _err_count += 1
        log.error(f'"command_error" error="{str(e)}"')
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/metrics")
def metrics():
    uptime = time.monotonic() - _start_time
    rate = round((_msg_count / uptime) * 60, 2) if uptime > 0 else 0
    return {
        "uptime_s": round(uptime, 1),
        "messages_sent": _msg_count,
        "errors": _err_count,
        "message_rate_per_min": rate,
        "connected_clients": manager.count,
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    if _last_sensors:
        await websocket.send_text(json.dumps({
            "type": "telemetry",
            "sensors": _last_sensors.dict(),
            "actuators": adapter.get_actuators().dict(),
            "alerts": check_alerts(_last_sensors),
            "server_uptime_s": round(time.monotonic() - _start_time, 1),
        }))
    confirmed = adapter.confirm_actuator_state()
    await websocket.send_text(json.dumps({
        "type": "actuator_confirm",
        "actuators": confirmed.dict(),
        "msg": "Actuator state confirmed on connect",
    }))
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({
                        "type": "pong",
                        "client_ts": msg["ts"]
                    }))
            except json.JSONDecodeError:
                pass 
    except WebSocketDisconnect:
        manager.disconnect(websocket)

import os
static_dir = os.path.join(os.path.dirname(__file__), "..", "ui")
if os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
