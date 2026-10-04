import os

from app.main import app

if __name__ == "__main__":
    # Render injects $PORT and expects the process to bind it. Hardcoding a port
    # only works while Render happens to auto-detect whatever we listened on.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
