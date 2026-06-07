# ALBON PBR Remote Monitoring & Control Stack

A modular IoT software stack for ALBON's solar-powered photobioreactor (PBR) prototype at Wiley Park. Designed, built, and tested as part of the ALBON SE Design Brief 2026.

> **Note:** For comprehensive documentation regarding architecture decisions (WebSocket vs. Polling), fail-safe logic, production deployment, and troubleshooting, please refer to the accompanying **ALBON_SE_Candidate_Submission_20260601.pdf** report.

---

## To start Programs

**Prerequisites:** Python 3.10+ (3.14 tested), `pip`

```bash
# 1. Clone and enter the project
git clone <repo-url> 

# 2. Install dependencies
pip install fastapi uvicorn websockets httpx pytest --break-system-packages

#NOTE: Can also pip install pytest-html (can be used to generate html reports)

# 3. Start the server
uvicorn server.main:app --host 127.0.0.1 --port 8000

# 4. Open the web UI
# Visit http://127.0.0.1:8000 in any browser

# 5. (Optional) Run the CLI operator client
python client/operator_client.py         # interactive shell
python client/operator_client.py monitor # live telemetry stream

# 6. Run tests
pytest tests/ -v

#Note if you want html report run:
pytest tests/ -v --html=report.html --self-contained-html