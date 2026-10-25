#!/usr/bin/env python3
"""Start KOTSU.

    python run.py                 dashboard at http://localhost:8000 (open it in Chrome)
    python run.py --tunnel        also open a public https address so phones can use
                                  their motion sensors (needs cloudflared installed)
    python run.py --seed          load the two synthetic rehearsal masters first
    python run.py --port 8010     use another port

Data is kept in ./data/kotsu.json (change with --data DIR). Stop with Ctrl+C.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def check_requirements() -> None:
    missing = []
    for mod, pkg in [("fastapi", "fastapi"), ("uvicorn", "uvicorn[standard]"), ("numpy", "numpy"),
                     ("qrcode", "qrcode"), ("multipart", "python-multipart")]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("Missing packages: " + ", ".join(missing))
        print("Install them with:\n    python -m pip install -r requirements.txt")
        sys.exit(1)


def print_qr(url: str) -> None:
    try:
        import qrcode
        q = qrcode.QRCode(border=1)
        q.add_data(url)
        q.make()
        q.print_ascii(invert=True)
    except Exception:
        pass


def start_tunnel(port: int, on_url) -> subprocess.Popen | None:
    exe = shutil.which("cloudflared")
    if not exe:
        print("\n[tunnel] cloudflared is not installed, so phones cannot get an https address.")
        print("[tunnel]   Mac:     brew install cloudflared")
        print("[tunnel]   Windows: winget install --id Cloudflare.cloudflared")
        print("[tunnel] The phone's Draw mode, the laptop drag pad and the trackpad station still work without it.\n")
        return None
    proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    pat = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

    def reader() -> None:
        found = False
        for line in proc.stdout:  # type: ignore[union-attr]
            m = pat.search(line)
            if m and not found:
                found = True
                on_url(m.group(0))
            elif not found and ("ERR" in line or "error" in line.lower()):
                print("[tunnel] " + line.strip())
    threading.Thread(target=reader, daemon=True).start()
    return proc


def open_dashboard(url: str) -> None:
    time.sleep(1.5)
    if sys.platform == "darwin" and Path("/Applications/Google Chrome.app").exists():
        subprocess.Popen(["open", "-a", "Google Chrome", url])
    else:
        webbrowser.open(url)


def main() -> None:
    ap = argparse.ArgumentParser(description="Start the KOTSU server.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0", help="0.0.0.0 lets phones on the same Wi-Fi or hotspot connect")
    ap.add_argument("--tunnel", action="store_true", help="open a public https address with cloudflared")
    ap.add_argument("--seed", action="store_true", help="load the synthetic rehearsal masters")
    ap.add_argument("--data", default=None, help="folder for kotsu.json and voice clips")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    check_requirements()
    if args.data:
        os.environ["KOTSU_DATA"] = str(Path(args.data).resolve())
    os.environ["KOTSU_PORT"] = str(args.port)

    import uvicorn
    from kotsu import server

    if args.seed:
        names = [m["name"] for m in server.store.data["masters"].values()]
        if any("synthetic" in n for n in names):
            print("Synthetic rehearsal masters are already loaded.")
        else:
            from kotsu.seed import seed_sample
            n = seed_sample(server.engine)
            print(f"Loaded synthetic rehearsal data ({n} strokes). Reset in Setup before presenting live numbers.")
        sample = next((m for m in server.store.data["masters"].values() if m["name"].startswith("Sample master A (synthetic)")), None)
        if sample:
            server.engine.set_session(mode="capture", master_id=sample["id"])

    lan = f"http://{server.lan_ip()}:{args.port}"
    print("\nKOTSU is starting")
    print(f"  Dashboard (this laptop, Chrome):  http://localhost:{args.port}/")
    print(f"  Same Wi-Fi or hotspot:            {lan}/   phone: {lan}/phone   trackpad (Safari): {lan}/force")
    print("  Phones need https for motion sensors: use --tunnel, or the phone's Draw mode.\n")

    proc = None
    if args.tunnel:
        def on_url(url: str) -> None:
            server.PUBLIC_URL["url"] = url
            print("\n[tunnel] Public https address: " + url)
            print("[tunnel] Phone page: " + url + "/phone   (scan this QR code with the phone camera)")
            print_qr(url + "/phone")
            print("[tunnel] Other laptops: " + url + "/   trackpad station in Safari: " + url + "/force\n")
        proc = start_tunnel(args.port, on_url)

    if not args.no_browser:
        threading.Thread(target=open_dashboard, args=(f"http://localhost:{args.port}/",), daemon=True).start()
    try:
        uvicorn.run(server.app, host=args.host, port=args.port, log_level="warning", ws_ping_interval=20, ws_ping_timeout=20)
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    main()
