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
import calendar
import json
import os
import re
import time
import unicodedata
import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar
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


def fetch_text(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "monitor-real-bot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
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


# Plazos Fijos por banco (sección "Plazos Fijos" dentro de Tasas Locales).
# Fuente: ArgentinaDatos (api.argentinadatos.com), que agrega el listado
# de bancos con tasas de depósitos a plazo fijo publicado por el BCRA.
# Solo valores (no se grafican): se usa tnaClientes como tasa de
# referencia de cada banco, tal como la publica el BCRA para comparación
# entre entidades. Se buscan los bancos pedidos por nombre (coincidencia
# de texto contra el nombre legal de la entidad) y se agregan además los
# 3 bancos con mayor TNA de todo el mercado (que no estén ya en la
# lista), todo ordenado de mayor a menor tasa.
PLAZO_FIJO_BANCOS = [
    ("Banco Santander", ["SANTANDER"]),
    ("Banco Patagonia", ["PATAGONIA"]),
    ("Banco Hipotecario", ["HIPOTECARIO"]),
    ("Comafi", ["COMAFI"]),
    ("Ciudad", ["CIUDAD DE BUENOS AIRES"]),
    ("Brubank", ["BRUBANK"]),
    ("ICBC", ["ICBC", "INDUSTRIAL AND COMMERCIAL BANK"]),
    ("Piano", ["PIANO"]),
    ("Galicia", ["GALICIA"]),
    ("BBVA", ["BBVA"]),
    ("Macro", ["MACRO"]),
    ("Nación", ["NACION ARGENTINA"]),
    ("Provincia BA", ["PROVINCIA DE BUENOS AIRES"]),
    ("Mariva", ["MARIVA"]),
    ("Del Sol", ["BANCO DEL SOL"]),
]


def _titulo_banco(entidad):
    return " ".join(w.capitalize() if len(w) > 3 else w for w in entidad.split())


def get_plazos_fijos():
    data = fetch_json("https://api.argentinadatos.com/v1/finanzas/tasas/plazoFijo")
    if not data:
        return None

    def valida(item):
        return isinstance(item, dict) and item.get("entidad") and item.get("tnaClientes")

    bancos_validos = [x for x in data if valida(x)]

    out = []
    usados = set()
    for nombre_pedido, keywords in PLAZO_FIJO_BANCOS:
        match = None
        for item in bancos_validos:
            entidad_upper = item["entidad"].upper()
            if any(kw in entidad_upper for kw in keywords):
                match = item
                break
        if match:
            out.append({
                "nombre": nombre_pedido,
                "entidad": match["entidad"],
                "tna": round(match["tnaClientes"] * 100, 3),
                "destacado": False,
            })
            usados.add(match["entidad"])

    # Los 3 bancos con mayor TNA del mercado que no estén ya incluidos.
    restantes = sorted(
        [x for x in bancos_validos if x["entidad"] not in usados],
        key=lambda x: x["tnaClientes"],
        reverse=True,
    )[:3]
    for item in restantes:
        out.append({
            "nombre": _titulo_banco(item["entidad"]),
            "entidad": item["entidad"],
            "tna": round(item["tnaClientes"] * 100, 3),
            "destacado": True,
        })

    out.sort(key=lambda x: x["tna"], reverse=True)
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


# ------------------------------------------------------------------
# FCI por moneda y categoria (Money Market / Renta Fija / Renta
# Variable / Renta Mixta / Retorno Total), cada subseccion con los
# fondos de las administradoras solicitadas (si operan en esa
# combinacion moneda+categoria) mas los 5 de mayor rentabilidad a 12
# meses y los 5 de mayor patrimonio. Fuente: api.argentinadatos.com,
# que expone moneda/administradora/rendimientos a 12 meses para el
# universo completo de fondos registrados en la CNV (a diferencia de
# rendimientos.co/api/cafci, que solo cubre 2 de las 5 categorias).
# ------------------------------------------------------------------

FCI_MANAGERS = [
    ("Cocos", {"admin": ["COCOS"]}),
    ("Allaria", {"admin": ["ALLARIA"]}),
    ("One618", {"admin": ["ONE618"]}),
    ("Toronto", {"nombre": ["TORONTO"]}),
    ("Schroders", {"admin": ["SCHRODER"]}),
    ("Compass", {"nombre": ["COMPASS"]}),
    ("Pellegrini", {"admin": ["PELLEGRINI"]}),
    ("Fima", {"nombre": ["FIMA"]}),
    ("Patagonia", {"admin": ["PATAGONIA"]}),
    ("Banco Industrial", {"admin": ["INDUSTRIAL"]}),
]

FCI_CATEGORIAS = [
    ("mm", "Money Market", "Mercado de Dinero"),
    ("rf", "Renta Fija", "Renta Fija"),
    ("rv", "Renta Variable", "Renta Variable"),
    ("rm", "Renta Mixta", "Renta Mixta"),
    ("rt", "Retorno Total", "Retorno Total"),
]

FCI_MONEDAS = [
    ("ars", "Pesos", ["Peso Argentina"]),
    ("usd", "Dolares", ["Dolar Estadounidense", "Dolar Estadounidense Billete"]),
]


def _slugify_fondo(nombre):
    if not nombre:
        return None
    s = unicodedata.normalize("NFKD", nombre)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or None


def _fci_match_manager(fondo, criterios):
    admin = (fondo.get("administradora") or "").upper()
    nombre = (fondo.get("nombre") or "").upper()
    for kw in criterios.get("admin", []):
        if kw in admin:
            return True
    for kw in criterios.get("nombre", []):
        if kw in nombre:
            return True
    return False


def _get_dolar_oficial_venta():
    data = fetch_json("https://dolarapi.com/v1/dolares/oficial")
    if not data or data.get("venta") is None:
        return None
    return data["venta"]


FCI_PATRIMONIO_MINIMO_USD = 20_000_000


def get_fci_secciones():
    data = fetch_json("https://api.argentinadatos.com/v1/finanzas/fci/fondos")
    if not data or "fondos" not in data:
        return None
    fondos = [f for f in data["fondos"] if isinstance(f, dict) and f.get("nombre") and f.get("fondoId") is not None]

    # Piso de patrimonio para fondos en Pesos: equivalente a USD 20 millones
    # al tipo de cambio oficial venta (el patrimonio de los fondos, sean en
    # pesos o en dolares, se reporta siempre en pesos). Se descartan los
    # fondos por debajo de ese piso antes de elegir gestoras y rankings,
    # para no listar fondos demasiado chicos. El campo "patrimonio" de la
    # API viene expresado en la moneda propia del fondo (confirmado
    # comparando patrimonio vs. valorCuotaparte de fondos en dolares: el
    # cociente da una cantidad de cuotapartes razonable solo si el
    # patrimonio ya esta en USD, no en pesos), por lo que a los fondos en
    # dolares el piso de USD 20 millones se les aplica directamente, y a
    # los fondos en pesos se les aplica su equivalente en pesos al tipo
    # de cambio oficial venta.
    dolar_oficial = _get_dolar_oficial_venta()
    piso_patrimonio_ars = (FCI_PATRIMONIO_MINIMO_USD * dolar_oficial) if dolar_oficial else None
    if piso_patrimonio_ars is None:
        print("[WARN] No se pudo obtener el dolar oficial: no se aplica piso de patrimonio a FCI en pesos.")

    secciones = {}
    for moneda_key, moneda_label, moneda_valores in FCI_MONEDAS:
        for cat_key, cat_label, tipo_renta in FCI_CATEGORIAS:
            subset = [
                f for f in fondos
                if f.get("moneda") in moneda_valores and f.get("tipoRenta") == tipo_renta
            ]
            if moneda_key == "ars" and piso_patrimonio_ars is not None:
                subset = [f for f in subset if (f.get("patrimonio") or 0) >= piso_patrimonio_ars]
            elif moneda_key == "usd":
                subset = [f for f in subset if (f.get("patrimonio") or 0) >= FCI_PATRIMONIO_MINIMO_USD]
            if not subset:
                continue

            elegidos = {}  # fondoId -> (fondo, origen)

            # 1) un fondo por cada administradora solicitada (el de mayor patrimonio si hay mas de uno)
            for nombre_mgr, criterios in FCI_MANAGERS:
                candidatos = [f for f in subset if _fci_match_manager(f, criterios)]
                if not candidatos:
                    continue
                mejor = max(candidatos, key=lambda f: f.get("patrimonio") or 0)
                elegidos[mejor["fondoId"]] = (mejor, "gestora")

            # 2) los 5 de mayor rentabilidad a 12 meses
            con_rendimiento = [
                f for f in subset
                if (f.get("rendimientos") or {}).get("doceMeses") is not None
            ]
            top_rentabilidad = sorted(
                con_rendimiento,
                key=lambda f: f["rendimientos"]["doceMeses"],
                reverse=True,
            )[:5]
            for f in top_rentabilidad:
                elegidos.setdefault(f["fondoId"], (f, "rentabilidad"))

            # 3) los 5 de mayor patrimonio
            con_patrimonio = [f for f in subset if f.get("patrimonio")]
            top_patrimonio = sorted(con_patrimonio, key=lambda f: f["patrimonio"], reverse=True)[:5]
            for f in top_patrimonio:
                elegidos.setdefault(f["fondoId"], (f, "patrimonio"))

            out = []
            for fondo, origen in elegidos.values():
                nombre = fondo.get("nombre")
                rend = fondo.get("rendimientos") or {}
                out.append({
                    "nombre": nombre,
                    "slug": _slugify_fondo(nombre),
                    "administradora": fondo.get("administradora"),
                    "patrimonio": fondo.get("patrimonio"),
                    "doce_meses": rend.get("doceMeses"),
                    "en_el_anio": rend.get("enElAnio"),
                    "origen": origen,
                })
            out.sort(key=lambda x: (x["doce_meses"] is None, -(x["doce_meses"] or 0)))

            secciones[f"fci_{moneda_key}_{cat_key}"] = {
                "categoria": cat_label,
                "moneda": moneda_label,
                "items": out,
            }

    return secciones or None


def build_history_fci_secciones(fci_secciones):
    if not fci_secciones:
        return None
    series_out = {}
    vistos = set()
    for seccion in fci_secciones.values():
        for item in seccion.get("items", []):
            slug = item.get("slug")
            nombre = item.get("nombre")
            if not slug or slug in vistos:
                continue
            vistos.add(slug)
            data = fetch_json(f"https://api.argentinadatos.com/v1/finanzas/fci/fondos/{slug}/historico")
            if not data or "historico" not in data:
                continue
            daily, weekly = build_daily_weekly(
                data["historico"],
                date_fn=lambda r: _parse_date(r.get("fecha")),
                point_fn=lambda r: {"c": r.get("valorCuotaparte")},
            )
            if daily or weekly:
                series_out[slug] = {"nombre": nombre, "daily": daily, "weekly": weekly}
            time.sleep(0.15)
    if not series_out:
        return None
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "series": series_out}


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

# ------------------------------------------------------------------
# Datos de flujos de fondos (cash flows) de los bonos soberanos en USD,
# fuente: rendimientos.co/api/config (seccion "soberanos"), que a su vez
# reconstruye el cronograma oficial de cada bono (Bonares ley local /
# Globales ley Nueva York, reestructuracion 2020). Cada flujo es el pago
# total (renta + amortizacion) por cada 100 de valor nominal ORIGINAL,
# en la fecha indicada. Se conservan tambien los flujos ya pagados (no
# solo los futuros) porque en algunos casos se necesitan para validar
# el cronograma; el filtro por fecha > hoy se aplica en tiempo de calculo.
# ------------------------------------------------------------------

# NOTA (chequeo automatico 2026-07-20, tarea #58): GD46 esta bien
# codificado en este diccionario, pero hoy no aparece en
# "bonos_soberanos_usd" del live_data.json publicado. Mismo patron que
# CUAP/8-CER (ver nota junto a CER_FLUJOS): get_bonos_soberanos_usd()
# hace "info = SOBERANOS_FLUJOS.get(sym); if not info or precio is None:
# continue" sobre el feed de https://rendimientos.co/api/soberanos -si
# ese feed todavia no trackea GD46 (emitido en el canje de agosto 2020,
# pero puede no estar en la cobertura de este agregador en particular),
# la entrada se descarta en silencio. No es un bug de flujos ni del
# filtro de duration/DTM (con duration ~6.6 anios y DTM >> 45 dias, GD46
# los pasaria sin problema). No se puede confirmar el contenido del feed
# desde este sandbox (dominio bloqueado por el allowlist de red).
SOBERANOS_FLUJOS = {
    "AL29": {"ley": "local", "vencimiento": "2029-07-09", "flujos": [
        ("2026-07-09", 10.35), ("2027-01-09", 10.3), ("2027-07-09", 10.25),
        ("2028-01-09", 10.2), ("2028-07-09", 10.15), ("2029-01-09", 10.1),
        ("2029-07-09", 10.05),
    ]},
    "GD29": {"ley": "NY", "vencimiento": "2029-07-09", "flujos": [
        ("2026-07-09", 10.35), ("2027-01-09", 10.3), ("2027-07-09", 10.25),
        ("2028-01-09", 10.2), ("2028-07-09", 10.15), ("2029-01-09", 10.1),
        ("2029-07-09", 10.05),
    ]},
    "AO28": {"ley": "local", "vencimiento": "2028-10-31", "flujos": [
        ("2026-04-30", 0.5), ("2026-05-29", 0.48), ("2026-06-30", 0.52),
        ("2026-07-31", 0.5), ("2026-08-31", 0.5), ("2026-09-30", 0.5),
        ("2026-10-30", 0.5), ("2026-11-30", 0.5), ("2026-12-30", 0.5),
        ("2027-01-29", 0.48), ("2027-02-26", 0.45), ("2027-03-31", 0.58),
        ("2027-04-30", 0.5), ("2027-05-31", 0.5), ("2027-06-30", 0.5),
        ("2027-07-30", 0.5), ("2027-08-31", 0.5), ("2027-09-30", 0.5),
        ("2027-10-29", 0.48), ("2027-11-30", 0.52), ("2027-12-30", 0.5),
        ("2028-01-31", 0.5), ("2028-02-29", 0.48), ("2028-03-31", 0.5),
        ("2028-04-28", 0.47), ("2028-05-31", 0.55), ("2028-06-30", 0.5),
        ("2028-07-31", 0.5), ("2028-08-31", 0.5), ("2028-09-29", 0.48),
        ("2028-10-31", 100.53),
    ]},
    "AO29": {"ley": "local", "vencimiento": "2029-10-31", "flujos": [
        ("2026-08-31", 0.73), ("2026-09-30", 0.5), ("2026-10-30", 0.5),
        ("2026-11-30", 0.5), ("2026-12-30", 0.5), ("2027-01-29", 0.48),
        ("2027-02-26", 0.45), ("2027-03-31", 0.58), ("2027-04-30", 0.5),
        ("2027-05-31", 0.5), ("2027-06-30", 0.5), ("2027-07-30", 0.5),
        ("2027-08-31", 0.5), ("2027-09-30", 0.5), ("2027-10-29", 0.48),
        ("2027-11-30", 0.52), ("2027-12-30", 0.5), ("2028-01-31", 0.5),
        ("2028-02-29", 0.48), ("2028-03-31", 0.53), ("2028-04-28", 0.47),
        ("2028-05-31", 0.55), ("2028-06-30", 0.5), ("2028-07-31", 0.5),
        ("2028-08-31", 0.5), ("2028-09-29", 0.48), ("2028-10-31", 0.53),
        ("2028-11-30", 0.5), ("2028-12-29", 0.48), ("2029-01-31", 0.53),
        ("2029-02-28", 0.47), ("2029-03-28", 0.5), ("2029-04-30", 0.53),
        ("2029-05-31", 0.5), ("2029-06-29", 0.48), ("2029-07-31", 0.53),
        ("2029-08-31", 0.5), ("2029-09-28", 0.47), ("2029-10-31", 100.55),
    ]},
    "AL30": {"ley": "local", "vencimiento": "2030-07-09", "flujos": [
        ("2026-07-09", 8.27), ("2027-01-09", 8.24), ("2027-07-09", 8.21),
        ("2028-01-09", 8.42), ("2028-07-09", 8.35), ("2029-01-09", 8.28),
        ("2029-07-09", 8.21), ("2030-01-09", 8.14), ("2030-07-09", 8.07),
    ]},
    "GD30": {"ley": "NY", "vencimiento": "2030-07-09", "flujos": [
        ("2026-07-09", 8.27), ("2027-01-09", 8.24), ("2027-07-09", 8.21),
        ("2028-01-09", 8.42), ("2028-07-09", 8.35), ("2029-01-09", 8.28),
        ("2029-07-09", 8.21), ("2030-01-09", 8.14), ("2030-07-09", 8.07),
    ]},
    "AL35": {"ley": "local", "vencimiento": "2035-07-09", "flujos": [
        ("2026-07-09", 2.0625), ("2027-01-09", 2.0625), ("2027-07-09", 2.0625),
        ("2028-01-09", 2.375), ("2028-07-09", 2.375), ("2029-01-09", 2.5),
        ("2029-07-09", 2.5), ("2030-01-09", 2.5), ("2030-07-09", 2.5),
        ("2031-01-09", 12.5), ("2031-07-09", 12.25), ("2032-01-09", 12),
        ("2032-07-09", 11.75), ("2033-01-09", 11.5), ("2033-07-09", 11.25),
        ("2034-01-09", 11), ("2034-07-09", 10.75), ("2035-01-09", 10.5),
        ("2035-07-09", 10.25),
    ]},
    "GD35": {"ley": "NY", "vencimiento": "2035-07-09", "flujos": [
        ("2026-07-09", 2.0625), ("2027-01-09", 2.0625), ("2027-07-09", 2.0625),
        ("2028-01-09", 2.375), ("2028-07-09", 2.375), ("2029-01-09", 2.5),
        ("2029-07-09", 2.5), ("2030-01-09", 2.5), ("2030-07-09", 2.5),
        ("2031-01-09", 12.5), ("2031-07-09", 12.25), ("2032-01-09", 12),
        ("2032-07-09", 11.75), ("2033-01-09", 11.5), ("2033-07-09", 11.25),
        ("2034-01-09", 11), ("2034-07-09", 10.75), ("2035-01-09", 10.5),
        ("2035-07-09", 10.25),
    ]},
    "AE38": {"ley": "local", "vencimiento": "2038-01-09", "flujos": [
        ("2026-07-09", 2.5), ("2027-01-09", 2.5), ("2027-07-09", 7.0455),
        ("2028-01-09", 6.9318), ("2028-07-09", 6.8182), ("2029-01-09", 6.7045),
        ("2029-07-09", 6.5909), ("2030-01-09", 6.4773), ("2030-07-09", 6.3636),
        ("2031-01-09", 6.25), ("2031-07-09", 6.1364), ("2032-01-09", 6.0227),
        ("2032-07-09", 5.9091), ("2033-01-09", 5.7955), ("2033-07-09", 5.6818),
        ("2034-01-09", 5.5682), ("2034-07-09", 5.4545), ("2035-01-09", 5.3409),
        ("2035-07-09", 5.2273), ("2036-01-09", 5.1136), ("2036-07-09", 5),
        ("2037-01-09", 4.8864), ("2037-07-09", 4.7727), ("2038-01-09", 4.6591),
    ]},
    "GD38": {"ley": "NY", "vencimiento": "2038-01-09", "flujos": [
        ("2026-07-09", 2.5), ("2027-01-09", 2.5), ("2027-07-09", 7.0455),
        ("2028-01-09", 6.9318), ("2028-07-09", 6.8182), ("2029-01-09", 6.7045),
        ("2029-07-09", 6.5909), ("2030-01-09", 6.4773), ("2030-07-09", 6.3636),
        ("2031-01-09", 6.25), ("2031-07-09", 6.1364), ("2032-01-09", 6.0227),
        ("2032-07-09", 5.9091), ("2033-01-09", 5.7955), ("2033-07-09", 5.6818),
        ("2034-01-09", 5.5682), ("2034-07-09", 5.4545), ("2035-01-09", 5.3409),
        ("2035-07-09", 5.2273), ("2036-01-09", 5.1136), ("2036-07-09", 5),
        ("2037-01-09", 4.8864), ("2037-07-09", 4.7727), ("2038-01-09", 4.6591),
    ]},
    "AL41": {"ley": "local", "vencimiento": "2041-07-09", "flujos": [
        ("2026-07-09", 1.5625), ("2027-01-09", 1.5625), ("2027-07-09", 1.5625),
        ("2028-01-09", 5.1339), ("2028-07-09", 5.0714), ("2029-01-09", 5.0089),
        ("2029-07-09", 4.9464), ("2030-01-09", 5.3996), ("2030-07-09", 5.3125),
        ("2031-01-09", 5.2254), ("2031-07-09", 5.1384), ("2032-01-09", 5.0513),
        ("2032-07-09", 4.9643), ("2033-01-09", 4.8772), ("2033-07-09", 4.7902),
        ("2034-01-09", 4.7031), ("2034-07-09", 4.6161), ("2035-01-09", 4.529),
        ("2035-07-09", 4.442), ("2036-01-09", 4.3549), ("2036-07-09", 4.2679),
        ("2037-01-09", 4.1808), ("2037-07-09", 4.0938), ("2038-01-09", 4.0067),
        ("2038-07-09", 3.9196), ("2039-01-09", 3.8326), ("2039-07-09", 3.7455),
        ("2040-01-09", 3.6585), ("2040-07-09", 3.7455), ("2041-01-09", 3.8326),
        ("2041-07-09", 3.9196),
    ]},
    "GD41": {"ley": "NY", "vencimiento": "2041-07-09", "flujos": [
        ("2026-07-09", 1.5625), ("2027-01-09", 1.5625), ("2027-07-09", 1.5625),
        ("2028-01-09", 5.1339), ("2028-07-09", 5.0714), ("2029-01-09", 5.0089),
        ("2029-07-09", 4.9464), ("2030-01-09", 5.3996), ("2030-07-09", 5.3125),
        ("2031-01-09", 5.2254), ("2031-07-09", 5.1384), ("2032-01-09", 5.0513),
        ("2032-07-09", 4.9643), ("2033-01-09", 4.8772), ("2033-07-09", 4.7902),
        ("2034-01-09", 4.7031), ("2034-07-09", 4.6161), ("2035-01-09", 4.529),
        ("2035-07-09", 4.442), ("2036-01-09", 4.3549), ("2036-07-09", 4.2679),
        ("2037-01-09", 4.1808), ("2037-07-09", 4.0938), ("2038-01-09", 4.0067),
        ("2038-07-09", 3.9196), ("2039-01-09", 3.8326), ("2039-07-09", 3.7455),
        ("2040-01-09", 3.6585), ("2040-07-09", 3.7455), ("2041-01-09", 3.8326),
        ("2041-07-09", 3.9196),
    ]},
    "AO27": {"ley": "local", "vencimiento": "2027-10-30", "flujos": [
        ("2026-03-31", 0.5667), ("2026-04-30", 0.5), ("2026-05-29", 0.4833),
        ("2026-06-30", 0.5167), ("2026-07-31", 0.5), ("2026-08-31", 0.5),
        ("2026-09-30", 0.5), ("2026-10-30", 0.5), ("2026-11-30", 0.5),
        ("2026-12-31", 0.5), ("2027-01-29", 0.5), ("2027-02-26", 0.5),
        ("2027-03-31", 0.5), ("2027-04-30", 0.5), ("2027-05-31", 0.5),
        ("2027-06-30", 0.5), ("2027-07-30", 0.5), ("2027-08-31", 0.5),
        ("2027-09-30", 0.5), ("2027-10-30", 100.5),
    ]},
    "AN29": {"ley": "local", "vencimiento": "2029-11-30", "flujos": [
        ("2026-06-01", 3.0333), ("2026-11-30", 3.25), ("2027-05-31", 3.25),
        ("2027-11-30", 3.25), ("2028-05-30", 3.25), ("2028-11-30", 3.25),
        ("2029-05-30", 3.25), ("2029-11-30", 103.25),
    ]},
    "BPD7": {"ley": "local", "vencimiento": "2027-11-01", "flujos": [
        ("2026-04-30", 2.5), ("2026-11-02", 2.5), ("2027-04-30", 52.5),
        ("2027-11-01", 51.25),
    ]},
    # GD46: cronograma verificado contra SEC EDGAR (Amendment No.2 Prospectus
    # Supplement 424B5, 17-ago-2020, CIK 0000914021, acc-no 0001193125-20-221606):
    # cupon step-up semestral 9-ene/9-jul, 44 amortizaciones iguales (1/44) desde
    # 2025-01-09 hasta el vencimiento. Flujos listados solo desde la primera fecha
    # futura (las 4 cuotas 2025-01-09 a 2026-07-09 ya se pagaron). Duration/TIR
    # resultantes a validar contra bonistas.com/GD46 (MD publicado ~6.61) una vez
    # que el script corra con el precio de mercado real de rendimientos.co; el
    # cronograma de tasas y amortizacion en si esta confirmado contra la fuente SEC.
    "GD46": {"ley": "NY", "vencimiento": "2046-07-09", "flujos": [
        ("2027-01-09", 4.1477), ("2027-07-09", 4.1009), ("2028-01-09", 4.1619),
        ("2028-07-09", 4.1122), ("2029-01-09", 4.3182), ("2029-07-09", 4.2614),
        ("2030-01-09", 4.2045), ("2030-07-09", 4.1477), ("2031-01-09", 4.0909),
        ("2031-07-09", 4.0341), ("2032-01-09", 3.9773), ("2032-07-09", 3.9205),
        ("2033-01-09", 3.8636), ("2033-07-09", 3.8068), ("2034-01-09", 3.75),
        ("2034-07-09", 3.6932), ("2035-01-09", 3.6364), ("2035-07-09", 3.5795),
        ("2036-01-09", 3.5227), ("2036-07-09", 3.4659), ("2037-01-09", 3.4091),
        ("2037-07-09", 3.3523), ("2038-01-09", 3.2955), ("2038-07-09", 3.2386),
        ("2039-01-09", 3.1818), ("2039-07-09", 3.125), ("2040-01-09", 3.0682),
        ("2040-07-09", 3.0114), ("2041-01-09", 2.9545), ("2041-07-09", 2.8977),
        ("2042-01-09", 2.8409), ("2042-07-09", 2.7841), ("2043-01-09", 2.7273),
        ("2043-07-09", 2.6705), ("2044-01-09", 2.6136), ("2044-07-09", 2.5568),
        ("2045-01-09", 2.5), ("2045-07-09", 2.4432), ("2046-01-09", 2.3864),
        ("2046-07-09", 2.3295),
    ]},
}


