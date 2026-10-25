"""KOTSU web server: REST + WebSocket around the engine, plus the web pages."""
from __future__ import annotations

import asyncio
import io
import json
import os
import socket
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import pitch
from .engine import Engine
from .mine import mine
from .seed import seed_sample
from .store import Store, new_id
from .words import LEXICON, word_info

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

app = FastAPI(title="KOTSU")
store = Store()
engine = Engine(store)
clients: set[WebSocket] = set()
PUBLIC_URL = {"url": os.environ.get("KOTSU_PUBLIC_URL", "")}
live_last: dict[str, float] = {}


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


async def broadcast(msg: dict) -> None:
    if not clients:
        return
    text = json.dumps(msg, ensure_ascii=False, default=float)
    dead = []
    for ws in list(clients):
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


async def pump() -> None:
    while True:
        for ev in engine.drain():
            await broadcast(ev)
        await asyncio.sleep(0.05)


@app.on_event("startup")
async def _startup() -> None:
    asyncio.create_task(pump())


# ---------------- pages ----------------
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/phone", response_class=HTMLResponse)
async def phone() -> FileResponse:
    return FileResponse(STATIC / "phone.html")


@app.get("/force", response_class=HTMLResponse)
async def force() -> FileResponse:
    return FileResponse(STATIC / "force.html")


@app.get("/clips/{name}")
async def clip(name: str) -> FileResponse:
    p = store.clip_dir / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/qr.svg")
async def qr(url: str) -> Response:
    try:
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
        buf = io.BytesIO()
        img.save(buf)
        return Response(buf.getvalue(), media_type="image/svg+xml")
    except Exception as e:  # pragma: no cover
        return Response(f"<svg xmlns='http://www.w3.org/2000/svg' width='300' height='40'><text x='4' y='24'>QR unavailable: {e}</text></svg>",
                        media_type="image/svg+xml")


# ---------------- state ----------------
@app.get("/api/state")
async def state() -> dict:
    # include the strokes behind the latest lessons, so their plots survive a restart
    lessons = sorted(store.data["lessons"].values(), key=lambda l: -l["t"])[:20]
    stroke_ids = {r["id"] for r in list(engine.recent)[-12:]}
    for lesson in lessons:
        stroke_ids.update(sid for sid in (lesson.get("try_id"), lesson.get("demo_id")) if sid)
    recent_strokes = sorted((store.data["strokes"][sid] for sid in stroke_ids if sid in store.data["strokes"]),
                            key=lambda st: st.get("t1", 0))
    return {
        "session": engine.session(),
        "masters": list(store.data["masters"].values()),
        "learners": list(store.data["learners"].values()),
        "dictionary": engine.dictionary(),
        "lexicon": [word_info(w) for w in LEXICON],
        "recent": recent_strokes,
        "lessons": lessons,
        "utterances": store.data["utterances"][-20:],
        "urls": {"lan": f"http://{lan_ip()}:{os.environ.get('KOTSU_PORT', '8000')}", "public": PUBLIC_URL["url"]},
        "server_time": time.time(),
    }


@app.post("/api/public_url")
async def set_public_url(body: dict) -> dict:
    PUBLIC_URL["url"] = (body.get("url") or "").rstrip("/")
    return {"public": PUBLIC_URL["url"]}


@app.post("/api/session")
async def session(body: dict) -> dict:
    return engine.set_session(mode=body.get("mode"), master_id=body.get("master_id"), learner_id=body.get("learner_id"),
                              word=body.get("word"))


@app.post("/api/master")
async def add_master(body: dict) -> dict:
    m = store.add_master((body.get("name") or "Master").strip())
    engine.set_session(master_id=m["id"])
    return m


@app.post("/api/master/{mid}/rename")
async def rename_master(mid: str, body: dict) -> dict:
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Give the master a name.")
    if mid not in store.data["masters"]:
        raise HTTPException(404, "No such master.")
    m = store.rename_master(mid, name[:60])
    engine.emit("session", session=engine.session())
    return m


@app.post("/api/learner/{lid}/rename")
async def rename_learner(lid: str, body: dict) -> dict:
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Give the learner a name.")
    if lid not in store.data["learners"]:
        raise HTTPException(404, "No such learner.")
    l = store.rename_learner(lid, name[:60])
    engine.emit("session", session=engine.session())
    return l


@app.post("/api/learner")
async def add_learner(body: dict) -> dict:
    l = store.add_learner((body.get("name") or "Learner").strip(), body.get("lang", "en"))
    return l


# ---------------- listen ----------------
@app.post("/api/utterance")
async def utterance(body: dict) -> dict:
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "empty text")
    return engine.utterance(text, source=body.get("source", "speech"))


@app.post("/api/stroke")
async def post_stroke(body: dict) -> dict:
    src = body.get("src", "sim-1")
    d = engine.ingest_stroke(src, body)
    if not d:
        raise HTTPException(400, "stroke too short")
    return d


@app.post("/api/stroke/{sid}/label")
async def label(sid: str, body: dict) -> dict:
    try:
        return engine.label(sid, body["word"], bool(body.get("positive", True)))
    except KeyError:
        raise HTTPException(404, "no such stroke")


# ---------------- ground ----------------
@app.get("/api/dictionary")
async def dictionary(master_id: str | None = None) -> list:
    return engine.dictionary(master_id)


@app.get("/api/word")
async def word(key: str) -> dict:
    if key not in store.data["words"]:
        raise HTTPException(404)
    return engine.word_summary(key)


