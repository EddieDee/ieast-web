"""iEAST / LinkPlay web controller.

Runs on the client's LAN (Windows server, Docker, anything with Python 3.11+).
Users reach it through the SonicWall VPN at http://<server-ip>:8080.

Config via environment variables:
  DEVICES   comma-separated IPs of the iEAST units (recommended, required in Docker).
            Example: DEVICES=192.168.1.50,192.168.1.51
  DISCOVER  "1" to also try multicast discovery (works only when running natively on the LAN).
  PORT      listening port, default 8080.
"""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from linkplay.bridge import LinkPlayBridge
from linkplay.consts import EqualizerMode, PlayingMode
from linkplay.discovery import discover_linkplay_bridges, linkplay_factory_httpapi_bridge
from linkplay.utils import MTLS_CERTIFICATE_CONTENTS, async_create_ssl_context, session_call_api
from aiohttp import ClientSession, TCPConnector
import socket

STATIC = Path(__file__).parent / "static"
LOG = Path(__file__).resolve().parent.parent / "ieast-web.log"


def log(msg: str) -> None:
    import datetime
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass

# Load .env from the project folder so the Task Scheduler job needs no env setup.
_env = Path(__file__).resolve().parent.parent / ".env"
if _env.is_file():
    for _line in _env.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
bridges: dict[str, LinkPlayBridge] = {}
selected: dict[str, str] = {}   # uuid -> name of the station the user last chose
last_pos: dict[str, tuple[int, float]] = {}  # uuid -> (position, timestamp)
session = None


async def load_devices() -> None:
    global session
    if session is None:
        # Write the LinkPlay client certificate to a regular file. The library's own
        # NamedTemporaryFile approach fails on Windows with "Permission denied".
        certfile = Path(__file__).parent / "linkplay.pem"
        certfile.write_text(MTLS_CERTIFICATE_CONTENTS, encoding="utf-8")
        ctx = await async_create_ssl_context(certfile=str(certfile))
        session = ClientSession(connector=TCPConnector(family=socket.AF_UNSPEC, ssl=ctx))
    found: dict[str, LinkPlayBridge] = {}
    for ip in filter(None, (s.strip() for s in os.getenv("DEVICES", "").split(","))):
        try:
            b = await linkplay_factory_httpapi_bridge(ip, session)
            found[b.device.uuid] = b
        except Exception as exc:  # noqa: BLE001
            log(f"[warn] could not reach {ip}: {exc}")
    if os.getenv("DISCOVER", "0") == "1":
        try:
            for b in await discover_linkplay_bridges(session):
                found.setdefault(b.device.uuid, b)
        except Exception as exc:  # noqa: BLE001
            log(f"[warn] discovery failed: {exc}")
    bridges.clear()
    bridges.update(found)
    log(f"[info] {len(bridges)} device(s): " + ", ".join(b.device.name for b in bridges.values()))


@asynccontextmanager
async def lifespan(_: FastAPI):
    await load_devices()
    yield
    if session:
        await session.close()