# ------------------------------------------------------------------
# Bonos ajustados por CER (BONCER/LECER). Cada flujo trae, por cada 100
# de valor nominal ORIGINAL: la fraccion de capital que amortiza en esa
# fecha ("amortizacion"), la tasa de interes anual real aplicada sobre
# el capital residual ("tasa_interes") y la fraccion de anio del periodo
# ("base", 0.5 = semestral). Fuente: rendimientos.co/api/config, seccion
# "bonos_cer". Se listan todos los flujos (pasados y futuros) porque la
# fraccion de capital ya amortizada hasta hoy hace falta para saber
# cuanto capital residual (VNR) queda vigente.
# ------------------------------------------------------------------

CER_FLUJOS = {
    # TZXS7/TZXS8/TZXM8/TZXM9: Boncer cero cupon (bullet, sin interes
    # periodico), confirmados via texto oficial de la resolucion conjunta
    # de emision (Boletin Oficial, Secretaria de Finanzas y Secretaria de
    # Hacienda, Ministerio de Economia): en los 4 casos la clausula dice
    # textualmente "Intereses: cero cupon - a descuento". cer_emision
    # verificado con la misma API oficial datos.gob.ar (serie
    # 94.2_CD_D_0_0_10, fuente BCRA) usada para TZXA7/TZXY7 arriba, tomando
    # el CER de la "Fecha de emision" exacta que fija cada resolucion (NO
    # la fecha de licitacion/llamado, que es distinta):
    #  - TZXS7 (vto. 30/09/2027): emitido por el Art.2 de la Resolucion
    #    Conjunta 16/2026 (27/03/2026, Boletin Oficial 31/03/2026).
    #    Fecha de emision: 31/03/2026. CER=735.3076309157017.
    #  - TZXS8 (vto. 29/09/2028): emitido por el Art.3 de la misma
    #    Resolucion Conjunta 16/2026. Fecha de emision: 31/03/2026 (igual
    #    que TZXS7). CER=735.3076309157017.
    #  - TZXM8 (vto. 31/03/2028): emitido por el Art.7 de la Resolucion
    #    Conjunta 16/2026 ("BONCER MARZO 2028"). Fecha de emision:
    #    01/04/2026. CER=735.9860267378622.
    #  - TZXM9 (vto. 28/03/2029): emitido por el Art.8 de la misma
    #    Resolucion Conjunta 16/2026 ("BONCER MARZO 2029"). Fecha de
    #    emision: 01/04/2026 (igual que TZXM8). CER=735.9860267378622.
    # Nota tecnica: la clausula "Ajuste de Capital" de estas resoluciones
    # especifica que el CER aplicable en rigor es el vigente 10 dias
    # habiles antes de cada fecha (emision y vencimiento del servicio),
    # no el CER del dia exacto. Este script (igual que el resto de
    # CER_FLUJOS ya cargado antes de esta sesion) usa el CER del dia
    # exacto tanto para cer_emision como para cer_hoy en tiempo de
    # ejecucion (_bcra_cer_hoy), sin aplicar ese rezago de 10 dias
    # habiles en ninguno de los dos lados: es una simplificacion
    # consistente con el resto del diccionario, con un sesgo marginal
    # (unos pocos dias de inflacion) y no un error de fuente.
    "TZXS7": {"vencimiento": "2027-09-30", "cer_emision": 735.3076309157017, "flujos": [
        ("2027-09-30", 1.0, 0.0, 0.5),
    ]},
    "TZXS8": {"vencimiento": "2028-09-29", "cer_emision": 735.3076309157017, "flujos": [
        ("2028-09-29", 1.0, 0.0, 0.5),
    ]},
    "TZXM8": {"vencimiento": "2028-03-31", "cer_emision": 735.9860267378622, "flujos": [
        ("2028-03-31", 1.0, 0.0, 0.5),
    ]},
    "TZXM9": {"vencimiento": "2029-03-28", "cer_emision": 735.9860267378622, "flujos": [
        ("2029-03-28", 1.0, 0.0, 0.5),
    ]},
    # TZXO7/TZXD8: Boncer cero cupon (bullet), confirmados via texto
    # oficial (Resolucion Conjunta 36/2026, 26/06/2026, Boletin Oficial
    # 01/07/2026, Art.2 y Art.3: "Intereses: cero cupon - a descuento"),
    # Fecha de emision: 30/06/2026 para ambos. cer_emision = CER del
    # 30/06/2026 = 799.20332676852, provisto directamente por el usuario
    # (tabla oficial BCRA) y verificado contra la misma API oficial
    # datos.gob.ar usada para el resto del diccionario (15/06/2026
    # coincide exactamente: 790.9415888555 vs 790.94158885550).
    "TZXO7": {"vencimiento": "2027-10-29", "cer_emision": 799.20332676852, "flujos": [
        ("2027-10-29", 1.0, 0.0, 0.5),
    ]},
    "TZXD8": {"vencimiento": "2028-12-15", "cer_emision": 799.20332676852, "flujos": [
        ("2028-12-15", 1.0, 0.0, 0.5),
    ]},
    # NOTA (chequeo automatico 2026-07-20, tarea #58): CUAP esta bien
    # codificado aqui (parametros confirmados por el usuario, ver abajo)
    # pero NO aparece hoy en "bonos_cer" del live_data.json publicado.
    # Causa confirmada: get_bonos_cer() itera sobre data["data"] de
    # https://rendimientos.co/api/cer-precios y hace
    # "info = CER_FLUJOS.get(sym); if not info or precio is None: continue"
    # -o sea, si ese feed externo no trae un item con symbol=="CUAP" (o
    # lo trae sin precio), la entrada se descarta en silencio sin error
    # ni log, exactamente igual que si el ticker no existiera en este
    # diccionario. No se pudo confirmar el contenido exacto de ese feed
    # desde este sandbox (dominio rendimientos.co bloqueado por el
    # allowlist de red del entorno de chequeo automatico), pero la misma
    # ausencia se repite para los 8 tickers CER agregados en la tarea 3.2
    # (TZXA7/TZXY7/TZXS7/TZXM8/TZXS8/TZXM9/TZXO7/TZXD8, todos tambien
    # bien codificados y tambien ausentes de "bonos_cer" hoy), lo que
    # descarta un problema puntual de CUAP y confirma que es un rezago de
    # cobertura del feed externo (probablemente no trackea aun estos
    # bonos/reaperturas mas recientes), no un bug de flujos ni del filtro
    # de duration<0.20/DTM<45 (ambos habrian pasado ese filtro sin
    # problema segun los parametros de arriba). Resolucion: esperar a que
    # rendimientos.co sume estos symbols, o -si se decide reemplazar la
    # fuente- migrar a otra API con estos tickers. NO se modifican los
    # parametros financieros de CUAP en este chequeo (eso corresponde a
    # la tarea "revision-tir-cuap" con Andres).
    # CUAP (Cuasipar en pesos, reestructuracion 2005, vto. 31/12/2045):
    # NO es bullet. Confirmado por el usuario (actuario, en base a IAMC):
    # el valor residual (VNR) esta al 100% del nominal (todavia no
    # empezo a amortizar capital; la primera cuota es recien en 2036),
    # por lo que NO hace falta una entrada en CER_VNR_CONOCIDO: al no
    # haber flujos con amortizacion en el pasado, get_bonos_cer() ya
    # calcula vnr_hoy=1.0 por default. Terminos (portfoliopersonal.com/
    # files/bonos/CUAP.pdf, confirmados por informe IAMC del 17/07/2026
    # -VT=75125.7-, consistente con un VNR=100% y CER actual/CER emision
    # ~549x mas la renta corrida): interes real 3.31% TNA (base 30/360)
    # semestral el 30/6 y 31/12 desde el 30/06/2014 (antes de esa fecha
    # el interes se capitalizaba, sin efecto sobre el VNR remanente), y
    # amortizacion en 20 cuotas semestrales iguales (1/20 c/u, 30/6 y
    # 31/12) desde el 30/06/2036 hasta el 31/12/2045. cer_emision = CER
    # del 31/12/2003 (fecha de emision), verificado con la API oficial
    # datos.gob.ar (serie 94.2_CD_D_0_0_10) = 1.4568. Se listan solo los
    # flujos futuros (proximo pago 2026-12-31 en adelante): igual
    # criterio que DICP/PARP ("Flujos pasados omitidos"), valido porque
    # no hay amortizacion pendiente de contar antes de hoy.
    "CUAP": {"vencimiento": "2045-12-31", "cer_emision": 1.4568, "flujos": [
        ("2026-12-31", 0.0, 0.0331, 0.5), ("2027-06-30", 0.0, 0.0331, 0.5),
        ("2027-12-31", 0.0, 0.0331, 0.5), ("2028-06-30", 0.0, 0.0331, 0.5),
        ("2028-12-31", 0.0, 0.0331, 0.5), ("2029-06-30", 0.0, 0.0331, 0.5),
        ("2029-12-31", 0.0, 0.0331, 0.5), ("2030-06-30", 0.0, 0.0331, 0.5),
        ("2030-12-31", 0.0, 0.0331, 0.5), ("2031-06-30", 0.0, 0.0331, 0.5),
        ("2031-12-31", 0.0, 0.0331, 0.5), ("2032-06-30", 0.0, 0.0331, 0.5),
        ("2032-12-31", 0.0, 0.0331, 0.5), ("2033-06-30", 0.0, 0.0331, 0.5),
        ("2033-12-31", 0.0, 0.0331, 0.5), ("2034-06-30", 0.0, 0.0331, 0.5),
        ("2034-12-31", 0.0, 0.0331, 0.5), ("2035-06-30", 0.0, 0.0331, 0.5),
        ("2035-12-31", 0.0, 0.0331, 0.5), ("2036-06-30", 0.05, 0.0331, 0.5),
        ("2036-12-31", 0.05, 0.0331, 0.5), ("2037-06-30", 0.05, 0.0331, 0.5),
        ("2037-12-31", 0.05, 0.0331, 0.5), ("2038-06-30", 0.05, 0.0331, 0.5),
        ("2038-12-31", 0.05, 0.0331, 0.5), ("2039-06-30", 0.05, 0.0331, 0.5),
        ("2039-12-31", 0.05, 0.0331, 0.5), ("2040-06-30", 0.05, 0.0331, 0.5),
        ("2040-12-31", 0.05, 0.0331, 0.5), ("2041-06-30", 0.05, 0.0331, 0.5),
        ("2041-12-31", 0.05, 0.0331, 0.5), ("2042-06-30", 0.05, 0.0331, 0.5),
        ("2042-12-31", 0.05, 0.0331, 0.5), ("2043-06-30", 0.05, 0.0331, 0.5),
        ("2043-12-31", 0.05, 0.0331, 0.5), ("2044-06-30", 0.05, 0.0331, 0.5),
        ("2044-12-31", 0.05, 0.0331, 0.5), ("2045-06-30", 0.05, 0.0331, 0.5),
        ("2045-12-31", 0.05, 0.0331, 0.5),
    ]},
    "TZXO6": {"vencimiento": "2026-10-30", "cer_emision": 480.1526, "flujos": [
        ("2026-10-30", 1.0, 0.0, 0.5),
    ]},
    "TX26": {"vencimiento": "2026-11-09", "cer_emision": 22.544, "flujos": [
        ("2021-05-10", 0.0, 0.02, 0.5), ("2021-11-09", 0.0, 0.02, 0.5),
        ("2022-05-09", 0.0, 0.02, 0.5), ("2022-11-09", 0.0, 0.02, 0.5),
        ("2023-05-09", 0.0, 0.02, 0.5), ("2023-11-09", 0.0, 0.02, 0.5),
        ("2024-05-09", 0.0, 0.02, 0.5), ("2024-11-11", 0.2, 0.02, 0.5),
        ("2025-05-09", 0.2, 0.02, 0.5), ("2025-11-10", 0.2, 0.02, 0.5),
        ("2026-05-11", 0.2, 0.02, 0.5), ("2026-11-09", 0.2, 0.02, 0.5),
    ]},
    "TZXD6": {"vencimiento": "2026-12-15", "cer_emision": 271.0476, "flujos": [
        ("2026-12-15", 1.0, 0.0, 0.5),
    ]},
    "TZXM7": {"vencimiento": "2027-03-31", "cer_emision": 361.3176, "flujos": [
        ("2027-03-31", 1.0, 0.0, 0.5),
    ]},
    # TZXA7/TZXY7: Boncer cero cupon (bullet, sin interes periodico),
    # ajuste CER +0.00% (sin spread), emitidos en 2025. cer_emision
    # verificado contra la API oficial del dataset "CER, UVA y UVI" de
    # datos.gob.ar (fuente primaria: BCRA), serie 94.2_CD_D_0_0_10,
    # tomando el valor de CER publicado en la fecha de emision de cada
    # bono (fecha "Fecha Emision" confirmada via
    # bonistas.com/bono-cotizacion-rendimiento-precio-hoy/{TICKER}):
    # TZXA7 emitido 28/11/2025 (CER=659.6788956665248), TZXY7 emitido
    # 15/12/2025 (CER=668.2343291710602). TZXS7 (mismo vencimiento
    # 2027-09-30) NO se carga: tiene ajuste "CER +2.16%" (spread real,
    # no bullet puro), ver nota en el bloque PENDIENTE mas arriba.
    "TZXA7": {"vencimiento": "2027-04-30", "cer_emision": 659.6788956665248, "flujos": [
        ("2027-04-30", 1.0, 0.0, 0.5),
    ]},
    "TZXY7": {"vencimiento": "2027-05-31", "cer_emision": 668.2343291710602, "flujos": [
        ("2027-05-31", 1.0, 0.0, 0.5),
    ]},
    "TZX27": {"vencimiento": "2027-06-30", "cer_emision": 200.388, "flujos": [
        ("2027-06-30", 1.0, 0.0, 0.5),
    ]},
    "TZXD7": {"vencimiento": "2027-12-15", "cer_emision": 271.0476, "flujos": [
        ("2027-12-15", 1.0, 0.0, 0.5),
    ]},
    "TZX28": {"vencimiento": "2028-06-30", "cer_emision": 200.388, "flujos": [
        ("2028-06-30", 1.0, 0.0, 0.5),
    ]},
    "TX28": {"vencimiento": "2028-11-09", "cer_emision": 22.544, "flujos": [
        ("2021-05-10", 0.0, 0.0225, 0.5), ("2021-11-09", 0.0, 0.0225, 0.5),
        ("2022-05-09", 0.0, 0.0225, 0.5), ("2022-11-09", 0.0, 0.0225, 0.5),
        ("2023-05-09", 0.0, 0.0225, 0.5), ("2023-11-09", 0.0, 0.0225, 0.5),
        ("2024-05-09", 0.1, 0.0225, 0.5), ("2024-11-11", 0.1, 0.0225, 0.5),
        ("2025-05-09", 0.1, 0.0225, 0.5), ("2025-11-10", 0.1, 0.0225, 0.5),
        ("2026-05-11", 0.1, 0.0225, 0.5), ("2026-11-09", 0.1, 0.0225, 0.5),
        ("2027-05-10", 0.1, 0.0225, 0.5), ("2027-11-09", 0.1, 0.0225, 0.5),
        ("2028-05-09", 0.1, 0.0225, 0.5), ("2028-11-09", 0.1, 0.0225, 0.5),
    ]},
    "DICP": {"vencimiento": "2034-01-02", "cer_emision": 1.4551, "flujos": [
        # Flujos pasados omitidos (bono emitido en 2004); el VNR vigente
        # a hoy se toma directamente como dato (ver DICP_VNR_HOY abajo).
        ("2026-12-31", 0.05, 0.0583, 0.5), ("2027-06-30", 0.05, 0.0583, 0.5),
        ("2027-12-31", 0.05, 0.0583, 0.5), ("2028-06-30", 0.05, 0.0583, 0.5),
        ("2029-01-02", 0.05, 0.0583, 0.5), ("2029-07-02", 0.05, 0.0583, 0.5),
        ("2029-12-31", 0.05, 0.0583, 0.5), ("2030-07-01", 0.05, 0.0583, 0.5),
        ("2030-12-31", 0.05, 0.0583, 0.5), ("2031-06-30", 0.05, 0.0583, 0.5),
        ("2031-12-31", 0.05, 0.0583, 0.5), ("2032-06-30", 0.05, 0.0583, 0.5),
        ("2032-12-31", 0.05, 0.0583, 0.5), ("2033-06-30", 0.05, 0.0583, 0.5),
        ("2034-01-02", 0.05, 0.0583, 0.5),
    ]},
    "PARP": {"vencimiento": "2038-12-31", "cer_emision": 1.4551, "flujos": [
        ("2026-09-30", 0.0, 0.0177, 0.5), ("2027-03-31", 0.0, 0.0177, 0.5),
        ("2027-09-30", 0.0, 0.0177, 0.5), ("2028-03-31", 0.0, 0.0177, 0.5),
        ("2028-10-02", 0.0, 0.0177, 0.5), ("2029-04-03", 0.0, 0.0177, 0.5),
        ("2029-10-01", 0.05, 0.0248, 0.5), ("2030-04-01", 0.05, 0.0248, 0.5),
        ("2030-09-30", 0.05, 0.0248, 0.5), ("2031-03-31", 0.05, 0.0248, 0.5),
        ("2031-09-30", 0.05, 0.0248, 0.5), ("2032-03-31", 0.05, 0.0248, 0.5),
        ("2032-09-30", 0.05, 0.0248, 0.5), ("2033-03-31", 0.05, 0.0248, 0.5),
        ("2033-09-30", 0.05, 0.0248, 0.5), ("2034-03-31", 0.05, 0.0248, 0.5),
        ("2034-10-02", 0.05, 0.0248, 0.5), ("2035-04-02", 0.05, 0.0248, 0.5),
        ("2035-10-01", 0.05, 0.0248, 0.5), ("2036-03-31", 0.05, 0.0248, 0.5),
        ("2036-09-30", 0.05, 0.0248, 0.5), ("2037-03-31", 0.05, 0.0248, 0.5),
        ("2037-09-30", 0.05, 0.0248, 0.5), ("2038-03-31", 0.05, 0.0248, 0.5),
        ("2038-09-30", 0.05, 0.0248, 0.5), ("2038-12-31", 0.05, 0.0248, 0.5),
    ]},
    "X31L6": {"vencimiento": "2026-07-31", "cer_emision": 685.5506, "flujos": [
        ("2026-07-31", 1.0, 0.0, 0.5),
    ]},
    "X30S6": {"vencimiento": "2026-09-30", "cer_emision": 714.9849, "flujos": [
        ("2026-09-30", 1.0, 0.0, 0.5),
    ]},
    "X30N6": {"vencimiento": "2026-11-30", "cer_emision": 659.6789, "flujos": [
        ("2026-11-30", 1.0, 0.0, 0.5),
    ]},
    "TX31": {"vencimiento": "2031-12-01", "cer_emision": 46.913, "flujos": [
        ("2026-11-30", 0.0, 0.025, 0.5), ("2027-05-31", 0.1, 0.025, 0.5),
        ("2027-11-30", 0.1, 0.025, 0.5), ("2028-05-30", 0.1, 0.025, 0.5),
        ("2028-11-30", 0.1, 0.025, 0.5), ("2029-05-30", 0.1, 0.025, 0.5),
        ("2029-11-30", 0.1, 0.025, 0.5), ("2030-05-30", 0.1, 0.025, 0.5),
        ("2030-12-02", 0.1, 0.025, 0.5), ("2031-05-30", 0.1, 0.025, 0.5),
        ("2031-12-01", 0.1, 0.025, 0.5),
    ]},
}

