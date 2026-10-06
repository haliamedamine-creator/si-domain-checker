#!/usr/bin/env python3
"""Web UI for the .si domain checker.

Run:  python app.py   then open http://127.0.0.1:5000
"""

import csv
import hashlib
import io
import os
import pathlib
import threading
import time
import uuid

from flask import Flask, abort, jsonify, render_template, request, send_file

from check_domains import DETAIL_COLS, RdapClient, sanitize, syntax_error

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB uploads

STATIC_DIR = pathlib.Path(app.static_folder)


def build_stamp():
    """Short hash of the front-end files, used as a cache-busting query arg."""
    h = hashlib.sha1()
    for name in ("app.css", "app.js"):
        p = STATIC_DIR / name
        h.update(str(p.stat().st_mtime_ns if p.exists() else 0).encode())
    return h.hexdigest()[:10]


@app.after_request
def no_stale_cache(resp):
    """HTML/JS/CSS must never be served from a stale browser cache."""
    ct = resp.headers.get("Content-Type", "")
    if ct.startswith(("text/html", "text/css")) or "javascript" in ct:
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
    return resp

JOBS = {}
LOCK = threading.Lock()

MAX_DOMAINS = 50000


def normalize_list(raw):
    """Trim, drop blanks/comments, dedupe case-insensitively, keep first order."""
    seen, cleaned, dupes = set(), [], 0
    for item in raw or []:
        d = sanitize(item)
        if not d:
            continue
        key = d.lower()
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        cleaned.append(d)
    return cleaned, dupes


def new_job(domains, details, delay, dupes=0):
    job = {
        "id": uuid.uuid4().hex[:12],
        "status": "running",
        "total": len(domains),
        "done": 0,
        "details": bool(details),
        "delay": delay,
        "counts": {},
        "results": [],
        "dupes": dupes,
        "started_at": time.time(),
        "finished_at": None,
        "stop": threading.Event(),
    }
    with LOCK:
        for old in JOBS.values():
            if old["status"] == "running":
                old["stop"].set()
        JOBS[job["id"]] = job
    threading.Thread(target=run_job, args=(job, domains), daemon=True).start()
    return job


def run_job(job, domains):
    client = RdapClient()
    try:
        for i, domain in enumerate(domains):
            if job["stop"].is_set():
                job["status"] = "stopped"
                return

            err = syntax_error(domain.lower())
            if err:
                status, note, detail = "invalid", err, {}
            else:
                status, note, detail = client.check(domain.lower())

            row = {
                "domain": domain,
                "status": status,
                "note": note,
                "checked_at": time.strftime("%H:%M:%S"),
            }
            if job["details"]:
                for c in DETAIL_COLS:
                    row[c] = detail.get(c, "")

            job["results"].append(row)
            job["counts"][status] = job["counts"].get(status, 0) + 1
            job["done"] = i + 1

            if i + 1 < len(domains) and job["stop"].wait(job["delay"]):
                job["status"] = "stopped"
                return
        job["status"] = "done"
    except Exception as exc:  # noqa: BLE001 - surface anything to the UI
        job["status"] = "error"
        job["counts"]["error"] = job["counts"].get("error", 0)
        job["results"].append({
            "domain": "(internal)",
            "status": "error",
            "note": f"{type(exc).__name__}: {exc}",
            "checked_at": time.strftime("%H:%M:%S"),
        })
    finally:
        job["finished_at"] = time.time()


def job_view(job):
    return {
        "id": job["id"],
        "status": job["status"],
        "total": job["total"],
        "done": job["done"],
        "details": job["details"],
        "counts": job["counts"],
        "dupes": job["dupes"],
        "elapsed": round((job["finished_at"] or time.time()) - job["started_at"], 1),
        "results": job["results"],
    }


@app.route("/")
def index():
    return render_template("index.html", build=build_stamp())


@app.post("/api/start")
def start():
    payload = request.get_json(silent=True) or {}
    domains, dupes = normalize_list(payload.get("domains"))
    if not domains:
        return jsonify({"error": "No domains to check."}), 400
    if len(domains) > MAX_DOMAINS:
        return jsonify({"error": f"Too many domains (max {MAX_DOMAINS})."}), 400

    try:
        delay = float(payload.get("delay", 0.5))
    except (TypeError, ValueError):
        delay = 0.5
    delay = max(0.0, min(delay, 30.0))

    job = new_job(domains, payload.get("details"), delay, dupes=dupes)
    return jsonify({"job": job_view(job)})


@app.get("/api/job/<job_id>")
def get_job(job_id):
    job = JOBS.get(job_id)
    if not job:
        abort(404)
    return jsonify({"job": job_view(job)})


@app.post("/api/job/<job_id>/stop")
def stop_job(job_id):
    job = JOBS.get(job_id)
    if not job:
        abort(404)
    job["stop"].set()
    return jsonify({"job": job_view(job)})


@app.get("/api/job/<job_id>/export")
def export_job(job_id):
    job = JOBS.get(job_id)
    if not job:
        abort(404)

    fields = ["domain", "status", "checked_at", "note"]
    if job["details"]:
        fields += DETAIL_COLS

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(job["results"])

    data = buf.getvalue().encode("utf-8-sig")  # BOM so Excel reads it correctly
    return send_file(
        io.BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name="domains-si.csv",
    )


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
        threaded=True,
    )
