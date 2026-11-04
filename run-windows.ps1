# Native run on Windows Server (no Docker). Requires Python 3.11+ from python.org.
# One-time:  py -m venv .venv ; .\.venv\Scripts\pip install -r requirements.txt
$env:DEVICES  = "192.168.1.50,192.168.1.51"
$env:DISCOVER = "1"
$env:PORT     = "8080"
.\.venv\Scripts\python -m app.main
