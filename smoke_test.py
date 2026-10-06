"""API smoke test. Starts its own server, so no manual setup is needed."""
import atexit
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = "5056"
BASE = "http://127.0.0.1:" + PORT


def req(path, data=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(
        BASE + path, data=body,
        headers={"Content-Type": "application/json"} if body else {},
        method=method or ("POST" if body else "GET"))
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


server = subprocess.Popen(
    [sys.executable, "app.py"],
    cwd=ROOT,
    env={**os.environ, "PORT": PORT},
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
atexit.register(server.terminate)

for _ in range(40):
    try:
        urllib.request.urlopen(BASE, timeout=1)
        break
    except Exception:
        time.sleep(0.25)
else:
    raise SystemExit("server did not start")

domains = ["abc.si", "MeritBeauty.si", "google.si", "si.si", "abc",
           "  https://www.abc.si/path  ", "a.si", "-bad.si", "ABC.SI",
           "", "#comment", "žig.si", "112.si", "google.si"]

s, body, _ = req("/api/start", {"domains": domains, "details": True, "delay": 0.2})
job = json.loads(body)["job"]
print("start:", s, "job", job["id"], "total", job["total"], "dupes", job["dupes"])

for _ in range(80):
    time.sleep(0.6)
    _, b, _ = req(f"/api/job/{job['id']}")
    job = json.loads(b)["job"]
    if job["status"] != "running":
        break

print("status:", job["status"], "| done:", f"{job['done']}/{job['total']}",
      "| elapsed:", job["elapsed"], "s")
print("counts:", job["counts"])
print()
for r in job["results"]:
    print(f"  {r['domain']:<28} {r['status']:<11} "
          f"{r.get('expires','-'):<11} {r.get('registrar','-'):<18} {r['note']}")

s, csv_bytes, hdr = req(f"/api/job/{job['id']}/export")
print("\nexport:", s, hdr.get("Content-Type"), hdr.get("Content-Disposition"))
print(csv_bytes.decode("utf-8-sig"))

# error paths (non-numeric delay is deliberately coerced to the default,
# because the UI sends NaN->null when the delay box is cleared)
print("empty start ->", req("/api/start", {"domains": []})[0], "(want 400)")
print("bad delay   ->", req("/api/start", {"domains": ["abc.si"], "delay": "abc"})[0], "(want 200, coerced)")
print("missing job ->", req("/api/job/doesnotexist")[0], "(want 404)")
print("stop        ->", req(f"/api/job/{job['id']}/stop", {}, method="POST")[0], "(want 200)")
