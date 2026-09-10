#!/usr/bin/env python3
"""
garmin_sync.py — holt deine eigenen Garmin-Daten nur lesend auf diesen Rechner.

Was das Script macht
--------------------
* Einmaliger Login (--login): fragt E-Mail + Passwort verdeckt ab, fängt einen
  2FA-Code ab und speichert danach nur ein Login-Token (~1 Jahr gültig).
  Passwort und Token laufen nie über stdout/Logs/Umgebungsvariablen.
* Danach: lädt Aktivitäten und Tageswerte (Schlaf, HRV, Ruhepuls, Body Battery,
  Stress, Schritte, Training Readiness) und legt sie unter garmin/ ab:
  - garmin/data.json          – alle Werte des Zeitraums, maschinenlesbar
  - garmin/tage/<datum>.md     – eine Notiz pro Tag
  - garmin/einheiten/<...>.md  – eine Notiz pro Workout
* Jeder einzelne Abruf ist abgesichert: ein fehlender Wert bricht den Lauf nie
  ab und wird als "keine Daten" (null) behandelt, niemals als 0.
* Es wird nichts in dein Garmin-Konto zurückgeschrieben.

Aufrufe
-------
  python garmin_sync.py --login              einmaliger Login (echtes Terminal nötig)
  python garmin_sync.py --days 3 --dry-run   Testlauf, schreibt keine Dateien
  python garmin_sync.py --days 3             letzte 3 Tage abrufen und schreiben
  python garmin_sync.py --days 60            einmalige Historie
  python garmin_sync.py                      Standard: letzte 7 Tage + Dashboard-Neubau
  python garmin_sync.py --no-dashboard       Dashboard nicht neu bauen
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# --- Pfade -------------------------------------------------------------------
BASE = Path(__file__).resolve().parent
TOKEN_DIR = BASE / "tokens"
OUT_DIR = BASE / "garmin"
DAYS_DIR = OUT_DIR / "tage"
ACT_DIR = OUT_DIR / "einheiten"
DATA_JSON = OUT_DIR / "data.json"
LOG_DIR = BASE / "logs"

# Kurze Pause zwischen einzelnen API-Abrufen, damit Garmin nicht drosselt.
CALL_PAUSE = 0.6
DAY_PAUSE = 0.4


# --- kleine Helfer ---------------------------------------------------------
def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(mode=0o700, exist_ok=True)
        with open(LOG_DIR / "sync.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def safe(label: str, fn, *args):
    """Ein einzelner Abruf. Schlägt er fehl -> None ('keine Daten'), kein Abbruch."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - bewusst alles abfangen
        log(f"  · keine Daten: {label} ({type(exc).__name__})")
        return None


def g(obj, *keys):
    """Verschachtelt lesen; jeder fehlende Schlüssel -> None."""
    for k in keys:
        if obj is None:
            return None
        if isinstance(obj, dict):
            obj = obj.get(k)
        elif isinstance(obj, list) and isinstance(k, int) and -len(obj) <= k < len(obj):
            obj = obj[k]
        else:
            return None
    return obj


