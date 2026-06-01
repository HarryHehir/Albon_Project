import asyncio
import json
import sys
import time
import argparse
from datetime import datetime

try:
    import websockets
except ImportError:
    print("Install websockets: pip install websockets --break-system-packages")
    sys.exit(1)

try:
    import httpx
except ImportError:
    print("Install httpx: pip install httpx --break-system-packages")
    sys.exit(1)

BASE_URL = "http://127.0.0.1:8000"
WS_URL  = "ws://127.0.0.1:8000/ws"
OPERATOR = "cli-operator"

G = "\033[32m"; Y = "\033[33m"; R = "\033[31m"; B = "\033[36m"; DIM = "\033[2m"; RST = "\033[0m"

def ts(): return datetime.now().strftime("%H:%M:%S")

def fmt_sensor(s: dict) -> str:
    return (
        f"  pH={G}{s['ph']:.3f}{RST}  "
        f"DO={G}{s['dissolved_oxygen']:.3f} mg/L{RST}  "
        f"Temp={G}{s['temperature']:.2f}°C{RST}  "
        f"Turbidity={G}{s['turbidity']:.2f} NTU{RST}"
    )

def fmt_actuators(a: dict) -> str:
    def state(v): return f"{G}ON {RST}" if v else f"{R}OFF{RST}"
    return (
        f"  FeedPump={state(a['pump_feed'])}  "
        f"RecircPump={state(a['pump_recirculation'])}  "
        f"CO2={state(a['co2_valve'])}  "
        f"LED={G}{a['led_intensity']}%{RST}"
    )

async def live_monitor():
    print(f"\n{B}[{ts()}] Connecting to {WS_URL}…{RST}")
    reconnects = 0
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                reconnects += 1
                if reconnects > 1:
                    print(f"{Y}[{ts()}] Reconnected (attempt {reconnects}){RST}")
                else:
                    print(f"{G}[{ts()}] Connected — streaming telemetry (Ctrl+C to exit){RST}\n")

                async for raw in ws:
                    msg = json.loads(raw)
                    if msg["type"] == "telemetry":
                        alerts = msg.get("alerts", [])
                        alert_str = f"  {R}⚠ {', '.join(alerts)}{RST}" if alerts else ""
                        print(f"{DIM}[{ts()}]{RST} {fmt_sensor(msg['sensors'])}{alert_str}")
                    elif msg["type"] == "actuator_confirm":
                        print(f"{G}[{ts()}] Actuator state confirmed:{RST}\n{fmt_actuators(msg['actuators'])}")
                    elif msg["type"] == "actuator_update":
                        print(f"{Y}[{ts()}] Actuator updated by {msg.get('operator','?')}:{RST}\n{fmt_actuators(msg['actuators'])}")

        except (websockets.exceptions.ConnectionClosed, OSError) as e:
            print(f"{R}[{ts()}] Connection lost ({e}) — retrying in 3s…{RST}")
            await asyncio.sleep(3)
        except KeyboardInterrupt:
            print(f"\n{DIM}Monitor stopped.{RST}")
            break

async def send_command(actuator: str, value):
    payload = {"actuator": actuator, "value": value, "operator": OPERATOR}
    async with httpx.AsyncClient() as client:
        t0 = time.monotonic()
        try:
            r = await client.post(f"{BASE_URL}/api/actuators/command", json=payload, timeout=5)
            latency_ms = round((time.monotonic() - t0) * 1000)
            if r.status_code == 202:
                data = r.json()
                print(f"{G}[{ts()}] ✓ Command accepted ({latency_ms}ms){RST}")
                print(f"  Confirmed state:\n{fmt_actuators(data['confirmed'])}")
            else:
                print(f"{R}[{ts()}] ✗ Command rejected ({r.status_code}): {r.text}{RST}")
        except httpx.ConnectError:
            print(f"{R}[{ts()}] Cannot connect to server at {BASE_URL}{RST}")

async def interactive_shell():
    print(f"\n{B}ALBON PBR Operator Client{RST}")
    print(f"{DIM}Commands: pump_feed on/off | pump_recirc on/off | co2 on/off | led <0-100> | status | exit{RST}\n")

    async with httpx.AsyncClient() as http:
        try:
            r = await http.get(f"{BASE_URL}/api/health", timeout=3)
            h = r.json()
            print(f"{G}Server online — uptime {h['uptime_s']}s, {h['clients']} client(s) connected{RST}\n")
        except Exception:
            print(f"{R}Server not reachable at {BASE_URL}. Is it running?{RST}\n")

    while True:
        try:
            cmd = input(f"{B}pbr>{RST} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if not cmd:
            continue
        elif cmd == "exit":
            break
        elif cmd in ("status", "s"):
            async with httpx.AsyncClient() as http:
                r = await http.get(f"{BASE_URL}/api/sensors/latest", timeout=3)
                a = await http.get(f"{BASE_URL}/api/actuators", timeout=3)
                print(fmt_sensor(r.json()))
                print(fmt_actuators(a.json()))
        elif cmd.startswith("pump_feed "):
            on = cmd.split()[1] == "on"
            await send_command("pump_feed", on)
        elif cmd.startswith("pump_recirc "):
            on = cmd.split()[1] == "on"
            await send_command("pump_recirculation", on)
        elif cmd.startswith("co2 "):
            on = cmd.split()[1] == "on"
            await send_command("co2_valve", on)
        elif cmd.startswith("led "):
            try:
                val = int(cmd.split()[1])
                await send_command("led_intensity", val)
            except (ValueError, IndexError):
                print(f"{R}Usage: led <0-100>{RST}")
        else:
            print(f"{Y}Unknown command. Try: pump_feed on | led 80 | status{RST}")

def main():
    parser = argparse.ArgumentParser(description="ALBON PBR Operator Client")
    sub = parser.add_subparsers(dest="mode")
    sub.add_parser("monitor", help="Live telemetry stream (WebSocket)")
    sub.add_parser("shell",   help="Interactive command shell (default)")
    cmd_p = sub.add_parser("cmd", help="Send single command and exit")
    cmd_p.add_argument("actuator", choices=["pump_feed","pump_recirculation","co2_valve","led_intensity"])
    cmd_p.add_argument("value")
    args = parser.parse_args()

    if args.mode == "monitor":
        asyncio.run(live_monitor())
    elif args.mode == "cmd":
        val = args.value
        if val.lower() in ("true","on"): val = True
        elif val.lower() in ("false","off"): val = False
        else:
            try: val = int(val)
            except ValueError: pass
        asyncio.run(send_command(args.actuator, val))
    else:
        asyncio.run(interactive_shell())

if __name__ == "__main__":
    main()