# VNR (capital residual, fraccion de 1) ya conocido a la fecha en que se
# releva esta tabla (18/07/2026) para los bonos cuyos flujos pasados no
# se transcribieron completos (emitidos hace muchos anios). Sirve como
# capital de partida para descontar los flujos futuros listados arriba.
CER_VNR_CONOCIDO = {
    "DICP": (0.75, "2026-07-18"),
    "PARP": (1.0, "2026-07-18"),
}

# TY30P: BONCAP ley Argentina, tasa fija 29.50% TNA (14.75% semestral),
# 8 pagos remanentes de cupon fijo, capital 100% en el ultimo pago
# (2030-05-30). A diferencia de las LECAPs/BONCAPs de LECAP_TERMS (pago
# unico al vencimiento), TY30P paga cupon periodico, por lo que NO se
# puede representar con el campo "pago_final" (subestimaria la duration
# al ignorar los cupones intermedios). Se usa el mismo formato de
# ON_FLUJOS/SOBERANOS_FLUJOS (fraccion del nominal, 1.0 = 100% capital)
# y se resuelve TIR/duration via _solve_ytm/_macaulay_duration, igual
# que get_ons_usd(). Fuente: bonistas.com/api/bond/TY30P (campo flow).
# NOTA: existe una variante TY30P_PUT con opcion de venta incorporada que
# cambia la TIR efectiva (bonistas.com muestra TIR 27.2% sin PUT vs 18.2%
# con PUT). Esta carga es SOLO TY30P sin la opcionalidad del put; si la
# TIR calculada por este script difiere fuertemente de la publicada con
# PUT, es la discrepancia esperada por no modelar esa opcionalidad, no un
# error de flujos.
# ------------------------------------------------------------------
# NOTA: tareas 3.3 (TAMAR) y 3.4 (Duales/Dolar Linked) implementadas.
# Ver get_bonos_tamar()/get_bonos_duales_dolarlinked() y TAMAR_BONOS/
# DUALES_DOLARLINKED_BONOS mas abajo en este archivo: usan TIR/duration
# publicados por bonistas.com (via _fetch_bonistas_bond_info), ya que no
# hay fuente propia de tasa TAMAR historica integrada.
# ------------------------------------------------------------------

BONOS_PESOS_CUPON_FLUJOS = {
    "TY30P": {"vencimiento": "2030-05-30", "flujos": [
        ("2026-11-30", 0.1475), ("2027-05-31", 0.1475), ("2027-11-30", 0.1475),
        ("2028-05-30", 0.1475), ("2028-11-30", 0.1475), ("2029-05-30", 0.1475),
        ("2029-11-30", 0.1475), ("2030-05-30", 1.1475),
    ]},
}


LECAP_TERMS = {
    "S31L6": {"nombre": "LECAP Julio 31", "pago_final": 117.677, "fecha_vencimiento": "2026-07-31"},
    "S14G6": {"nombre": "LECAP Agosto 14", "pago_final": 108.03, "fecha_vencimiento": "2026-08-14"},
    "S31G6": {"nombre": "LECAP Agosto 31", "pago_final": 127.064, "fecha_vencimiento": "2026-08-31"},
    "S30S6": {"nombre": "LECAP Sept 30", "pago_final": 117.536, "fecha_vencimiento": "2026-09-30"},
    "S30O6": {"nombre": "LECAP Oct 30", "pago_final": 135.278, "fecha_vencimiento": "2026-10-30"},
    "S13N6": {"nombre": "LECAP Nov 13", "pago_final": 109.65, "fecha_vencimiento": "2026-11-13"},
    "S30N6": {"nombre": "LECAP Nov 30", "pago_final": 129.888, "fecha_vencimiento": "2026-11-30"},
    "T15E7": {"nombre": "BONCAP Ene 15/27", "pago_final": 161.104, "fecha_vencimiento": "2027-01-15"},
    "T30A7": {"nombre": "BONCAP Abr 30/27", "pago_final": 157.341, "fecha_vencimiento": "2027-04-30"},
    "T31Y7": {"nombre": "BONCAP May 31/27", "pago_final": 151.563, "fecha_vencimiento": "2027-05-31"},
    "T30J7": {"nombre": "BONCAP Jun 30/27", "pago_final": 156.037, "fecha_vencimiento": "2027-06-30"},
    # TO26: bono ley Argentina, tasa fija, un unico pago remanente
    # (7.75% cupon + 100% capital) el 2026-10-19. Fuente: bonistas.com/TO26 (campo flow).
    # NOTA (corregido 2026-07-20, tarea #58): TO26 y TY30P no aparecian en
    # "bonos_pesos" porque https://rendimientos.co/api/lecaps no los
    # cubre (11 tickers fijos). get_bonos_pesos() se corrigio para
    # completar con data912.com/live/arg_bonds como fuente de precio
    # complementaria.
    "TO26": {"nombre": "Bonte 2026", "pago_final": 107.75, "fecha_vencimiento": "2026-10-19"},
}



ON_FLUJOS = {
    "AERB": {"ticker_d912": "AERBD", "emisor": "Aeropuertos Argentina 2000", "vencimiento": "2026-12-15",
              "flujos": [("2026-12-15", 1.02637)]},
    "ARC1": {"ticker_d912": "ARC1D", "emisor": "Aeropuertos Argentina 2000", "vencimiento": "2031-08-01",
              "flujos": [("2026-08-03", 0.0296), ("2026-11-02", 0.0205), ("2027-02-01", 0.0683), ("2027-05-03", 0.0475), ("2027-08-02", 0.0612), ("2027-11-01", 0.0509), ("2028-02-01", 0.0739), ("2028-05-02", 0.0515), ("2028-08-01", 0.0154), ("2028-11-01", 0.0662), ("2029-02-01", 0.0856), ("2029-05-02", 0.0627), ("2029-08-01", 0.0777), ("2029-11-01", 0.0665), ("2030-02-01", 0.0832), ("2030-05-02", 0.0605), ("2030-08-01", 0.0755), ("2030-11-01", 0.0643), ("2031-02-03", 0.0851), ("2031-05-01", 0.0621), ("2031-08-01", 0.0466)]},
    "AFCI": {"ticker_d912": "AFCID", "emisor": "Banco Comafi", "vencimiento": "2026-11-09",
              "flujos": [("2026-11-09", 1.0331)]},
    "BACG": {"ticker_d912": "BACGD", "emisor": "Banco Macro", "vencimiento": "2029-06-25",
              "flujos": [("2026-12-23", 0.04), ("2027-06-23", 0.04), ("2027-12-23", 0.04), ("2028-06-23", 0.04), ("2028-12-26", 0.04), ("2029-06-25", 1.04)]},
    "BACH": {"ticker_d912": "BACHD", "emisor": "Banco Macro", "vencimiento": "2031-01-28",
              "flujos": [("2026-07-28", 0.04), ("2027-01-28", 0.04), ("2027-07-28", 0.04), ("2028-01-28", 0.04), ("2028-07-28", 0.04), ("2029-01-29", 0.04), ("2029-07-30", 0.04), ("2030-01-28", 0.04), ("2030-07-29", 0.04), ("2031-01-28", 1.04)]},
    "BF37": {"ticker_d912": "BF37D", "emisor": "Banco BBVA Argentina", "vencimiento": "2026-08-24",
              "flujos": [("2026-08-24", 1.030082)]},
    "BF40": {"ticker_d912": "BF40D", "emisor": "Banco BBVA Argentina", "vencimiento": "2027-08-27",
              "flujos": [("2026-08-27", 0.024795), ("2027-03-01", 0.025205), ("2027-08-27", 1.024795)]},
    "BYCH": {"ticker_d912": "BYCHD", "emisor": "Banco Galicia", "vencimiento": "2028-10-10",
              "flujos": [("2026-10-13", 0.0388), ("2027-04-12", 0.0388), ("2027-10-11", 0.0388), ("2028-04-10", 0.0388), ("2028-10-10", 1.0388)]},
    "BYCV": {"ticker_d912": "BYCVD", "emisor": "Banco Galicia", "vencimiento": "2026-08-31",
              "flujos": [("2026-08-31", 1.033904)]},
    "BYCW": {"ticker_d912": "BYCWD", "emisor": "Banco Galicia", "vencimiento": "2026-11-30",
              "flujos": [("2026-11-30", 1.0329)]},
    "CP36O": {"ticker_d912": "CP36D", "emisor": "Compania General de Combustibles", "vencimiento": "2027-10-11",
              "flujos": [("2026-10-13", 0.0326), ("2027-04-12", 0.0324), ("2027-10-11", 1.0328)]},
    "CP37": {"ticker_d912": "CP37D", "emisor": "Compania General de Combustibles", "vencimiento": "2027-03-10",
              "flujos": [("2026-09-10", 0.0176), ("2026-12-10", 0.0175), ("2027-03-10", 1.0173)]},
    "CS44": {"ticker_d912": "CS44D", "emisor": "Cresud", "vencimiento": "2027-01-18",
              "flujos": [("2027-01-18", 1.030247)]},
    "CS47": {"ticker_d912": "CS47D", "emisor": "Cresud", "vencimiento": "2028-11-15",
              "flujos": [("2026-11-16", 0.0353), ("2027-05-17", 0.0347), ("2027-11-15", 0.0353), ("2028-05-15", 0.0349), ("2028-11-15", 1.0353)]},
    "CS50": {"ticker_d912": "CS50D", "emisor": "Cresud", "vencimiento": "2029-03-12",
              "flujos": [("2026-09-10", 0.0544), ("2027-03-10", 0.036), ("2027-09-10", 0.0365), ("2028-03-10", 0.0362), ("2028-09-11", 0.0365), ("2029-03-12", 1.036)]},
    "CS51": {"ticker_d912": "CS51D", "emisor": "Cresud", "vencimiento": "2027-01-20",
              "flujos": [("2026-07-20", 0.0285), ("2027-01-20", 1.029)]},
    "FO4A": {"ticker_d912": "FO4AD", "emisor": "Fideicomiso Financiero", "vencimiento": "2027-07-20",
              "flujos": [("2026-07-20", 0.019696), ("2026-10-20", 0.019912), ("2027-01-20", 0.019912), ("2027-04-20", 0.019479), ("2027-07-20", 1.019696)]},
    "GN47": {"ticker_d912": "GN47D", "emisor": "Genneia", "vencimiento": "2028-10-17",
              "flujos": [("2026-10-19", 0.0301), ("2027-04-19", 0.0299), ("2027-10-18", 0.0301), ("2028-04-17", 0.0301), ("2028-10-17", 1.0301)]},
    "GN49": {"ticker_d912": "GN49D", "emisor": "Genneia", "vencimiento": "2033-12-02",
              "flujos": [("2026-12-02", 0.03875), ("2027-06-02", 0.03875), ("2027-12-02", 0.03875), ("2028-06-02", 0.03875), ("2028-12-04", 0.03875), ("2029-06-04", 0.03875), ("2029-12-03", 0.03875), ("2030-06-03", 0.03875), ("2030-12-02", 0.03875), ("2031-06-02", 0.03875), ("2031-12-02", 0.36875), ("2032-06-02", 0.025962), ("2032-12-02", 0.355962), ("2033-06-02", 0.013175), ("2033-12-02", 0.353175)]},
    "HBCA": {"ticker_d912": "HBCAD", "emisor": "Banco Hipotecario", "vencimiento": "2026-08-24",
              "flujos": [("2026-08-24", 1.0351)]},
    "HBC": {"ticker_d912": "HBCDD", "emisor": "Banco Hipotecario", "vencimiento": "2026-11-23",
              "flujos": [("2026-11-23", 1.03074)]},
    "HJCF": {"ticker_d912": "HJCFD", "emisor": "John Deere Credit Compania Financiera", "vencimiento": "2026-10-21",
              "flujos": [("2026-10-21", 1.0251)]},
    "IRCN": {"ticker_d912": "IRCND", "emisor": "IRSA", "vencimiento": "2027-10-25",
              "flujos": [("2026-07-23", 0.0285), ("2027-01-25", 0.029), ("2027-07-23", 0.0285), ("2027-10-25", 1.0145)]},
    "IRCO": {"ticker_d912": "IRCOD", "emisor": "IRSA", "vencimiento": "2029-10-23",
              "flujos": [("2026-07-23", 0.036), ("2027-01-25", 0.0365), ("2027-07-23", 0.036), ("2028-01-24", 0.0365), ("2028-07-24", 0.0362), ("2029-01-23", 0.0365), ("2029-07-23", 0.036), ("2029-10-23", 1.0183)]},
    "LDCG": {"ticker_d912": "LDCGD", "emisor": "Ledesma", "vencimiento": "2027-10-04",
              "flujos": [("2026-08-04", 0.0176), ("2026-11-04", 0.0176), ("2027-02-04", 0.0176), ("2027-05-04", 0.0171), ("2027-08-04", 0.0176), ("2027-10-04", 1.0117)]},
    "LMS7": {"ticker_d912": "LMS7D", "emisor": "Aluar", "vencimiento": "2028-10-13",
              "flujos": [("2026-10-13", 0.0967), ("2027-01-12", 0.0952), ("2027-04-12", 0.0935), ("2027-07-12", 0.0922), ("2027-10-13", 0.0908), ("2028-01-12", 0.0893), ("2028-04-12", 0.0877), ("2028-07-12", 0.0863), ("2028-10-13", 0.0852)]},
    "LMS8": {"ticker_d912": "LMS8D", "emisor": "Aluar", "vencimiento": "2027-03-22",
              "flujos": [("2026-09-21", 0.261979), ("2026-12-21", 0.257899), ("2027-03-22", 0.25395)]},
    "LOC5": {"ticker_d912": "LOC5D", "emisor": "Loma Negra", "vencimiento": "2027-07-26",
              "flujos": [("2026-07-24", 0.039671), ("2027-01-25", 0.040329), ("2027-07-26", 1.04011)]},
    "MGCN": {"ticker_d912": "MGCND", "emisor": "Pampa Energia", "vencimiento": "2028-10-04",
              "flujos": [("2026-10-05", 0.0288), ("2027-04-05", 0.0287), ("2027-10-04", 0.0288), ("2028-04-04", 0.0288), ("2028-10-04", 1.0288)]},
    "MGCO": {"ticker_d912": "MGCOD", "emisor": "Pampa Energia", "vencimiento": "2034-12-18",
              "flujos": [("2026-12-16", 0.039375), ("2027-06-16", 0.039375), ("2027-12-16", 0.039375), ("2028-06-16", 0.039375), ("2028-12-18", 0.039375), ("2029-06-18", 0.039375), ("2029-12-17", 0.039375), ("2030-06-18", 0.039375), ("2030-12-16", 0.039375), ("2031-06-16", 0.039375), ("2031-12-16", 0.039375), ("2032-06-16", 0.039375), ("2032-12-16", 0.039375), ("2033-06-16", 0.039375), ("2033-12-16", 0.039375), ("2034-06-16", 0.039375), ("2034-12-18", 1.039375)]},
    "MGCR": {"ticker_d912": "MGCRD", "emisor": "Pampa Energia", "vencimiento": "2037-11-16",
              "flujos": [("2026-11-16", 0.03875), ("2027-05-14", 0.03875), ("2027-11-15", 0.03875), ("2028-05-15", 0.03875), ("2028-11-14", 0.03875), ("2029-05-14", 0.03875), ("2029-11-14", 0.03875), ("2030-05-14", 0.03875), ("2030-11-14", 0.03875), ("2031-05-14", 0.03875), ("2031-11-14", 0.03875), ("2032-05-14", 0.03875), ("2032-11-15", 0.03875), ("2033-05-16", 0.03875), ("2033-11-14", 0.03875), ("2034-05-15", 0.03875), ("2034-11-14", 0.03875), ("2035-05-14", 0.03875), ("2035-11-14", 0.03875), ("2036-05-14", 0.03875), ("2036-11-14", 0.03875), ("2037-05-14", 0.03875), ("2037-11-16", 1.03875)]},
    "OLC6": {"ticker_d912": "OLC6D", "emisor": "Oleoductos del Valle (Oldelval)", "vencimiento": "2029-06-05",
              "flujos": [("2026-12-09", 0.037603), ("2027-06-07", 0.037397), ("2027-12-06", 0.037603), ("2028-06-05", 0.037603), ("2028-12-05", 0.037603), ("2029-06-05", 1.037397)]},
    "PLC1": {"ticker_d912": "PLC1D", "emisor": "Pluspetrol", "vencimiento": "2028-01-27",
              "flujos": [("2026-10-27", 0.0301), ("2027-04-27", 0.0299), ("2027-10-27", 0.0301), ("2028-01-27", 1.0151)]},
    "PN35": {"ticker_d912": "PN35D", "emisor": "Pan American Energy", "vencimiento": "2029-09-27",
              "flujos": [("2026-09-28", 0.0353), ("2027-03-29", 0.0347), ("2027-09-27", 0.0353), ("2028-03-27", 0.0349), ("2028-09-27", 0.0353), ("2029-03-27", 0.0347), ("2029-09-27", 1.0353)]},
    "PN36": {"ticker_d912": "PN36D", "emisor": "Pan American Energy", "vencimiento": "2031-11-13",
              "flujos": [("2026-11-13", 0.0365), ("2027-05-13", 0.036), ("2027-11-15", 0.0365), ("2028-05-15", 0.0362), ("2028-11-13", 0.0365), ("2029-05-14", 0.036), ("2029-11-13", 0.0365), ("2030-05-13", 0.036), ("2030-11-13", 0.0365), ("2031-05-13", 0.036), ("2031-11-13", 1.0365)]},
    "RC2C": {"ticker_d912": "RC2CD", "emisor": "Arcor", "vencimiento": "2026-10-06",
              "flujos": [("2026-10-06", 1.029581)]},
    "T662": {"ticker_d912": "T662D", "emisor": "Tarjeta Naranja", "vencimiento": "2026-08-31",
              "flujos": [("2026-08-31", 1.017274)]},
    "TSC4": {"ticker_d912": "TSC4D", "emisor": "Transportadora de Gas del Sur (TGS)", "vencimiento": "2035-11-20",
              "flujos": [("2026-11-23", 0.03875), ("2027-05-20", 0.03875), ("2027-11-22", 0.03875), ("2028-05-22", 0.03875), ("2028-11-21", 0.03875), ("2029-05-21", 0.03875), ("2029-11-21", 0.03875), ("2030-05-20", 0.03875), ("2030-11-21", 0.03875), ("2031-05-20", 0.03875), ("2031-11-20", 0.03875), ("2032-05-20", 0.03875), ("2032-11-22", 0.03875), ("2033-05-20", 0.03875), ("2033-11-21", 0.03875), ("2034-05-22", 0.03875), ("2034-11-20", 0.03875), ("2035-05-21", 0.03875), ("2035-11-20", 1.03875)]},
    "TTC8": {"ticker_d912": "TTC8D", "emisor": "Tecpetrol", "vencimiento": "2027-10-25",
              "flujos": [("2026-10-26", 0.025068), ("2027-04-26", 0.024932), ("2027-10-25", 1.025205)]},
    "TTCB": {"ticker_d912": "TTCBD", "emisor": "Tecpetrol", "vencimiento": "2027-10-18",
              "flujos": [("2026-10-16", 0.032589), ("2027-04-16", 0.032411), ("2027-10-18", 1.032945)]},
    "TTC": {"ticker_d912": "TTCDD", "emisor": "Tecpetrol", "vencimiento": "2030-11-04",
              "flujos": [("2026-11-03", 0.038125), ("2027-05-03", 0.038125), ("2027-11-03", 0.038125), ("2028-05-03", 0.038125), ("2028-11-03", 0.038125), ("2029-05-03", 0.038125), ("2029-11-05", 0.038125), ("2030-05-03", 0.038125), ("2030-11-04", 1.038125)]},
    "VBC1": {"ticker_d912": "VBC1D", "emisor": "Banco de Valores", "vencimiento": "2027-03-11",
              "flujos": [("2026-09-11", 0.037808), ("2027-03-11", 1.037192)]},
    "VSCO": {"ticker_d912": "VSCOD", "emisor": "Vista Energy", "vencimiento": "2027-03-08",
              "flujos": [("2026-09-07", 0.032767), ("2027-03-08", 1.032589)]},
    "VSCP": {"ticker_d912": "VSCPD", "emisor": "Vista Energy", "vencimiento": "2029-05-03",
              "flujos": [("2026-11-03", 0.040329), ("2027-05-03", 0.039671), ("2027-11-03", 0.290329), ("2028-05-03", 0.279918), ("2028-11-03", 0.270164), ("2029-05-03", 0.259918)]},
    "VSCT": {"ticker_d912": "VSCTD", "emisor": "Vista Energy", "vencimiento": "2035-12-10",
              "flujos": [("2026-12-10", 0.038125), ("2027-06-10", 0.038125), ("2027-12-10", 0.038125), ("2028-06-12", 0.038125), ("2028-12-11", 0.038125), ("2029-06-11", 0.038125), ("2029-12-10", 0.038125), ("2030-06-10", 0.038125), ("2030-12-10", 0.038125), ("2031-06-10", 0.038125), ("2031-12-10", 0.038125), ("2032-06-10", 0.038125), ("2032-12-10", 0.038125), ("2033-06-10", 0.038125), ("2033-12-12", 0.368125), ("2034-06-12", 0.025544), ("2034-12-11", 0.355544), ("2035-06-11", 0.012962), ("2035-12-10", 0.352963)]},
    "VSCW": {"ticker_d912": "VSCWD", "emisor": "Vista Energy", "vencimiento": "2027-04-15",
              "flujos": [("2026-10-15", 0.015123), ("2027-01-15", 0.015123), ("2027-04-15", 1.014795)]},
    "YM34": {"ticker_d912": "YM34D", "emisor": "YPF", "vencimiento": "2034-01-17",
              "flujos": [("2027-01-18", 0.04125), ("2027-07-19", 0.04125), ("2028-01-17", 0.04125), ("2028-07-17", 0.04125), ("2029-01-17", 0.04125), ("2029-07-17", 0.04125), ("2030-01-17", 0.04125), ("2030-07-17", 0.04125), ("2031-01-17", 0.04125), ("2031-07-17", 0.04125), ("2032-01-19", 0.34125), ("2032-07-19", 0.028875), ("2033-01-17", 0.328875), ("2033-07-18", 0.0165), ("2034-01-17", 0.4165)]},
    "YM37": {"ticker_d912": "YM37D", "emisor": "YPF", "vencimiento": "2027-05-07",
              "flujos": [("2026-08-07", 0.017644), ("2026-11-09", 0.017644), ("2027-02-08", 0.017644), ("2027-05-07", 1.017068)]},
    "YM38": {"ticker_d912": "YM38D", "emisor": "YPF", "vencimiento": "2027-07-22",
              "flujos": [("2026-07-22", 0.018699), ("2026-10-22", 0.018904), ("2027-01-22", 0.018904), ("2027-04-22", 0.018493), ("2027-07-22", 1.018699)]},
    "YM40": {"ticker_d912": "YM40D", "emisor": "YPF", "vencimiento": "2028-08-28",
              "flujos": [("2026-08-28", 0.0189), ("2026-11-30", 0.0189), ("2027-02-26", 0.0189), ("2027-05-28", 0.0183), ("2027-08-30", 0.0189), ("2027-11-29", 0.0189), ("2028-02-28", 0.0189), ("2028-05-29", 0.0185), ("2028-08-28", 1.0189)]},
    "YMCX": {"ticker_d912": "YMCXD", "emisor": "YPF", "vencimiento": "2031-09-11",
              "flujos": [("2026-09-11", 0.04375), ("2027-03-11", 0.04375), ("2027-09-13", 0.04375), ("2028-03-13", 0.04375), ("2028-09-11", 0.04375), ("2029-03-12", 0.04375), ("2029-09-11", 0.24375), ("2030-03-11", 0.035), ("2030-09-11", 0.235), ("2031-03-11", 0.02625), ("2031-09-11", 0.62625)]},
    "YMCY": {"ticker_d912": "YMCYD", "emisor": "YPF", "vencimiento": "2028-10-10",
              "flujos": [("2026-10-13", 0.016384), ("2027-01-11", 0.016384), ("2027-04-12", 0.016027), ("2027-07-12", 0.016205), ("2027-10-11", 0.016384), ("2028-01-10", 0.016384), ("2028-04-10", 0.016205), ("2028-07-10", 0.016205), ("2028-10-10", 1.016384)]},
    "YMCZ": {"ticker_d912": "YMCZD", "emisor": "YPF", "vencimiento": "2028-10-10",
              "flujos": [("2026-10-13", 0.035096), ("2027-04-12", 0.034904), ("2027-10-11", 0.035096), ("2028-04-10", 0.035096), ("2028-10-10", 1.035096)]},
}


