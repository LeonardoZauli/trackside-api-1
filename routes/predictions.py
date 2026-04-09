"""
Route pubbliche — prossime uscite, prodotti, previsione per giorno, prodotto singolo.
Logica invariata rispetto all'originale, con logging e gestione eccezioni aggiunti.
"""

from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from security import verify_signature
from model_manager import ModelManager
from helpers import (
    date_confidence, product_confidence,
    find_product_matches, dedupe_by_name,
    normalize_key, clean_name, build_fallback_dates,
)
from logger import get_logger

logger = get_logger(__name__)
router = APIRouter(dependencies=[Depends(verify_signature)])


# ──────────────────────────────────────────────────────────────
# Helper: predictor shortcut
# ──────────────────────────────────────────────────────────────

def _p():
    return ModelManager.get()


# ──────────────────────────────────────────────────────────────
# GET /prossime-uscite
# ──────────────────────────────────────────────────────────────

@router.get("/prossime-uscite", summary="Prossime N uscite previste")
def prossime_uscite(n: int = Query(3, ge=1, le=10)):
    p = _p()
    today = datetime.now()

    candidates = []
    d, end = today + timedelta(days=1), today + timedelta(days=120)
    while d <= end:
        if d.weekday() in (0, 3):
            candidates.append(d)
        d += timedelta(days=1)

    recent = p.recent_gaps
    gap_mode = Counter(recent).most_common(1)[0][0] if recent else 7
    max_allowed_gap = max(gap_mode * 3, 21)
    min_gap = max(3, gap_mode // 3)

    selected, last_selected = [], datetime.strptime(p.all_dates[-1], "%Y-%m-%d")
    for c in candidates:
        gap = (c - last_selected).days
        if min_gap <= gap <= max_allowed_gap:
            selected.append(c)
            last_selected = c
            if len(selected) >= n:
                break

    if len(selected) < n:
        seen = {s.strftime("%Y-%m-%d") for s in selected}
        for c in candidates:
            if c.strftime("%Y-%m-%d") not in seen:
                selected.append(c)
                seen.add(c.strftime("%Y-%m-%d"))
                if len(selected) >= n:
                    break
        selected.sort()

    results = []
    for dt in selected:
        month = dt.month
        conf = date_confidence(dt)
        raw_products = p.predict_products_for_date(dt, top_n=40)
        raw_products = dedupe_by_name(raw_products, "nome", "score_raw")[:20]
        products_enriched = [{**prod, "confidenza": product_confidence(prod["nome"], month)} for prod in raw_products]
        themes = p.month_theme_simple.get(month, Counter())
        total_t = sum(themes.values()) or 1
        tema = (
            "Giardino" if dt.weekday() == 3 and themes.get("Giardino", 0) > 0
            else (themes.most_common(1)[0][0] if themes else "Fai da te")
        )
        results.append({
            "data": dt.strftime("%Y-%m-%d"),
            "giorno": p.DOW_IT[dt.weekday()],
            "giorni_da_oggi": (dt - today).days,
            "tema_principale": tema,
            "temi_probabili": [{"tema": t, "probabilita_pct": round(c / total_t * 100, 1)} for t, c in themes.most_common()],
            "confidenza_data": conf,
            "n_prodotti_attesi": int(p.avg_products_per_release.get(month, 20)),
            "prodotti_previsti": products_enriched,
            "prodotti_ricorrenti_annuali": p.recurring.get(month, [])[:10],
        })

    logger.info("prossime-uscite richieste: n=%d, restituite=%d", n, len(results))
    return {
        "generato_il": today.strftime("%Y-%m-%d %H:%M"),
        "basato_su": {
            "totale_prodotti_storici": len(p.rows),
            "totale_date_storiche": len(p.all_dates),
            "range_dati": f"{p.all_dates[0]} → {p.all_dates[-1]}",
        },
        "prossime_uscite": results,
    }


# ──────────────────────────────────────────────────────────────
# GET /prodotti
# ──────────────────────────────────────────────────────────────

@router.get("/prodotti", summary="Catalogo prodotti con filtri e ricerca fuzzy")
def tutti_i_prodotti(
    cerca: str = Query(None),
    mese: int = Query(None, ge=1, le=12),
    ordina: str = Query("frequenza"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    p = _p()
    product_data: dict = {}
    for r in p.rows:
        raw_name = r.get("nome", "")
        key = normalize_key(raw_name)
        if not key:
            continue
        name = clean_name(raw_name)
        if key not in product_data:
            product_data[key] = {
                "nome": name, "apparizioni_totali": 0, "mesi_apparizione": Counter(),
                "anni_apparizione": set(), "prezzi": [], "ultima_apparizione": r["data"],
                "temi": Counter(),
            }
        pd = product_data[key]
        pd["apparizioni_totali"] += 1
        pd["mesi_apparizione"][r["_dt"].month] += 1
        pd["anni_apparizione"].add(r["_dt"].year)
        pd["ultima_apparizione"] = max(pd["ultima_apparizione"], r["data"])
        pd["temi"][r["titolo_offerta"]] += 1
        try:
            pd["prezzi"].append(float(r["prezzo"].replace("€", "").replace(",", ".").strip()))
        except (ValueError, AttributeError):
            pass

    simili, match_score_map = [], {}
    if cerca:
        all_matches = find_product_matches(cerca, limit=500)
        simili = all_matches[:10]
        match_score_map = {normalize_key(m["nome"]): m["match_score_pct"] for m in all_matches}
        product_data = {k: v for k, v in product_data.items() if k in match_score_map}

    if mese:
        product_data = {k: v for k, v in product_data.items() if mese in v["mesi_apparizione"]}

    products_list = []
    for _, pd in product_data.items():
        mesi_sorted = sorted(pd["mesi_apparizione"].items(), key=lambda x: -x[1])
        products_list.append({
            "nome": pd["nome"],
            "apparizioni_totali": pd["apparizioni_totali"],
            "mesi_principali": [{"mese": p.MESE_IT[m], "mese_num": m, "frequenza": c} for m, c in mesi_sorted],
            "n_mesi_diversi": len(pd["mesi_apparizione"]),
            "anni_apparizione": sorted(pd["anni_apparizione"]),
            "n_anni": len(pd["anni_apparizione"]),
            "ultima_apparizione": pd["ultima_apparizione"],
            "prezzo_medio": round(sum(pd["prezzi"]) / len(pd["prezzi"]), 2) if pd["prezzi"] else None,
            "prezzo_min": min(pd["prezzi"]) if pd["prezzi"] else None,
            "prezzo_max": max(pd["prezzi"]) if pd["prezzi"] else None,
        })

    if cerca and match_score_map:
        products_list.sort(key=lambda x: -match_score_map.get(normalize_key(x["nome"]), 0))
    elif ordina == "nome":
        products_list.sort(key=lambda x: x["nome"])
    elif ordina == "mesi":
        products_list.sort(key=lambda x: -x["n_mesi_diversi"])
    else:
        products_list.sort(key=lambda x: -x["apparizioni_totali"])

    total = len(products_list)
    logger.debug("prodotti: query='%s' mese=%s → %d risultati", cerca, mese, total)
    return {
        "totale": total, "offset": offset, "limit": limit, "query": cerca,
        "stringhe_simili": simili,
        "prodotti": products_list[offset:offset + limit],
    }


# ──────────────────────────────────────────────────────────────
# GET /previsione-giorno/{data}
# ──────────────────────────────────────────────────────────────

@router.get("/previsione-giorno/{data}", summary="Prodotti previsti per una data specifica")
def previsione_giorno(data: str, top_n: int = Query(30, ge=5, le=100)):
    try:
        target_dt = datetime.strptime(data, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(422, detail={"errore": "Formato data non valido", "atteso": "YYYY-MM-DD"})

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if target_dt <= today:
        raise HTTPException(422, detail={"errore": "La data deve essere nel futuro", "oggi": today.strftime("%Y-%m-%d")})

    p = _p()
    month, dow = target_dt.month, target_dt.weekday()
    days_away = (target_dt - today).days

    dow_release_prob = p.dow_probs.get(dow, 0.0)
    is_release_day = dow in (0, 3)
    month_total_releases = sum(1 for d in p.all_dates if datetime.strptime(d, "%Y-%m-%d").month == month)
    dow_in_month = sum(1 for d in p.all_dates
                       if datetime.strptime(d, "%Y-%m-%d").month == month
                       and datetime.strptime(d, "%Y-%m-%d").weekday() == dow)
    dow_in_month_prob = dow_in_month / max(month_total_releases, 1)
    distance_factor = max(0.4, 1.0 - (days_away / 90) * 0.3)
    day_release_score = dow_release_prob * 0.50 + dow_in_month_prob * 0.30 + distance_factor * 0.20
    day_release_pct = round(min(day_release_score * 100, 99.9), 1)

    scored_products = p._score_products_for_date(target_dt)
    scored_products = dedupe_by_name(scored_products, "nome", "score_raw")
    scored_products.sort(key=lambda x: x["score_raw"], reverse=True)

    if not scored_products:
        raise HTTPException(404, detail={"errore": "Nessun dato storico disponibile per questo giorno"})

    max_score = scored_products[0]["score_raw"] or 1.0
    products_out = []
    for rank, prod in enumerate(scored_products[:top_n], 1):
        conf_pct = round(prod["score_raw"] / max_score * 100, 2)
        if not is_release_day:
            conf_pct = round(conf_pct * day_release_score, 2)
        sig = prod["signals"]
        products_out.append({
            "rank": rank,
            "prodotto": prod["nome"],
            "confidenza_pct": conf_pct,
            "score_raw": round(prod["score_raw"], 6),
            "livello_confidenza": "alta" if conf_pct >= 60 else "media" if conf_pct >= 30 else "bassa",
            "segnali": {
                "frequenza_mensile_pct": round(sig["month"] * 100, 1),
                "settimana_del_mese_pct": round(sig["week"] * 100, 1),
                "ricorrenza_annuale_pct": round(sig["recurrence"] * 100, 1),
                "stagionalita_pct": round(sig["seasonal"] * 100, 1),
                "presenza_ultimi_3anni_pct": round(sig["recent"] * 100, 1),
            },
            "storico": {
                "apparizioni_nel_mese": prod["apparizioni_storiche"],
                "anni_nel_mese": prod["anni_nel_mese"],
                "n_anni": len(prod["anni_nel_mese"]),
                "ultima_apparizione": p.last_seen[prod["nome"]].strftime("%Y-%m-%d") if prod["nome"] in p.last_seen else None,
                "giorni_da_ultima": prod.get("days_since_last"),
            },
        })

    themes = p.month_theme_simple.get(month, Counter())
    total_t = sum(themes.values()) or 1
    n_years = len({datetime.strptime(d, "%Y-%m-%d").year for d in p.all_dates if datetime.strptime(d, "%Y-%m-%d").month == month})

    logger.info("previsione-giorno %s → %d prodotti", data, len(products_out))
    return {
        "data_richiesta": data,
        "giorno_settimana": p.DOW_IT[dow],
        "mese": p.MESE_IT[month],
        "giorni_da_oggi": days_away,
        "analisi_giorno": {
            "e_giorno_di_uscita_tipico": is_release_day,
            "avviso": None if is_release_day else f"Lidl pubblica quasi esclusivamente di lunedì e giovedì. {p.DOW_IT[dow].capitalize()} ha solo {round(dow_release_prob * 100, 1)}% delle uscite storiche.",
            "probabilita_uscita_quel_giorno_pct": day_release_pct,
            "livello_probabilita_uscita": "alta" if day_release_pct >= 60 else "media" if day_release_pct >= 30 else "bassa",
        },
        "temi_mensili": [{"tema": t, "probabilita_pct": round(c / total_t * 100, 1)} for t, c in themes.most_common()],
        "n_prodotti_attesi": int(p.avg_products_per_release.get(month, 20)),
        "prodotti_ricorrenti_annuali": p.recurring.get(month, [])[:10],
        "prodotti_previsti": {
            "n_totale_candidati_analizzati": len(scored_products),
            "n_mostrati": len(products_out),
            "lista": products_out,
        },
        "basato_su": {
            "totale_righe_dataset": len(p.rows),
            "totale_date_storiche": len(p.all_dates),
            "range_dati": f"{p.all_dates[0]} → {p.all_dates[-1]}",
            "anni_con_dati_in_questo_mese": n_years,
        },
    }


# ──────────────────────────────────────────────────────────────
# GET /prodotto/{nome}
# ──────────────────────────────────────────────────────────────

@router.get("/prodotto/{nome}", summary="Previsione date future per un singolo prodotto")
def previsione_prodotto(nome: str):
    p = _p()
    query_key = normalize_key(nome)
    exact = [r for r in p.rows if normalize_key(r.get("nome", "")) == query_key]

    if not exact:
        matches = find_product_matches(nome)
        if not matches:
            raise HTTPException(404, detail={"errore": f"Prodotto '{nome}' non trovato"})
        best = matches[0]["nome"]
        best_key = normalize_key(best)
        exact = [r for r in p.rows if normalize_key(r.get("nome", "")) == best_key]
        fuzzy_used, all_matches = True, matches
    else:
        fuzzy_used, best, all_matches = False, clean_name(nome), []

    seen_exact, exact_unique = set(), []
    for r in exact:
        k = (r.get("data"), r.get("titolo_offerta"), normalize_key(r.get("nome", "")))
        if k not in seen_exact:
            seen_exact.add(k)
            exact_unique.append(r)

    product_name = clean_name(best)
    apparizioni, prezzi = [], []
    mesi_count, dow_count, anni = Counter(), Counter(), set()

    for r in exact_unique:
        dt = r["_dt"]
        apparizioni.append({"data": r["data"], "giorno": p.DOW_IT[dt.weekday()], "tema": r["titolo_offerta"]})
        mesi_count[dt.month] += 1
        anni.add(dt.year)
        dow_count[dt.weekday()] += 1
        try:
            prezzi.append(float(r["prezzo"].replace("€", "").replace(",", ".").strip()))
        except (ValueError, AttributeError):
            pass

    apparizioni.sort(key=lambda x: x["data"], reverse=True)
    total_app = len(apparizioni)
    mesi_prob = [
        {"mese": p.MESE_IT[m], "mese_num": m, "frequenza": c, "probabilita_pct": round(c / total_app * 100, 1)}
        for m, c in mesi_count.most_common()
    ]

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    best_window = p.predict_product_best_window(product_name=product_name, from_date=today, months_ahead=12, alternatives_n=10)

    top_dates_enriched = []
    for d in best_window.get("top_date_probabili_12_mesi", []):
        dt = datetime.strptime(d["data"], "%Y-%m-%d")
        dc = date_confidence(dt)
        combined = round(d["confidenza_prodotto_nel_giorno_pct"] * 0.7 + dc["confidenza_pct"] * 0.3, 1)
        top_dates_enriched.append({
            **d,
            "mese": p.MESE_IT[dt.month],
            "confidenza_data": dc,
            "confidenza_combinata_pct": combined,
            "livello": "alta" if combined >= 60 else "media" if combined >= 35 else "bassa",
            "is_fallback": False,
        })

    dedup_by_date = {}
    for item in top_dates_enriched:
        if item.get("data") and item["data"] not in dedup_by_date:
            dedup_by_date[item["data"]] = item
    top_dates_enriched = list(dedup_by_date.values())

    if len(top_dates_enriched) < 5:
        fallback = build_fallback_dates(
            product_name=product_name, from_date=today,
            top_n=5 - len(top_dates_enriched), exclude_dates=set(dedup_by_date.keys()),
        )
        top_dates_enriched.extend(fallback)

    top_dates_enriched.sort(key=lambda x: (-float(x.get("confidenza_combinata_pct", 0) or 0), int(x.get("giorni_da_oggi", 9999) or 9999)))

    data_piu_probabile = top_dates_enriched[0] if top_dates_enriched else None
    altre_10 = top_dates_enriched[1:11]

    logger.info("prodotto '%s' (fuzzy=%s) → %d date trovate", product_name, fuzzy_used, len(top_dates_enriched))
    return {
        "prodotto": product_name,
        "match_fuzzy": fuzzy_used,
        "altri_match": all_matches[:5] if fuzzy_used else [],
        "stringhe_simili": all_matches[:10],
        "statistiche": {
            "apparizioni_totali": total_app,
            "anni_presenza": sorted(anni),
            "n_anni": len(anni),
            "mesi_probabili": mesi_prob,
            "giorno_settimana_frequente": {p.DOW_IT[dow]: cnt for dow, cnt in dow_count.most_common()},
            "prezzo_medio": round(sum(prezzi) / len(prezzi), 2) if prezzi else None,
            "prezzo_range": f"€{min(prezzi):.2f} - €{max(prezzi):.2f}" if prezzi else None,
            "ultima_apparizione": apparizioni[0] if apparizioni else None,
        },
        "finestra_previsione_mesi": 12,
        "data_piu_probabile_12_mesi": data_piu_probabile,
        "altre_10_date_probabili_12_mesi": altre_10,
        "top_5_date_probabili_12_mesi": p.get_top_n_dates_most_probable(top_dates_enriched, top_n=5),
        "prossima_uscita_prevista": data_piu_probabile,
        "storico_apparizioni": apparizioni[:20],
    }
