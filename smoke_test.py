import json, subprocess, sys, time, urllib.request

BASE = "http://127.0.0.1:5000"

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

# error paths
print("empty start ->", req("/api/start", {"domains": []})[0])
print("bad delay   ->", req("/api/start", {"domains": ["abc.si"], "delay": "abc"})[0])
print("missing job ->", req("/api/job/doesnotexist")[0])
print("stop        ->", req(f"/api/job/{job['id']}/stop", {}, method="POST")[0])