def _on_description(config_key):
    info = ON_FLUJOS.get(config_key)
    if not info:
        return config_key
    return f"{info['emisor']} - ON {info['ticker_d912']} (vto. {info['vencimiento']})"

BOND_DESCRIPTIONS = {
    # Soberanos USD (reestructuracion 2020)
    "AL29": "Bonar 2029 (ley Argentina)", "GD29": "Global 2029 (ley Nueva York)",
    "AL30": "Bonar 2030 (ley Argentina)", "GD30": "Global 2030 (ley Nueva York)",
    "AL35": "Bonar 2035 (ley Argentina)", "GD35": "Global 2035 (ley Nueva York)",
    "AE38": "Bonar 2038 (ley Argentina)", "GD38": "Global 2038 (ley Nueva York)",
    "AL41": "Bonar 2041 (ley Argentina)", "GD41": "Global 2041 (ley Nueva York)",
    "GD46": "Bono Global de la Republica Argentina en USD, ley Nueva York, vto. 2046",
    "TY30P": "BONCAP tasa fija 29.50% TNA, ley Argentina, vto. 2030",
    "AO27": "Bono del Tesoro en USD, ley Argentina, vto. 2027",
    "AO28": "Bono del Tesoro en USD, ley Argentina, vto. 2028",
    "AO29": "Bono del Tesoro en USD, ley Argentina, vto. 2029",
    "AN29": "Bono del Tesoro en USD, ley Argentina, vto. 2029",
    "BPD7": "Bono del Tesoro en USD, ley Argentina, vto. 2027",
    # CER
    "TX26": "Bono del Tesoro en pesos ajustado por CER, vto. 2026",
    "TX28": "Bono del Tesoro en pesos ajustado por CER, vto. 2028",
    "TX31": "Bono del Tesoro en pesos ajustado por CER, vto. 2031",
    "TZX27": "Bono del Tesoro en pesos ajustado por CER, vto. 2027",
    "TZX28": "Bono del Tesoro en pesos ajustado por CER, vto. 2028",
    "TZXD6": "Bono del Tesoro en pesos ajustado por CER, vto. dic. 2026",
    "TZXD7": "Bono del Tesoro en pesos ajustado por CER, vto. dic. 2027",
    "TZXM7": "Bono del Tesoro en pesos ajustado por CER, vto. mar. 2027",
    "TZXO6": "Bono del Tesoro en pesos ajustado por CER, vto. oct. 2026",
    "X30N6": "Letra del Tesoro (LECER) ajustada por CER, vto. nov. 2026",
    "X30S6": "Letra del Tesoro (LECER) ajustada por CER, vto. sep. 2026",
    "X31L6": "Letra del Tesoro (LECER) ajustada por CER, vto. jul. 2026",
    "DICP": "Discount en pesos ajustado por CER (reestructuracion 2005)",
    "PARP": "Par en pesos ajustado por CER (reestructuracion 2005)",
    "TZXA7": "Bono del Tesoro en pesos ajustado por CER, vto. abr. 2027",
    "TZXY7": "Bono del Tesoro en pesos ajustado por CER, vto. may. 2027",
    "TZXS7": "Bono del Tesoro en pesos ajustado por CER, vto. sep. 2027",
    "TZXS8": "Bono del Tesoro en pesos ajustado por CER, vto. sep. 2028",
    "TZXM8": "Bono del Tesoro en pesos ajustado por CER, vto. mar. 2028",
    "TZXM9": "Bono del Tesoro en pesos ajustado por CER, vto. mar. 2029",
    "TZXO7": "Bono del Tesoro en pesos ajustado por CER, vto. oct. 2027",
    "TZXD8": "Bono del Tesoro en pesos ajustado por CER, vto. dic. 2028",
    "CUAP": "Cuasipar en pesos ajustado por CER (reestructuracion 2005)",
    # TAMAR (tarea 3.3)
    "TMF27": "Bono del Tesoro en pesos TAMAR, vto. feb. 2027",
    "TML27": "Bono del Tesoro en pesos TAMAR, vto. jul. 2027",
    "TMG27": "Bono del Tesoro en pesos TAMAR, vto. ago. 2027",
    "TMF28": "Bono del Tesoro en pesos TAMAR, vto. feb. 2028",
    "TMG28": "Bono del Tesoro en pesos TAMAR, vto. ago. 2028",
    # Duales y Dolar Linked (tarea 3.4)
    "TXMJ8": "Bono del Tesoro Dual CER/TAMAR, vto. jun. 2028",
    "TXMD8": "Bono del Tesoro Dual, vto. dic. 2028",
    "TXMJ9": "Bono del Tesoro Dual, vto. jun. 2029",
    "TXMJ0": "Bono del Tesoro Dual, vto. jun. 2030",
    "TTD26": "Bono del Tesoro Dual, vto. dic. 2026",
    "D31M7": "Bono del Tesoro Dolar Linked, vto. mar. 2027",
    "TZV27": "Bono del Tesoro Dolar Linked, vto. jun. 2027",
    "TZV28": "Bono del Tesoro Dolar Linked, vto. jun. 2028",
}


def _bond_description(ticker, categoria):
    desc = BOND_DESCRIPTIONS.get(ticker)
    if desc:
        return desc
    if categoria == "lecap":
        info = LECAP_TERMS.get(ticker)
        return info["nombre"] if info else ticker
    return ticker


# ------------------------------------------------------------------
# Matematica financiera generica: valor presente / TIR / duration de
# Macaulay a partir de un cronograma de flujos de fondos. Se usa tanto
# para los bonos soberanos en USD (flujos nominales) como, con el
# criterio de "TIR sobre CER" (CER congelado desde hoy), para los bonos
# CER (flujos en unidades reales). Se resuelve la TIR por biseccion en
# vez de Newton-Raphson: con cronogramas irregulares (cupones variables,
# amortizaciones no uniformes) la biseccion es mas robusta y siempre
# converge si existe una raiz en el rango [-90%, 500%], que cubre
# cualquier rendimiento realista de estos instrumentos.
# ------------------------------------------------------------------

def _years_between(d1, d2):
    return (d2 - d1).days / 365.0


def _bond_pv_at_yield(flujos_futuros, valuation_date, r):
    """flujos_futuros: lista de (date, monto). Devuelve el valor presente."""
    pv = 0.0
    for fecha, monto in flujos_futuros:
        t = _years_between(valuation_date, fecha)
        if t <= 0:
            continue
        pv += monto / ((1 + r) ** t)
    return pv


def _solve_ytm(flujos_futuros, valuation_date, target_price):
    if not flujos_futuros or target_price is None or target_price <= 0:
        return None
    lo, hi = -0.9, 5.0

    def f(r):
        return _bond_pv_at_yield(flujos_futuros, valuation_date, r) - target_price

    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = f(mid)
        if abs(fm) < 1e-9:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def _macaulay_duration(flujos_futuros, valuation_date, r):
    pv_total = 0.0
    weighted = 0.0
    for fecha, monto in flujos_futuros:
        t = _years_between(valuation_date, fecha)
        if t <= 0:
            continue
        pv = monto / ((1 + r) ** t)
        pv_total += pv
        weighted += t * pv
    if pv_total <= 0:
        return None
    return weighted / pv_total


# Shocks de TIR (en puntos porcentuales) para la tabla de sensibilidad
# TIR-precio, replicando los escenarios que muestra bonistas.com en la
# pagina de detalle de cada bono.
TIR_SHOCKS_PP = [-3, -2, -1, 1, 2, 3, 5, 10]


def _tir_price_sensitivity(flujos_futuros, valuation_date, tir_base, precio_base):
    """Tabla de sensibilidad precio-TIR: para cada shock de TIR (en
    puntos porcentuales) sobre la TIR actual, la variacion porcentual
    que tendria el precio si el mercado convalidara esa nueva TIR,
    manteniendo el mismo cronograma de flujos futuros. Se calcula
    revaluando el valor presente de los mismos flujos a la TIR
    desplazada. Devuelve None si falta algun dato base (TIR o precio)."""
    if tir_base is None or precio_base is None or precio_base <= 0 or not flujos_futuros:
        return None
    out = {}
    for shock_pp in TIR_SHOCKS_PP:
        nueva_tir = tir_base + shock_pp / 100.0
        if nueva_tir <= -0.99:
            out[str(shock_pp)] = None
            continue
        nuevo_precio = _bond_pv_at_yield(flujos_futuros, valuation_date, nueva_tir)
        out[str(shock_pp)] = round((nuevo_precio - precio_base) / precio_base * 100, 2)
    return out


def _add_months(d, months):
    """Suma (o resta, si months es negativo) una cantidad entera de
    meses a una fecha, ajustando el dia si el mes destino tiene menos
    dias (ej. 31/agosto - 6 meses = 28/febrero o 29 en bisiesto)."""
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    ultimo_dia_mes = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, ultimo_dia_mes))


def _bcra_cer_hoy():
    """Indice CER (BCRA, variable 30), valor vigente a la fecha de hoy o
    la ultima fecha publicada anterior (el BCRA tambien publica valores
    "proyectados" a futuro segun el cronograma de indices, asi que no
    hay que tomar directamente el primer registro sin mirar la fecha)."""
    data = fetch_json("https://api.bcra.gob.ar/estadisticas/v4.0/monetarias/30")
    if not data or not data.get("results"):
        return None
    detalle = data["results"][0].get("detalle") or []
    hoy = date.today().isoformat()
    validos = [d for d in detalle if d.get("fecha") and d["fecha"] <= hoy and d.get("valor") is not None]
    if not validos:
        return None
    validos.sort(key=lambda d: d["fecha"], reverse=True)
    return validos[0]["valor"]


def get_bonos_soberanos_usd():
    # rendimientos.co/api/soberanos no cubre GD46 (verificado 20/07/2026:
    # solo 15 tickers, no incluye el agregado en el canje 2020 de mayor
    # plazo). Se completa con data912.com/live/arg_bonds, usando el
    # ticker con sufijo "D" (liquidacion en USD, ej. "GD46D") que es
    # comparable al "price_usd" de rendimientos.co.
    precios_por_symbol = {}
    data_rend = fetch_json("https://rendimientos.co/api/soberanos")
    if data_rend and "data" in data_rend:
        for item in data_rend["data"]:
            sym = item.get("symbol")
            precio = item.get("price_usd")
            if sym and precio is not None:
                precios_por_symbol[sym] = precio
    data912 = fetch_json("https://data912.com/live/arg_bonds")
    if data912:
        for item in data912:
            sym912 = item.get("symbol") or ""
            precio = item.get("c")
            if sym912.endswith("D") and precio is not None:
                sym = sym912[:-1]
                if sym not in precios_por_symbol:
                    precios_por_symbol[sym] = precio
    if not precios_por_symbol:
        return None
    hoy = date.today()
    out = []
    for sym, precio in precios_por_symbol.items():
        info = SOBERANOS_FLUJOS.get(sym)
        if not info:
            continue
        flujos = [(_parse_date(f), m) for f, m in info["flujos"]]
        flujos_fut = [(f, m) for f, m in flujos if f and f > hoy]
        ytm = _solve_ytm(flujos_fut, hoy, precio)
        dur = _macaulay_duration(flujos_fut, hoy, ytm) if ytm is not None else None
        vto = _parse_date(info["vencimiento"])
        dtm = (vto - hoy).days if vto else None
        # Se excluyen bonos con duration menor a 0.20 y/o DTM menor a 45
        # dias: son instrumentos practicamente a la par de su vencimiento,
        # sin utilidad para el analisis de curva TIR/Duration.
        if (dur is not None and dur < 0.20) or (dtm is not None and dtm < 45):
            continue
        # Nota: Valor Tecnico y Paridad no se calculan para bonos
        # soberanos en USD: SOBERANOS_FLUJOS solo registra el monto total
        # de cada pago (cupon+amortizacion combinados), sin separar la
        # tasa de interes del cronograma de amortizacion, dato
        # imprescindible para calcular el interes corrido sin inventarlo.
        # La sensibilidad TIR-precio si es exacta: solo depende del
        # cronograma de flujos totales, no de la separacion capital/interes.
        out.append({
            "symbol": sym,
            "descripcion": _bond_description(sym, "usd"),
            "ley": info["ley"],
            "vencimiento": info["vencimiento"],
            "precio": precio,
            "pct_change": item.get("pct_change"),
            "tir": round(ytm * 100, 3) if ytm is not None else None,
            "duration": round(dur, 3) if dur is not None else None,
            "sensibilidad_tir": _tir_price_sensitivity(flujos_fut, hoy, ytm, precio),
        })
    out.sort(key=lambda x: (x["duration"] is None, x["duration"] or 0))
    return out or None


def get_bonos_cer():
    # rendimientos.co/api/cer-precios solo cubre los 14 tickers CER
    # originales (verificado 20/07/2026: no incluye TZXA7/TZXY7/TZXS7/
    # TZXM8/TZXS8/TZXM9/TZXO7/TZXD8/CUAP, que por eso quedaban afuera del
    # listado publicado pese a estar bien definidos en CER_FLUJOS). Se
    # complementa con data912.com (arg_bonds + arg_notes, mismo campo
    # "c" de precio), que si cubre esos 9 tickers nuevos.
    precios_por_symbol = {}
    data_rend = fetch_json("https://rendimientos.co/api/cer-precios")
    if data_rend and "data" in data_rend:
        for item in data_rend["data"]:
            sym = item.get("symbol")
            precio = item.get("c")
            if sym and precio is not None:
                precios_por_symbol[sym] = precio
    for url912 in ("https://data912.com/live/arg_bonds", "https://data912.com/live/arg_notes"):
        data912 = fetch_json(url912)
        if not data912:
            continue
        for item in data912:
            sym = item.get("symbol")
            precio = item.get("c")
            if sym and precio is not None and sym not in precios_por_symbol:
                precios_por_symbol[sym] = precio
    if not precios_por_symbol:
        return None
    cer_hoy = _bcra_cer_hoy()
    if cer_hoy is None:
        print("[WARN] No se pudo obtener el indice CER del BCRA: no se calcula TIR/duration de bonos CER.")
    hoy = date.today()
    out = []
    for sym, precio in precios_por_symbol.items():
        info = CER_FLUJOS.get(sym)
        if not info:
            continue
        cer_emision = info["cer_emision"]
        flujos_all = [(_parse_date(f), am, ti, base) for f, am, ti, base in info["flujos"]]

        # Capital residual (VNR) vigente hoy: si el ticker tiene un punto
        # de referencia conocido (CER_VNR_CONOCIDO, para bonos con
        # historia muy larga) se parte de ahi y se descuentan las
        # amortizaciones futuras ya transcurridas desde esa referencia;
        # si no, se calcula sumando las amortizaciones de flujos pasados
        # incluidos en la lista completa.
        if sym in CER_VNR_CONOCIDO:
            vnr_ref, fecha_ref = CER_VNR_CONOCIDO[sym]
            fecha_ref_d = _parse_date(fecha_ref)
            vnr_hoy = vnr_ref - sum(am for f, am, ti, b in flujos_all if fecha_ref_d < f <= hoy)
        else:
            vnr_hoy = 1.0 - sum(am for f, am, ti, b in flujos_all if f and f <= hoy)

        flujos_fut_raw = [(f, am, ti, b) for f, am, ti, b in flujos_all if f and f > hoy]
        # Flujo real (en unidades de 100 nominal ORIGINAL, sin ajuste por
        # CER) de cada pago futuro: interes sobre el capital residual
        # vigente justo antes de ese pago, mas la amortizacion de ese pago.
        flujos_reales = []
        vnr_restante = vnr_hoy
        for fecha, amortizacion, tasa, base in flujos_fut_raw:
            interes = vnr_restante * tasa * base * 100
            principal = amortizacion * 100
            flujos_reales.append((fecha, interes + principal))
            vnr_restante -= amortizacion

        tir = dur = valor_tecnico = paridad = None
        sensibilidad = None
        precio_real = None
        if cer_hoy is not None and flujos_reales:
            coeficiente = cer_hoy / cer_emision
            precio_real = precio / coeficiente
            tir = _solve_ytm(flujos_reales, hoy, precio_real)
            dur = _macaulay_duration(flujos_reales, hoy, tir) if tir is not None else None

            # Valor Tecnico = capital residual ajustado por CER + interes
            # corrido del cupon en curso. Para bonos bullet (tasa=0 en el
            # unico flujo futuro, la mayoria de este diccionario) el
            # interes corrido es 0 por construccion. Para bonos con
            # cupon real (ej. CUAP) se prorratea la tasa del proximo pago
            # sobre los dias transcurridos desde el pago anterior, que se
            # infiere restando periodos de "base" años (asumiendo pagos
            # calendario fijos, valido para todos los cronogramas
            # semestrales de este diccionario).
            capital_hoy = vnr_hoy * 100 * coeficiente
            interes_corrido = 0.0
            if flujos_fut_raw:
                fecha_prox, _am_prox, tasa_prox, base_prox = flujos_fut_raw[0]
                if tasa_prox and tasa_prox > 0:
                    meses_periodo = round(base_prox * 12)
                    fecha_pago_anterior = _add_months(fecha_prox, -meses_periodo)
                    dias_periodo = (fecha_prox - fecha_pago_anterior).days
                    dias_transcurridos = (hoy - fecha_pago_anterior).days
                    if dias_periodo > 0:
                        fraccion = max(0.0, min(1.0, dias_transcurridos / dias_periodo))
                        interes_corrido = vnr_hoy * 100 * tasa_prox * fraccion * coeficiente
            valor_tecnico = round(capital_hoy + interes_corrido, 2)
            interes_corrido = round(interes_corrido, 2)
            if valor_tecnico > 0:
                paridad = round(precio / valor_tecnico * 100, 2)
            sensibilidad = _tir_price_sensitivity(flujos_reales, hoy, tir, precio_real)

        vto_cer = _parse_date(info["vencimiento"])
        dtm_cer = (vto_cer - hoy).days if vto_cer else None
        # Mismo criterio que Bonos Soberanos, LECAPs/BONCAPs y ONs: se
        # excluyen instrumentos con duration menor a 0.20 y/o DTM menor
        # a 45 dias.
        if (dur is not None and dur < 0.20) or (dtm_cer is not None and dtm_cer < 45):
            continue

        out.append({
            "symbol": sym,
            "descripcion": _bond_description(sym, "cer"),
            "vencimiento": info["vencimiento"],
            "precio": precio,
            "pct_change": item.get("pct_change"),
            "tir": round(tir * 100, 3) if tir is not None else None,
            "duration": round(dur, 3) if dur is not None else None,
            "valor_tecnico": valor_tecnico,
            "paridad": paridad,
            "interes_corrido": interes_corrido if cer_hoy is not None and flujos_reales else None,
            "sensibilidad_tir": sensibilidad,
        })
    out.sort(key=lambda x: (x["duration"] is None, x["duration"] or 0))
    return out or None


