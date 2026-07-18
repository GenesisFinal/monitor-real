#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Actualiza data/live_data.json (valores actuales) y data/history/*.json
(series históricas para gráficos con selector de rango y velas/OHLC).
Se ejecuta diariamente vía GitHub Actions a las 20:00 hora Argentina (23:00 UTC).

Fuentes:
- Dólar (actual e histórico): dolarapi.com / api.argentinadatos.com (sin key)
- Banda cambiaria (piso/techo, actual e histórico): BCRA API oficial v4.0
  (sin key), idVariable 1187 (límite inferior) y 1188 (límite superior)
- Riesgo país e inflación: api.argentinadatos.com (sin key)
- Criptomonedas (actual e histórico): CoinGecko (sin key)
- Índices bursátiles globales (actual e histórico): Yahoo Finance (sin key),
  tickers reales (^DJI, ^GSPC, ^IXIC, ^FTSE, ^GDAXI, ^FCHI, ^IBEX, ^N225,
  ^HSI, 000001.SS, ^BVSP, ^MERV, ^GSPTSE, ^AXJO), agrupados por región
- Acciones/ETFs/Commodities/Divisas globales (actual e histórico):
  Twelve Data (requiere TWELVEDATA_API_KEY)
- Tasas locales (actual): rendimientos.co (no oficial, sin key)
- Tasas locales (histórico): BCRA API oficial v4.0 (sin key)
- Fondos Comunes de Inversión (actual): rendimientos.co (no oficial, sin key)
- Fondos Comunes de Inversión (histórico, fuente CAFCI): api.argentinadatos.com
- Bonos Soberanos (actual): rendimientos.co (no oficial, sin key)
- Bonos Soberanos (histórico OHLCV): data912.com (no oficial, sin key)
- Obligaciones Negociables (actual): rendimientos.co (no oficial, sin key)
- Obligaciones Negociables (histórico): SIN FUENTE GRATUITA CONOCIDA.
  Se acumula una serie propia día a día a partir de hoy (ver build_history_ons_acumulado()).
- Acciones Argentinas (actual): Yahoo Finance (sin key)
- Acciones Argentinas (histórico OHLCV): data912.com (no oficial, sin key)

Twelve Data free tier: 8 créditos/minuto, 800/día. Este script agrupa los
símbolos en lotes de máximo 8 y espera entre llamadas para no exceder el límite.

