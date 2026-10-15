"""Web paneli: /panel (HTTP Basic, PANEL_PASSWORD), /panel/data, /panel/live."""
import hmac
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import config, devices, home, members, power, speed, stats
from .db import db, kv_get

router = APIRouter(prefix="/panel")
basic = HTTPBasic(auto_error=False)
failures: list[float] = []
HTML = Path(__file__).parent / "static" / "panel.html"


def auth(c: HTTPBasicCredentials | None = Depends(basic)) -> None:
    if not config.PANEL_PASSWORD:
        raise HTTPException(status_code=404)
    now = time.time()
    failures[:] = [t for t in failures if now - t < 600]
    if len(failures) >= 10:
        raise HTTPException(status_code=429, detail="çok fazla hatalı deneme")
    if c is None or not hmac.compare_digest(c.password.encode(), config.PANEL_PASSWORD.encode()):
        if c is not None:
            failures.append(now)
        # HTTP başlıkları yalnızca Latin-1 olabilir: "ı" yazılırsa yanıt çöker
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Ev Asistani"'})


def data() -> dict:
    s = home.state
    with db() as con:
        outs = [dict(r) for r in con.execute("SELECT kind, started, ended, note FROM outages ORDER BY id DESC LIMIT 30")]
    stat = {}
    for d in (7, 30):
        st = stats.outage_stats(d)
        stat[str(d)] = {
            "total": round(st["total"]), "avail": st["avail"],
            "longest": round(st["longest"][0]) if st["longest"] else 0,
            "kinds": {k: {"n": v["n"], "sec": round(v["sec"])} for k, v in st["kinds"].items()},
        }
    return {
        "now": time.time(),
        "status": "down" if s["down_since"] is not None else ("up" if s["last_beat"] else "empty"),
        "last_beat": s["last_beat"], "down_since": s["down_since"], "info": s["info"],
        "power": power.power_text(s["info"]),
        "timeout_sec": home.timeout_sec(), "members": members.count(),
        "since": float(kv_get("since") or 0) or None,
        "stats": stat, "daily": stats.daily_downtime(30), "outages": outs, "devices": devices.listing(),
        "speedtests": speed.history(30),
    }


@router.get("", response_class=HTMLResponse, dependencies=[Depends(auth)])
async def page():
    return HTMLResponse(HTML.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@router.get("/data", dependencies=[Depends(auth)])
async def get_data():
    return data()


@router.post("/live", dependencies=[Depends(auth)])
async def live():
    if home.state["down_since"] is None and home.state["last_beat"]:
        await home.live_measure()
    return data()