# ------------------------------------------------------------------
# Fase 2 del "detalle de bono" (bonistas.com-style): acumulacion diaria
# de TIR/paridad/valor tecnico/precio limpio-sucio/interes corrido, base
# de los graficos historicos del modal de detalle. No existe ninguna
# fuente externa gratuita con esta serie ya armada (se calcula en este
# mismo script, ver get_bonos_cer/get_bonos_soberanos_usd/get_ons_usd/
# get_bonos_pesos), asi que se acumula un punto por dia de corrida,
# igual criterio que _guardar_lecap_precios() para el precio de LECAPs:
# arranca vacio y se va completando dia a dia desde que se activa esta
# funcion, sin historia retroactiva inventada.
# ------------------------------------------------------------------

BOND_METRICS_HISTORY_FILE = "bonos_metricas_historia.json"


def _accumulate_bond_metrics(categoria, items):
    if not items:
        return
    path = os.path.join(HISTORY_DIR, BOND_METRICS_HISTORY_FILE)
    existing = load_json(path) or {}
    hoy = date.today().isoformat()
    for it in items:
        # Solo se acumulan instrumentos con TIR calculada por flujo de
        # fondos propio (con "sensibilidad_tir" presente en el dict de
        # salida, aunque su valor pueda ser None): descarta LECAPs/
        # BONCAPs dentro de bonos_pesos (TEA de pago unico, sin
        # cronograma de flujos ni sensibilidad TIR-precio calculada) y
        # cualquier item sin TIR resuelta ese dia.
        if "sensibilidad_tir" not in it or it.get("tir") is None:
            continue
        sym = it["symbol"]
        precio_dirty = it.get("precio")
        interes_corrido = it.get("interes_corrido")
        precio_clean = None
        if precio_dirty is not None and interes_corrido is not None:
            precio_clean = round(precio_dirty - interes_corrido, 2)
        punto = {
            "date": hoy,
            "tir": it.get("tir"),
            "duration": it.get("duration"),
            "paridad": it.get("paridad"),
            "valor_tecnico": it.get("valor_tecnico"),
            "interes_corrido": interes_corrido,
            "precio_dirty": precio_dirty,
            "precio_clean": precio_clean,
        }
        entry = existing.setdefault(sym, {"categoria": categoria, "history": []})
        entry["categoria"] = categoria
        hist = entry["history"]
        if hist and hist[-1].get("date") == hoy:
            hist[-1] = punto
        else:
            hist.append(punto)
        # Limite de seguridad (no deberia alcanzarse en la practica: a
        # un punto por dia habil, ~2000 puntos son casi 8 años).
        if len(hist) > 2000:
            del hist[: len(hist) - 2000]
    existing["_updated_at"] = datetime.now(timezone.utc).isoformat()
    save_json(path, existing)


def _lecap_precios_previos():
    path = os.path.join(HISTORY_DIR, "bonos_pesos_precios.json")
    return load_json(path) or {}


def _guardar_lecap_precios(precios_hoy):
    path = os.path.join(HISTORY_DIR, "bonos_pesos_precios.json")
    existing = load_json(path) or {"series": {}}
    series = existing.setdefault("series", {})
    hoy = date.today().isoformat()
    for sym, precio in precios_hoy.items():
        serie = series.setdefault(sym, [])
        if serie and serie[-1].get("t") == hoy:
            serie[-1]["c"] = precio
        else:
            serie.append({"t": hoy, "c": precio})
        if len(serie) > 2000:
            del serie[: len(serie) - 2000]
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_json(path, existing)
    return series


def get_bonos_pesos():
    # rendimientos.co/api/lecaps no cubre TO26 ni TY30P (verificado
    # 20/07/2026: 11 tickers, no incluye ninguno de los dos). Se
    # completa con data912.com/live/arg_bonds (mismo campo "c" de
    # precio, ticker sin sufijo para bonos en pesos).
    precios_por_symbol = {}
    data_rend = fetch_json("https://rendimientos.co/api/lecaps")
    if data_rend and "data" in data_rend:
        for item in data_rend["data"]:
            sym = item.get("symbol")
            precio = item.get("price")
            if sym and precio is not None:
                precios_por_symbol[sym] = precio
    data912 = fetch_json("https://data912.com/live/arg_bonds")
    if data912:
        for item in data912:
            sym = item.get("symbol")
            precio = item.get("c")
            if sym and precio is not None and sym not in precios_por_symbol:
                precios_por_symbol[sym] = precio
    if not precios_por_symbol:
        return None
    hoy = date.today()
    precios_hoy = {}
    candidatos = []
    for sym, precio in precios_por_symbol.items():
        info = LECAP_TERMS.get(sym)
        if not info:
            continue
        vto = _parse_date(info["fecha_vencimiento"])
        if not vto or vto <= hoy:
            continue
        dias = (vto - hoy).days
        duration_lecap = dias / 365.0
        # Se excluyen LECAPs/BONCAPs con duration menor a 0.20 y/o DTM
        # menor a 45 dias: practicamente a la par de su vencimiento.
        if duration_lecap < 0.20 or dias < 45:
            continue
        pago_final = info["pago_final"]
        tea = (pago_final / precio) ** (365.0 / dias) - 1.0
        precios_hoy[sym] = precio
        candidatos.append({
            "symbol": sym,
            "descripcion": _bond_description(sym, "lecap"),
            "vencimiento": info["fecha_vencimiento"],
            "precio": precio,
            "tir": round(tea * 100, 3),
            "duration": round(dias / 365.0, 3),
            "dias": dias,
        })

    # Variacion del dia: se auto-acumula un historial propio (no existe
    # fuente publica gratuita con el cierre del dia anterior para LECAPs)
    # y se compara el precio de hoy contra el ultimo guardado ayer. El
    # primer dia que corre esto no hay punto de comparacion, asi que la
    # variacion queda en None (se muestra "-") en vez de inventar un dato.
    series = _guardar_lecap_precios(precios_hoy)
    for c in candidatos:
        serie = series.get(c["symbol"], [])
        pct = None
        if len(serie) >= 2:
            ayer, hoy_pt = serie[-2], serie[-1]
            if ayer.get("c"):
                pct = (hoy_pt["c"] - ayer["c"]) / ayer["c"] * 100
        c["pct_change"] = round(pct, 3) if pct is not None else None
        del c["dias"]

    candidatos.sort(key=lambda x: x["duration"])

    # TY30P: cupon fijo periodico, no encaja en el modelo de pago unico
    # de LECAP_TERMS (ver comentario en BONOS_PESOS_CUPON_FLUJOS). Se
    # matchea aparte contra el mismo feed de precios y se calcula
    # TIR/duration por flujo de fondos real, igual que get_ons_usd().
    for sym, precio in precios_por_symbol.items():
        info = BONOS_PESOS_CUPON_FLUJOS.get(sym)
        if not info:
            continue
        flujos = [(_parse_date(f), m * 100) for f, m in info["flujos"]]
        flujos_fut = [(f, m) for f, m in flujos if f and f > hoy]
        ytm = _solve_ytm(flujos_fut, hoy, precio)
        dur = _macaulay_duration(flujos_fut, hoy, ytm) if ytm is not None else None
        vto_c = _parse_date(info["vencimiento"])
        dtm_c = (vto_c - hoy).days if vto_c else None
        if (dur is not None and dur < 0.20) or (dtm_c is not None and dtm_c < 45):
            continue
        candidatos.append({
            "symbol": sym,
            "descripcion": _bond_description(sym, "lecap"),
            "vencimiento": info["vencimiento"],
            "precio": precio,
            "pct_change": None,
            "tir": round(ytm * 100, 3) if ytm is not None else None,
            "duration": round(dur, 3) if dur is not None else None,
            "sensibilidad_tir": _tir_price_sensitivity(flujos_fut, hoy, ytm, precio),
        })

    candidatos.sort(key=lambda x: (x["duration"] is None, x["duration"] or 0))
    return candidatos or None




# ------------------------------------------------------------------
# Bonos TAMAR (tarea 3.3) y Duales/Dolar Linked (tarea 3.4). No existe
# en este script una fuente de tasa TAMAR historica integrada (ni un
# indice diario publico y gratuito equivalente al CER de datos.gob.ar
# para poder modelar el flujo de fondos propio con el mismo helper de
# VR-adjustment que usa get_bonos_cer()), asi que -tal como habilita el
# enunciado de la tarea- se toma directamente TIR/duration (modified
# duration) y precio publicados por bonistas.com para cada ticker,
# reutilizando el mismo parseo de __NEXT_DATA__ que ya usa
# _fetch_bonistas_history() mas abajo en este archivo (bloque
# "props.pageProps.bondData"), documentando la fuente en el campo
# "fuente" de cada item. Esto corre en vivo en cada ejecucion de GitHub
# Actions (no son valores hardcodeados en este script). Los nombres
# exactos de los campos del objeto "bond" (fair_value/tir_val/
# modified_duration_val, segun la tarea original) no pudieron
# verificarse desde este sandbox por falta de acceso de red a
# bonistas.com; se prueban varios alias razonables por robustez y se
# devuelve None si no aparece ninguno (en runtime real de GitHub
# Actions, revisar el log si "bonos_pesos_tamar"/"bonos_pesos_duales"
# salen vacios para ajustar los nombres de campo).
# ------------------------------------------------------------------

# NOTA (corregido 2026-07-20, tarea #58): la ausencia de TMF27, TTD26 y
# TZV27 en el live_data.json publicado NO era un problema de cobertura
# de bonistas.com. Era que _fetch_bonistas_bond_info() usaba la pagina
# HTML bono-cotizacion-rendimiento-precio-hoy/{ticker}, que redirige al
# home de bonistas.com para esos 3 tickers puntuales. El endpoint JSON
# https://bonistas.com/api/bond/{ticker} si publica ficha completa para
# los 3 (confirmado via Chrome). _fetch_bonistas_bond_info() se
# reescribio para usar ese endpoint directamente.
TAMAR_BONOS = {
    "TMF27": {"vencimiento": "2027-02-26"},
    "TML27": {"vencimiento": "2027-07-30"},
    "TMG27": {"vencimiento": "2027-08-31"},
    "TMF28": {"vencimiento": "2028-02-25"},
    "TMG28": {"vencimiento": "2028-08-31"},
}

DUALES_DOLARLINKED_BONOS = {
    "TXMJ8": {"vencimiento": "2028-06-30", "tipo": "Dual CER/TAMAR"},
    "TXMD8": {"vencimiento": "2028-12-15", "tipo": "Dual"},
    "TXMJ9": {"vencimiento": "2029-06-29", "tipo": "Dual"},
    "TXMJ0": {"vencimiento": "2030-06-28", "tipo": "Dual"},
    "TTD26": {"vencimiento": "2026-12-15", "tipo": "Dual"},
    "D31M7": {"vencimiento": "2027-03-31", "tipo": "Dolar Linked"},
    "TZV27": {"vencimiento": "2027-06-30", "tipo": "Dolar Linked"},
    "TZV28": {"vencimiento": "2028-06-30", "tipo": "Dolar Linked"},
}


def _fetch_bonistas_bond_info(ticker):
    """TIR/MD/precio publicados por bonistas.com para un ticker, via el
    endpoint JSON https://bonistas.com/api/bond/{ticker} (confirmado
    20/07/2026 via Chrome: mas confiable que la pagina HTML
    bono-cotizacion-rendimiento-precio-hoy, que redirige al home para
    varios tickers, ej. TMF27, TTD26, TZV27)."""
    data = fetch_json(f"https://bonistas.com/api/bond/{ticker}")
    if not data or not isinstance(data, dict):
        return None
    bond = data.get("bond")
    if not isinstance(bond, dict):
        return None

    def _first(*keys):
        for k in keys:
            v = bond.get(k)
            if v is not None:
                return v
        return None

    precio = _first("last_price", "fair_value", "last_close")
    tir = _first("tir_val", "tir")
    dur = _first("modified_duration_val", "modified_duration")
    if precio is None and tir is None and dur is None:
        return None
    try:
        precio = float(precio) if precio is not None else None
    except (TypeError, ValueError):
        precio = None
    try:
        tir = float(tir) if tir is not None else None
        # Algunas fuentes publican la TIR como proporcion (0.2675) y otras
        # ya en porcentaje (26.75); se normaliza a porcentaje aqui para
        # que quede consistente con el resto de las secciones de bonos.
        if tir is not None and abs(tir) < 1:
            tir *= 100
    except (TypeError, ValueError):
        tir = None
    try:
        dur = float(dur) if dur is not None else None
    except (TypeError, ValueError):
        dur = None
    return {"precio": precio, "tir": tir, "duration": dur}


def get_bonos_tamar():
    hoy = date.today()
    out = []
    for sym, info in TAMAR_BONOS.items():
        binfo = _fetch_bonistas_bond_info(sym)
        if not binfo:
            continue
        vto = _parse_date(info["vencimiento"])
        dtm = (vto - hoy).days if vto else None
        dur = binfo.get("duration")
        if (dur is not None and dur < 0.20) or (dtm is not None and dtm < 45):
            continue
        out.append({
            "symbol": sym,
            "descripcion": _bond_description(sym, "tamar"),
            "vencimiento": info["vencimiento"],
            "precio": binfo.get("precio"),
            "pct_change": None,
            "tir": round(binfo["tir"], 3) if binfo.get("tir") is not None else None,
            "duration": round(dur, 3) if dur is not None else None,
            "fuente": "bonistas.com",
        })
    out.sort(key=lambda x: (x["duration"] is None, x["duration"] or 0))
    return out or None


def get_bonos_duales_dolarlinked():
    hoy = date.today()
    out = []
    for sym, info in DUALES_DOLARLINKED_BONOS.items():
        binfo = _fetch_bonistas_bond_info(sym)
        if not binfo:
            continue
        vto = _parse_date(info["vencimiento"])
        dtm = (vto - hoy).days if vto else None
        dur = binfo.get("duration")
        if (dur is not None and dur < 0.20) or (dtm is not None and dtm < 45):
            continue
        out.append({
            "symbol": sym,
            "descripcion": _bond_description(sym, "dual"),
            "vencimiento": info["vencimiento"],
            "tipo": info["tipo"],
            "precio": binfo.get("precio"),
            "pct_change": None,
            "tir": round(binfo["tir"], 3) if binfo.get("tir") is not None else None,
            "duration": round(dur, 3) if dur is not None else None,
            "fuente": "bonistas.com",
        })
    out.sort(key=lambda x: (x["duration"] is None, x["duration"] or 0))
    return out or None


def get_ons_usd(top_n=20):
    """ONs en USD con mayor volumen operado. Mismo criterio que
    get_bonos_soberanos_usd(): se matchea contra ON_FLUJOS (cronograma de
    flujos futuros transcripto desde rendimientos.co/api/config) via el
    ticker de liquidacion en USD (sufijo "D"), se ordena por volumen
    operado (campo "v" de la API, vivo, no hardcodeado) y se toma el
    top_n. No hay fuente publica y gratuita con cronograma de flujos para
    ONs en Pesos ni ajustadas por CER (verificado: rendimientos.co y
    data912.com solo cubren ONs liquidacion USD; el panel "BONOS IAMC" de
    BYMA que si tiene TIR/Duracion para ONs en pesos esta pago)."""
    data = fetch_json("https://rendimientos.co/api/ons")
    if not data or "data" not in data:
        return None
    by_ticker = {}
    for config_key, info in ON_FLUJOS.items():
        by_ticker[info["ticker_d912"]] = config_key

    candidatos = []
    for item in data["data"]:
        sym = item.get("symbol")
        config_key = by_ticker.get(sym)
        if not config_key:
            continue
        precio = item.get("c")
        vol = item.get("v") or 0
        if precio is None:
            continue
        candidatos.append((vol, config_key, sym, precio, item.get("pct_change")))

    candidatos.sort(key=lambda x: x[0], reverse=True)
    candidatos = candidatos[:top_n]

    hoy = date.today()
    out = []
    for vol, config_key, sym, precio, pct_change in candidatos:
        info = ON_FLUJOS[config_key]
        # Los flujos de ON_FLUJOS estan expresados como fraccion del
        # nominal original (1.0 = 100% del capital); los precios de
        # mercado (campo "c") se cotizan cada 100 de valor nominal, igual
        # que los bonos soberanos, asi que se escalan x100 para que sean
        # comparables.
        flujos = [(_parse_date(f), m * 100) for f, m in info["flujos"]]
        flujos_fut = [(f, m) for f, m in flujos if f and f > hoy]
        ytm = _solve_ytm(flujos_fut, hoy, precio)
        dur = _macaulay_duration(flujos_fut, hoy, ytm) if ytm is not None else None
        vto_on = _parse_date(info["vencimiento"])
        dtm_on = (vto_on - hoy).days if vto_on else None
        # Se excluyen ONs con duration menor a 0.20 y/o DTM menor a 45
        # dias: practicamente a la par de su vencimiento.
        if (dur is not None and dur < 0.20) or (dtm_on is not None and dtm_on < 45):
            continue
        out.append({
            "symbol": sym,
            "series_id": config_key,
            "descripcion": _on_description(config_key),
            "emisor": info["emisor"],
            "vencimiento": info["vencimiento"],
            "precio": precio,
            "pct_change": pct_change,
            "tir": round(ytm * 100, 3) if ytm is not None else None,
            "duration": round(dur, 3) if dur is not None else None,
            "volumen": vol,
            "sensibilidad_tir": _tir_price_sensitivity(flujos_fut, hoy, ytm, precio),
        })
    out.sort(key=lambda x: (x["duration"] is None, x["duration"] or 0))
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
    # "stocks" (Apple/Microsoft/Amazon/Alphabet, 4 tickers fijos) fue
    # reemplazado por get_acciones_mundiales(): 10 subsecciones de
    # ranking (top capitalizacion, suba/baja diaria, nuevos maximos/
    # minimos, volumen, volatilidad semanal, RSI) sobre todo el universo
    # NYSE/Nasdaq con capitalizacion >= USD 1.000M, via el screener de
    # Yahoo Finance.
    "etfs": {
        "SPY": "SPY (S&P 500)", "QQQ": "QQQ (Nasdaq 100)", "DIA": "DIA (Dow Jones)",
        "EEM": "EEM (Emergentes)", "EWZ": "EWZ (Brasil)", "IWM": "IWM (Small Caps EEUU)",
        "ARKK": "ARKK (Innovacion)", "XLE": "XLE (Energia)", "XLF": "XLF (Financiero)",
        "XLV": "XLV (Salud)", "SMH": "SMH (Semiconductores)", "IBIT": "IBIT (Bitcoin)",
        "GLD": "GLD (Oro)", "XLK": "XLK (Tecnologia)", "TLT": "TLT (Bonos largo plazo EEUU)",
        "FXI": "FXI (China)", "SLV": "SLV (Plata)", "USO": "USO (Petroleo)",
        "XLP": "XLP (Consumo basico)", "XLY": "XLY (Consumo discrecional)",
    },
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


# Tasas internacionales de referencia. Fuentes, todas gratuitas y sin API
# key:
#  - FED Funds Rate, BCE, SOFR y US1Y: FRED (Federal Reserve Economic Data,
#    api del St. Louis Fed) vía el endpoint público fredgraph.csv, que
#    entrega la serie completa sin necesidad de key. FED Funds Rate se
#    arma con el rango objetivo real de la FOMC (DFEDTARL/DFEDTARU), no un
#    valor estático; BCE usa la tasa de refinanciación principal
#    (ECBMRRFR); SOFR usa la serie SOFR publicada por la Fed de Nueva
#    York/FRED; US1Y usa el rendimiento del Tesoro de EE.UU. a 1 año
#    (DGS1). Antes FED Funds/BCE eran valores fijos sin histórico y SOFR/
#    US1Y no tenían gráfico real - por eso no graficaban.
#  - FVX/TNX/TYX: índices CBOE de rendimiento del Tesoro de EE.UU. a
#    5/10/30 años, vía Yahoo Finance.
#  - JP10Y-JP/GB10Y-GB/DE10Y-DE: valor en vivo vía la API pública de
#    cotizaciones de CNBC (misma que usa cnbc.com/quotes/<symbol>); el
#    histórico usa fuentes oficiales (Ministry of Finance, Bundesbank,
#    Bank of England).
TASAS_INTL_FRED = [
    ("FEDFUNDS-TARGET", "FED Funds Rate (EE.UU.)", "range", ("DFEDTARL", "DFEDTARU")),
    ("ECBMRRFR", "BCE (Zona Euro)", "single", None),
    ("SOFR", "SOFR", "single", None),
    ("DGS1", "US1Y", "single", None),
]
TASAS_INTL_YAHOO = [("^FVX", "FVX"), ("^TNX", "TNX"), ("^TYX", "TYX")]
TASAS_INTL_CNBC = [
    ("JP10Y-JP", "JP10Y-JP"),
    ("GB10Y-GB", "GB10Y-GB"),
    ("DE10Y-DE", "DE10Y-DE"),
]


