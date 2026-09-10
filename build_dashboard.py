#!/usr/bin/env python3
"""
build_dashboard.py — baut garmin/dashboard.html aus garmin/data.json.

Das Aussehen liegt in dashboard_template.html. Dieses Script tut nur eins:
es schreibt die Daten als eingebettetes JSON in die Vorlage. So kann das
Dashboard bei jedem Sync neu gebaut werden, ohne dass jemand HTML anfasst.
Die fertige Datei ist in sich geschlossen (Daten eingebettet, keine externen
Schriften/Skripte/Bilder) und per Doppelklick offline im Browser zu öffnen.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
TEMPLATE = BASE / "dashboard_template.html"
DATA_JSON = BASE / "garmin" / "data.json"
OUT = BASE / "garmin" / "dashboard.html"
MARKER = "/*__DATA__*/{}"

# Anzeige-Sperre fürs Web-Hosting. KEIN echter Schutz: die Daten stehen im Klartext
# in der HTML-Datei, der Code blendet sie nur aus. Kann in config.json überschrieben werden.
GATE_CODE = "7009"

# Repo für den "jetzt neu laden"-Knopf (repository_dispatch). Der Knopf braucht einen
# GitHub-Token mit Contents:write, der NUR im Browser des Nutzers gespeichert wird –
# nie in dieser Datei. Owner/Repo sind nicht geheim.
GH_REPO = "Jonas737Jonas/Trainings-Overview"


def main() -> None:
    if not TEMPLATE.exists():
        sys.exit(f"Vorlage fehlt: {TEMPLATE}")
    if not DATA_JSON.exists():
        sys.exit(f"Keine Daten: {DATA_JSON} — bitte zuerst garmin_sync.py laufen lassen.")

    data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    data.setdefault("days", [])
    data.setdefault("activities", [])
    data["built"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    template = TEMPLATE.read_text(encoding="utf-8")
    if MARKER not in template:
        sys.exit(f"Marker {MARKER} nicht in der Vorlage gefunden.")

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # </script> im Datenstrom neutralisieren, damit der Browser den Block nicht
    # vorzeitig schließt.
    payload = payload.replace("</", "<\\/")
    html = template.replace(MARKER, payload)

    code = GATE_CODE
    cfg = BASE / "config.json"
    if cfg.exists():
        try:
            code = str(json.loads(cfg.read_text(encoding="utf-8")).get("gate_code", code))
        except (json.JSONDecodeError, OSError):
            pass
    repo = GH_REPO
    if cfg.exists():
        try:
            repo = str(json.loads(cfg.read_text(encoding="utf-8")).get("gh_repo", repo))
        except (json.JSONDecodeError, OSError):
            pass
    html = html.replace("__GATE_CODE__", code).replace("__GH_REPO__", repo)

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    # Suchmaschinen zusätzlich per robots.txt aussperren.
    (OUT.parent / "robots.txt").write_text(
        "User-agent: *\nDisallow: /\n", encoding="utf-8"
    )

    n_days = sum(
        1 for d in data["days"] if any(v is not None for k, v in d.items() if k != "date")
    )
    size_kb = OUT.stat().st_size / 1024
    print(
        f"dashboard.html geschrieben ({size_kb:.0f} KB) — "
        f"{n_days} Tage mit Werten, {len(data['activities'])} Einheiten."
    )


if __name__ == "__main__":
    main()
