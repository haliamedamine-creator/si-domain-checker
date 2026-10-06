#!/usr/bin/env python3
"""Check .si domain status from a CSV, using the public RDAP service.

Usage:
    python check_domains.py domains.csv
    python check_domains.py domains.csv --recheck
    python check_domains.py domains.csv --details --delay 0.2

Backend: https://rdap.register.si (public RDAP, no auth, no 25/hour limit that
the WHOIS widget on www.register.si enforces).

Statuses written to the `status` column:
    available   free to register            (HTTP 404)
    registered  taken                       (HTTP 200)
    reserved    reserved / not registrable  (HTTP 403)
    invalid     breaks .si syntax rules, checked locally, no request made
    error       network/server failure after retries (see `note`)

Rows that already have a status are skipped, so re-running resumes where it
left off. The file is rewritten after every lookup - Ctrl+C loses at most one.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime

import requests

RDAP = "https://rdap.register.si/domain/{}"
USER_AGENT = "register-csv-checker/1.0 (local .si domain status checker)"

DOMAIN_COL = "domain"
STATUS_COL = "status"
CHECKED_COL = "checked_at"
NOTE_COL = "note"
DETAIL_COLS = ["registered", "expires", "registrar", "nameservers"]

MAX_RETRIES = 4
RETRY_STATUS = {429, 500, 502, 503, 504}


def log(msg=""):
    print(msg, flush=True)


# --------------------------------------------------------------------------
# .si syntax rules (from https://www.register.si/dovoljeni-znaki/ and
# https://www.register.si/registracija-domene/)
# --------------------------------------------------------------------------

def _allowed_char(c: str) -> bool:
    if c == "-":
        return True
    if c.isascii():
        return c.isalnum()
    return c.isalpha() and ord(c) <= 0x024F  # Latin-1 / Ext-A / Ext-B only


def syntax_error(domain: str):
    """Return a reason string if the domain cannot be a valid .si name, else None."""
    labels = domain.split(".")
    if labels[-1].lower() != "si":
        return "not a .si domain"
    if len(labels) < 2:
        return "missing label"
    for label in labels[:-1]:
        if not 2 <= len(label) <= 63:
            return f"label '{label}' must be 2-63 chars"
        if label.startswith("-") or label.endswith("-"):
            return f"label '{label}' cannot start or end with '-'"
        if len(label) >= 4 and label[2] == "-" and label[3] == "-":
            return f"label '{label}' cannot have '--' at position 3-4"
        bad = {c for c in label if not _allowed_char(c)}
        if bad:
            return f"disallowed characters: {''.join(sorted(bad))}"
    return None


def sanitize(raw: str):
    """Clean a pasted URL/hostname down to a bare domain name."""
    d = (raw or "").strip()
    if not d:
        return ""
    if "://" in d:
        d = d.split("://", 1)[1]
    d = d.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    d = d.split("@")[-1]
    if d.count(":") == 1:
        d = d.split(":", 1)[0]
    d = d.strip().strip(".")
    if d and "." not in d:
        d += ".si"
    return d


# --------------------------------------------------------------------------
# RDAP client
# --------------------------------------------------------------------------

class RdapClient:
    def __init__(self, timeout=30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": USER_AGENT, "Accept": "application/rdap+json, application/json"}
        )

    def check(self, domain):
        """Return (status, note, detail_dict)."""
        url = RDAP.format(domain.lower())
        last = ""
        for attempt in range(MAX_RETRIES):
            try:
                r = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as e:
                last = f"network: {e.__class__.__name__}"
                log(f"    retry {attempt + 1}/{MAX_RETRIES} ({last})")
                time.sleep(2 ** attempt)
                continue

            if r.status_code == 200:
                return "registered", "", self._detail(r)
            if r.status_code == 404:
                return "available", "", {}
            if r.status_code == 403:
                reason = ""
                m = re.search(r"is forbidden\s*\((.*)\)\s*$", r.text.strip())
                if m:
                    reason = m.group(1)
                return "reserved", reason, {}

            if r.status_code in RETRY_STATUS:
                last = f"HTTP {r.status_code}"
                log(f"    retry {attempt + 1}/{MAX_RETRIES} ({last})")
                time.sleep(5 * (attempt + 1))
                continue

            return "error", f"HTTP {r.status_code}: {r.text[:80]}", {}

        return "error", last or "gave up", {}

    @staticmethod
    def _detail(r):
        try:
            d = r.json()
        except ValueError:
            return {}
        events = {e.get("eventAction"): e.get("eventDate") for e in d.get("events", [])}
        registrar = ""
        for ent in d.get("entities", []):
            if "registrar" in (ent.get("roles") or []):
                for item in (ent.get("vcardArray") or [None, []])[1]:
                    if item[0] == "fn":
                        registrar = str(item[3])
                        break
        return {
            "registered": events.get("registration", ""),
            "expires": events.get("expiration", ""),
            "registrar": registrar,
            "nameservers": " ".join(
                n.get("ldhName", "") for n in d.get("nameservers", [])
            ),
        }


# --------------------------------------------------------------------------
# CSV plumbing
# --------------------------------------------------------------------------

def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit(f"{path}: empty file or missing header row")
        fieldnames = list(reader.fieldnames)
        rows = [dict(r) for r in reader]

    lowered = {n.lower().strip(): n for n in fieldnames}
    if DOMAIN_COL not in lowered:
        lowered[DOMAIN_COL] = fieldnames[0]
        log(f"note: no '{DOMAIN_COL}' column found, using first column '{fieldnames[0]}'")

    for col in [STATUS_COL, CHECKED_COL, NOTE_COL] + DETAIL_COLS:
        if col not in lowered:
            fieldnames.append(col)
            lowered[col] = col

    return fieldnames, rows, lowered


def write_csv(path, fieldnames, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="Check .si domain status from a CSV via RDAP.")
    ap.add_argument("csv_file", help="CSV with a 'domain' column")
    ap.add_argument("--delay", type=float, default=0.5,
                    help="seconds between lookups (default 0.5)")
    ap.add_argument("--recheck", action="store_true",
                    help="recheck rows that already have a status")
    ap.add_argument("--details", action="store_true",
                    help="also fill registered/expires/registrar/nameservers (no extra requests)")
    ap.add_argument("--limit", type=int, default=0, help="stop after N lookups (0 = all)")
    ap.add_argument("--quiet", action="store_true", help="only print the summary")
    args = ap.parse_args()

    if not os.path.exists(args.csv_file):
        raise SystemExit(f"no such file: {args.csv_file}")

    fieldnames, rows, cols = read_csv(args.csv_file)
    cols_to_write = [STATUS_COL, CHECKED_COL, NOTE_COL] + (DETAIL_COLS if args.details else [])

    def raw_domain(row):
        return row.get(cols[DOMAIN_COL]) or ""

    pending = []
    for i, row in enumerate(rows):
        done = (row.get(cols[STATUS_COL]) or "").strip()
        if done and not args.recheck:
            continue
        if not sanitize(raw_domain(row)):
            continue
        pending.append(i)

    log(f"{len(pending)} to check, {len(rows) - len(pending)} already done / empty "
        f"({os.path.basename(args.csv_file)})")
    if not pending:
        log("nothing to do")
        return

    client = RdapClient()
    counts = {}
    errors = []
    t0 = time.time()
    done = 0

    try:
        for i in pending:
            row = rows[i]
            display = sanitize(raw_domain(row))
            key = display.lower()

            err = syntax_error(key)
            if err:
                status, note, detail = "invalid", err, {}
            else:
                status, note, detail = client.check(key)

            now = datetime.now().isoformat(timespec="seconds")
            row[cols[DOMAIN_COL]] = display
            row[cols[STATUS_COL]] = status
            row[cols[CHECKED_COL]] = now
            row[cols[NOTE_COL]] = note
            if args.details:
                for c in DETAIL_COLS:
                    row[cols[c]] = detail.get(c, "")

            counts[status] = counts.get(status, 0) + 1
            if status in ("error", "invalid"):
                errors.append((display, note))
            done += 1

            if not args.quiet:
                suffix = f"  ({note})" if note else ""
                if args.details and detail.get("expires"):
                    suffix += f"  exp {detail['expires']}"
                log(f"[{done}/{len(pending)}] {display:<40} {status}{suffix}")

            write_csv(args.csv_file, fieldnames, rows)

            if args.limit and done >= args.limit:
                log("--limit reached")
                break
            if done < len(pending):
                time.sleep(args.delay)
    except KeyboardInterrupt:
        log("\ninterrupted - progress saved")

    elapsed = time.time() - t0
    log()
    log(f"checked {done} in {elapsed:.0f}s "
        f"({done / elapsed:.1f}/s)" if elapsed else f"checked {done}")
    log("summary: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "-"))
    if errors:
        log("\nneeds attention:")
        for d, n in errors:
            log(f"  {d:<40} {n}")


if __name__ == "__main__":
    sys.exit(main())