def _cnbc_pct(s):
    try:
        return float(str(s).replace("%", "").replace("+", ""))
    except (TypeError, ValueError):
        return None


def _cnbc_yield(s):
    try:
        return float(str(s).replace("%", ""))
    except (TypeError, ValueError):
        return None


def _fetch_cnbc_quotes(symbols):
    url = (
        "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
        f"?symbols={urllib.parse.quote('|'.join(symbols))}"
        "&requestMethod=quick&noform=1&partnerId=2&fund=1&exthrs=1&output=json"
    )
    data = fetch_json(url)
    by_symbol = {}
    if data:
        try:
            for q in data.get("FormattedQuoteResult", {}).get("FormattedQuote", []):
                by_symbol[q.get("symbol")] = q
        except (AttributeError, TypeError):
            pass
    return by_symbol


def _fetch_fred_csv(series_id, cosd=None):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={urllib.parse.quote(series_id)}"
    if cosd:
        url += f"&cosd={cosd}"
    return fetch_text(url)


def _parse_fred_csv(text):
    if not text:
        return []
    lines = text.strip().splitlines()
    records = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        fecha_raw, valor_raw = parts[0].strip(), parts[1].strip()
        if valor_raw in ("", ".", "NA"):
            continue
        fecha = _parse_date(fecha_raw)
        if not fecha:
            continue
        try:
            records.append({"fecha": fecha, "c": float(valor_raw)})
        except (TypeError, ValueError):
            continue
    return records


def _fred_last_value(series_id, cosd):
    records = _parse_fred_csv(_fetch_fred_csv(series_id, cosd=cosd))
    if not records:
        return None, None
    records.sort(key=lambda r: r["fecha"])
    last = records[-1]["c"]
    prev = records[-2]["c"] if len(records) >= 2 else None
    pct = ((last - prev) / prev * 100) if prev else None
    return last, pct


def get_tasas_internacionales():
    out = []
    cnbc_by_symbol = _fetch_cnbc_quotes([sym for sym, _ in TASAS_INTL_CNBC])

    # FED Funds Rate, BCE, SOFR, US1Y - todas vía FRED, con ~60 días de
    # ventana para asegurar al menos dos observaciones (series diarias que
    # a veces no se actualizan en fines de semana/feriados).
    cosd = (date.today() - timedelta(days=60)).isoformat()
    for symbol, nombre, kind, extra in TASAS_INTL_FRED:
        if kind == "range":
            lower_id, upper_id = extra
            lower, lower_pct = _fred_last_value(lower_id, cosd)
            upper, upper_pct = _fred_last_value(upper_id, cosd)
            if lower is None or upper is None:
                continue
            out.append({
                "symbol": symbol,
                "nombre": nombre,
                "close": round((lower + upper) / 2, 3),
                "percent_change": None,
                "display": f"{lower:.2f}% - {upper:.2f}%",
            })
        else:
            valor, pct = _fred_last_value(symbol, cosd)
            if valor is None:
                continue
            out.append({
                "symbol": symbol,
                "nombre": nombre,
                "close": valor,
                "percent_change": pct,
            })

    # FVX, TNX, TYX (Yahoo Finance - índices CBOE de rendimiento del Tesoro).
    for symbol, nombre in TASAS_INTL_YAHOO:
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
            out.append({
                "symbol": symbol,
                "nombre": nombre,
                "close": price,
                "percent_change": pct_dia,
            })
        except (KeyError, IndexError, TypeError, ZeroDivisionError) as e:
            print(f"[WARN] No se pudo parsear tasa {symbol}: {e}")

    # JP10Y-JP, GB10Y-GB, DE10Y-DE (CNBC).
    for symbol, nombre in TASAS_INTL_CNBC:
        q = cnbc_by_symbol.get(symbol)
        if q:
            out.append({
                "symbol": symbol,
                "nombre": nombre,
                "close": _cnbc_yield(q.get("last")),
                "percent_change": _cnbc_pct(q.get("change_pct")),
            })
    return out or None


def _build_history_jp10y():
    """Japón 10Y: Ministry of Finance, CSV histórico oficial diario desde 1974."""
    url = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv"
    data = fetch_text(url)
    if not data:
        return None
    lines = data.strip().splitlines()
    records = []
    for line in lines[2:]:
        parts = line.split(",")
        if len(parts) < 11:
            continue
        fecha_raw, v10y = parts[0].strip(), parts[10].strip()
        if not v10y or v10y == "-":
            continue
        try:
            y, m, d = fecha_raw.split("/")
            fecha = date(int(y), int(m), int(d))
            records.append({"fecha": fecha, "c": float(v10y)})
        except (ValueError, IndexError):
            continue
    return build_daily_weekly(records, date_fn=lambda r: r["fecha"], point_fn=lambda r: {"c": r["c"]})


def _build_history_de10y():
    """Alemania 10Y (Bund): Deutsche Bundesbank, API SDMX oficial (BBSIS)."""
    url = (
        "https://api.statistiken.bundesbank.de/rest/data/BBSIS/"
        "D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A?format=json&lang=en"
    )
    data = fetch_json(url)
    if not data:
        return None
    try:
        obs_dim = data["data"]["structure"]["dimensions"]["observation"][0]["values"]
        series_keys = list(data["data"]["dataSets"][0]["series"].keys())
        obs = data["data"]["dataSets"][0]["series"][series_keys[0]]["observations"]
        records = []
        for k, v in obs.items():
            fecha_raw = obs_dim[int(k)]["name"]
            try:
                records.append({"fecha": _parse_date(fecha_raw), "c": float(v[0])})
            except (ValueError, TypeError, IndexError):
                continue
    except (KeyError, IndexError, TypeError):
        return None
    return build_daily_weekly(records, date_fn=lambda r: r["fecha"], point_fn=lambda r: {"c": r["c"]})


def _build_history_gb10y():
    """Reino Unido 10Y (Gilt): Bank of England, Interactive Statistical Database (IUDMNZC)."""
    url = (
        "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
        "?csv.x=yes&Datefrom=01/Jan/2000&Dateto=now&SeriesCodes=IUDMNZC"
        "&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N"
    )
    data = fetch_text(url)
    if not data:
        return None
    lines = data.strip().splitlines()
    records = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        fecha_raw, valor = parts[0].strip(), parts[1].strip()
        try:
            fecha = datetime.strptime(fecha_raw, "%d %b %Y").date()
            records.append({"fecha": fecha, "c": float(valor)})
        except (ValueError, IndexError):
            continue
    return build_daily_weekly(records, date_fn=lambda r: r["fecha"], point_fn=lambda r: {"c": r["c"]})


def _build_history_fred_single(series_id):
    records = _parse_fred_csv(_fetch_fred_csv(series_id))
    if not records:
        return None
    return build_daily_weekly(records, date_fn=lambda r: r["fecha"], point_fn=lambda r: {"c": r["c"]})


def _build_history_fed_funds_range():
    lower = {r["fecha"]: r["c"] for r in _parse_fred_csv(_fetch_fred_csv("DFEDTARL"))}
    upper = {r["fecha"]: r["c"] for r in _parse_fred_csv(_fetch_fred_csv("DFEDTARU"))}
    fechas = sorted(set(lower) & set(upper))
    if not fechas:
        return None
    records = [{"fecha": f, "c": round((lower[f] + upper[f]) / 2, 3)} for f in fechas]
    return build_daily_weekly(records, date_fn=lambda r: r["fecha"], point_fn=lambda r: {"c": r["c"]})


def build_history_tasas_internacionales(rates_actuales):
    """
    FED Funds Rate, BCE, SOFR y US1Y: histórico real vía FRED (rango
    objetivo de la FOMC para FED Funds, ECBMRRFR para BCE, SOFR y DGS1
    para US1Y). FVX/TNX/TYX: histórico real de 5 años vía Yahoo Finance
    (mismo patrón que índices y commodities). Los rendimientos soberanos
    de Japón, Alemania y Reino Unido usan histórico real oficial:
    Ministry of Finance (Japón), Deutsche Bundesbank (Alemania) y Bank of
    England (Reino Unido).
    """
    series_out = {}

    for symbol, nombre, kind, extra in TASAS_INTL_FRED:
        try:
            result = _build_history_fed_funds_range() if kind == "range" else _build_history_fred_single(symbol)
        except Exception as e:
            print(f"[WARN] No se pudo obtener histórico FRED de {symbol}: {e}")
            result = None
        if result:
            daily, weekly = result
            if daily or weekly:
                series_out[symbol] = {"nombre": nombre, "daily": daily, "weekly": weekly}
        time.sleep(0.3)

    for symbol, nombre in TASAS_INTL_YAHOO:
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
                records.append({"ts": t, "c": c})
        except (KeyError, IndexError, TypeError):
            print(f"[WARN] No se pudo parsear histórico Yahoo para {symbol}")
            continue
        daily, weekly = build_daily_weekly(
            records,
            date_fn=lambda r: datetime.fromtimestamp(r["ts"], tz=timezone.utc).date(),
            point_fn=lambda r: {"c": r["c"]},
        )
        if daily or weekly:
            series_out[symbol] = {"nombre": nombre, "daily": daily, "weekly": weekly}
        time.sleep(0.5)

    oficiales = {
        "JP10Y-JP": ("JP10Y-JP", _build_history_jp10y),
        "DE10Y-DE": ("DE10Y-DE", _build_history_de10y),
        "GB10Y-GB": ("GB10Y-GB", _build_history_gb10y),
    }
    for sym, (nombre, fn) in oficiales.items():
        try:
            result = fn()
        except Exception as e:
            print(f"[WARN] No se pudo obtener histórico oficial de {sym}: {e}")
            result = None
        if result:
            daily, weekly = result
            if daily or weekly:
                series_out[sym] = {"nombre": nombre, "daily": daily, "weekly": weekly}

    if not series_out:
        return None
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "series": series_out}


# ------------------------------------------------------------------
# Acciones Mundiales: 10 subsecciones de ranking (top capitalizacion,
# suba/baja diaria, nuevos maximos/minimos de 52 semanas, volumen,
# volatilidad semanal alta/baja, RSI sobrecomprada/sobrevendida) sobre
# el universo NYSE/Nasdaq (EE.UU.) con capitalizacion >= USD 1.000M.
#
# Fuente: el screener publico de Yahoo Finance (el mismo que usa
# finance.yahoo.com/screener). No requiere API key, pero desde 2024
# exige un token "crumb" ligado a una cookie de sesion anonima (sin
# login) para las consultas POST personalizadas -- se replica el mismo
# flujo que usan librerias como yfinance: 1) GET a finance.yahoo.com
# para obtener las cookies, 2) GET a /v1/test/getcrumb con esas cookies
# para obtener el crumb, 3) POST a /v1/finance/screener con el crumb en
# la query string y las cookies en cada request subsiguiente.
# ------------------------------------------------------------------

ACCIONES_MUNDIALES_MKTCAP_MIN = 1_000_000_000


_YAHOO_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _yahoo_get_crumb():
    # Replica el flujo de yfinance: 1) visitar finance.yahoo.com para
    # obtener cookies de sesion anonima, 2) usar esas cookies para pedir
    # el crumb. Yahoo bloquea trafico que no "parece" un navegador real
    # (headers minimos, sin Accept/Accept-Language), asi que se imitan
    # esos headers explicitamente.
    try:
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        req1 = urllib.request.Request("https://fc.yahoo.com", headers=_YAHOO_BROWSER_HEADERS)
        try:
            opener.open(req1, timeout=20).read()
        except urllib.error.HTTPError:
            pass  # fc.yahoo.com puede devolver 404 pero igual setea cookies
        req1b = urllib.request.Request("https://finance.yahoo.com/", headers=_YAHOO_BROWSER_HEADERS)
        opener.open(req1b, timeout=20).read()
        req2 = urllib.request.Request(
            "https://query2.finance.yahoo.com/v1/test/getcrumb", headers=_YAHOO_BROWSER_HEADERS
        )
        crumb = opener.open(req2, timeout=20).read().decode("utf-8", errors="replace").strip()
        if not crumb or "<html" in crumb.lower():
            return None, None
        return opener, crumb
    except Exception as e:
        print(f"[WARN] No se pudo obtener el crumb de Yahoo Finance: {e}")
        return None, None


def _yahoo_screener_query(opener, crumb, query, sort_field, sort_type, size=15, offset=0):
    try:
        body = json.dumps({
            "size": size,
            "offset": offset,
            "sortField": sort_field,
            "sortType": sort_type,
            "quoteType": "EQUITY",
            "query": query,
        }).encode("utf-8")
        url = "https://query2.finance.yahoo.com/v1/finance/screener?crumb=" + urllib.parse.quote(crumb)
        headers = dict(_YAHOO_BROWSER_HEADERS)
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        with opener.open(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        result = data.get("finance", {}).get("result")
        if not result:
            return []
        return result[0].get("quotes", [])
    except Exception as e:
        print(f"[WARN] Fallo la consulta al screener de Yahoo (sort={sort_field}): {e}")
        return []


def _yahoo_screener_paginated(opener, crumb, query, sort_field, sort_type, total_needed, page_size=250):
    out = []
    offset = 0
    while len(out) < total_needed:
        quotes = _yahoo_screener_query(opener, crumb, query, sort_field, sort_type, size=page_size, offset=offset)
        if not quotes:
            break
        out.extend(quotes)
        offset += page_size
        if len(quotes) < page_size:
            break
    return out[:total_needed]


def _accion_mundial_item(q):
    return {
        "symbol": q.get("symbol"),
        "nombre": q.get("shortName") or q.get("longName") or q.get("symbol"),
        "precio": q.get("regularMarketPrice"),
        "pct_change": round(q["regularMarketChangePercent"], 3) if q.get("regularMarketChangePercent") is not None else None,
        "market_cap": q.get("marketCap"),
        "volumen": q.get("regularMarketVolume"),
        "wk52_high": q.get("fiftyTwoWeekHigh"),
        "wk52_low": q.get("fiftyTwoWeekLow"),
    }


def _rsi_14(closes):
    """RSI de Wilder (14 periodos). Necesita al menos 15 cierres diarios."""
    if len(closes) < 15:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[:14]) / 14
    avg_loss = sum(losses[:14]) / 14
    for i in range(14, len(deltas)):
        avg_gain = (avg_gain * 13 + gains[i]) / 14
        avg_loss = (avg_loss * 13 + losses[i]) / 14
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _volatilidad_semanal(closes):
    """Desvio estandar de los retornos diarios de la ultima semana (5
    ruedas), expresado en % (no anualizado: es una medida relativa
    simple para comparar entre acciones, no una cifra financiera
    estandar tipo VIX)."""
    if len(closes) < 6:
        return None
    ultimos = closes[-6:]
    retornos = [(ultimos[i] / ultimos[i - 1] - 1) for i in range(1, len(ultimos)) if ultimos[i - 1]]
    if len(retornos) < 2:
        return None
    media = sum(retornos) / len(retornos)
    var = sum((r - media) ** 2 for r in retornos) / (len(retornos) - 1)
    return round((var ** 0.5) * 100, 3)


def get_acciones_mundiales():
    opener, crumb = _yahoo_get_crumb()
    if not opener or not crumb:
        print("[WARN] No se pudo autenticar contra el screener de Yahoo Finance: se omite Acciones Mundiales.")
        return None

    filtro_base = {
        "operator": "AND",
        "operands": [
            {"operator": "GT", "operands": ["intradaymarketcap", ACCIONES_MUNDIALES_MKTCAP_MIN]},
            {"operator": "EQ", "operands": ["region", "us"]},
        ],
    }

    out = {}

    # 1) Top capitalizacion, 2) Top suba diaria, 3) Top baja diaria,
    # 6) Top volumen: el screener ya devuelve el ranking ordenado sobre
    # TODO el universo filtrado (no hace falta traer mas datos).
    out["top_market_cap"] = [_accion_mundial_item(q) for q in
        _yahoo_screener_query(opener, crumb, filtro_base, "intradaymarketcap", "DESC", size=15)]
    out["top_suba_diaria"] = [_accion_mundial_item(q) for q in
        _yahoo_screener_query(opener, crumb, filtro_base, "percentchange", "DESC", size=15)]
    out["top_baja_diaria"] = [_accion_mundial_item(q) for q in
        _yahoo_screener_query(opener, crumb, filtro_base, "percentchange", "ASC", size=15)]
    out["top_volumen"] = [_accion_mundial_item(q) for q in
        _yahoo_screener_query(opener, crumb, filtro_base, "dayvolume", "DESC", size=15)]

    # 4) Nuevos maximos y 5) nuevos minimos de 52 semanas: el screener no
    # tiene un campo directo para "distancia al maximo/minimo de 52
    # semanas", asi que se trae un pool amplio (top 500 por
    # capitalizacion, paginado) y se filtra localmente comparando precio
    # actual vs. fiftyTwoWeekHigh/Low (ya vienen en cada cotizacion).
    pool_500 = _yahoo_screener_paginated(opener, crumb, filtro_base, "intradaymarketcap", "DESC", 500)

    nuevos_maximos = [
        q for q in pool_500
        if q.get("regularMarketPrice") and q.get("fiftyTwoWeekHigh")
        and q["regularMarketPrice"] >= q["fiftyTwoWeekHigh"] * 0.999
    ]
    nuevos_maximos.sort(key=lambda q: q.get("regularMarketChangePercent") or -999, reverse=True)
    out["top_nuevo_maximo"] = [_accion_mundial_item(q) for q in nuevos_maximos[:15]]

    nuevos_minimos = [
        q for q in pool_500
        if q.get("regularMarketPrice") and q.get("fiftyTwoWeekLow")
        and q["regularMarketPrice"] <= q["fiftyTwoWeekLow"] * 1.001
    ]
    nuevos_minimos.sort(key=lambda q: q.get("regularMarketChangePercent") or 999)
    out["top_nuevo_minimo"] = [_accion_mundial_item(q) for q in nuevos_minimos[:15]]

    # 7-10) Volatilidad semanal y RSI: requieren historial diario de
    # precios (no vienen en el screener), asi que se calculan sobre un
    # subconjunto acotado -- las primeras 200 del pool de 500 por
    # capitalizacion (empresas grandes/liquidas, todas ya por encima del
    # piso de USD 1.000M) -- para mantener acotada la cantidad de
    # pedidos HTTP adicionales dentro de la corrida diaria.
    candidatos_hist = pool_500[:200]
    metricas = []
    for q in candidatos_hist:
        sym = q.get("symbol")
        if not sym:
            continue
        data = fetch_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?interval=1d&range=2mo"
        )
        if not data:
            continue
        try:
            result = data["chart"]["result"][0]
            closes = result.get("indicators", {}).get("quote", [{}])[0].get("close") or []
            closes = [c for c in closes if c is not None]
        except (KeyError, IndexError, TypeError):
            continue
        if len(closes) < 15:
            continue
        vol = _volatilidad_semanal(closes)
        rsi = _rsi_14(closes)
        metricas.append((q, vol, rsi))

    con_vol = [(q, v, r) for q, v, r in metricas if v is not None]
    con_vol.sort(key=lambda x: x[1], reverse=True)
    out["top_volatilidad_alta"] = [dict(_accion_mundial_item(q), volatilidad_semanal=v) for q, v, r in con_vol[:5]]
    con_vol.sort(key=lambda x: x[1])
    out["top_volatilidad_baja"] = [dict(_accion_mundial_item(q), volatilidad_semanal=v) for q, v, r in con_vol[:5]]

    con_rsi = [(q, v, r) for q, v, r in metricas if r is not None]
    con_rsi.sort(key=lambda x: x[2], reverse=True)
    out["top_sobrecomprada"] = [dict(_accion_mundial_item(q), rsi=r) for q, v, r in con_rsi[:5]]
    con_rsi.sort(key=lambda x: x[2])
    out["top_sobrevendida"] = [dict(_accion_mundial_item(q), rsi=r) for q, v, r in con_rsi[:5]]

    if not any(out.values()):
        return None
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "secciones": out}


def build_history_acciones_mundiales(acciones_mundiales_data):
    """Historico OHLC (5 anios, diario + semanal) de todos los tickers que
    aparecen en alguna de las 10 subsecciones de Acciones Mundiales. Mismo
    patron que build_history_indices() (Yahoo chart, sin auth). Un ticker
    puede repetirse en varias subsecciones (p.ej. una accion top-cap que
    tambien esta en top-volumen); se trae el historico una sola vez por
    simbolo."""
    if not acciones_mundiales_data or not acciones_mundiales_data.get("secciones"):
        return None

    simbolos = set()
    for items in acciones_mundiales_data["secciones"].values():
        for it in items:
            if it.get("symbol"):
                simbolos.add(it["symbol"])

    series_out = {}
    for symbol in sorted(simbolos):
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
                    "v": q.get("volume", [None] * len(ts))[i] if i < len(q.get("volume", [])) else None,
                })
        except (KeyError, IndexError, TypeError):
            print(f"[WARN] No se pudo parsear histórico Yahoo para {symbol}")
            continue
        daily, weekly = build_daily_weekly(
            records,
            date_fn=lambda r: datetime.fromtimestamp(r["ts"], tz=timezone.utc).date(),
            point_fn=lambda r: {"o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "v": r["v"]},
        )
        if daily or weekly:
            series_out[symbol] = {"daily": daily, "weekly": weekly}
        time.sleep(0.3)  # cortesía con Yahoo Finance

    if not series_out:
        return None
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "series": series_out}


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


