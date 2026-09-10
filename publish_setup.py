#!/usr/bin/env python3
"""
publish_setup.py — einmalige Einrichtung fürs Veröffentlichen auf GitHub Pages.

Fragt GitHub-Benutzername, Repo-Name und einen Personal Access Token (verdeckt)
ab und speichert sie unter tokens/github.json (Rechte 600). Der Token läuft nie
über stdout/Logs. Danach lädt jeder Sync das Dashboard automatisch hoch.

Vorher im Browser erledigen:
  1. GitHub-Konto anlegen (github.com).
  2. Neues, LEERES, öffentliches Repository anlegen, z. B. "sport-ki".
  3. Settings → Pages → Source: "Deploy from a branch", Branch: main / root, speichern.
  4. Fine-grained Token erstellen: Settings → Developer settings →
     Personal access tokens → Fine-grained tokens → Generate new token.
     - Repository access: nur das eine Repo
     - Permissions → Repository permissions → Contents: Read and write
     - Ablauf: z. B. 1 Jahr
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
TOKEN_DIR = BASE / "tokens"
CONF = TOKEN_DIR / "github.json"
API = "https://api.github.com"


def api_get(path: str, token: str):
    req = urllib.request.Request(
        API + path,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "garmin-ai-publish",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode())


def main() -> None:
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        sys.exit("Bitte in einem echten Terminalfenster ausführen (verdeckte Token-Eingabe).")
    import getpass

    print("── GitHub-Pages-Veröffentlichung einrichten ──────────────")
    owner = input("GitHub-Benutzername: ").strip()
    repo = input("Repository-Name (z. B. sport-ki): ").strip()
    branch = (input("Branch [main]: ").strip() or "main")
    token = getpass.getpass("Personal Access Token (bleibt unsichtbar): ").strip()
    if not (owner and repo and token):
        sys.exit("Abbruch: Angabe fehlt.")

    print("\nPrüfe Zugriff …")
    try:
        status, data = api_get(f"/repos/{owner}/{repo}", token)
    except urllib.error.HTTPError as e:
        sys.exit(
            f"Fehler {e.code}: Repo nicht erreichbar oder Token ohne Zugriff. "
            "Benutzername/Repo/Token prüfen."
        )
    perms = data.get("permissions", {})
    if not perms.get("push"):
        sys.exit(
            "Der Token darf in dieses Repo nicht schreiben. "
            "Beim Token unter 'Contents' auf 'Read and write' stellen."
        )

    TOKEN_DIR.mkdir(mode=0o700, exist_ok=True)
    os.chmod(TOKEN_DIR, 0o700)
    CONF.write_text(
        json.dumps({"owner": owner, "repo": repo, "branch": branch, "token": token}, indent=2),
        encoding="utf-8",
    )
    os.chmod(CONF, 0o600)

    pages_url = f"https://{owner}.github.io/{repo}/dashboard.html"
    print("\nGespeichert:", CONF, "(Rechte 600)")
    print("Repo ok:", data.get("full_name"), "· privat" if data.get("private") else "· öffentlich")
    print(f"\nNach dem nächsten Sync erreichbar unter:\n    {pages_url}")
    print("\nFalls die Seite 404 zeigt: Settings → Pages muss auf")
    print(f"'Deploy from a branch' / {branch} / root stehen.")
    print("\nJetzt einmal testen mit:")
    print(f"    {sys.executable} {BASE/'publish.py'}")


if __name__ == "__main__":
    main()
