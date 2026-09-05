import csv
import os
import tempfile
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import requests
from flask import Flask, jsonify, render_template

app = Flask(__name__)

CSV_URL = os.getenv("CSV_URL", "")
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "600"))
_cache = {"at": 0.0, "payload": None}


def text(value):
    return "" if value is None else str(value).strip()


def norm(value):
    value = unicodedata.normalize("NFKD", text(value))
    return "".join(c for c in value if not unicodedata.combining(c)).lower()


def parse_dt(value):
    value = text(value)
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def find_col(headers, options):
    lookup = {norm(h): h for h in headers}
    for option in options:
        found = lookup.get(norm(option))
        if found:
            return found
    return None


def download_to_temp():
    if not CSV_URL:
        raise ValueError("Falta configurar CSV_URL en Render.")
    response = requests.get(CSV_URL, timeout=60, stream=True, allow_redirects=True)
    response.raise_for_status()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if chunk:
                tmp.write(chunk)
        return tmp.name


def open_csv(path):
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
        handle.read(2048)
        handle.seek(0)
        return handle
    except UnicodeDecodeError:
        try:
            handle.close()
        except Exception:
            pass
        return open(path, "r", encoding="latin-1", newline="")


def container_type(row, container_col, iso_group_col, iso_type_col):
    if container_col and text(row.get(container_col)):
        return text(row.get(container_col))
    source = " ".join((text(row.get(iso_group_col)) if iso_group_col else "",
                       text(row.get(iso_type_col)) if iso_type_col else "")).lower()
    if any(x in source for x in ("reefer", "refrigerated", "heated", "self-powered")):
        return "Reefer"
    if any(x in source for x in ("tank", "tanque")):
        return "Tanque"
    return "Dry"


def build_payload():
    path = download_to_temp()
    try:
        with open_csv(path) as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or []

            cols = {
                "start": find_col(headers, ["Start Date"]),
                "handled": find_col(headers, ["Handled"]),
                "license": find_col(headers, ["Truck Visit Truck License"]),
                "status": find_col(headers, ["Status"]),
                "transaction": find_col(headers, ["Transaction Type"]),
                "freight": find_col(headers, ["Unit Frght Kind"]),
                "sede": find_col(headers, ["SEDE", "Stow"]),
                "gate": find_col(headers, ["Truck Visit Gate"]),
                "booking": find_col(headers, ["Booking Number"]),
                "line": find_col(headers, ["Line Id"]),
                "stow": find_col(headers, ["Stow"]),
                "iso_group": find_col(headers, ["ISO Group (order)"]),
                "iso_type": find_col(headers, ["ISO Type", "CÓDIGO ISO", "CODIGO ISO"]),
                "container": find_col(headers, ["CONTAINER TYPE"]),
            }
            required = ["start", "handled", "license", "status", "transaction", "freight"]
            missing = [name for name in required if not cols[name]]
            if missing:
                raise ValueError("Faltan columnas requeridas: " + ", ".join(missing))

            duplicate_counts = Counter()
            row_count = 0
            for row in reader:
                start = parse_dt(row.get(cols["start"]))
                if not start:
                    continue
                key = "|".join((text(row.get(cols["status"])), text(row.get(cols["license"])), start.isoformat()))
                duplicate_counts[key] += 1
                row_count += 1

        targets = {
            "Deliver EmptyDry": 45, "Deliver ImportDry": 45,
            "Dray OffDry": 35, "Dray InDry": 35,
            "Receive EmptyDry": 45, "Receive ExportDry": 35,
            "Deliver EmptyReefer": 90, "Deliver ImportReefer": 45,
            "Dray OffReefer": 35, "Dray InReefer": 35,
            "Receive EmptyReefer": 45, "Receive ExportReefer": 35,
        }

        groups = defaultdict(lambda: {"count": 0, "sumTime": 0.0, "targetCount": 0,
                                      "sumTarget": 0.0, "sumCompliance": 0, "duplicates": 0})
        now = datetime.now()

        with open_csv(path) as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                start = parse_dt(row.get(cols["start"]))
                if not start:
                    continue
                handled = parse_dt(row.get(cols["handled"])) or now
                license_value = text(row.get(cols["license"]))
                status = text(row.get(cols["status"]))
                sede = text(row.get(cols["sede"])) if cols["sede"] else "Sin sede"
                transaction = text(row.get(cols["transaction"]))
                freight = text(row.get(cols["freight"]))
                if transaction == "Receive Export" and freight == "Empty":
                    transaction = "Receive Empty"
                elif transaction == "Deliver Import" and freight == "Empty":
                    transaction = "Deliver Empty"

                duplicate_key = "|".join((status, license_value, start.isoformat()))
                is_duplicate = duplicate_counts[duplicate_key] > 1
                ctype = container_type(row, cols["container"], cols["iso_group"], cols["iso_type"])
                target = targets.get(transaction + ctype)
                minutes = round((handled - start).total_seconds() / 60, 1)
                if transaction in ("Deliver Empty", "Receive Empty") and is_duplicate:
                    minutes = round(minutes / 2, 1)

                acceptable = minutes > 10
                gate = text(row.get(cols["gate"])) if cols["gate"] else ""
                booking = text(row.get(cols["booking"])) if cols["booking"] else ""
                stow = text(row.get(cols["stow"])) if cols["stow"] else ""
                line = text(row.get(cols["line"])) if cols["line"] else ""
                consider = not (
                    gate in {"ARR_ARGGATE", "DAS_ARGGATE", "ARR_VNTGATE"}
                    or booking.startswith(("DAS", "ARR"))
                    or stow.startswith("AR")
                    or ctype == "Tanque"
                    or not acceptable
                    or line in {"DPW", "NEP", "NPT"}
                    or target is None
                )
                hour = start.hour
                shift = "DÍA" if 6 < hour < 19 else "NOCHE"
                operational_date = (start - timedelta(days=1)).date() if hour < 7 else start.date()
                compliance = 1 if target is not None and minutes <= target else 0

                key = (license_value, sede, status, "CONSIDERAR" if consider else "NO CONSIDERAR",
                       shift, transaction, ctype, operational_date.isoformat())
                group = groups[key]
                group["count"] += 1
                group["sumTime"] += minutes
                if target is not None:
                    group["targetCount"] += 1
                    group["sumTarget"] += target
                    group["sumCompliance"] += compliance
                if is_duplicate:
                    group["duplicates"] += 1

        records = []
        for key, value in groups.items():
            license_value, sede, status, consider, shift, transaction, ctype, date_value = key
            records.append({
                "license": license_value, "sede": sede, "status": status,
                "considerar": consider, "turno": shift, "transaccion": transaction,
                "containerType": ctype, "fecha": date_value,
                "count": value["count"], "sumTime": round(value["sumTime"], 1),
                "targetCount": value["targetCount"], "sumTarget": value["sumTarget"],
                "sumCompliance": value["sumCompliance"], "duplicates": value["duplicates"],
            })

        return {
            "records": records,
            "sourceRows": row_count,
            "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sedeSource": cols["sede"] or "Sin sede",
        }
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def get_payload():
    now = time.time()
    if _cache["payload"] is not None and now - _cache["at"] < CACHE_SECONDS:
        return _cache["payload"]
    payload = build_payload()
    _cache.update({"at": now, "payload": payload})
    return payload


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/data")
def api_data():
    try:
        return jsonify(get_payload())
    except Exception as exc:
        app.logger.exception("Error procesando CSV")
        return jsonify({"error": str(exc)}), 500


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