# ------------------------------------------------------------------
# Indicadores Economicos - Precios y Costo de Vida (rediseno tipo
# tarjetas, ver index.html). A diferencia de get_inflacion() (que solo
# guarda el ultimo dato para el resumen en vivo), esta funcion guarda
# la SERIE COMPLETA de los indicadores de precios que tienen fuente
# real y gratuita identificada hasta ahora: inflacion mensual,
# inflacion interanual y valor UVA, los tres via ArgentinaDatos
# (fuente primaria BCRA/INDEC, mismo servicio que ya usa get_inflacion()
# y build_history_dolar() para el dolar). Confirmado contra la
# documentacion oficial (argentinadatos.com/docs) que los tres
# endpoints devuelven [{"fecha":..., "valor":...}] con la serie
# historica completa, no solo el ultimo punto.
#
# El resto de "Precios y Costo de Vida" (Canasta Basica, IPC Nucleo,
# etc.) y las otras 10 categorias de Indicadores Economicos
# (Agregados Monetarios, Sector Fiscal, Comercio Internacional,
# Reservas y Deuda, Empleo y Salarios, Jubilaciones y Social, Actividad
# y Consumo, Industria y Energia, Campo y Bioeconomia, Construccion e
# Inmobiliario) siguen mostrando datos de muestra ilustrativos en el
# frontend (marcados como tales): no se identifico todavia una fuente
# gratuita con serie historica para esos indicadores. Se iran
# reemplazando categoria por categoria en proximas sesiones.
# ------------------------------------------------------------------

def _serie_argentinadatos(url):
    data = fetch_json(url)
    if not data or not isinstance(data, list):
        return []
    pts = [{"fecha": d.get("fecha"), "valor": d.get("valor")} for d in data
           if d.get("fecha") and d.get("valor") is not None]
    pts.sort(key=lambda p: p["fecha"])
    return pts


def build_history_indicadores_precios():
    out = {}

    mensual = _serie_argentinadatos("https://api.argentinadatos.com/v1/finanzas/indices/inflacion")
    if mensual:
        out["ipc_mensual"] = {
            "nombre": "Inflación IPC - Tasa Mensual",
            "unidad": "%", "tipo": "variacion", "periodicidad": "Mensual",
            "fuente": "INDEC", "serie": mensual,
        }

    interanual = _serie_argentinadatos("https://api.argentinadatos.com/v1/finanzas/indices/inflacionInteranual")
    if interanual:
        out["ipc_interanual"] = {
            "nombre": "Inflación IPC - Interanual",
            "unidad": "%", "tipo": "variacion", "periodicidad": "Mensual",
            "fuente": "INDEC", "serie": interanual,
        }

    uva = _serie_argentinadatos("https://api.argentinadatos.com/v1/finanzas/indices/uva")
    if uva:
        out["uva"] = {
            "nombre": "Valor UVA",
            "unidad": "$", "tipo": "valor", "periodicidad": "Diaria",
            "fuente": "BCRA", "serie": uva,
        }

    if not out:
        return None
    out["_updated_at"] = datetime.now(timezone.utc).isoformat()
    return out


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


# ------------------------------------------------------------------
# Agregados Monetarios (Indicadores Economicos, tarjetas). En vez de
# hardcodear los idVariable de Base Monetaria/Reservas/M2 (el catalogo
# de variables del BCRA se renumero entre v3 y v4, y no se pudo
# verificar en vivo desde este entorno sandbox contra la version
# actual por restricciones de red), se consulta el catalogo OFICIAL
# completo (GET /estadisticas/v4.0/Monetarias sin idVariable, la misma
# variable que ya se usa via bcra_fetch_all para CER/banda cambiaria/
# tasas) y se busca en vivo, por texto de la descripcion, la variable
# correcta en cada corrida. Si no se encuentra una coincidencia clara,
# se omite esa serie (no se arriesga a usar un id incorrecto).
# ------------------------------------------------------------------

_bcra_catalogo_cache = None


def _bcra_catalogo_monetarias():
    global _bcra_catalogo_cache
    if _bcra_catalogo_cache is not None:
        return _bcra_catalogo_cache
    resultados = []
    offset = 0
    while True:
        data = fetch_json(f"https://api.bcra.gob.ar/estadisticas/v4.0/monetarias?limit=1000&offset={offset}")
        if not data or "results" not in data:
            break
        pagina = data["results"]
        resultados.extend(pagina)
        count = data.get("metadata", {}).get("resultset", {}).get("count", 0)
        offset += 1000
        if offset >= count or not pagina:
            break
    _bcra_catalogo_cache = resultados
    return resultados


def _bcra_find_variable(keyword, excluir=None):
    """Busca en el catalogo oficial de variables monetarias del BCRA
    (consultado en vivo en cada corrida, no hardcodeado) la primera
    variable cuya descripcion contenga el texto dado (case-insensitive).
    Devuelve (idVariable, descripcion) o (None, None) si no se
    encuentra ninguna coincidencia."""
    catalogo = _bcra_catalogo_monetarias()
    kw = keyword.lower()
    for item in catalogo:
        desc = (item.get("descripcion") or "")
        desc_low = desc.lower()
        if kw in desc_low and (not excluir or excluir.lower() not in desc_low):
            return item.get("idVariable"), desc
    return None, None


# (nombre_interno, texto_a_buscar_en_la_descripcion, texto_a_excluir_o_None)
AGREGADOS_MONETARIOS_BUSQUEDA = [
    ("base_monetaria", "base monetaria", None),
    ("reservas_internacionales", "reservas internacionales", None),
    ("m2_privado", "m2 privado", None),
    ("circulacion_monetaria", "circulación monetaria", None),
]


def build_history_agregados_monetarios():
    hoy = date.today()
    desde = (hoy - timedelta(days=5 * 365 + 10)).isoformat()
    hasta = hoy.isoformat()
    out = {}
    for key, keyword, excluir in AGREGADOS_MONETARIOS_BUSQUEDA:
        id_var, descripcion = _bcra_find_variable(keyword, excluir)
        if id_var is None:
            print(f"[WARN] No se encontro en el catalogo BCRA una variable que coincida con '{keyword}': se omite.")
            continue
        detalle = bcra_fetch_all(id_var, desde, hasta)
        if not detalle:
            continue
        serie = [{"fecha": d.get("fecha"), "valor": d.get("valor")} for d in detalle
                 if d.get("fecha") and d.get("valor") is not None]
        serie.sort(key=lambda p: p["fecha"])
        if not serie:
            continue
        out[key] = {
            "nombre": descripcion, "unidad": "", "tipo": "valor",
            "periodicidad": "Mensual", "fuente": "BCRA", "serie": serie,
        }
    if not out:
        return None
    out["_updated_at"] = datetime.now(timezone.utc).isoformat()
    return out

# ------------------------------------------------------------------
# Actividad Economica (Indicadores Economicos, tarjetas). Fuente: API
# de Series de Tiempo de la Republica Argentina (apis.datos.gob.ar),
# oficial, gratuita, sin autenticacion, mantenida por Jefatura de
# Gabinete de Ministros, que republica series de INDEC/Ministerio de
# Economia con id estables.
#
# El endpoint de catalogo masivo (apis.datos.gob.ar/series/api/dump/...)
# devolvio 403 Forbidden en la corrida real de GitHub Actions, con o
# sin User-Agent de navegador: es un bloqueo de infraestructura (WAF
# del lado del gobierno bloqueando rangos de IP de datacenters en la
# nube, algo comun en sitios .gob.ar), no un problema de headers.
#
# En vez de insistir con ese mismo endpoint, se usa la API de busqueda
# de CKAN del portal (www.datos.gob.ar/api/3/action/package_search),
# un dominio y servicio distinto, para descubrir datasets de EMAE y
# extraer los identificadores de serie desde los metadatos de campo de
# cada recurso (el mismo identificador que se ve en la tabla "Campos
# de este recurso" de la pagina publica del dataset). Si este segundo
# dominio tambien esta bloqueado, o no se encuentra ninguna serie con
# datos recientes, se omite con un warning (nunca se muestra un dato
# no verificado o desactualizado).

CKAN_SEARCH_URL = "https://www.datos.gob.ar/api/3/action/package_search"

import re as _re

_SERIE_ID_RE = _re.compile(r"^\d+(\.\d+)?_[A-Za-z0-9_]+$")


def _series_tiempo_candidatas(keyword, excluir=None, frecuencia=None):
    """Busca datasets en el portal (CKAN, dominio www.datos.gob.ar,
    distinto del endpoint de catalogo masivo que devolvio 403) que
    coincidan con el texto dado, y extrae de sus recursos los
    identificadores de serie candidatos junto con el titulo/
    descripcion de cada campo. No implica que sea la serie vigente:
    eso se valida aparte, por recencia real de datos."""
    out = []
    try:
        url = f"{CKAN_SEARCH_URL}?q={urllib.parse.quote(keyword)}&rows=20"
        data = fetch_json(url)
    except Exception as exc:
        print(f"[WARN] Fallo al buscar '{keyword}' en la API CKAN de datos.gob.ar: {exc}")
        data = None
    if not data or not data.get("success"):
        if data is not None:
            print(f"[WARN] La API CKAN de datos.gob.ar no devolvio resultados validos para '{keyword}'.")
        return out
    resultados = data.get("result", {}).get("results", [])
    kw = keyword.lower()
    for paquete in resultados:
        for recurso in paquete.get("resources", []):
            campos = (recurso.get("schema") or {}).get("fields") or recurso.get("fields") or []
            for campo in campos:
                serie_id = campo.get("id")
                if not serie_id or not _SERIE_ID_RE.match(str(serie_id)):
                    continue
                titulo = str(campo.get("title") or campo.get("description") or "")
                texto = (titulo + " " + str(campo.get("description") or "")).lower()
                if kw.split()[0] not in texto and "emae" not in texto and "actividad" not in texto:
                    continue
                if excluir and excluir.lower() in texto:
                    continue
                out.append((serie_id, titulo or serie_id))
    return out


def _series_tiempo_fetch(serie_id, start_date=None):
    """Descarga el historico completo de una serie de la API de Series
    de Tiempo (paginado, limite 1000 por pagina)."""
    out = []
    start = 0
    while True:
        url = (
            f"https://apis.datos.gob.ar/series/api/series/?ids={serie_id}"
            f"&format=json&metadata=none&limit=1000&start={start}&sort=asc"
        )
        if start_date:
            url += f"&start_date={start_date}"
        data = fetch_json(url)
        if not data or "data" not in data or not data["data"]:
            break
        rows = data["data"]
        out.extend(rows)
        if len(rows) < 1000:
            break
        start += 1000
    return out


def _series_tiempo_elegir_vigente(candidatas, dias_recencia_max=150):
    """De una lista de series candidatas (mismo texto de busqueda),
    consulta el ultimo dato real de cada una y devuelve la primera
    cuyo ultimo dato sea reciente (dentro de dias_recencia_max desde
    hoy). Evita mostrar una serie discontinuada como si fuera vigente."""
    hoy = date.today()
    for serie_id, titulo in candidatas:
        url = f"https://apis.datos.gob.ar/series/api/series/?ids={serie_id}&last=1&format=json&metadata=none"
        data = fetch_json(url)
        if not data or not data.get("data"):
            continue
        ultima_fecha_str = data["data"][-1][0]
        ultima_fecha = _parse_date(ultima_fecha_str)
        if ultima_fecha and (hoy - ultima_fecha).days <= dias_recencia_max:
            return serie_id, titulo
        time.sleep(0.3)
    return None, None


def build_history_actividad():
    out = {}

    candidatas = _series_tiempo_candidatas(
        "estimador mensual de actividad", excluir="apertura", frecuencia="M"
    )
    candidatas_deses = [c for c in candidatas if "desestacional" in c[1].lower()]
    lista_final = candidatas_deses or candidatas
    # Diagnostico: se agrega al final el id historico ya confirmado
    # (aunque discontinuado desde 2012) para verificar, en cada
    # corrida, si el dominio apis.datos.gob.ar/series/api/series es
    # alcanzable desde este entorno. Si tambien devuelve error, el
    # bloqueo es de todo el dominio, no solo del endpoint de catalogo.
    lista_final = lista_final + [("143.3_NO_PR_2004_A_31", "EMAE desestacionalizado (id historico, diagnostico)")]
    serie_id, titulo = _series_tiempo_elegir_vigente(lista_final)
    if serie_id is None:
        print("[WARN] No se encontro ninguna serie vigente de EMAE desestacionalizado (ni via CKAN ni el id historico de diagnostico): se omite.")
    else:
        rows = _series_tiempo_fetch(serie_id, start_date="2004-01-01")
        serie = [{"fecha": r[0], "valor": r[1]} for r in rows if r[0] and r[1] is not None]
        serie.sort(key=lambda p: p["fecha"])
        if serie:
            out["emae_desestacionalizado"] = {
                "nombre": "EMAE (Estimador Mensual de Actividad Económica) - Serie desestacionalizada",
                "unidad": "Índice 2004=100", "tipo": "valor", "periodicidad": "Mensual",
                "fuente": "INDEC (vía Series de Tiempo, Jefatura de Gabinete)", "serie": serie,
            }

    if not out:
        return None
    out["_updated_at"] = datetime.now(timezone.utc).isoformat()
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


# ------------------------------------------------------------------
# Historia OHLC generica para instrumentos sin cobertura en data912
# (ONs, algunos soberanos USD, la mayoria de los bonos CER, LECAPs/
# BONCAPs en pesos): se auto-acumula un punto por dia (o=h=l=c=precio
# de cierre del dia, ya que las fuentes gratuitas disponibles solo dan
# un precio de cierre diario, no rango intradiario real) en un archivo
# que se conserva entre corridas del workflow. La serie crece dia a dia
# desde que se empieza a trackear cada instrumento.
# ------------------------------------------------------------------

def _accumulate_ohlc_series(path, today_points, max_records=2000):
    """
    today_points: dict symbol -> {"o","h","l","c","v"} de hoy.
    Devuelve el acumulado completo {symbol: [{"date": iso, "o","h","l","c","v"}, ...]}.
    """
    existing = load_json(path) or {}
    hoy = date.today().isoformat()
    for sym, pt in today_points.items():
        if pt.get("c") is None:
            continue
        serie = existing.setdefault(sym, [])
        record = {"date": hoy, "o": pt.get("o"), "h": pt.get("h"), "l": pt.get("l"),
                  "c": pt.get("c"), "v": pt.get("v")}
        if serie and serie[-1].get("date") == hoy:
            serie[-1] = record
        else:
            serie.append(record)
        if len(serie) > max_records:
            existing[sym] = serie[-max_records:]
    save_json(path, existing)
    return existing


def _empirical_vr_adjust(points, payment_dates, window_days=6, umbral=-0.02):
    """
    points: lista de dicts con 'date' (ISO) y 'o','h','l','c', ordenada o no.
    payment_dates: fechas ISO de pagos conocidos (pasados y futuros) del
    instrumento (de *_FLUJOS). Cuando no se conoce el desglose exacto
    amortizacion/interes de cada pago (caso de SOBERANOS_FLUJOS y
    ON_FLUJOS, que traen el monto total del cupon), se detecta empiricamente:
    si hay una caida de precio fuerte (> umbral) dentro de una ventana
    alrededor de una fecha de pago conocida, se interpreta esa caida como
    amortizacion de capital (no perdida real, ya que el bono se cotiza
    "por 100 de valor nominal ORIGINAL") y se reescala el "precio tecnico"
    de todos los puntos posteriores para que la serie sea continua.
    """
    pts = sorted(points, key=lambda r: r.get("date") or "")
    if not pts or not payment_dates:
        return pts
    closes = [(p.get("date"), p.get("c")) for p in pts]
    cum_factor = 1.0
    applied_from = []
    for fecha_pago in payment_dates:
        exp = _parse_date(fecha_pago)
        if exp is None:
            continue
        lo = (exp - timedelta(days=window_days)).isoformat()
        hi = (exp + timedelta(days=window_days)).isoformat()
        best_date, best_drop = None, umbral
        for i in range(1, len(closes)):
            d, c = closes[i]
            if not d or not (lo <= d <= hi) or c is None:
                continue
            prev_c = closes[i - 1][1]
            if not prev_c:
                continue
            chg = (c - prev_c) / prev_c
            if chg < best_drop:
                best_drop, best_date = chg, d
        if best_date:
            cum_factor = cum_factor / (1.0 + best_drop)
            applied_from.append((best_date, cum_factor))
    if not applied_from:
        return pts
    applied_from.sort(key=lambda x: x[0])
    for p in pts:
        d = p.get("date")
        factor = 1.0
        for fecha_desde, f in applied_from:
            if d and d >= fecha_desde:
                factor = f
        if factor != 1.0:
            for k in ("o", "h", "l", "c"):
                if p.get(k) is not None:
                    p[k] = round(p[k] * factor, 4)
    return pts


def _build_bond_series(records, payment_dates=None, exact_schedule=None):
    """
    records: lista de dicts con 'date' (ISO) y 'o','h','l','c','v'.
    exact_schedule: [(fecha_pago, pct_amortizado_0a100), ...] cuando se
    conoce el desglose exacto de amortizacion (bonos CER, via
    CER_FLUJOS, o los soberanos con cronograma oficial conocido via
    BOND_AMORT_SCHEDULES) - mas preciso que la deteccion empirica.
    payment_dates: fechas de pago conocidas, usadas solo si no hay
    exact_schedule (deteccion empirica de saltos, ver _empirical_vr_adjust).
    Devuelve (daily, weekly) ya ajustados por valor residual.
    """
    pts = [dict(r) for r in (records or [])]
    if exact_schedule:
        # La fecha teorica del cronograma oficial puede no coincidir con
        # la fecha real en que el mercado refleja el pago (settlement,
        # feriados, convencion de cada camara compensadora) - se ajusta
        # cada fecha teorica a la "ex-fecha" real detectada en los datos
        # (ver _find_ex_date), igual que hacia build_history_bonos().
        dated_closes = sorted(
            [(p.get("date"), p.get("c")) for p in pts if p.get("c") is not None and p.get("date")],
            key=lambda x: x[0],
        )
        effective_schedule = [
            (_find_ex_date(dated_closes, fecha_pago), pct) for fecha_pago, pct in exact_schedule
        ]
        for p in pts:
            vr = _residual_value_from_schedule(effective_schedule, p.get("date"))
            factor = 100.0 / vr
            for k in ("o", "h", "l", "c"):
                if p.get(k) is not None:
                    p[k] = round(p[k] * factor, 4)
    elif payment_dates:
        pts = _empirical_vr_adjust(pts, payment_dates)

    daily, weekly = build_daily_weekly(
        pts,
        date_fn=lambda r: _parse_date(r.get("date")),
        point_fn=lambda r: {"o": r.get("o"), "h": r.get("h"), "l": r.get("l"),
                              "c": r.get("c"), "v": r.get("v")},
    )
    return daily, weekly


# ------------------------------------------------------------------
# Historia OHLC de Bonos Soberanos en USD, ajustada por valor residual.
# 10 de los 15 tickers tienen historia real (OHLCV) en data912; los
# otros 5 (AO27, AO28, AO29, AN29, BPD7 - bonos del Tesoro en USD que
# no forman parte de la reestructuracion 2020) no tienen fuente publica
# con historico, asi que se auto-acumulan desde hoy.
# ------------------------------------------------------------------

BONOS_USD_DATA912 = ["AE38", "AL29", "AL30", "AL35", "AL41", "GD29", "GD30", "GD35", "GD38", "GD41"]
# GD46 (agregado 2026-07-20, tarea #57) no estaba en ninguna de las dos
# listas, por lo que build_history_bonos_usd() nunca generaba ni
# siquiera un punto auto-acumulado para el. Se agrega a la lista de
# auto-acum, que intenta IOL y despues bonistas.com/api/bond/GD46
# (confirmado con historia real).
BONOS_USD_AUTOACUM = ["AO27", "AO28", "AO29", "AN29", "BPD7", "GD46"]


def build_history_bonos_usd():
    out = {}
    for sym in BONOS_USD_DATA912:
        data = fetch_json(f"https://data912.com/historical/bonds/{sym}")
        if not data or not isinstance(data, list):
            continue
        records = [{"date": str(r.get("date", ""))[:10], "o": r.get("o"), "h": r.get("h"),
                    "l": r.get("l"), "c": r.get("c"), "v": r.get("v")} for r in data]
        info = SOBERANOS_FLUJOS.get(sym, {})
        payment_dates = [f for f, m in info.get("flujos", [])]
        exact = BOND_AMORT_SCHEDULES.get(sym)
        daily, weekly = _build_bond_series(records, payment_dates, exact_schedule=exact)
        if daily or weekly:
            out[sym] = {"daily": daily, "weekly": weekly}

    # Para los tickers sin cobertura en data912, se intenta primero
    # iol.invertironline.com (historia real mucho mas profunda, sin
    # login), despues bonistas.com, y recien al final se cae a la
    # auto-acumulacion diaria.
    IOL_USD_MAP = {"AO27": "AO27", "AO28": "AO28", "AN29": "AN29", "BPD7": "BPD7D", "GD46": "GD46D"}
    BONISTAS_USD_MAP = {"AO27": "AO27", "AO28": "AO28", "AN29": "AN29", "BPD7": "BPD7D", "GD46": "GD46"}
    faltantes = []
    for sym in BONOS_USD_AUTOACUM:
        ticker_iol = IOL_USD_MAP.get(sym)
        records = _fetch_iol_history(ticker_iol) if ticker_iol else None
        if records:
            info = SOBERANOS_FLUJOS.get(sym, {})
            payment_dates = [f for f, m in info.get("flujos", [])]
            exact = BOND_AMORT_SCHEDULES.get(sym)
            daily, weekly = _build_bond_series(records, payment_dates, exact_schedule=exact)
            if daily or weekly:
                out[sym] = {"daily": daily, "weekly": weekly}
                continue
        ticker_bonistas = BONISTAS_USD_MAP.get(sym)
        records = _fetch_bonistas_history(ticker_bonistas) if ticker_bonistas else None
        if records:
            daily, weekly = _series_from_adjusted_records(records)
            if daily or weekly:
                out[sym] = {"daily": daily, "weekly": weekly}
                continue
        faltantes.append(sym)

    live = fetch_json("https://rendimientos.co/api/soberanos")
    today_points = {}
    if live and "data" in live:
        for item in live["data"]:
            sym = item.get("symbol")
            precio = item.get("price_usd")
            if sym in faltantes and precio is not None:
                today_points[sym] = {"o": precio, "h": precio, "l": precio, "c": precio, "v": None}
    acumulado = _accumulate_ohlc_series(os.path.join(HISTORY_DIR, "_acum_bonos_usd.json"), today_points)
    for sym in faltantes:
        info = SOBERANOS_FLUJOS.get(sym, {})
        payment_dates = [f for f, m in info.get("flujos", [])]
        daily, weekly = _build_bond_series(acumulado.get(sym, []), payment_dates)
        if daily or weekly:
            out[sym] = {"daily": daily, "weekly": weekly}

    if not out:
        return None
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "moneda": "USD",
        "ajuste": "precio_tecnico_por_valor_residual",
        "series": out,
    }


