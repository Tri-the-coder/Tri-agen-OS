# tri-buddy-agent

Phase 1 scaffold for a lightweight bot service.

## Files
- bot.py: Flask app exposing health and message endpoints.
- zenzap_client.py: small helper for testing Zenzap connectivity.
- requirements.txt: Python dependencies.

## Local run
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 bot.py
```

## Endpoints
- GET / -> service status
- GET /health -> checks the configured Zenzap connection
- POST /message -> forwards a message to Zenzap