def num(v):
    """Zahl zurückgeben oder None. Leerstring/None -> None (nie 0)."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def harden_permissions(path: Path) -> None:
    """Token-Ordner 700, enthaltene Dateien 600."""
    try:
        if path.is_dir():
            os.chmod(path, 0o700)
            for child in path.iterdir():
                if child.is_file():
                    os.chmod(child, 0o600)
    except OSError as exc:
        log(f"  · Hinweis: Zugriffsrechte nicht setzbar ({exc})")


def slugify(text: str) -> str:
    text = (text or "einheit").lower()
    text = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "einheit"


# --- Login ---------------------------------------------------------------
def require_real_terminal() -> None:
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        sys.exit(
            "Abbruch: Der Login braucht ein echtes Terminalfenster, in dem die "
            "Passworteingabe verdeckt werden kann.\n"
            "Bitte in der macOS-App 'Terminal' ausführen, nicht über einen "
            "Editor, eine Pipe oder diesen Chat."
        )


def do_login() -> None:
    require_real_terminal()
    import getpass

    from garminconnect import Garmin

    print("── Einmaliger Garmin-Login ─────────────────────────────────")
    print("E-Mail und Passwort gehen nur an Garmin. Gespeichert wird danach")
    print("ausschließlich ein Login-Token (rund ein Jahr gültig).\n")

    email = input("Garmin-E-Mail: ").strip()
    if not email:
        sys.exit("Keine E-Mail eingegeben.")
    password = getpass.getpass("Garmin-Passwort (Eingabe bleibt unsichtbar): ")
    if not password:
        sys.exit("Kein Passwort eingegeben.")

    def prompt_mfa() -> str:
        print("\nGarmin verlangt einen Zwei-Faktor-Code (SMS oder Authenticator-App).")
        return input("2FA-Code: ").strip()

    TOKEN_DIR.mkdir(mode=0o700, exist_ok=True)
    harden_permissions(TOKEN_DIR)

    try:
        api = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
        api.login(str(TOKEN_DIR))  # speichert das Token selbst in TOKEN_DIR
    finally:
        # Passwort so früh wie möglich aus dem Speicher entfernen.
        password = "x" * len(password)
        del password

    harden_permissions(TOKEN_DIR)
    token_file = TOKEN_DIR / "garmin_tokens.json"
    ok = token_file.exists()
    print()
    if ok:
        st = oct(stat.S_IMODE(token_file.stat().st_mode))
        log(f"Login erfolgreich. Token gespeichert: {token_file}  (Rechte {st})")
        print("\nFertig. Ab jetzt läuft der Sync ohne weitere Eingabe.")
    else:
        sys.exit("Login lief durch, aber es wurde keine Token-Datei angelegt.")


def connect():
    """Verbindung nur über das gespeicherte Token, ohne Zugangsdaten."""
    from garminconnect import Garmin

    token_file = TOKEN_DIR / "garmin_tokens.json"
    if not token_file.exists():
        sys.exit(
            "Kein Login-Token gefunden.\n"
            "Bitte zuerst einmalig einloggen:\n"
            f"    {sys.executable} {__file__} --login"
        )
    api = Garmin()
    try:
        api.login(str(TOKEN_DIR))
    except Exception as exc:  # noqa: BLE001
        sys.exit(
            f"Das gespeicherte Token wird nicht mehr akzeptiert ({type(exc).__name__}).\n"
            "Bitte einmalig neu einloggen:\n"
            f"    {sys.executable} {__file__} --login"
        )
    return api


# --- Datenabruf --------------------------------------------------------
def fetch_day(api, d: str) -> dict:
    """Alle Tageswerte für ein Datum d (YYYY-MM-DD). Fehlende Werte -> None."""
    rec: dict = {"date": d}

    stats = safe(f"tagesstatistik {d}", api.get_stats, d)
    rec["resting_hr"] = num(g(stats, "restingHeartRate"))
    rec["steps"] = num(g(stats, "totalSteps"))
    rec["distance_m"] = num(g(stats, "totalDistanceMeters"))
    rec["stress_avg"] = num(g(stats, "averageStressLevel"))
    rec["stress_max"] = num(g(stats, "maxStressLevel"))
    rec["bb_low"] = num(g(stats, "bodyBatteryLowestValue"))
    rec["bb_high"] = num(g(stats, "bodyBatteryHighestValue"))
    rec["bb_last"] = num(g(stats, "bodyBatteryMostRecentValue"))
    time.sleep(CALL_PAUSE)

    hrv = safe(f"hrv {d}", api.get_hrv_data, d)
    rec["hrv_avg"] = num(g(hrv, "hrvSummary", "lastNightAvg"))
    rec["hrv_baseline_low"] = num(g(hrv, "hrvSummary", "baseline", "balancedLow"))
    rec["hrv_baseline_high"] = num(g(hrv, "hrvSummary", "baseline", "balancedUpper"))
    rec["hrv_status"] = g(hrv, "hrvSummary", "status")
    time.sleep(CALL_PAUSE)

    sleep = safe(f"schlaf {d}", api.get_sleep_data, d)
    secs = g(sleep, "dailySleepDTO", "sleepTimeSeconds")
    rec["sleep_hours"] = round(secs / 3600, 2) if num(secs) else None
    rec["sleep_score"] = num(g(sleep, "dailySleepDTO", "sleepScores", "overall", "value"))
    deep = g(sleep, "dailySleepDTO", "deepSleepSeconds")
    rem = g(sleep, "dailySleepDTO", "remSleepSeconds")
    light = g(sleep, "dailySleepDTO", "lightSleepSeconds")
    rec["sleep_deep_hours"] = round(deep / 3600, 2) if num(deep) else None
    rec["sleep_rem_hours"] = round(rem / 3600, 2) if num(rem) else None
    rec["sleep_light_hours"] = round(light / 3600, 2) if num(light) else None
    time.sleep(CALL_PAUSE)

    tr = safe(f"training readiness {d}", api.get_training_readiness, d)
    if isinstance(tr, list) and tr:
        tr = tr[0]
    rec["training_readiness"] = num(g(tr, "score"))
    rec["training_readiness_level"] = g(tr, "level")
    rec["training_readiness_feedback"] = g(tr, "feedbackLong") or g(tr, "feedbackShort")
    time.sleep(CALL_PAUSE)

    return rec


def fetch_range_metrics(api, start: str, end: str) -> dict:
    """VO2max und Wettkampfprognosen für den ganzen Zeitraum in einem Abruf je Quelle.
    Rückgabe: {datum: {vo2max, race_5k_s, race_10k_s, race_hm_s, race_m_s}}"""
    out: dict[str, dict] = {}

    mm = safe(f"vo2max {start}..{end}", api.get_max_metrics_range, start, end) or []
    for item in mm if isinstance(mm, list) else []:
        gen = g(item, "generic")
        cd = g(gen, "calendarDate")
        v = num(g(gen, "vo2MaxPreciseValue")) or num(g(gen, "vo2MaxValue"))
        if cd and v:
            out.setdefault(cd, {})["vo2max"] = v

    rp = safe(
        f"wettkampfprognosen {start}..{end}",
        api.get_race_predictions, start, end, "daily",
    ) or []
    for item in rp if isinstance(rp, list) else []:
        cd = g(item, "calendarDate")
        if not cd:
            continue
        r = out.setdefault(cd, {})
        r["race_5k_s"] = num(g(item, "time5K"))
        r["race_10k_s"] = num(g(item, "time10K"))
        r["race_hm_s"] = num(g(item, "timeHalfMarathon"))
        r["race_m_s"] = num(g(item, "timeMarathon"))
    return out


def fetch_activities(api, start: str, end: str) -> list[dict]:
    raw = safe(
        f"aktivitäten {start}..{end}", api.get_activities_by_date, start, end
    ) or []
    out = []
    for a in raw:
        start_local = g(a, "startTimeLocal") or ""
        d = start_local[:10]
        dist = num(g(a, "distance"))
        dur = num(g(a, "duration"))
        pace = None
        if dist and dur and dist > 0:
            pace = dur / (dist / 1000.0)  # Sekunden pro Kilometer
        out.append(
            {
                "date": d,
                "start_time": start_local,
                "activity_id": g(a, "activityId"),
                "name": g(a, "activityName") or "Aktivität",
                "type": g(a, "activityType", "typeKey"),
                "distance_km": round(dist / 1000, 3) if dist else None,
                "duration_s": dur,
                "pace_s_per_km": round(pace, 1) if pace else None,
                "avg_hr": num(g(a, "averageHR")),
                "max_hr": num(g(a, "maxHR")),
                "aerobic_te": num(g(a, "aerobicTrainingEffect")),
                "anaerobic_te": num(g(a, "anaerobicTrainingEffect")),
                "calories": num(g(a, "calories")),
            }
        )
    out.sort(key=lambda x: x["start_time"], reverse=True)
    return out


# --- Ausgabe schreiben ------------------------------------------------
def fmt(v, suffix="", nd=0):
    if v is None:
        return "keine Daten"
    if isinstance(v, float) and nd == 0 and v.is_integer():
        v = int(v)
    if isinstance(v, float):
        v = round(v, nd)
    return f"{v}{suffix}"


def hm(seconds):
    if not seconds:
        return "keine Daten"
    seconds = int(seconds)
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def pace_str(s_per_km):
    if not s_per_km:
        return "keine Daten"
    s = int(round(s_per_km))
    return f"{s // 60}:{s % 60:02d} min/km"


def pace_str_total(seconds):
    if not seconds:
        return "keine Daten"
    return hm(seconds)


def write_day_note(rec: dict) -> None:
    d = rec["date"]
    lines = [
        f"# Tag {d}",
        "",
        "## erholung",
        f"- training readiness: {fmt(rec['training_readiness'])}"
        + (f" ({rec['training_readiness_level']})" if rec.get("training_readiness_level") else ""),
        f"- hrv (letzte nacht): {fmt(rec['hrv_avg'], ' ms')}"
        + (
            f" · baseline {fmt(rec['hrv_baseline_low'])}–{fmt(rec['hrv_baseline_high'])} ms"
            if rec.get("hrv_baseline_low")
            else ""
        )
        + (f" · status {rec['hrv_status']}" if rec.get("hrv_status") else ""),
        f"- ruhepuls: {fmt(rec['resting_hr'], ' bpm')}",
        f"- schlaf: {fmt(rec['sleep_hours'], ' h', 2)}"
        + (f" · score {fmt(rec['sleep_score'])}" if rec.get("sleep_score") else ""),
        f"  - tief {fmt(rec['sleep_deep_hours'], ' h', 2)}, rem {fmt(rec['sleep_rem_hours'], ' h', 2)}, leicht {fmt(rec['sleep_light_hours'], ' h', 2)}",
        f"- body battery: {fmt(rec['bb_low'])} tief → {fmt(rec['bb_high'])} hoch (zuletzt {fmt(rec['bb_last'])})",
        f"- stress: ⌀ {fmt(rec['stress_avg'])} · max {fmt(rec['stress_max'])}",
        f"- schritte: {fmt(rec['steps'])}",
        f"- distanz gesamt: {fmt(rec['distance_m'] / 1000 if rec['distance_m'] else None, ' km', 2)}",
        "",
        "## leistung",
        f"- vo2max: {fmt(rec.get('vo2max'), '', 1)}",
        f"- prognose 5 km: {pace_str_total(rec.get('race_5k_s'))}",
        f"- prognose 10 km: {pace_str_total(rec.get('race_10k_s'))}",
        f"- prognose halbmarathon: {pace_str_total(rec.get('race_hm_s'))}",
        f"- prognose marathon: {pace_str_total(rec.get('race_m_s'))}",
        "",
        f"_zuletzt aktualisiert: {datetime.now():%Y-%m-%d %H:%M}_",
        "",
    ]
    (DAYS_DIR / f"{d}.md").write_text("\n".join(lines), encoding="utf-8")


def write_activity_note(a: dict) -> None:
    d = a["date"] or "undatiert"
    name = a["name"]
    fname = f"{d}-{slugify(name)}.md"
    lines = [
        f"# {name}",
        "",
        f"- datum: {d}",
        f"- start: {a['start_time'] or 'keine Daten'}",
        f"- typ: {a['type'] or 'keine Daten'}",
        f"- distanz: {fmt(a['distance_km'], ' km', 2)}",
        f"- dauer: {hm(a['duration_s'])}",
        f"- pace: {pace_str(a['pace_s_per_km'])}",
        f"- durchschnittspuls: {fmt(a['avg_hr'], ' bpm')}",
        f"- maximalpuls: {fmt(a['max_hr'], ' bpm')}",
        f"- aerober trainingseffekt: {fmt(a['aerobic_te'], '', 1)}",
        f"- anaerober trainingseffekt: {fmt(a['anaerobic_te'], '', 1)}",
        f"- kalorien: {fmt(a['calories'])}",
        "",
        f"_zuletzt aktualisiert: {datetime.now():%Y-%m-%d %H:%M}_",
        "",
    ]
    (ACT_DIR / fname).write_text("\n".join(lines), encoding="utf-8")


def merge_json(new_days: list[dict], new_acts: list[dict], rng: dict) -> dict:
    """Bestehende data.json einlesen und mit neuen Werten zusammenführen."""
    old = {"days": [], "activities": []}
    if DATA_JSON.exists():
        try:
            old = json.loads(DATA_JSON.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    days = {r["date"]: r for r in old.get("days", [])}
    for r in new_days:
        days[r["date"]] = r  # neuer Abruf gewinnt

    acts = {a.get("activity_id") or a.get("start_time"): a for a in old.get("activities", [])}
    for a in new_acts:
        acts[a.get("activity_id") or a.get("start_time")] = a

    merged = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "range": rng,
        "days": sorted(days.values(), key=lambda r: r["date"]),
        "activities": sorted(
            acts.values(), key=lambda a: a.get("start_time") or "", reverse=True
        ),
    }
    return merged


def rebuild_dashboard() -> None:
    builder = BASE / "build_dashboard.py"
    if not builder.exists():
        log("  · Dashboard-Build übersprungen (build_dashboard.py fehlt noch)")
        return
    import subprocess

    res = subprocess.run(
        [sys.executable, str(builder)], capture_output=True, text=True
    )
    if res.returncode == 0:
        log(f"Dashboard neu gebaut: {OUT_DIR / 'dashboard.html'}")
    else:
        log(f"  · Dashboard-Build fehlgeschlagen: {res.stderr.strip()[:400]}")


def publish_dashboard() -> None:
    """Optionales Hochladen auf GitHub Pages (nur wenn eingerichtet)."""
    pub = BASE / "publish.py"
    if not pub.exists() or not (TOKEN_DIR / "github.json").exists():
        return
    import subprocess

    res = subprocess.run([sys.executable, str(pub)], capture_output=True, text=True)
    for line in (res.stdout + res.stderr).splitlines():
        if line.strip():
            log("  " + line.strip())


# --- Hauptablauf ------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(description="Garmin-Daten nur lesend synchronisieren.")
    p.add_argument("--login", action="store_true", help="einmaliger Login (echtes Terminal nötig)")
    p.add_argument("--days", type=int, default=7, help="Anzahl zurückliegender Tage (Standard 7)")
    p.add_argument("--start", help="Startdatum YYYY-MM-DD (überschreibt --days)")
    p.add_argument("--end", help="Enddatum YYYY-MM-DD (Standard: heute)")
    p.add_argument("--dry-run", action="store_true", help="nur abrufen und anzeigen, nichts schreiben")
    p.add_argument("--no-dashboard", action="store_true", help="Dashboard nicht neu bauen")
    p.add_argument("--no-publish", action="store_true", help="Dashboard nicht hochladen (GitHub Pages)")
    args = p.parse_args()

    if args.login:
        do_login()
        return

    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    else:
        start = end - timedelta(days=args.days - 1)
    all_days = [
        (start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)
    ]

    log(f"Sync {start} .. {end}  ({len(all_days)} Tage){'  [dry-run]' if args.dry_run else ''}")
    api = connect()
    log(f"Verbunden als: {safe('name', api.get_full_name) or 'unbekannt'}")

    day_records = []
    for i, d in enumerate(all_days, 1):
        rec = fetch_day(api, d)
        day_records.append(rec)
        has = sum(1 for k, v in rec.items() if k != "date" and v is not None)
        log(f"[{i}/{len(all_days)}] {d}: {has} Werte")
        time.sleep(DAY_PAUSE)

    acts = fetch_activities(api, all_days[0], all_days[-1])
    log(f"{len(acts)} Aktivitäten im Zeitraum")

    rng = fetch_range_metrics(api, all_days[0], all_days[-1])
    # frühere Werte laden, damit ein kurzer Sync VO2max/Prognosen nicht überschreibt
    old_by_date: dict[str, dict] = {}
    if DATA_JSON.exists():
        try:
            old_by_date = {
                x["date"]: x
                for x in json.loads(DATA_JSON.read_text(encoding="utf-8")).get("days", [])
            }
        except (json.JSONDecodeError, OSError):
            pass
    last_vo2 = None
    for dt in sorted(old_by_date):
        if dt < all_days[0] and old_by_date[dt].get("vo2max") is not None:
            last_vo2 = old_by_date[dt]["vo2max"]
    for r in day_records:
        extra = rng.get(r["date"], {})
        old = old_by_date.get(r["date"], {})
        for k in ("race_5k_s", "race_10k_s", "race_hm_s", "race_m_s"):
            r[k] = extra.get(k) if extra.get(k) is not None else old.get(k)
        if extra.get("vo2max") is not None:
            last_vo2 = extra["vo2max"]
        r["vo2max"] = last_vo2 if last_vo2 is not None else old.get("vo2max")
    vo2_days = sum(1 for r in day_records if r.get("vo2max") is not None)
    race_days = sum(1 for r in day_records if r.get("race_5k_s") is not None)
    log(f"VO2max auf {vo2_days} Tagen, Wettkampfprognosen auf {race_days} Tagen")

    with_data = sum(
        1
        for r in day_records
        if any(r[k] is not None for k in r if k != "date")
    )
    log(f"Tage mit mindestens einem Wert: {with_data} von {len(day_records)}")

    if args.dry_run:
        print("\n── Vorschau (dry-run, es wurde nichts geschrieben) ──")
        for r in day_records:
            print(
                f"  {r['date']}  TR {fmt(r['training_readiness'])}  "
                f"HRV {fmt(r['hrv_avg'])}  RHR {fmt(r['resting_hr'])}  "
                f"Schlaf {fmt(r['sleep_hours'],'h',2)}  Stress⌀ {fmt(r['stress_avg'])}  "
                f"Schritte {fmt(r['steps'])}"
            )
        for a in acts[:10]:
            print(
                f"  {a['date']}  {a['name']}  {fmt(a['distance_km'],'km',2)}  "
                f"{hm(a['duration_s'])}  {pace_str(a['pace_s_per_km'])}  "
                f"HR {fmt(a['avg_hr'])}  aTE {fmt(a['aerobic_te'],'',1)}"
            )
        return

    OUT_DIR.mkdir(exist_ok=True)
    DAYS_DIR.mkdir(exist_ok=True)
    ACT_DIR.mkdir(exist_ok=True)

    for r in day_records:
        write_day_note(r)
    for a in acts:
        write_activity_note(a)

    merged = merge_json(
        day_records, acts, {"start": all_days[0], "end": all_days[-1]}
    )
    DATA_JSON.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(
        f"Geschrieben: {len(day_records)} Tagesnotizen, {len(acts)} Einheiten, "
        f"data.json ({len(merged['days'])} Tage gesamt)"
    )

    if not args.no_dashboard:
        rebuild_dashboard()
        if not args.no_publish:
            publish_dashboard()


if __name__ == "__main__":
    main()
