#!/usr/bin/env python3
"""
publish.py — lädt garmin/dashboard.html (+ robots.txt) auf GitHub Pages hoch.

Nutzt die GitHub-Contents-API über urllib (kein git nötig). Konfiguration und
Token kommen aus tokens/github.json (von publish_setup.py angelegt). Ist die
Datei nicht da, passiert nichts (Exit 0) — so bricht der tägliche Sync nicht ab,
wenn das Veröffentlichen nicht eingerichtet ist.

ACHTUNG: Die hochgeladene HTML-Datei enthält die Gesundheitsdaten im Klartext.
Der Code im Dashboard blendet sie nur aus, er verschlüsselt nichts.
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONF = BASE / "tokens" / "github.json"
FILES = ["dashboard.html", "robots.txt"]
API = "https://api.github.com"


def req(method: str, url: str, token: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "garmin-ai-publish",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(r, timeout=45) as resp:
        return resp.status, json.loads(resp.read().decode() or "{}")


def put_file(owner, repo, branch, token, name: str, content: bytes):
    url = f"{API}/repos/{owner}/{repo}/contents/{name}"
    b64 = base64.b64encode(content).decode()
    for attempt in (1, 2):
        sha = None
        try:
            _, cur = req("GET", f"{url}?ref={branch}", token)
            sha = cur.get("sha")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        payload = {
            "message": f"sync {datetime.now():%Y-%m-%d %H:%M}",
            "content": b64,
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        try:
            req("PUT", url, token, payload)
            return
        except urllib.error.HTTPError as e:
            # 409: zwischenzeitlich anderweitig committet (z. B. GitHub Actions) -> sha neu holen
            if e.code == 409 and attempt == 1:
                continue
            raise


def main() -> int:
    if not CONF.exists():
        print("Veröffentlichung nicht eingerichtet (tokens/github.json fehlt) – übersprungen.")
        return 0
    cfg = json.loads(CONF.read_text(encoding="utf-8"))
    owner, repo = cfg["owner"], cfg["repo"]
    branch, token = cfg.get("branch", "main"), cfg["token"]

    out = BASE / "garmin"
    for name in FILES:
        p = out / name
        if not p.exists():
            print(f"  · {name} fehlt – übersprungen")
            continue
        try:
            put_file(owner, repo, branch, token, name, p.read_bytes())
            print(f"  · hochgeladen: {name}")
        except urllib.error.HTTPError as e:
            print(f"  · Fehler bei {name}: HTTP {e.code} {e.reason}", file=sys.stderr)
            return 1

    print(f"Veröffentlicht: https://{owner}.github.io/{repo}/dashboard.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