# ------------------------------------------------------------------
# Historia OHLC de Bonos CER, ajustada por valor residual. 4 de los 14
# tickers tienen historia real en data912; el resto se auto-acumula.
# El ajuste por VR es exacto (no empirico) porque CER_FLUJOS ya trae el
# desglose de "amortizacion" por separado del interes.
# ------------------------------------------------------------------

BONOS_CER_DATA912 = ["TX26", "TX28", "DICP", "PARP"]


def _cer_amort_schedule(config_key):
    info = CER_FLUJOS.get(config_key)
    if not info:
        return None
    return [(f, amort * 100) for f, amort, tasa, base in info["flujos"] if amort]


def build_history_bonos_cer():
    out = {}
    for sym in BONOS_CER_DATA912:
        data = fetch_json(f"https://data912.com/historical/bonds/{sym}")
        if not data or not isinstance(data, list):
            continue
        records = [{"date": str(r.get("date", ""))[:10], "o": r.get("o"), "h": r.get("h"),
                    "l": r.get("l"), "c": r.get("c"), "v": r.get("v")} for r in data]
        daily, weekly = _build_bond_series(records, exact_schedule=_cer_amort_schedule(sym))
        if daily or weekly:
            out[sym] = {"daily": daily, "weekly": weekly}

    # Para el resto (10 de 14), se intenta primero bonistas.com (mismo
    # ticker, historia real ya ajustada por su propio vR) antes de caer
    # a la auto-acumulacion diaria.
    resto = [s for s in CER_FLUJOS if s not in BONOS_USD_DATA912 and s not in BONOS_CER_DATA912]
    faltantes = []
    for sym in resto:
        records = _fetch_iol_history(sym)
        if records:
            daily, weekly = _build_bond_series(records, exact_schedule=_cer_amort_schedule(sym))
            if daily or weekly:
                out[sym] = {"daily": daily, "weekly": weekly}
                continue
        records = _fetch_bonistas_history(sym)
        if records:
            daily, weekly = _series_from_adjusted_records(records)
            if daily or weekly:
                out[sym] = {"daily": daily, "weekly": weekly}
                continue
        faltantes.append(sym)

    live = fetch_json("https://rendimientos.co/api/cer-precios")
    today_points = {}
    if live and "data" in live:
        for item in live["data"]:
            sym = item.get("symbol")
            precio = item.get("c")
            if sym in faltantes and precio is not None:
                today_points[sym] = {"o": precio, "h": precio, "l": precio, "c": precio, "v": None}
    acumulado = _accumulate_ohlc_series(os.path.join(HISTORY_DIR, "_acum_bonos_cer.json"), today_points)
    for sym in faltantes:
        daily, weekly = _build_bond_series(acumulado.get(sym, []), exact_schedule=_cer_amort_schedule(sym))
        if daily or weekly:
            out[sym] = {"daily": daily, "weekly": weekly}

    if not out:
        return None
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "moneda": "ARS",
        "ajuste": "precio_tecnico_por_valor_residual",
        "series": out,
    }


# ------------------------------------------------------------------
# Historia OHLC de Bonos en Pesos (LECAP/BONCAP). Son bullet (un unico
# pago al vencimiento, sin amortizaciones intermedias), asi que no
# necesitan ajuste por valor residual. Se reutiliza/amplia el mismo
# archivo que ya se usaba para calcular la variacion diaria.
# ------------------------------------------------------------------

def build_history_bonos_pesos():
    out = {}
    faltantes = []
    # TY30P (BONCAP cupon fijo periodico, tarea #58) se agrega 2026-07-20
    # al mismo bucle IOL/bonistas que LECAP_TERMS: no estaba en ninguna
    # lista de build_history_bonos_pesos(), por lo que nunca tenia
    # historia (ni siquiera auto-acumulada). bonistas.com ya devuelve la
    # serie ajustada por su propio vR (_series_from_adjusted_records),
    # igual que para los bullet de LECAP_TERMS.
    todos_los_simbolos = list(LECAP_TERMS.keys()) + list(BONOS_PESOS_CUPON_FLUJOS.keys())
    for sym in todos_los_simbolos:
        # Bullet (un unico pago al vencimiento): no hace falta ajuste
        # por valor residual. iol.invertironline.com da mucha mas
        # profundidad historica real que bonistas.com y que la
        # auto-acumulacion propia.
        records = _fetch_iol_history(sym)
        if records:
            daily, weekly = _series_from_adjusted_records(records)
            if daily or weekly:
                out[sym] = {"daily": daily, "weekly": weekly}
                continue
        records = _fetch_bonistas_history(sym)
        if records:
            daily, weekly = _series_from_adjusted_records(records)
            if daily or weekly:
                out[sym] = {"daily": daily, "weekly": weekly}
                continue
        faltantes.append(sym)

    path = os.path.join(HISTORY_DIR, "bonos_pesos_precios.json")
    existing = load_json(path) or {"series": {}}
    series = existing.get("series", {})
    for sym in faltantes:
        puntos = series.get(sym, [])
        records = [{"date": p.get("t"), "o": p.get("c"), "h": p.get("c"),
                    "l": p.get("c"), "c": p.get("c"), "v": None} for p in puntos]
        daily, weekly = _build_bond_series(records)
        if daily or weekly:
            out[sym] = {"daily": daily, "weekly": weekly}

    if not out:
        return None
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "moneda": "ARS",
        "ajuste": "sin_ajuste_bullet",
        "series": out,
    }


# ------------------------------------------------------------------
# Historia OHLC de Bonos TAMAR y Duales/Dolar Linked (agregado
# 2026-07-20, tarea #61). Estas dos categorias nunca tuvieron una
# funcion build_history propia (a diferencia de CER/Pesos/USD/ONs), por
# lo que sus graficos de "Evolucion" en el frontend caian en
# renderSampleChartFor() (datos de muestra, no reales) al no encontrar
# la clave en REAL_DATA_CONFIG. Mismo patron IOL -> bonistas.com que el
# resto (sin ajuste por VR propio, ya que estas categorias tampoco lo
# tienen en la cotizacion en vivo, ver comentario en get_bonos_tamar()).
# ------------------------------------------------------------------

def _build_history_bonistas_iol(tickers, precios_filename):
    out = {}
    faltantes = []
    for sym in tickers:
        records = _fetch_iol_history(sym)
        if records:
            daily, weekly = _series_from_adjusted_records(records)
            if daily or weekly:
                out[sym] = {"daily": daily, "weekly": weekly}
                continue
        records = _fetch_bonistas_history(sym)
        if records:
            daily, weekly = _series_from_adjusted_records(records)
            if daily or weekly:
                out[sym] = {"daily": daily, "weekly": weekly}
                continue
        faltantes.append(sym)

    if faltantes:
        path = os.path.join(HISTORY_DIR, precios_filename)
        existing = load_json(path) or {"series": {}}
        series = existing.get("series", {})
        for sym in faltantes:
            puntos = series.get(sym, [])
            records = [{"date": p.get("t"), "o": p.get("c"), "h": p.get("c"),
                        "l": p.get("c"), "c": p.get("c"), "v": None} for p in puntos]
            daily, weekly = _build_bond_series(records)
            if daily or weekly:
                out[sym] = {"daily": daily, "weekly": weekly}

    if not out:
        return None
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "moneda": "ARS",
        "ajuste": "sin_ajuste_bullet",
        "series": out,
    }


def build_history_bonos_pesos_tamar():
    return _build_history_bonistas_iol(list(TAMAR_BONOS.keys()), "bonos_pesos_tamar_precios.json")


def build_history_bonos_pesos_duales():
    return _build_history_bonistas_iol(list(DUALES_DOLARLINKED_BONOS.keys()), "bonos_pesos_duales_precios.json")


# ------------------------------------------------------------------
# Historia OHLC de Obligaciones Negociables en USD. Sin cobertura en
# data912: se auto-acumula desde hoy para los 53 tickers trackeados en
# ON_FLUJOS (no solo el top-N por volumen del dia, para que la serie no
# tenga huecos si un ticker sale/entra del top-N de un dia a otro). El
# ajuste por VR es empirico (ON_FLUJOS trae el monto total del cupon,
# sin desglose amortizacion/interes).
# ------------------------------------------------------------------

def build_history_ons_usd():
    out = {}
    faltantes = []
    for config_key, info in ON_FLUJOS.items():
        # iol.invertironline.com usa el mismo ticker de liquidacion en
        # USD (ticker_d912, p.ej. "MGCRD") como simbolo de busqueda.
        # Cubre practicamente todas las 53 ONs trackeadas, con
        # historia real de meses/anios (precios brutos, se ajustan por
        # VR empirico igual que la auto-acumulacion).
        ticker = info["ticker_d912"]
        records = _fetch_iol_history(ticker)
        if records:
            payment_dates = [f for f, m in info["flujos"]]
            daily, weekly = _build_bond_series(records, payment_dates)
            if daily or weekly:
                out[config_key] = {"daily": daily, "weekly": weekly}
                continue
        # bonistas.com como segundo intento (ya trae ajuste por su
        # propio vR).
        records = _fetch_bonistas_history(ticker)
        if records:
            daily, weekly = _series_from_adjusted_records(records)
            if daily or weekly:
                out[config_key] = {"daily": daily, "weekly": weekly}
                continue
        faltantes.append(config_key)

    live = fetch_json("https://rendimientos.co/api/ons")
    if live and "data" in live:
        by_ticker = {info["ticker_d912"]: key for key, info in ON_FLUJOS.items()}
        today_points = {}
        for item in live["data"]:
            sym = item.get("symbol")
            config_key = by_ticker.get(sym)
            precio = item.get("c")
            if config_key in faltantes and precio is not None:
                today_points[config_key] = {"o": precio, "h": precio, "l": precio, "c": precio, "v": item.get("v")}
        acumulado = _accumulate_ohlc_series(os.path.join(HISTORY_DIR, "_acum_ons_usd.json"), today_points)
        for config_key in faltantes:
            info = ON_FLUJOS[config_key]
            registros = acumulado.get(config_key, [])
            if not registros:
                continue
            payment_dates = [f for f, m in info["flujos"]]
            daily, weekly = _build_bond_series(registros, payment_dates)
            if daily or weekly:
                out[config_key] = {"daily": daily, "weekly": weekly}

    if not out:
        return None
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "moneda": "USD",
        "ajuste": "precio_tecnico_por_valor_residual",
        "series": out,
    }


# ------------------------------------------------------------------
# bonistas.com: fuente publica y gratuita adicional con historia diaria
# real (o,h,l,c,v) para muchos bonos CER, LECAP/BONCAP, algunos
# soberanos USD, y algunas ONs que no tienen cobertura en data912. La
# pagina es un sitio Next.js estatico (SSG): el HTML plano ya trae el
# JSON completo embebido en <script id="__NEXT_DATA__">, sin necesidad
# de ejecutar JS ni de un navegador. Ademas del precio, el sitio ya
# calcula un "valor residual" (vR, fraccion 0-1 del capital original no
# amortizado) por dia - se usa para ajustar el precio "por 100 de VR
# vigente" a "por 100 de capital ORIGINAL" (factor = 1/vR), el mismo
# criterio de valor tecnico/paridad que ya se usa en el resto del
# script, pero tomado directo del vR que reporta la fuente en vez de
# estimarlo (mas preciso).
# ------------------------------------------------------------------

def _fetch_bonistas_history(ticker):
    # Reescrito 2026-07-20 (mismo hallazgo que _fetch_bonistas_bond_info):
    # la pagina HTML bono-cotizacion-rendimiento-precio-hoy/{ticker}
    # redirige al home de bonistas.com para varios tickers (GD46, TMF27,
    # TTD26, TZV27, los 9 CER nuevos, TO26, TY30P, entre otros),
    # provocando que su historia caiga siempre en auto-acumulacion (1
    # solo punto por dia desde que se agrego el ticker). El endpoint
    # JSON https://bonistas.com/api/bond/{ticker} si publica el campo
    # "history" completo (verificado: 11 a 252 registros) para todos
    # los tickers probados, con las mismas columnas (fecha/open/high/
    # low/close/volume/vR) que se parseaban de la pagina HTML.
    data = fetch_json(f"https://bonistas.com/api/bond/{ticker}")
    if not data or not isinstance(data, dict):
        return None
    h = data.get("history")
    if not isinstance(h, dict):
        return None
    fechas = h.get("fecha")
    if not fechas:
        return None
    n = len(fechas)

    def _col(name):
        vals = h.get(name) or []
        # Algunos tickers traen columnas mas cortas que "fecha" (datos
        # inconsistentes del lado de la fuente); se rellena con None en
        # vez de asumir que todas las columnas tienen el mismo largo.
        return (list(vals) + [None] * n)[:n]

    opens, highs, lows, closes = _col("open"), _col("high"), _col("low"), _col("close")
    vols, vrs = _col("volume"), _col("vR")
    out = []
    for i, f in enumerate(fechas):
        vr = vrs[i]
        factor = (1.0 / vr) if vr else 1.0
        def _adj(v):
            return round(v * factor, 4) if v is not None else None
        out.append({
            "date": f,
            "o": _adj(opens[i]), "h": _adj(highs[i]), "l": _adj(lows[i]), "c": _adj(closes[i]),
            "v": vols[i],
        })
    return out


# ------------------------------------------------------------------
# iol.invertironline.com: fuente publica y gratuita, SIN login, con
# historia diaria real (o,h,l,c,v) mucho mas profunda y con mayor
# cobertura que data912 y bonistas.com juntos (verificado: cubre CER,
# USD soberanos, LECAP/BONCAP y las 53 ONs trackeadas, con series de
# hasta varios anios). Flujo de 2 pedidos, sin cookies ni token CSRF:
#   1) GET /titulo/datoshistoricos?simbolo=X&mercado=bcba -> resuelve
#      el "idtitulo" interno (input oculto en el HTML).
#   2) POST /Titulo/DatosHistoricos con idtitulo + rango de fechas
#      amplio -> devuelve un fragmento HTML con la tabla completa.
# Los precios que devuelve son BRUTOS (sin ajustar por vR), a
# diferencia de bonistas.com, asi que se procesan con el mismo
# _build_bond_series (ajuste exacto/empirico) que data912.
# ------------------------------------------------------------------

def _fetch_iol_history(ticker):
    try:
        page = fetch_text(
            f"https://iol.invertironline.com/titulo/datoshistoricos?simbolo={ticker.lower()}&mercado=bcba"
        )
        if not page:
            return None
        m = re.search(r'id="IdTitulo"[^>]*value="(\d+)"', page)
        if not m:
            return None
        idtitulo = m.group(1)
        hoy = datetime.now().strftime("%d/%m/%Y")
        body = urllib.parse.urlencode({
            "desdehasta": f"01/01/2015 - {hoy}",
            "idtitulo": idtitulo,
            "idfrecuencias": "1",
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://iol.invertironline.com/Titulo/DatosHistoricos",
            data=body,
            headers={
                "User-Agent": "monitor-real-bot/1.0",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[WARN] Fallo IOL historico {ticker}: {e}")
        return None

    def _num(s):
        s = s.strip().replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    out = []
    for row_html in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.S)]
        if len(cells) < 5:
            continue
        dm = re.match(r'(\d{2})/(\d{2})/(\d{4})', cells[0])
        if not dm:
            continue
        mm, dd, yyyy = dm.groups()
        fecha_iso = f"{yyyy}-{mm}-{dd}"
        o, h, l, c = _num(cells[1]), _num(cells[2]), _num(cells[3]), _num(cells[4])
        vol = _num(cells[7]) if len(cells) > 7 else None
        if c is None:
            continue
        out.append({"date": fecha_iso, "o": o, "h": h, "l": l, "c": c, "v": vol})
    out.sort(key=lambda r: r["date"])
    return out or None


def _series_from_adjusted_records(records):
    """Ya vienen ajustados (bonistas aplica vR); solo arma daily/weekly."""
    return build_daily_weekly(
        records,
        date_fn=lambda r: _parse_date(r.get("date")),
        point_fn=lambda r: {"o": r.get("o"), "h": r.get("h"), "l": r.get("l"),
                              "c": r.get("c"), "v": r.get("v")},
    )


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
        "fci_secciones": get_fci_secciones(),
        "bonos_soberanos_usd": get_bonos_soberanos_usd(),
        "bonos_cer": get_bonos_cer(),
        "bonos_pesos": get_bonos_pesos(),
        "bonos_pesos_tamar": get_bonos_tamar(),
        "bonos_pesos_duales": get_bonos_duales_dolarlinked(),
        "ons_usd": get_ons_usd(),
        "acciones_arg": get_acciones_arg(),
        "indices": get_indices_globales(),
        "commodities": get_commodities_globales(),
        "rates_intl": get_tasas_internacionales(),
        "plazos_fijos": get_plazos_fijos(),
        "acciones_mundiales": get_acciones_mundiales(),
    }

    td = get_twelvedata()
    if td:
        # Retornos 1M/YTD/12M de ETFs: se calculan sobre el historico diario
        # ya acumulado en data/history/mercados_globales.json (misma fuente
        # Twelve Data, generado por build_history_twelvedata), comparando el
        # precio actual contra el cierre de ~21 dias habiles atras (1M), el
        # primer dia habil del anio en curso (YTD) y ~252 dias habiles atras
        # (12M). Mismo criterio de "close mas cercano disponible hacia atras"
        # que usa get_indices_globales() para percent_change_1y.
        etfs_hist = (load_json(os.path.join(HISTORY_DIR, "mercados_globales.json")) or {}) \
            .get("categorias", {}).get("etfs", {})

        def _retornos_etf(sym, close_actual):
            serie = (etfs_hist.get(sym) or {}).get("daily") or []
            closes = [(p.get("t"), p.get("c")) for p in serie if p.get("c") is not None]
            closes.sort(key=lambda x: x[0])
            if not closes or close_actual is None:
                return None, None, None
            try:
                close_actual = float(close_actual)
            except (TypeError, ValueError):
                return None, None, None
            anio_actual = date.today().year

            def pct_desde(idx_atras=None, desde_anio=False):
                if desde_anio:
                    ref = next((c for t, c in closes if t[:4] == str(anio_actual)), None)
                else:
                    ref = closes[-idx_atras][1] if len(closes) >= idx_atras else None
                if not ref:
                    return None
                try:
                    ref = float(ref)
                except (TypeError, ValueError):
                    return None
                return (close_actual - ref) / ref * 100

            r_1m = pct_desde(idx_atras=21)
            r_ytd = pct_desde(desde_anio=True)
            r_12m = pct_desde(idx_atras=252)
            return r_1m, r_ytd, r_12m

        for cat, symmap in TWELVEDATA_SYMBOLS.items():
            cat_out = []
            for sym, nombre in symmap.items():
                q = td.get(sym)
                if q and isinstance(q, dict) and q.get("close"):
                    item = {
                        "symbol": sym,
                        "nombre": nombre,
                        "close": q.get("close"),
                        "percent_change": q.get("percent_change"),
                        "currency": q.get("currency"),
                    }
                    if cat == "etfs":
                        r_1m, r_ytd, r_12m = _retornos_etf(sym, q.get("close"))
                        item["percent_change_1m"] = round(r_1m, 3) if r_1m is not None else None
                        item["percent_change_ytd"] = round(r_ytd, 3) if r_ytd is not None else None
                        item["percent_change_12m"] = round(r_12m, 3) if r_12m is not None else None
                    cat_out.append(item)
            if cat_out:
                live_data[cat] = cat_out

    save_json(OUT_PATH, live_data)
    print(f"OK: {OUT_PATH} actualizado.")

    # ---- Acumulacion diaria de metricas de bonos (fase 2 detalle de
    # bono): TIR/paridad/VT/precio limpio-sucio/interes corrido. ----
    for categoria, items_key in (
        ("bonos_cer", "bonos_cer"),
        ("bonos_soberanos_usd", "bonos_soberanos_usd"),
        ("ons_usd", "ons_usd"),
        ("bonos_pesos_cupon_fijo", "bonos_pesos"),
    ):
        _accumulate_bond_metrics(categoria, live_data.get(items_key))
    print(f"OK: data/history/{BOND_METRICS_HISTORY_FILE} actualizado.")

    # ---- Series históricas ----
    historicos = {
        "indicadores_precios.json": build_history_indicadores_precios(),
        "indicadores_monetarios.json": build_history_agregados_monetarios(),
      "indicadores_actividad.json": build_history_actividad(),
        "dolar.json": build_history_dolar(),
        "tasas_locales.json": build_history_tasas_locales(),
        "fci_secciones.json": build_history_fci_secciones(live_data.get("fci_secciones")),
        "acciones_arg.json": build_history_acciones_arg(),
        "cripto.json": build_history_cripto(),
        "mercados_globales.json": build_history_twelvedata(),
        "indices.json": build_history_indices(),
        "commodities.json": build_history_commodities(),
        "tasas_internacionales.json": build_history_tasas_internacionales(live_data.get("rates_intl")),
        "acciones_mundiales_historia.json": build_history_acciones_mundiales(live_data.get("acciones_mundiales")),
        "bonos_usd_historia.json": build_history_bonos_usd(),
        "bonos_cer_historia.json": build_history_bonos_cer(),
        "bonos_pesos_historia.json": build_history_bonos_pesos(),
        "bonos_pesos_tamar_historia.json": build_history_bonos_pesos_tamar(),
        "bonos_pesos_duales_historia.json": build_history_bonos_pesos_duales(),
        "ons_usd_historia.json": build_history_ons_usd(),
    }
    for filename, payload in historicos.items():
        if payload:
            save_json(os.path.join(HISTORY_DIR, filename), payload)
            print(f"OK: data/history/{filename} actualizado.")
        else:
            print(f"[WARN] No se pudo actualizar data/history/{filename} (fuente sin datos hoy).")


if __name__ == "__main__":
    main()
