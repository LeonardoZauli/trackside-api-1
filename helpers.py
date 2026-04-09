"""
Helpers condivisi — confidenza, fuzzy match, deduplicazione nomi.
Estratti da api.py originale, invariati nella logica.
"""

import math
import re
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

from model_manager import ModelManager


# ──────────────────────────────────────────────────────────────
# Nome prodotto
# ──────────────────────────────────────────────────────────────

def normalize_key(name: str) -> str:
    return " ".join(str(name).strip().split()).lower() if name else ""


def clean_name(name: str) -> str:
    return " ".join(str(name).strip().split()) if name else ""


def dedupe_by_name(
    items: list[dict],
    name_field: str,
    score_field: Optional[str] = None,
) -> list[dict]:
    by_name: dict[str, dict] = {}
    for item in items:
        key = normalize_key(item.get(name_field, ""))
        if not key:
            continue
        existing = by_name.get(key)
        if existing is None:
            by_name[key] = {**item, name_field: clean_name(item.get(name_field, ""))}
        elif score_field and float(item.get(score_field, 0) or 0) > float(existing.get(score_field, 0) or 0):
            by_name[key] = {**item, name_field: clean_name(item.get(name_field, ""))}
    return list(by_name.values())


# ──────────────────────────────────────────────────────────────
# Confidenza data
# ──────────────────────────────────────────────────────────────

def date_confidence(dt: datetime) -> dict:
    p = ModelManager.get()
    dow = dt.weekday()
    dow_pct = p.dow_probs.get(dow, 0)
    day_score = min(dow_pct / 0.5, 1.0)
    days_away = (dt - datetime.now()).days
    distance_score = max(0.4, 1.0 - (days_away / 30) * 0.05)

    recent = p.recent_gaps
    if recent:
        avg_gap = sum(recent) / len(recent)
        variance = sum((g - avg_gap) ** 2 for g in recent) / len(recent)
        regularity = max(0.5, 1.0 - math.sqrt(variance) / avg_gap)
    else:
        regularity = 0.5

    confidence = round(day_score * 0.50 + distance_score * 0.25 + regularity * 0.25, 3)
    pct = round(confidence * 100, 1)
    return {
        "confidenza_pct": pct,
        "livello": "alta" if pct >= 75 else "media" if pct >= 50 else "bassa",
        "dettaglio": {
            "giorno_settimana_storico_pct": round(dow_pct * 100, 1),
            "regolarita_cadenza_pct": round(regularity * 100, 1),
            "penalita_distanza_pct": round(distance_score * 100, 1),
        },
    }


# ──────────────────────────────────────────────────────────────
# Confidenza prodotto
# ──────────────────────────────────────────────────────────────

def _count_years_with_month(month: int) -> int:
    p = ModelManager.get()
    return len({datetime.strptime(d, "%Y-%m-%d").year for d in p.all_dates
                if datetime.strptime(d, "%Y-%m-%d").month == month})


def product_confidence(product_name: str, month: int) -> dict:
    p = ModelManager.get()
    total_in_month = p.month_products[month].get(product_name, 0)
    total_products_month = sum(p.month_products[month].values())
    freq_score = min(1.0, total_in_month / max(1, total_products_month) * 100)

    years_appeared = {r["_dt"].year for r in p.rows
                      if r["_dt"].month == month and r["nome"] == product_name}
    total_years = _count_years_with_month(month)
    recurrence_rate = len(years_appeared) / max(1, total_years)
    recent_years = {y for y in years_appeared if y >= 2023}
    recency_score = len(recent_years) / 3.0

    confidence = round(freq_score * 0.20 + recurrence_rate * 0.50 + min(1.0, recency_score) * 0.30, 3)
    pct = round(confidence * 100, 1)
    return {
        "confidenza_pct": pct,
        "livello": "alta" if pct >= 60 else "media" if pct >= 30 else "bassa",
        "dettaglio": {
            "apparizioni_nel_mese": total_in_month,
            "anni_con_apparizione": sorted(years_appeared),
            "tasso_ricorrenza_pct": round(recurrence_rate * 100, 1),
            "presente_ultimi_3_anni": sorted(recent_years),
        },
    }


# ──────────────────────────────────────────────────────────────
# Fuzzy search prodotti
# ──────────────────────────────────────────────────────────────