app = FastAPI(title="iEAST Web Control", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

SOURCES = {
    "wifi": PlayingMode.NETWORK, "line_in": PlayingMode.LINE_IN, "bluetooth": PlayingMode.BLUETOOTH,
    "optical": PlayingMode.OPTICAL, "coaxial": PlayingMode.COAXIAL, "usb": PlayingMode.UDISK,
}


def get(uuid: str) -> LinkPlayBridge:
    if uuid not in bridges:
        raise HTTPException(404, "device not found")
    return bridges[uuid]


def snapshot(b: LinkPlayBridge) -> dict:
    import time
    p = b.device.playmode_support
    u = b.device.uuid
    pos = b.player.current_position
    prev = last_pos.get(u)
    last_pos[u] = (pos, time.time())
    # "streaming" = position advanced since the last poll while status is play
    streaming = bool(prev and pos > prev[0] and b.player.status.value == "play")
    return {
        "selected": selected.get(u, ""), "position": pos, "streaming": streaming,
        "uuid": b.device.uuid, "name": b.device.name, "model": b.device.model,
        "ip": b.endpoint._endpoint if hasattr(b.endpoint, "_endpoint") else str(b.endpoint),
        "status": b.player.status.value, "volume": b.player.volume, "muted": b.player.muted,
        "title": b.player.title, "artist": b.player.artist, "album": b.player.album,
        "art": b.player.album_art, "source": b.player.play_mode.name.lower(),
        "equalizer": b.player.equalizer_mode.name.lower(),
        "equalizers": [e.name.lower() for e in b.player.available_equalizer_modes],
        "sources": [k for k, v in SOURCES.items() if v in p],
    }


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/devices")
async def devices():
    async def refresh(b: LinkPlayBridge):
        try:
            await b.player.update_status()
        except Exception as exc:  # noqa: BLE001
            log(f"[warn] status refresh failed for {b.device.name}: {exc}")
        return snapshot(b)
    return await asyncio.gather(*(refresh(b) for b in bridges.values()))


@app.post("/api/rescan")
async def rescan():
    await load_devices()
    return {"count": len(bridges)}


class Cmd(BaseModel):
    action: str
    value: int | str | None = None
    label: str | None = None


@app.post("/api/devices/{uuid}/cmd")
async def command(uuid: str, cmd: Cmd):
    b = get(uuid)
    p = b.player
    a, v = cmd.action, cmd.value
    log(f"[cmd] {b.device.name}: {a} {v if v is not None else ''}")
    reply = "OK"
    try:
        if a == "play": await p.resume()
        elif a == "pause": await p.pause()
        elif a == "toggle": await p.toggle()
        elif a == "stop": await p.stop()
        elif a == "next": await p.next()
        elif a == "prev": await p.previous()
        elif a == "mute": await p.mute()
        elif a == "unmute": await p.unmute()
        elif a == "volume": await p.set_volume(max(0, min(100, int(v))))
        elif a == "play_url":
            # Send directly so we can report the device's reply ("OK", "Failed", ...)
            reply = await session_call_api(b.endpoint._endpoint, session, f"setPlayerCmd:play:{v}")
            log(f"[cmd] device replied: {reply!r}")
            selected[uuid] = cmd.label or str(v)
        elif a == "preset":
            await p.play_preset(int(v))
            selected[uuid] = f"Preset {v}"
        elif a == "source": await p.set_play_mode(SOURCES[str(v)])
        elif a == "equalizer": await p.set_equalizer_mode(EqualizerMode[str(v).upper()])
        elif a == "reboot": await get(uuid).device.reboot()
        else: raise HTTPException(400, f"unknown action {a}")
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log(f"[cmd] failed: {exc}")
        raise HTTPException(502, f"device error: {exc}") from exc
    # Devices sometimes answer "unknown command" to a status refresh right after a
    # command; retry once, then fall back to the last known state.
    for _ in range(2):
        try:
            await asyncio.sleep(0.3)
            await p.update_status()
            break
        except Exception:  # noqa: BLE001
            pass
    return {**snapshot(b), "reply": reply}


RADIO_API = "https://de1.api.radio-browser.info/json/stations/search"
_station_cache: dict[str, list] = {}


@app.get("/api/stations")
async def stations(q: str = "", tag: str = "classical", limit: int = 40):
    """Verified, top-voted internet radio stations from radio-browser.info."""
    key = f"{q}|{tag}|{limit}"
    if key in _station_cache:
        return _station_cache[key]
    params = {"order": "votes", "reverse": "true", "hidebroken": "true", "limit": str(limit * 3)}
    if q:
        params["name"] = q
    if tag:
        params["tag"] = tag
    try:
        async with ClientSession() as http:
            async with http.get(RADIO_API, params=params, timeout=15,
                                headers={"User-Agent": "ieast-web/1.0"}) as r:
                data = await r.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"radio directory unavailable: {exc}") from exc
    # LinkPlay modules play MP3/AAC http(s) streams; skip Opus, HLS playlists, etc.
    out = []
    for st in data:
        url = st.get("url_resolved") or st.get("url") or ""
        codec = (st.get("codec") or "").upper()
        if not url.startswith("http") or url.endswith((".m3u8", ".pls", ".m3u")):
            continue
        if codec not in ("MP3", "AAC", "AAC+"):
            continue
        out.append({"name": st["name"].strip(), "url": url, "country": st.get("country", ""),
                    "codec": codec, "bitrate": st.get("bitrate", 0)})
        if len(out) >= limit:
            break
    _station_cache[key] = out
    return out


@app.get("/api/devices/{uuid}/raw/{command}")
async def raw(uuid: str, command: str):
    """Escape hatch: forward any httpapi.asp command, e.g. /raw/getStatusEx."""
    return JSONResponse(await get(uuid).json_request(command))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
