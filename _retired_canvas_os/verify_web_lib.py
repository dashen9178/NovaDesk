"""
共享的 CDP 工具，给 verify-web.py 和 _channel_test.py 用。
"""
import base64
import json
import os
import socket
import subprocess
import time
import urllib.request

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def free_port(p):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p))
        return True
    except OSError:
        return False
    finally:
        s.close()


def start_edge(html, base_port=9222):
    port = None
    for p in range(base_port, base_port + 6):
        if free_port(p):
            port = p
            break
    if port is None:
        raise RuntimeError("no free debug port")

    profile = os.path.join(os.environ.get("TEMP", "."), "nova-edge-profile-%d" % port)
    args = [
        EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions",
        "--remote-debugging-port=%d" % port,
        "--user-data-dir=%s" % profile,
        "--window-size=1280,720",
        "--allow-file-access-from-files",
        "file:///" + html.replace("\\", "/"),
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, port


def wait_debug(port, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json" % port, timeout=2) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(0.4)
    return None


class CDP:
    def __init__(self, ws_url):
        from urllib.parse import urlparse
        u = urlparse(ws_url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=25)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n"
               % (u.path, u.hostname, u.port, key))
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.sock.recv(4096)
        self._id = 0

    def call(self, method, params=None):
        self._id += 1
        self._send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                data = self._recv()
            except IOError:
                return None
            if not data:
                continue
            try:
                obj = json.loads(data)
            except Exception:
                continue
            if obj.get("id") == self._id:
                return obj
        return None

    def _send(self, text):
        payload = text.encode()
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += n.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += n.to_bytes(8, "big")
        mask = os.urandom(4)
        header += mask
        masked = bytearray(payload)
        for i in range(len(masked)):
            masked[i] ^= mask[i % 4]
        self.sock.sendall(bytes(header) + bytes(masked))

    def _recv(self):
        def rd(n):
            buf = b""
            while len(buf) < n:
                c = self.sock.recv(n - len(buf))
                if not c:
                    raise IOError("closed")
                buf += c
            return buf
        h = rd(2)
        opcode = h[0] & 0x0F
        length = h[1] & 0x7F
        if length == 126:
            length = int.from_bytes(rd(2), "big")
        elif length == 127:
            length = int.from_bytes(rd(8), "big")
        if opcode == 0x8:
            raise IOError("ws closed")
        return rd(length).decode("utf-8", "replace")


def evaluate(cdp, expr):
    r = cdp.call("Runtime.evaluate", {
        "expression": expr, "returnByValue": True, "awaitPromise": True})
    if not r:
        return None
    res = r.get("result", {})
    if "exceptionDetails" in res:
        return "ERR " + json.dumps(res["exceptionDetails"])[:400]
    return res.get("result", {}).get("value")