def find_product_matches(query: str, limit: int = 10) -> list[dict]:
    p = ModelManager.get()
    query_low   = query.lower().strip()
    query_clean = re.sub(r"[^a-z0-9 ]+", " ", query_low).strip()
    query_tokens = [t for t in query_clean.split() if len(t) >= 2]
    query_tokens_set = set(query_tokens)

    variants: dict[str, Counter] = {}
    for r in p.rows:
        raw = r.get("nome", "")
        key = normalize_key(raw)
        cn  = clean_name(raw)
        if key and cn:
            if key not in variants:
                variants[key] = Counter()
            variants[key][cn] += 1

    all_products = [cnt.most_common(1)[0][0] for cnt in variants.values() if cnt]

    scored = []
    for name in all_products:
        name_low   = name.lower()
        name_clean = re.sub(r"[^a-z0-9 ]+", " ", name_low).strip()
        name_tokens = [t for t in name_clean.split() if t]

        if query_low == name_low:
            score = 1.0
        elif query_low in name_low:
            is_word_prefix = any(t.startswith(query_clean) for t in name_tokens)
            score = 0.95 if is_word_prefix else 0.85
        else:
            prefix_match = any(t.startswith(query_clean) for t in name_tokens)
            if prefix_match:
                score = 0.80
            elif query_tokens:
                token_prefix_hits = sum(
                    1 for qt in query_tokens if any(nt.startswith(qt) for nt in name_tokens)
                )
                token_prefix = token_prefix_hits / len(query_tokens)
                ratio_raw   = SequenceMatcher(None, query_low, name_low).ratio()
                ratio_clean = SequenceMatcher(None, query_clean, name_clean).ratio()
                token_overlap = (
                    len(query_tokens_set & set(name_tokens)) / len(query_tokens_set)
                    if query_tokens_set else 0.0
                )
                score = max(ratio_raw, ratio_clean, token_overlap, token_prefix)
                if score < 0.42:
                    continue
            else:
                continue

        scored.append((name, score))

    scored.sort(key=lambda x: -x[1])
    matches = [{"nome": clean_name(n), "match_score_pct": round(s * 100, 1)} for n, s in scored]
    deduped = dedupe_by_name(matches, name_field="nome", score_field="match_score_pct")
    deduped.sort(key=lambda x: -x["match_score_pct"])
    return deduped[:limit]


# ──────────────────────────────────────────────────────────────
# Fallback date per prodotto singolo
# ──────────────────────────────────────────────────────────────

from collections import Counter as _Counter
from datetime import timedelta


def build_fallback_dates(
    product_name: str,
    from_date: datetime,
    top_n: int,
    exclude_dates: Optional[set] = None,
) -> list[dict]:
    p = ModelManager.get()
    exclude_dates = exclude_dates or set()
    pname_key = normalize_key(product_name)

    month_counts = _Counter()
    dow_counts   = _Counter()
    for r in p.rows:
        if normalize_key(r.get("nome", "")) != pname_key:
            continue
        dt = r.get("_dt")
        if dt:
            month_counts[dt.month] += 1
            dow_counts[dt.weekday()] += 1

    total_hist = sum(month_counts.values())
    max_avg_products = max(p.avg_products_per_release.values()) if p.avg_products_per_release else 1

    candidates, d = [], from_date + timedelta(days=1)
    end = from_date + timedelta(days=366)
    while d <= end:
        if d.weekday() in (0, 3) and d.strftime("%Y-%m-%d") not in exclude_dates:
            candidates.append(d)
        d += timedelta(days=1)

    scored = []
    for dt in candidates:
        month_prob = month_counts[dt.month] / total_hist if total_hist else 0.0
        dow_prob   = dow_counts[dt.weekday()] / total_hist if total_hist else p.dow_probs.get(dt.weekday(), 0.0)
        seasonal   = p.avg_products_per_release.get(dt.month, 0) / max(1, max_avg_products)
        score      = month_prob * 0.7 + dow_prob * 0.2 + seasonal * 0.1
        prod_conf  = round(score * 100, 2)
        dc         = date_confidence(dt)
        combined   = round(prod_conf * 0.7 + dc["confidenza_pct"] * 0.3, 1)
        scored.append({
            "data": dt.strftime("%Y-%m-%d"),
            "giorno": p.DOW_IT[dt.weekday()],
            "giorni_da_oggi": (dt - from_date).days,
            "rank_nel_giorno": None,
            "confidenza_prodotto_nel_giorno_pct": prod_conf,
            "score_finale_pct": round(score * 100, 2),
            "score_raw": round(score, 6),
            "mese": p.MESE_IT[dt.month],
            "confidenza_data": dc,
            "confidenza_combinata_pct": combined,
            "livello": "alta" if combined >= 60 else "media" if combined >= 35 else "bassa",
            "is_fallback": True,
        })

    scored.sort(key=lambda x: (-x["confidenza_combinata_pct"], x["giorni_da_oggi"]))
    return scored[:top_n]
