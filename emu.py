#!/usr/bin/env python3
"""J49 emulator: the screen program of the thermostat on a PC, in the browser.

It runs the real `j49-screen` (a PC build of tools/j49-screen from the nest-v2-firmware repository)
with a file as its frame buffer and two pipes as the ring, and serves a page that shows the
round screen. Mouse wheel = turn the ring, click = press it. The thermostat behind it is
simulated by j49-screen itself (J49_DEMO=live): a room of 19.4 degrees and a heating that
follows the target.

    python3 emu.py --screen /path/to/j49-screen [--port 8050] [--lang en] [--theme default]

Standard library only. Linux (it uses named pipes). The server listens on localhost only.
"""
import argparse, os, struct, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

W = H = 320
COUNTS_PER_NOTCH = 30      # ring counts for one notch of the mouse wheel: one step on the screen (28 counts)

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--screen", required=True, help="the j49-screen binary built for this PC")
ap.add_argument("--port", type=int, default=8050)
ap.add_argument("--lang", default="en")
ap.add_argument("--theme", default="default")
args = ap.parse_args()
if not os.access(args.screen, os.X_OK):
    sys.exit("not an executable: " + args.screen)

HERE = os.path.dirname(os.path.abspath(__file__))
work = tempfile.mkdtemp(prefix="j49-emu-")
fb = os.path.join(work, "fb")
with open(fb, "wb") as f:
    f.write(bytes(W * H * 4))
for n in (0, 1):
    os.mkfifo(os.path.join(work, "event%d" % n))

lock = threading.Lock()
state = {"proc": None, "pipes": [None, None], "lang": args.lang, "theme": args.theme}

def start():
    """(Re)start j49-screen with the chosen language and theme."""
    with lock:
        if state["proc"]:
            state["proc"].terminate()
            for p in state["pipes"]:
                if p: p.close()
        env = dict(os.environ, J49_DEMO="live", J49_INPUT=work, J49_LANG=state["lang"], J49_THEME=state["theme"])
        state["proc"] = subprocess.Popen([args.screen, "--fb", fb, "--idle", "86400"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # opening a pipe for writing waits until j49-screen has opened it for reading
        state["pipes"] = [open(os.path.join(work, "event%d" % n), "wb", buffering=0) for n in (0, 1)]

def event(pipe, typ, code, value):
    """One input event as the thermostat's kernel gives it: 16 bytes (32-bit ARM layout)."""
    with lock:
        try:
            state["pipes"][pipe].write(struct.pack("<IIHHi", 0, 0, typ, code, value))
        except (OSError, AttributeError):
            pass

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def reply(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self.reply(200, "text/html; charset=utf-8", f.read())
        elif u.path == "/frame":           # the frame buffer as it is: 320x320, 4 bytes B G R A per pixel
            with open(fb, "rb") as f:
                self.reply(200, "application/octet-stream", f.read())
        else:
            self.reply(404, "text/plain", b"not found")
    def do_POST(self):
        # only the page itself may send input: other sites cannot set this header
        if self.headers.get("X-J49-Emu") != "1":
            return self.reply(403, "text/plain", b"forbidden")
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/turn":
            try: n = max(-20, min(20, int(q.get("n", ["0"])[0])))
            except ValueError: n = 0
            event(1, 2, 0, -n * COUNTS_PER_NOTCH)           # EV_REL; the sensor counts down clockwise
        elif u.path == "/click":
            event(0, 1, 116, 1); event(0, 1, 116, 0)        # EV_KEY KEY_POWER down, up
        elif u.path == "/look":
            name = lambda k: "".join(c for c in q.get(k, [""])[0] if c.isalnum() or c in "-_")[:40]
            state["lang"] = name("lang") or state["lang"]; state["theme"] = name("theme") or state["theme"]
            start()
        else:
            return self.reply(404, "text/plain", b"not found")
        self.reply(200, "text/plain", b"ok")

start()
print("J49 emulator: http://localhost:%d/   (Ctrl+C stops it)" % args.port)
try:
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
except KeyboardInterrupt:
    pass
finally:
    if state["proc"]: state["proc"].terminate()
