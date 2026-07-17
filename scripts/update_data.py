#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Actualiza data/live_data.json con datos reales de mercado.
Se ejecuta diariamente vía GitHub Actions a las 20:00 hora Argentina (23:00 UTC).

Fuentes:
- Dólar (Argentina): dolarapi.com (sin key)
- Riesgo país (Argentina): api.argentinadatos.com (sin key)
- Criptomonedas: CoinGecko (sin key)
- Índices/Acciones/ETFs/Commodities/Divisas globales: Twelve Data (requiere TWELVEDATA_API_KEY)

Twelve Data free tier: 8 créditos/minuto, 800/día. Este script agrupa los
símbolos en lotes de máximo 8 y espera 65 segundos entre lotes para no
exceder el límite.
"""
import json
import os
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone

TWELVEDATA_KEY = os.environ.get("TWELVEDATA_API_KEY", "")

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "live_data.json")


def fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "monitor-real-bot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[WARN] Fallo al pedir {url}: {e}")
        return None


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


# Símbolos Twelve Data por categoría (usando ETFs/acciones como proxy cuando
# el activo "puro" no está disponible en el plan gratuito).
TWELVEDATA_SYMBOLS = {
    "indices": {"SPY": "S&P 500 (proxy SPY)", "DIA": "Dow Jones (proxy DIA)", "QQQ": "Nasdaq (proxy QQQ)"},
    "stocks": {"AAPL": "Apple Inc.", "MSFT": "Microsoft Corp.", "AMZN": "Amazon.com Inc.", "GOOGL": "Alphabet Inc."},
    "etfs": {"SPY": "SPY (S&P 500)", "QQQ": "QQQ (Nasdaq 100)", "EEM": "EEM (Emergentes)"},
    "commodities": {"GLD": "Oro (proxy GLD)", "USO": "Petróleo (proxy USO)", "SLV": "Plata (proxy SLV)", "CORN": "Maíz (proxy CORN)"},
    "forex": {"EUR/USD": "EUR/USD", "USD/BRL": "USD/BRL", "USD/JPY": "USD/JPY", "GBP/USD": "GBP/USD"},
}


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


def main():
    live_data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "dolar": get_dolares(),
        "riesgo_pais": get_riesgo_pais(),
        "inflacion": get_inflacion(),
        "cripto": get_cripto(),
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

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(live_data, f, ensure_ascii=False, indent=2)
    print(f"OK: {OUT_PATH} actualizado.")


if __name__ == "__main__":
    main()
