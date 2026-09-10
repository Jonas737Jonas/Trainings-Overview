#!/usr/bin/env python3
"""
push_repo.py — lädt die Quelldateien des Projekts ins GitHub-Repo (für GitHub Actions).

Nutzt tokens/github.json (von publish_setup.py). Lädt NUR Code hoch — keine Token,
keine Gesundheitsdaten, keine Logs (siehe FILES). Die Workflow-Datei
.github/workflows/sync.yml muss separat über die GitHub-Weboberfläche angelegt werden
(dafür bräuchte der Token die Extra-Berechtigung "Workflows").
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONF = BASE / "tokens" / "github.json"
API = "https://api.github.com"

FILES = [
    "garmin_sync.py",
    "build_dashboard.py",
    "dashboard_template.html",
    "requirements.txt",
    "publish.py",
    "publish_setup.py",
    "push_repo.py",
    ".gitignore",
]


def req(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "garmin-ai-pushrepo",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(r, timeout=45) as resp:
        return resp.status, json.loads(resp.read().decode() or "{}")


def main() -> int:
    if not CONF.exists():
        sys.exit("tokens/github.json fehlt – erst publish_setup.py ausführen.")
    c = json.loads(CONF.read_text(encoding="utf-8"))
    owner, repo, branch, token = c["owner"], c["repo"], c.get("branch", "main"), c["token"]

    for name in FILES:
        p = BASE / name
        if not p.exists():
            print(f"  · übersprungen (fehlt): {name}")
            continue
        url = f"{API}/repos/{owner}/{repo}/contents/{name}"
        sha = None
        try:
            _, cur = req("GET", f"{url}?ref={branch}", token)
            sha = cur.get("sha")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        payload = {
            "message": f"push source: {name}",
            "content": base64.b64encode(p.read_bytes()).decode(),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        try:
            req("PUT", url, token, payload)
            print(f"  · hochgeladen: {name}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:200]
            print(f"  · FEHLER {name}: HTTP {e.code} {detail}", file=sys.stderr)
            return 1
    print("Quelldateien im Repo aktualisiert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