@app.post("/api/clip")
async def upload_clip(key: str = Form(...), file: UploadFile = File(...)) -> dict:
    if key not in store.data["words"]:
        raise HTTPException(404)
    ext = ".webm"
    if file.content_type and "mp4" in file.content_type:
        ext = ".mp4"
    elif file.content_type and "ogg" in file.content_type:
        ext = ".ogg"
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "Voice clips must be 10 MB or smaller.")
    name = new_id("clip") + ext
    (store.clip_dir / name).write_bytes(content)
    store.data["words"][key]["clip"] = name
    store.save()
    ws = engine.word_summary(key)
    engine.emit("word", word=ws)
    return ws


# ---------------- sharpen ----------------
@app.post("/api/nearmiss")
async def nearmiss(body: dict) -> dict:
    key = body.get("key")
    if key not in store.data["words"]:
        raise HTTPException(404)
    q = engine.nearmiss_next(key)
    if not q:
        raise HTTPException(409, "Not enough examples yet to ask a useful question. Record one or two more demos.")
    return q


@app.post("/api/nearmiss/{qid}")
async def nearmiss_answer(qid: str, body: dict) -> dict:
    if qid not in engine.questions:
        raise HTTPException(404)
    return engine.nearmiss_answer(qid, bool(body.get("yes")))


# ---------------- transfer ----------------
@app.post("/api/coach/start")
async def coach_start(body: dict) -> dict:
    if body.get("key") not in store.data["words"]:
        raise HTTPException(404)
    lid = body.get("learner_id") or store.add_learner("Learner")["id"]
    return engine.coach_start(lid, body["key"], body.get("condition", "kotsu"))


@app.post("/api/coach/test")
async def coach_test() -> dict:
    try:
        return engine.coach_test()
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/drill")
async def drill(body: dict) -> dict:
    if body.get("key") not in store.data["words"]:
        raise HTTPException(404)
    d = engine.drill_next(body["key"], body.get("learner_id"))
    if not d:
        raise HTTPException(409, "This word needs a few more examples before drills make sense.")
    return d


@app.post("/api/drill/{did}")
async def drill_answer(did: str, body: dict) -> dict:
    if did not in engine.drills:
        raise HTTPException(404)
    return engine.drill_answer(did, body.get("choice", "A"))


# ---------------- prove ----------------
@app.get("/api/passport/{learner_id}")
async def passport(learner_id: str) -> dict:
    return engine.passport(learner_id)


# ---------------- results ----------------
@app.get("/api/results")
async def results() -> dict:
    return engine.results()


@app.get("/api/graph")
async def graph() -> dict:
    return engine.graph()


@app.get("/api/compare")
async def compare(word: str, stype: str | None = None) -> dict:
    return engine.compare(word, stype)


# ---------------- pitch screen ----------------
@app.get("/pitch", response_class=HTMLResponse)
async def pitch_page() -> FileResponse:
    return FileResponse(STATIC / "pitch.html")


@app.get("/api/pitch")
async def pitch_data() -> dict:
    data = pitch.payload(engine)
    media = store.path.parent / "media"
    data["media"] = sorted(p.name for p in media.iterdir() if p.is_file()) if media.exists() else []
    return data


@app.post("/api/pitch/whose")
async def pitch_whose(body: dict) -> dict:
    try:
        return pitch.whose(engine, body.get("sid") or "", body.get("word"))
    except KeyError:
        raise HTTPException(404, "That press was not found. Press again.")


@app.post("/api/pitch/judge")
async def pitch_judge(body: dict) -> dict:
    sids = [s for s in (body.get("sids") or []) if isinstance(s, str)]
    if not sids:
        raise HTTPException(400, "Press the trackpad first.")
    try:
        return pitch.save_judge(engine, sids, body.get("word"), (body.get("name") or "Judge").strip()[:40] or "Judge")
    except KeyError:
        raise HTTPException(404, "Those presses were not found.")


@app.get("/media/{name}")
async def media_file(name: str) -> FileResponse:
    p = store.path.parent / "media" / Path(name).name
    if not p.exists() or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.post("/api/mine")
async def mine_transcript(body: dict) -> dict:
    text = body.get("text") or ""
    if not text.strip():
        raise HTTPException(400, "Paste a transcript first.")
    if len(text) > 400_000:
        raise HTTPException(413, "That transcript is too long. Paste up to about 400,000 characters at a time.")
    return mine(text)


# ---------------- data ----------------
@app.post("/api/reset")
async def reset(body: dict) -> dict:
    if body.get("confirm") != "RESET":
        raise HTTPException(400, "send {confirm: 'RESET'}")
    store.reset()
    engine.__init__(store)
    engine.emit("reset")
    return {"ok": True}


@app.post("/api/seed")
async def seed() -> dict:
    n = seed_sample(engine)
    return {"ok": True, "strokes": n}


@app.get("/api/export")
async def export() -> JSONResponse:
    return JSONResponse(store.data)


# ---------------- websocket ----------------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    clients.add(ws)
    src = ws.query_params.get("src")
    try:
        await ws.send_text(json.dumps({"type": "hello", "session": engine.session(), "src": src}, ensure_ascii=False, default=float))
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            typ = msg.get("type")
            s = msg.get("src") or src or "unknown-1"
            if typ == "samples":
                engine.ingest_samples(s, msg.get("data") or [])
                engine.seen(s)
            elif typ == "stroke":
                engine.ingest_stroke(s, msg)
            elif typ == "live":
                engine.seen(s)
                now = time.time()
                if now - live_last.get(s, 0) >= 0.04:
                    live_last[s] = now
                    await broadcast({"type": "live", "src": s, "v": msg.get("v"), "t": now})
            elif typ == "hello":
                engine.seen(s)
                engine.emit("source", src=s, kind=msg.get("kind"))
            elif typ == "ping":
                await ws.send_text(json.dumps({"type": "pong", "t": time.time()}))
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)
