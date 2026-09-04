import io
import os
import time
import unicodedata
from datetime import datetime

import pandas as pd
import requests
from flask import Flask, jsonify, render_template

app = Flask(__name__)

DEFAULT_CSV_URL = (
    "https://1drv.ms/x/c/820F29C1224AE7AA/"
    "IQAqBHkbmeBeS4muk_3hGd3_AYU6nc1Cx-OpGVLqbnBPUuM"
    "?e=tvQ3Jh&download=1"
)
CSV_URL = os.getenv("CSV_URL", DEFAULT_CSV_URL)
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "600"))

_cache = {"at": 0.0, "payload": None}


def clean_text(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


def ascii_lower(value):
    text = unicodedata.normalize("NFKD", clean_text(value))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def first_existing(df, names):
    lookup = {ascii_lower(c): c for c in df.columns}
    for name in names:
        if ascii_lower(name) in lookup:
            return lookup[ascii_lower(name)]
    return None


def infer_container_type(row, iso_group_col, iso_type_col):
    source = " ".join([
        clean_text(row.get(iso_group_col, "")) if iso_group_col else "",
        clean_text(row.get(iso_type_col, "")) if iso_type_col else "",
    ]).lower()
    if any(term in source for term in ("reefer", "refrigerated", "heated", "self-powered")):
        return "Reefer"
    if any(term in source for term in ("tank", "tanque")):
        return "Tanque"
    return "Dry"


def download_csv():
    response = requests.get(CSV_URL, timeout=45, allow_redirects=True)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    sample = response.content[:300].lower()
    if b"<html" in sample or "text/html" in content_type:
        raise ValueError("OneDrive devolvió una página HTML y no el CSV. Revisa el enlace con &download=1.")
    return response.content


def transform(content):
    # utf-8-sig handles files exported with BOM; latin-1 is a fallback for older exports.
    try:
        df = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig", dtype=str)
    except UnicodeDecodeError:
        df = pd.read_csv(io.BytesIO(content), encoding="latin-1", dtype=str)

    df.columns = [clean_text(c) for c in df.columns]
    required = {
        "start": first_existing(df, ["Start Date"]),
        "handled": first_existing(df, ["Handled"]),
        "license": first_existing(df, ["Truck Visit Truck License"]),
        "status": first_existing(df, ["Status"]),
        "transaction": first_existing(df, ["Transaction Type"]),
        "freight": first_existing(df, ["Unit Frght Kind"]),
    }
    missing = [label for label, col in required.items() if col is None]
    if missing:
        raise ValueError("Faltan columnas requeridas: " + ", ".join(missing))

    start_col = required["start"]
    handled_col = required["handled"]
    license_col = required["license"]
    status_col = required["status"]
    transaction_col = required["transaction"]
    freight_col = required["freight"]

    sede_col = first_existing(df, ["SEDE", "Stow"])
    number_col = first_existing(df, ["Number"])
    gate_col = first_existing(df, ["Truck Visit Gate"])
    booking_col = first_existing(df, ["Booking Number"])
    line_col = first_existing(df, ["Line Id"])
    stow_col = first_existing(df, ["Stow"])
    iso_group_col = first_existing(df, ["ISO Group (order)"])
    iso_type_col = first_existing(df, ["ISO Type", "CÓDIGO ISO", "CODIGO ISO"])
    container_col = first_existing(df, ["CONTAINER TYPE"])

    df["StartDate_dt"] = pd.to_datetime(df[start_col], errors="coerce")
    df["Handled_dt"] = pd.to_datetime(df[handled_col], errors="coerce")
    df = df[df["StartDate_dt"].notna()].copy()

    now = pd.Timestamp.now().floor("s")
    df["HandledLimpio_dt"] = df["Handled_dt"].fillna(now)
    df["MES"] = df["StartDate_dt"].dt.strftime("%b")
    df["HORA"] = df["StartDate_dt"].dt.hour
    df["TURNO"] = df["HORA"].apply(lambda h: "DÍA" if 6 < h < 19 else "NOCHE")
    df["SEMANA"] = df["StartDate_dt"].dt.isocalendar().week.astype(int)
    df["FECHA"] = df["StartDate_dt"].dt.normalize()
    df["FILTRO_FECHA"] = df["FECHA"] - pd.to_timedelta((df["HORA"] < 7).astype(int), unit="D")

    trans = df[transaction_col].map(clean_text)
    freight = df[freight_col].map(clean_text)
    df["TRANSACCION"] = trans
    df.loc[(trans == "Receive Export") & (freight == "Empty"), "TRANSACCION"] = "Receive Empty"
    df.loc[(trans == "Deliver Import") & (freight == "Empty"), "TRANSACCION"] = "Deliver Empty"

    # Reproduce CONCA 20 using Status + truck license + exact Start Date.
    df["CONCA20"] = (
        df[status_col].map(clean_text) + "|" +
        df[license_col].map(clean_text) + "|" +
        df["StartDate_dt"].dt.strftime("%Y-%m-%d %H:%M:%S")
    )
    counts = df.groupby("CONCA20")["CONCA20"].transform("size")
    df["Estado Unicidad"] = counts.map(lambda n: "Duplicado" if n > 1 else "Único")

    if container_col:
        df["CONTAINER_TYPE"] = df[container_col].map(clean_text)
    else:
        df["CONTAINER_TYPE"] = df.apply(
            lambda r: infer_container_type(r, iso_group_col, iso_type_col), axis=1
        )

    targets = {
        "Deliver EmptyDry": 45, "Deliver ImportDry": 45,
        "Dray OffDry": 35, "Dray InDry": 35,
        "Receive EmptyDry": 45, "Receive ExportDry": 35,
        "Deliver EmptyReefer": 90, "Deliver ImportReefer": 45,
        "Dray OffReefer": 35, "Dray InReefer": 35,
        "Receive EmptyReefer": 45, "Receive ExportReefer": 35,
    }
    df["CONCA_TARGET"] = df["TRANSACCION"] + df["CONTAINER_TYPE"]
    df["TARGET"] = df["CONCA_TARGET"].map(targets)

    minutes = (df["HandledLimpio_dt"] - df["StartDate_dt"]).dt.total_seconds() / 60
    empty_duplicate = (
        df["TRANSACCION"].isin(["Deliver Empty", "Receive Empty"])
        & (df["Estado Unicidad"] == "Duplicado")
    )
    df["TIEMPO"] = minutes.where(~empty_duplicate, minutes / 2).round(1)
    df["ACEPTABLE"] = df["TIEMPO"].apply(lambda x: "NO" if x <= 10 else "SI")
    df["CUMPLIMIENTO"] = ((df["TARGET"].notna()) & (df["TIEMPO"] <= df["TARGET"])).astype(int)

    def considerar(row):
        gate = clean_text(row.get(gate_col, "")) if gate_col else ""
        booking = clean_text(row.get(booking_col, "")) if booking_col else ""
        stow = clean_text(row.get(stow_col, "")) if stow_col else ""
        line = clean_text(row.get(line_col, "")) if line_col else ""
        if gate in {"ARR_ARGGATE", "DAS_ARGGATE", "ARR_VNTGATE"}: return "NO CONSIDERAR"
        if booking.startswith(("DAS", "ARR")): return "NO CONSIDERAR"
        if stow.startswith("AR"): return "NO CONSIDERAR"
        if row["CONTAINER_TYPE"] == "Tanque": return "NO CONSIDERAR"
        if row["ACEPTABLE"] == "NO": return "NO CONSIDERAR"
        if line in {"DPW", "NEP", "NPT"}: return "NO CONSIDERAR"
        if pd.isna(row["TARGET"]): return "NO CONSIDERAR"
        return "CONSIDERAR"

    df["CONSIDERAR"] = df.apply(considerar, axis=1)
    df["SEDE_DASH"] = df[sede_col].map(clean_text) if sede_col else "Sin sede"
    df["STATUS_DASH"] = df[status_col].map(clean_text)
    df["LICENSE_DASH"] = df[license_col].map(clean_text)
    if number_col:
        df["NUMBER_DASH"] = df[number_col].map(clean_text)
    else:
        df["NUMBER_DASH"] = ""

    # Remove exact duplicates, like Table.Distinct.
    output_cols = ["LICENSE_DASH", "STATUS_DASH", "SEDE_DASH", "TIEMPO", "TARGET",
                   "CUMPLIMIENTO", "Estado Unicidad", "CONSIDERAR", "TRANSACCION",
                   "CONTAINER_TYPE", "NUMBER_DASH", "FILTRO_FECHA", "TURNO"]
    df = df[output_cols].drop_duplicates().copy()

    records = []
    for row in df.to_dict("records"):
        records.append({
            "license": row["LICENSE_DASH"],
            "status": row["STATUS_DASH"],
            "sede": row["SEDE_DASH"],
            "tiempo": None if pd.isna(row["TIEMPO"]) else float(row["TIEMPO"]),
            "target": None if pd.isna(row["TARGET"]) else float(row["TARGET"]),
            "cumplimiento": int(row["CUMPLIMIENTO"]),
            "unicidad": row["Estado Unicidad"],
            "considerar": row["CONSIDERAR"],
            "transaccion": row["TRANSACCION"],
            "containerType": row["CONTAINER_TYPE"],
            "number": row["NUMBER_DASH"],
            "fecha": row["FILTRO_FECHA"].strftime("%Y-%m-%d"),
            "turno": row["TURNO"],
        })

    return {
        "records": records,
        "sourceRows": int(len(df)),
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sedeSource": sede_col or "Sin sede",
    }


def get_payload(force=False):
    now = time.time()
    if not force and _cache["payload"] is not None and now - _cache["at"] < CACHE_SECONDS:
        return _cache["payload"]
    payload = transform(download_csv())
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
        app.logger.exception("No se pudo preparar el dashboard")
        return jsonify({"error": str(exc)}), 500


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
