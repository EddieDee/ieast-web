# iEAST Web Control

Small web app that controls iEAST (LinkPlay-based) speakers/amps over their local HTTP API.
Install it on the client's server inside the LAN; users open it through the SonicWall VPN
at `http://<server-ip>:8080` from any browser or phone. No Android VM, no cloud.

## Setup
1. Give each iEAST unit a fixed IP (DHCP reservation). Test from the server:
   `curl http://<ip>/httpapi.asp?command=getStatusEx`
2. Put those IPs in `DEVICES` (docker-compose.yml or run-windows.ps1).

### Docker (Windows Server with Docker Desktop / Linux)
    docker compose up -d --build
Discovery via multicast does not cross Docker NAT, so `DEVICES` is required there.

### Native on Windows Server
    py -m venv .venv
    .\.venv\Scripts\pip install -r requirements.txt
    .\run-windows.ps1
Unattended (starts at boot, no login needed): copy `.env.example` to `.env`, set DEVICES,
then run `.\install-service.ps1` once as Administrator. Manage it in Task Scheduler as
"iEAST Web Control".

## Streaming services (Amazon Music, Spotify...)
The local API cannot browse a service catalog; that only exists in the iEAST app.
Save the stations/playlists the client uses as presets 1-6 in the iEAST app, then
recall them from this page.

## Features
Play/pause/stop/next/prev, volume, mute, input source, equalizer, presets 1-6,
auto-refresh every 5 s, multiple devices on one page.
`POST /api/devices/{uuid}/cmd {"action":"play_url","value":"http://stream..."}` plays any URL.
`GET /api/devices/{uuid}/raw/<command>` forwards any raw httpapi.asp command.

## Security
Only reachable from the LAN/VPN. Restrict with a SonicWall access rule to port 8080 if needed.
No authentication is built in; add one (reverse proxy / basic auth) if VPN users should not all
have control.