Nota sobre robustez: todas las fuentes son de terceros no oficiales (salvo
dolarapi/argentinadatos/coingecko/BCRA que son APIs públicas estables). Si
alguna falla un día puntual, el script sigue con las demás y esa sección
simplemente no se actualiza ese día (no se rompe todo el proceso).
"""
import json
import os
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, date, timedelta

TWELVEDATA_KEY = os.environ.get("TWELVEDATA_API_KEY", "")

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
OUT_PATH = os.path.join(BASE_DIR, "data", "live_data.json")
HISTORY_DIR = os.path.join(BASE_DIR, "data", "history")


def fetch_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "monitor-real-bot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[WARN] Fallo al pedir {url}: {e}")
        return None


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ------------------------------------------------------------------
# Utilidades genéricas para armar series "daily" (últimos ~13 meses) y
# "weekly" (últimos ~5 años, un punto por semana ISO) a partir de una
# lista de registros con fecha.
# ------------------------------------------------------------------

def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def build_daily_weekly(records, date_fn, point_fn, days_daily=400, years_weekly=5):
    """
    records: lista de objetos crudos (dicts) ya ordenados o no.
    date_fn(record) -> date | None
    point_fn(record) -> dict (con clave "t" = fecha ISO agregada por esta función)
    """
    parsed = []
    for r in records or []:
        d = date_fn(r)
        if d is None:
            continue
        pt = point_fn(r)
        if pt is None:
            continue
        pt["t"] = d.isoformat()
        parsed.append((d, pt))
    parsed.sort(key=lambda x: x[0])
    if not parsed:
        return [], []

    today = date.today()
    cutoff_daily = today - timedelta(days=days_daily)
    daily = [pt for d, pt in parsed if d >= cutoff_daily]

    cutoff_weekly = today - timedelta(days=years_weekly * 365 + 10)
    by_week = {}
    for d, pt in parsed:
        if d < cutoff_weekly:
            continue
        key = (d.isocalendar()[0], d.isocalendar()[1])
        by_week[key] = pt  # se queda el último valor de cada semana ISO
    weekly = [by_week[k] for k in sorted(by_week.keys())]

    return daily, weekly


# ------------------------------------------------------------------
# Valores actuales (live_data.json) - sin cambios respecto a versiones
# anteriores del script.
# ------------------------------------------------------------------

def get_dolares():
    data = fetch_json("https://dolarapi.com/v1/dolares")
    if not data:
        return None
    out = []
    for d in data:
        out.append({
            "casa": d.get("casa"),
            "nombre": d.get("nombre"),
            "compra": d.get("compra"),
            "venta": d.get("venta"),
            "fecha": d.get("fechaActualizacion"),
        })
    return out


def get_riesgo_pais():
    data = fetch_json("https://api.argentinadatos.com/v1/finanzas/indices/riesgo-pais/ultimo")
    if not data:
        return None
    return {"valor": data.get("valor"), "fecha": data.get("fecha")}


def get_inflacion():
    data = fetch_json("https://api.argentinadatos.com/v1/finanzas/indices/inflacion")
    if not data or not isinstance(data, list) or len(data) == 0:
        return None
    ultimo = data[-1]
    return {"valor": ultimo.get("valor"), "fecha": ultimo.get("fecha")}


def get_cripto():
    ids = "bitcoin,ethereum,tether,binancecoin,solana"
    data = fetch_json(
        f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"
    )
    if not data:
        return None
    nombres = {
        "bitcoin": "Bitcoin (BTC)",
        "ethereum": "Ethereum (ETH)",
        "tether": "USDT",
        "binancecoin": "BNB",
        "solana": "Solana (SOL)",
    }
    out = []
    for cid, nombre in nombres.items():
        v = data.get(cid)
        if not v:
            continue
        out.append({
            "id": cid,
            "nombre": nombre,
            "usd": v.get("usd"),
            "change_24h": v.get("usd_24h_change"),
        })
    return out


TASAS_LOCALES_KEYS = {
    "badlar_tna": "BADLAR Privados (TNA)",
    "tm20": "TM20 Privados",
    "tamar_tna": "TAMAR Privados (TNA)",
    "tasa_depositos_30d": "Depósitos a 30 días",
    "tasa_adelantos": "Adelantos en Cta. Cte.",
    "tasa_prestamos": "Préstamos Personales",
}


def get_tasas_locales():
    data = fetch_json("https://rendimientos.co/api/bcra")
    if not data or "data" not in data:
        return None
    by_key = {x.get("key"): x for x in data["data"]}
    out = []
    for key, nombre in TASAS_LOCALES_KEYS.items():
        item = by_key.get(key)
        if item and item.get("valor") is not None:
            out.append({
                "nombre": nombre,
                "key": key,
                "valor": item.get("valor"),
                "unidad": item.get("unidad"),
                "fecha": item.get("fecha"),
            })
    return out or None


def get_fci():
    data = fetch_json("https://rendimientos.co/api/cafci")
    if not data or "data" not in data:
        return None
    top = sorted(
        [x for x in data["data"] if x.get("patrimonio")],
        key=lambda x: x["patrimonio"],
        reverse=True,
    )[:6]
    nombre_a_slug = {v: k for k, v in FCI_SLUGS.items()}
    out = []
    for x in top:
        nombre = x.get("nombre")
        out.append({
            "nombre": nombre,
            "slug": nombre_a_slug.get(nombre),
            "tna": x.get("tna"),
            "categoria": x.get("category"),
        })
    return out or None


BONOS_SOBERANOS = ["GD30", "GD35", "AL30", "AL29", "AE38", "AL35"]


def get_bonos():
    data = fetch_json("https://rendimientos.co/api/soberanos")
    if not data or "data" not in data:
        return None
    by_symbol = {x.get("symbol"): x for x in data["data"]}
    out = []
    for sym in BONOS_SOBERANOS:
        item = by_symbol.get(sym)
        if item:
            out.append({
                "symbol": sym,
                "price_usd": item.get("price_usd"),
                "pct_change": item.get("pct_change"),
            })
    return out or None


def get_ons():
    data = fetch_json("https://rendimientos.co/api/ons")
    if not data or "data" not in data:
        return None
    top = sorted(data["data"], key=lambda x: x.get("v") or 0, reverse=True)[:6]
    out = []
    for x in top:
        out.append({
            "symbol": x.get("symbol"),
            "price": x.get("c"),
            "pct_change": x.get("pct_change"),
        })
    return out or None


ACCIONES_ARG_SYMBOLS = {
    "YPFD.BA": "YPF S.A.",
    "GGAL.BA": "Grupo Financiero Galicia",
    "PAMP.BA": "Pampa Energía",
    "BMA.BA": "Banco Macro",
}


def get_acciones_arg():
    out = []
    for symbol, nombre in ACCIONES_ARG_SYMBOLS.items():
        data = fetch_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}")
        if not data:
            continue
        try:
            meta = data["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice")
            prev = meta.get("previousClose")
            pct = ((price - prev) / prev * 100) if (price and prev) else None
            out.append({
                "symbol": symbol,
                "nombre": nombre,
                "price": price,
                "pct_change": pct,
            })
        except (KeyError, IndexError, TypeError, ZeroDivisionError) as e:
            print(f"[WARN] No se pudo parsear Yahoo Finance para {symbol}: {e}")
    return out or None


TWELVEDATA_SYMBOLS = {
    "stocks": {"AAPL": "Apple Inc.", "MSFT": "Microsoft Corp.", "AMZN": "Amazon.com Inc.", "GOOGL": "Alphabet Inc."},
    "etfs": {"SPY": "SPY (S&P 500)", "QQQ": "QQQ (Nasdaq 100)", "EEM": "EEM (Emergentes)"},
    "forex": {
        "EUR/USD": "Euro",
        "GBP/USD": "Libra",
        "USD/JPY": "Yen",
        "USD/BRL": "Real",
        "USD/MXN": "Peso Mexicano",
        "USD/UYU": "Peso Uruguayo",
        "USD/ARS": "Peso Argentino",
    },
}

# Índices bursátiles globales: antes se usaban ETFs como "proxy" de los
# índices reales vía Twelve Data (SPY por S&P 500, DIA por Dow Jones, QQQ
# por Nasdaq), lo cual daba valores que NO son los del índice real. Se
# reemplaza por Yahoo Finance (query1.finance.yahoo.com/v8/finance/chart),
# que sí publica los tickers reales de cada índice (^DJI, ^GSPC, etc.) con
# OHLCV histórico real, sin key. Agrupados por región para la UI.
INDICES_GLOBALES = [
    ("USA", [
        ("^DJI", "Dow Jones Industrial Average"),
        ("^GSPC", "S&P 500"),
        ("^IXIC", "Nasdaq Composite"),
    ]),
    ("Europa", [
        ("^FTSE", "FTSE 100"),
        ("^GDAXI", "DAX"),
        ("^FCHI", "CAC 40"),
        ("^IBEX", "IBEX 35 (España)"),
    ]),
    ("Asia", [
        ("^N225", "Nikkei 225"),
        ("^HSI", "Hang Seng"),
        ("000001.SS", "Shanghai Composite"),
    ]),
    ("LATAM", [
        ("^BVSP", "Bovespa"),
        ("^MERV", "Merval"),
    ]),
    ("Otros", [
        ("^GSPTSE", "S&P/TSX"),
        ("^AXJO", "ASX 200"),
    ]),
]


# Yahoo Finance no informa "currency" en meta para algunos índices (p.ej.
# el Merval, ^MERV, devuelve currency=""). Fallback con la moneda real de
# cotización de cada índice.
INDICES_CURRENCY_FALLBACK = {
    "^MERV": "ARS",
}


def get_indices_globales():
    out = []
    for region, symbols in INDICES_GLOBALES:
        for symbol, nombre in symbols:
            data = fetch_json(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?interval=1d&range=1y"
            )
            if not data:
                continue
            try:
                result = data["chart"]["result"][0]
                meta = result["meta"]
                price = meta.get("regularMarketPrice")
                closes = result.get("indicators", {}).get("quote", [{}])[0].get("close") or []
                closes_validas = [c for c in closes if c is not None]
                # Var. Día %: precio actual vs. el cierre diario anterior real
                # (NO se usa meta.chartPreviousClose: para range=1y ese campo
                # es el cierre previo al INICIO del rango, es decir de hace
                # ~1 año, y da variaciones diarias absurdas del 15-20%).
                pct_dia = None
                if price and len(closes_validas) >= 2:
                    prev_close = closes_validas[-2]
                    pct_dia = ((price - prev_close) / prev_close * 100) if prev_close else None
                pct_1y = None
                if price and closes_validas and closes_validas[0]:
                    pct_1y = (price - closes_validas[0]) / closes_validas[0] * 100
                out.append({
                    "symbol": symbol,
                    "nombre": nombre,
                    "region": region,
                    "close": price,
                    "currency": meta.get("currency") or INDICES_CURRENCY_FALLBACK.get(symbol, ""),
                    "percent_change": pct_dia,
                    "percent_change_1y": pct_1y,
                })
            except (KeyError, IndexError, TypeError, ZeroDivisionError) as e:
                print(f"[WARN] No se pudo parsear índice {symbol}: {e}")
    return out or None


def build_history_indices():
    indices_out = {}
    for region, symbols in INDICES_GLOBALES:
        for symbol, nombre in symbols:
            data = fetch_json(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?interval=1d&range=5y"
            )
            if not data:
                continue
            try:
                result = data["chart"]["result"][0]
                ts = result["timestamp"]
                q = result["indicators"]["quote"][0]
                records = []
                for i, t in enumerate(ts):
                    c = q.get("close", [None] * len(ts))[i] if i < len(q.get("close", [])) else None
                    if c is None:
                        continue
                    records.append({
                        "ts": t,
                        "o": q.get("open", [None] * len(ts))[i] if i < len(q.get("open", [])) else None,
                        "h": q.get("high", [None] * len(ts))[i] if i < len(q.get("high", [])) else None,
                        "l": q.get("low", [None] * len(ts))[i] if i < len(q.get("low", [])) else None,
                        "c": c,
                    })
            except (KeyError, IndexError, TypeError):
                print(f"[WARN] No se pudo parsear histórico Yahoo para {symbol}")
                continue
            daily, weekly = build_daily_weekly(
                records,
                date_fn=lambda r: datetime.fromtimestamp(r["ts"], tz=timezone.utc).date(),
                point_fn=lambda r: {"o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"]},
            )
            if daily or weekly:
                indices_out[symbol] = {"nombre": nombre, "region": region, "daily": daily, "weekly": weekly}
            time.sleep(0.5)  # cortesía con Yahoo Finance
    if not indices_out:
        return None
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "indices": indices_out}


# Commodities: antes se usaban ETFs como "proxy" (GLD por Oro, USO por
# Petróleo, SLV por Plata, CORN por Maíz) vía Twelve Data, lo cual no refleja
# el precio real de cada materia prima. Se reemplaza por Yahoo Finance
# (futuros reales de cada commodity: COMEX, NYMEX, CBOT, ICE), agrupados por
# categoría para la UI. Zinc no tiene fuente gratuita confiable en tiempo
# real (ni Yahoo ni Twelve Data lo ofrecen como spot/futuro) y se omite.
# Yahoo Finance cotiza granos/algodón/café/azúcar/jugo de naranja en
# centavos de dólar (currency="USX"), no en dólares. Se normalizan a USD
# (dividiendo por 100) para que la tabla muestre un valor homogéneo.
COMMODITIES_USX_SYMBOLS = {"ZS=F", "ZC=F", "ZW=F", "CT=F", "KC=F", "SB=F", "OJ=F"}

COMMODITIES_GLOBALES = [
    ("Metales", [
        ("GC=F", "Oro"),
        ("SI=F", "Plata"),
        ("PL=F", "Platino"),
        ("HG=F", "Cobre"),
        ("ALI=F", "Aluminio"),
    ]),
    ("Energía", [
        ("CL=F", "WTI"),
        ("BZ=F", "Brent"),
        ("NG=F", "Gas"),
        ("RB=F", "Gasolina"),
    ]),
    ("Granos", [
        ("ZS=F", "Soja"),
        ("ZC=F", "Maíz"),
        ("ZW=F", "Trigo"),
    ]),
    ("Otros", [
        ("CT=F", "Algodón"),
        ("KC=F", "Café"),
        ("CC=F", "Cacao"),
        ("SB=F", "Azúcar"),
        ("OJ=F", "Jugo de Naranja"),
    ]),
]


def get_commodities_globales():
    out = []
    for region, symbols in COMMODITIES_GLOBALES:
        for symbol, nombre in symbols:
            data = fetch_json(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?interval=1d&range=1y"
            )
            if not data:
                continue
            try:
                result = data["chart"]["result"][0]
                meta = result["meta"]
                price = meta.get("regularMarketPrice")
                closes = result.get("indicators", {}).get("quote", [{}])[0].get("close") or []
                closes_validas = [c for c in closes if c is not None]
                pct_dia = None
                if price and len(closes_validas) >= 2:
                    prev_close = closes_validas[-2]
                    pct_dia = ((price - prev_close) / prev_close * 100) if prev_close else None
                if symbol in COMMODITIES_USX_SYMBOLS and price is not None:
                    price = price / 100
                out.append({
                    "symbol": symbol,
                    "nombre": nombre,
                    "region": region,
                    "close": price,
                    "currency": "USD",
                    "percent_change": pct_dia,
                })
            except (KeyError, IndexError, TypeError, ZeroDivisionError) as e:
                print(f"[WARN] No se pudo parsear commodity {symbol}: {e}")
    return out or None


def build_history_commodities():
    commodities_out = {}
    for region, symbols in COMMODITIES_GLOBALES:
        for symbol, nombre in symbols:
            data = fetch_json(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?interval=1d&range=5y"
            )
            if not data:
                continue
            try:
                result = data["chart"]["result"][0]
                ts = result["timestamp"]
                q = result["indicators"]["quote"][0]
                records = []
                for i, t in enumerate(ts):
                    c = q.get("close", [None] * len(ts))[i] if i < len(q.get("close", [])) else None
                    if c is None:
                        continue
                    def _norm(v):
                        return (v / 100) if (v is not None and symbol in COMMODITIES_USX_SYMBOLS) else v
                    records.append({
                        "ts": t,
                        "o": _norm(q.get("open", [None] * len(ts))[i] if i < len(q.get("open", [])) else None),
                        "h": _norm(q.get("high", [None] * len(ts))[i] if i < len(q.get("high", [])) else None),
                        "l": _norm(q.get("low", [None] * len(ts))[i] if i < len(q.get("low", [])) else None),
                        "c": _norm(c),
                    })
            except (KeyError, IndexError, TypeError):
                print(f"[WARN] No se pudo parsear histórico Yahoo para {symbol}")
                continue
            daily, weekly = build_daily_weekly(
                records,
                date_fn=lambda r: datetime.fromtimestamp(r["ts"], tz=timezone.utc).date(),
                point_fn=lambda r: {"o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"]},
            )
            if daily or weekly:
                commodities_out[symbol] = {"nombre": nombre, "region": region, "daily": daily, "weekly": weekly}
            time.sleep(0.5)  # cortesía con Yahoo Finance
    if not commodities_out:
        return None
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "commodities": commodities_out}


def chunked(items, n):
    items = list(items)
    for i in range(0, len(items), n):
        yield items[i:i + n]


def get_twelvedata():
    if not TWELVEDATA_KEY:
        print("[WARN] No hay TWELVEDATA_API_KEY configurada, se omite mercado global.")
        return None

    all_symbols = []
    for cat, symmap in TWELVEDATA_SYMBOLS.items():
        for sym in symmap:
            if sym not in all_symbols:
                all_symbols.append(sym)

    results = {}
    batches = list(chunked(all_symbols, 8))
    for i, batch in enumerate(batches):
        symbol_param = ",".join(batch)
        url = f"https://api.twelvedata.com/quote?symbol={urllib.parse.quote(symbol_param)}&apikey={TWELVEDATA_KEY}"
        data = fetch_json(url)
        if data:
            if isinstance(data, dict) and data.get("code") and data.get("status") == "error":
                print(f"[WARN] Twelve Data error: {data.get('message')}")
            elif len(batch) == 1:
                results[batch[0]] = data
            else:
                results.update(data)
        if i < len(batches) - 1:
            time.sleep(65)  # respetar límite de 8 créditos/minuto
    return results


# ------------------------------------------------------------------
# Series históricas (data/history/*.json)
# ------------------------------------------------------------------

DOLAR_CASAS = ["oficial", "blue", "bolsa", "contadoconliqui", "mayorista", "cripto", "tarjeta"]


def build_history_dolar():
    casas_out = {}
    for casa in DOLAR_CASAS:
        data = fetch_json(f"https://api.argentinadatos.com/v1/cotizaciones/dolares/{casa}")
        if not data:
            continue
        daily, weekly = build_daily_weekly(
            data,
            date_fn=lambda r: _parse_date(r.get("fecha")),
            point_fn=lambda r: {"c": r.get("venta")},
        )
        if daily or weekly:
            casas_out[casa] = {"daily": daily, "weekly": weekly}
    if not casas_out:
        return None
    result = {"updated_at": datetime.now(timezone.utc).isoformat(), "casas": casas_out}
    banda = build_history_banda_cambiaria()
    if banda:
        result["banda"] = banda
    return result


# Régimen de bandas cambiarias (BCRA, API oficial v4.0/monetarias).
# idVariable confirmado por inspección directa del listado completo de
# variables (no figuran en la categoría "tasas" sino en "Principales
# Variables"): 1187 = límite inferior (piso), 1188 = límite superior (techo).
# Vigente desde 2025-04-14 (lanzamiento del régimen de bandas).
BANDA_IDS = {"piso": 1187, "techo": 1188}
BANDA_DESDE = "2025-04-14"


def get_banda_cambiaria():
    hoy = date.today()
    desde = (hoy - timedelta(days=10)).isoformat()
    hasta = hoy.isoformat()
    piso_detalle = bcra_fetch_all(BANDA_IDS["piso"], desde, hasta)
    techo_detalle = bcra_fetch_all(BANDA_IDS["techo"], desde, hasta)
    if not piso_detalle or not techo_detalle:
        return None
    piso_last = piso_detalle[0]
    techo_last = techo_detalle[0]
    return {
        "piso": piso_last.get("valor"),
        "techo": techo_last.get("valor"),
        "fecha": piso_last.get("fecha"),
    }


def build_history_banda_cambiaria():
    hoy = date.today()
    hasta = hoy.isoformat()
    piso_detalle = bcra_fetch_all(BANDA_IDS["piso"], BANDA_DESDE, hasta)
    time.sleep(1)
    techo_detalle = bcra_fetch_all(BANDA_IDS["techo"], BANDA_DESDE, hasta)
    if not piso_detalle or not techo_detalle:
        return None
    piso_by_fecha = {r.get("fecha"): r.get("valor") for r in piso_detalle}
    techo_by_fecha = {r.get("fecha"): r.get("valor") for r in techo_detalle}
    fechas = sorted(set(piso_by_fecha) & set(techo_by_fecha))
    merged = [
        {"fecha": f, "piso": piso_by_fecha[f], "techo": techo_by_fecha[f]}
        for f in fechas
    ]
    daily, weekly = build_daily_weekly(
        merged,
        date_fn=lambda r: _parse_date(r.get("fecha")),
        point_fn=lambda r: {"piso": r.get("piso"), "techo": r.get("techo")},
    )
    if not (daily or weekly):
        return None
    return {"daily": daily, "weekly": weekly}


BCRA_TASAS_IDS = {
    "badlar_tna": 7,
    "tm20": 142,
    "tamar_tna": 136,
    "tasa_depositos_30d": 12,
    "tasa_adelantos": 13,
    "tasa_prestamos": 14,
}


def bcra_fetch_all(id_variable, desde, hasta):
    """Pagina si hace falta (límite 1000 registros por página)."""
    out = []
    offset = 0
    while True:
        url = (
            f"https://api.bcra.gob.ar/estadisticas/v4.0/monetarias/{id_variable}"
            f"?desde={desde}&hasta={hasta}&offset={offset}&limit=1000"
        )
        data = fetch_json(url)
        if not data or "results" not in data or not data["results"]:
            break
        detalle = data["results"][0].get("detalle", [])
        out.extend(detalle)
        count = data.get("metadata", {}).get("resultset", {}).get("count", 0)
        offset += 1000
        if offset >= count or not detalle:
            break
    return out


def build_history_tasas_locales():
    hoy = date.today()
    desde5 = (hoy - timedelta(days=5 * 365 + 10)).isoformat()
    hasta = hoy.isoformat()
    series_out = {}
    for key, id_var in BCRA_TASAS_IDS.items():
        detalle = bcra_fetch_all(id_var, desde5, hasta)
        if not detalle:
            continue
        daily, weekly = build_daily_weekly(
            detalle,
            date_fn=lambda r: _parse_date(r.get("fecha")),
            point_fn=lambda r: {"c": r.get("valor")},
        )
        if daily or weekly:
            series_out[key] = {"daily": daily, "weekly": weekly}
        time.sleep(1)  # cortesía con la API pública del BCRA
    if not series_out:
        return None
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "series": series_out}


FCI_SLUGS = {
    "mercado-fondo-clase-a": "Mercado Fondo - Clase A",
    "fima-premium-clase-b": "Fima Premium - Clase B",
    "fima-premium-clase-a": "Fima Premium - Clase A",
    "pionero-pesos-plus-clase-b": "Pionero Pesos Plus - Clase B",
    "pellegrini-renta-pesos-clase-b": "Pellegrini Renta Pesos - Clase B",
    "super-ahorro-clase-a": "Super Ahorro $ - Clase A",
}


def build_history_fci():
    fondos_out = {}
    for slug, nombre in FCI_SLUGS.items():
        data = fetch_json(f"https://api.argentinadatos.com/v1/finanzas/fci/fondos/{slug}/historico")
        if not data or "historico" not in data:
            continue
        daily, weekly = build_daily_weekly(
            data["historico"],
            date_fn=lambda r: _parse_date(r.get("fecha")),
            point_fn=lambda r: {"c": r.get("valorCuotaparte")},
        )
        if daily or weekly:
            fondos_out[slug] = {"nombre": nombre, "daily": daily, "weekly": weekly}
    if not fondos_out:
        return None
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "fondos": fondos_out}


def _ohlcv_point(r):
    if r.get("c") is None:
        return None
    return {
        "o": r.get("o"),
        "h": r.get("h"),
        "l": r.get("l"),
        "c": r.get("c"),
        "v": r.get("v"),
    }


# ------------------------------------------------------------------
# Bonos soberanos: cronograma oficial de amortización, para ajustar el
# precio histórico por valor residual (VR) y evitar los "saltos" que se
# ven en el gráfico cada vez que el bono paga una cuota de capital (el
# precio de mercado se cotiza "por 100 VN original", así que cuando se
# amortiza una cuota el precio cae de golpe aunque no haya pérdida real
# para el tenedor: cobra esa cuota en efectivo). El ajuste consiste en
# recalcular el precio "por 100 de capital ORIGINAL" a "por 100 de VR
# vigente a esa fecha" (precio técnico / paridad), que es continuo.
#
# Fuente (oficial, decretos de la reestructuración 2020, infoleg.gob.ar):
# - Decreto 701/2020 Anexo (Globales, ley NY): GD30, GD35
# - Decreto 676/2020 Anexo IV (Bonares, ley argentina): AL29, AL30, AL35,
#   AE38 - términos económicos idénticos a su par "Global".
# Confirmado que AL30/AE38 replican textualmente a GD30/GD38.
# ------------------------------------------------------------------

def _semestral_dates(start_year, start_month, count):
    dates = []
    y, m = start_year, start_month
    for _ in range(count):
        dates.append(f"{y:04d}-{m:02d}-09")
        m += 6
        if m > 12:
            m -= 12
            y += 1
    return dates


# AL30 / GD30: 13 cuotas semestrales (4% + 12x8%), 9-jul-2024 a 9-jul-2030
_AL30_GD30_SCHEDULE = list(zip(_semestral_dates(2024, 7, 13), [4] + [8] * 12))
# AL29: 10 cuotas iguales del 10%, 9-ene-2025 a 9-jul-2029
_AL29_SCHEDULE = list(zip(_semestral_dates(2025, 1, 10), [10] * 10))
# AL35 / GD35: 10 cuotas iguales del 10%, 9-ene-2031 a 9-jul-2035
_AL35_GD35_SCHEDULE = list(zip(_semestral_dates(2031, 1, 10), [10] * 10))
# AE38: 22 cuotas iguales de 100/22 %, 9-jul-2027 a 9-ene-2038
_AE38_SCHEDULE = list(zip(_semestral_dates(2027, 7, 22), [100.0 / 22] * 22))

BOND_AMORT_SCHEDULES = {
    "AL30": _AL30_GD30_SCHEDULE,
    "GD30": _AL30_GD30_SCHEDULE,
    "AL29": _AL29_SCHEDULE,
    "AL35": _AL35_GD35_SCHEDULE,
    "GD35": _AL35_GD35_SCHEDULE,
    "AE38": _AE38_SCHEDULE,
}


def _find_ex_date(dated_closes, fecha_teorica, window_days=5, umbral=-0.03):
    """
    La fecha de "corte" que efectivamente usa el mercado para reflejar el
    pago de una cuota puede no coincidir con la fecha exacta del decreto
    (settlement T+1/T+2, feriados, convención de cada cámara compensadora).
    Se busca, dentro de +-window_days días corridos de la fecha teórica, el
    día con la mayor caída porcentual (probable "ex-fecha" real de mercado).
    Si no se detecta ninguna caída relevante (>3%), se usa la fecha teórica
    sin modificar.
    """
    exp = _parse_date(fecha_teorica)
    if exp is None:
        return fecha_teorica
    lo = (exp - timedelta(days=window_days)).isoformat()
    hi = (exp + timedelta(days=window_days)).isoformat()
    best_date, best_chg = None, umbral
    for i in range(1, len(dated_closes)):
        d, c = dated_closes[i]
        if not (lo <= d <= hi) or not c:
            continue
        prev_c = dated_closes[i - 1][1]
        if not prev_c:
            continue
        chg = (c - prev_c) / prev_c
        if chg < best_chg:
            best_chg, best_date = chg, d
    return best_date or fecha_teorica


def _residual_value_from_schedule(schedule, fecha_iso):
    """% de valor residual (VR) vigente en fecha_iso, sobre 100 original."""
    if not schedule or not fecha_iso:
        return 100.0
    amortizado = sum(pct for fecha_pago, pct in schedule if fecha_pago <= fecha_iso)
    return max(100.0 - amortizado, 0.01)


def build_history_bonos():
    bonos_out = {}
    for sym in BONOS_SOBERANOS:
        data = fetch_json(f"https://data912.com/historical/bonds/{sym}")
        if not data or not isinstance(data, list):
            continue

        # Detectar la fecha real ("ex-fecha") de cada pago dentro de los
        # datos de mercado, en vez de asumir que coincide con el decreto.
        dated_closes = sorted(
            [(str(r.get("date", ""))[:10], r.get("c")) for r in data if r.get("c") is not None and r.get("date")],
            key=lambda x: x[0],
        )
        schedule = BOND_AMORT_SCHEDULES.get(sym)
        effective_schedule = None
        if schedule:
            effective_schedule = [
                (_find_ex_date(dated_closes, fecha_pago), pct) for fecha_pago, pct in schedule
            ]

        def _point_fn(r, _sched=effective_schedule):
            p = _ohlcv_point(r)
            if p is None:
                return None
            vr = _residual_value_from_schedule(_sched, str(r.get("date", ""))[:10])
            factor = 100.0 / vr
            for k in ("o", "h", "l", "c"):
                if p.get(k) is not None:
                    p[k] = round(p[k] * factor, 4)
            return p

        daily, weekly = build_daily_weekly(
            data,
            date_fn=lambda r: _parse_date(r.get("date")),
            point_fn=_point_fn,
        )
        if daily or weekly:
            bonos_out[sym] = {"daily": daily, "weekly": weekly}
    if not bonos_out:
        return None
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "moneda": "ARS",
        "ajuste": "precio_tecnico_por_valor_residual",
        "bonos": bonos_out,
    }


ACCIONES_ARG_TICKERS_DATA912 = {
    "YPFD.BA": "YPFD",
    "GGAL.BA": "GGAL",
    "PAMP.BA": "PAMP",
    "BMA.BA": "BMA",
}


def build_history_acciones_arg():
    acciones_out = {}
    for symbol_full, ticker912 in ACCIONES_ARG_TICKERS_DATA912.items():
        data = fetch_json(f"https://data912.com/historical/stocks/{ticker912}")
        if not data or not isinstance(data, list):
            continue
        daily, weekly = build_daily_weekly(
            data,
            date_fn=lambda r: _parse_date(r.get("date")),
            point_fn=_ohlcv_point,
        )
        if daily or weekly:
            acciones_out[symbol_full] = {"daily": daily, "weekly": weekly}
    if not acciones_out:
        return None
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "acciones": acciones_out}


def build_history_cripto():
    ids = {
        "bitcoin": "Bitcoin (BTC)",
        "ethereum": "Ethereum (ETH)",
        "binancecoin": "BNB",
        "solana": "Solana (SOL)",
    }
    monedas_out = {}
    for cid, nombre in ids.items():
        data = fetch_json(
            f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart?vs_currency=usd&days=1825"
        )
        if not data or "prices" not in data:
            continue
        records = [{"ts": p[0], "c": p[1]} for p in data["prices"]]
        daily, weekly = build_daily_weekly(
            records,
            date_fn=lambda r: datetime.fromtimestamp(r["ts"] / 1000, tz=timezone.utc).date(),
            point_fn=lambda r: {"c": r["c"]},
        )
        if daily or weekly:
            monedas_out[cid] = {"nombre": nombre, "daily": daily, "weekly": weekly}
        time.sleep(2)  # cortesía con el límite de CoinGecko free tier
    if not monedas_out:
        return None
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "monedas": monedas_out}


def build_history_twelvedata():
    if not TWELVEDATA_KEY:
        print("[WARN] No hay TWELVEDATA_API_KEY, se omite histórico de mercados globales.")
        return None

    all_symbols = []
    for cat, symmap in TWELVEDATA_SYMBOLS.items():
        for sym in symmap:
            if sym not in all_symbols:
                all_symbols.append(sym)

    raw_daily = {}
    raw_weekly = {}
    for i, sym in enumerate(all_symbols):
        url_d = (
            f"https://api.twelvedata.com/time_series?symbol={urllib.parse.quote(sym)}"
            f"&interval=1day&outputsize=380&apikey={TWELVEDATA_KEY}"
        )
        data_d = fetch_json(url_d)
        if data_d and data_d.get("status") == "ok":
            raw_daily[sym] = data_d.get("values", [])
        time.sleep(8)  # 8 créditos/minuto -> 1 símbolo cada ~8s

        url_w = (
            f"https://api.twelvedata.com/time_series?symbol={urllib.parse.quote(sym)}"
            f"&interval=1week&outputsize=260&apikey={TWELVEDATA_KEY}"
        )
        data_w = fetch_json(url_w)
        if data_w and data_w.get("status") == "ok":
            raw_weekly[sym] = data_w.get("values", [])
        time.sleep(8)

    def to_points(values):
        out = []
        for v in values or []:
            try:
                out.append({
                    "t": v["datetime"][:10],
                    "o": float(v["open"]),
                    "h": float(v["high"]),
                    "l": float(v["low"]),
                    "c": float(v["close"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
        out.sort(key=lambda p: p["t"])
        return out

    categorias_out = {}
    for cat, symmap in TWELVEDATA_SYMBOLS.items():
        cat_out = {}
        for sym, nombre in symmap.items():
            daily = to_points(raw_daily.get(sym))
            weekly = to_points(raw_weekly.get(sym))
            if daily or weekly:
                cat_out[sym] = {"nombre": nombre, "daily": daily, "weekly": weekly}
        if cat_out:
            categorias_out[cat] = cat_out
    if not categorias_out:
        return None
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "categorias": categorias_out}


def build_history_ons_acumulado(ons_actuales):
    """
    No existe una fuente gratuita con histórico de ONs. En su lugar, se
    acumula una serie propia: cada corrida agrega el snapshot del día a
    un archivo que se conserva entre ejecuciones (se lee el existente y
    se le agrega el punto de hoy, evitando duplicados por fecha).
    """
    if not ons_actuales:
        return None
    path = os.path.join(HISTORY_DIR, "ons.json")
    existing = load_json(path) or {"acumulado_desde": date.today().isoformat(), "ons": {}}
    hoy = date.today().isoformat()
    ons_dict = existing.get("ons", {})
    for item in ons_actuales:
        sym = item.get("symbol")
        if not sym or item.get("price") is None:
            continue
        serie = ons_dict.setdefault(sym, [])
        if serie and serie[-1].get("t") == hoy:
            serie[-1]["c"] = item["price"]
        else:
            serie.append({"t": hoy, "c": item["price"]})
        # conservar como máximo ~5 años de puntos diarios
        if len(serie) > 1900:
            del serie[: len(serie) - 1900]
    existing["ons"] = ons_dict
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    return existing


def main():
    live_data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "dolar": get_dolares(),
        "banda_cambiaria": get_banda_cambiaria(),
        "riesgo_pais": get_riesgo_pais(),
        "inflacion": get_inflacion(),
        "cripto": get_cripto(),
        "tasas_locales": get_tasas_locales(),
        "fci": get_fci(),
        "bonds": get_bonos(),
        "corporate": get_ons(),
        "acciones_arg": get_acciones_arg(),
        "indices": get_indices_globales(),
        "commodities": get_commodities_globales(),
    }

    td = get_twelvedata()
    if td:
        for cat, symmap in TWELVEDATA_SYMBOLS.items():
            cat_out = []
            for sym, nombre in symmap.items():
                q = td.get(sym)
                if q and isinstance(q, dict) and q.get("close"):
                    cat_out.append({
                        "symbol": sym,
                        "nombre": nombre,
                        "close": q.get("close"),
                        "percent_change": q.get("percent_change"),
                        "currency": q.get("currency"),
                    })
            if cat_out:
                live_data[cat] = cat_out

    save_json(OUT_PATH, live_data)
    print(f"OK: {OUT_PATH} actualizado.")

    # ---- Series históricas ----
    historicos = {
        "dolar.json": build_history_dolar(),
        "tasas_locales.json": build_history_tasas_locales(),
        "fci.json": build_history_fci(),
        "bonos.json": build_history_bonos(),
        "acciones_arg.json": build_history_acciones_arg(),
        "cripto.json": build_history_cripto(),
        "mercados_globales.json": build_history_twelvedata(),
        "indices.json": build_history_indices(),
        "commodities.json": build_history_commodities(),
    }
    for filename, payload in historicos.items():
        if payload:
            save_json(os.path.join(HISTORY_DIR, filename), payload)
            print(f"OK: data/history/{filename} actualizado.")
        else:
            print(f"[WARN] No se pudo actualizar data/history/{filename} (fuente sin datos hoy).")

    ons_hist = build_history_ons_acumulado(live_data.get("corporate"))
    if ons_hist:
        save_json(os.path.join(HISTORY_DIR, "ons.json"), ons_hist)
        print("OK: data/history/ons.json actualizado (serie propia acumulada).")


if __name__ == "__main__":
    main()
