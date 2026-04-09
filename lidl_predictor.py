"""
Lidl Parkside Predictor — Modello Statistico (v2)
==================================================

Replica esattamente l'analisi fatta da Claude: trova i pattern
nel dataset e prevede date + prodotti futuri. Nessun LLM, nessuna
GPU, risultati istantanei e deterministici.

INSTALLAZIONE:
    pip install pandas

USO DA TERMINALE:
    python lidl_predictor.py --csv lidl_prodotti.csv --mesi 3

USO DA PYTHON:
    from lidl_predictor import LidlPredictor
    p = LidlPredictor("lidl_prodotti.csv")
    previsioni = p.predict(mesi_avanti=3)
    for prev in previsioni:
        print(prev)
"""

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional


class LidlPredictor:
    """
    Modello predittivo basato su pattern statistici.
    Analizza 11 anni di dati per trovare:
    - Cadenza settimanale (lunedì/giovedì)
    - Temi stagionali per mese
    - Prodotti ricorrenti per periodo dell'anno
    - Gap tipici tra uscite
    """

    DOW_IT = {
        0: "lunedì", 1: "martedì", 2: "mercoledì",
        3: "giovedì", 4: "venerdì", 5: "sabato", 6: "domenica",
    }
    MESE_IT = {
        1: "gennaio", 2: "febbraio", 3: "marzo", 4: "aprile",
        5: "maggio", 6: "giugno", 7: "luglio", 8: "agosto",
        9: "settembre", 10: "ottobre", 11: "novembre", 12: "dicembre",
    }

    def __init__(self, csv_path: str):
        self.rows = self._load(csv_path)
        self._analyze()
        self._analyze_extended()   # ← NUOVO

    # ────────────────────────────────────────────
    # Caricamento dati
    # ────────────────────────────────────────────

    def _load(self, path: str) -> list[dict]:
        rows = []
        with open(path, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                r["_dt"] = datetime.strptime(r["data"], "%Y-%m-%d")
                rows.append(r)
        return rows

    # ────────────────────────────────────────────
    # Analisi pattern
    # ────────────────────────────────────────────

    def _analyze(self):
        rows = self.rows

        # 1. Raggruppa per data
        self.by_date: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            self.by_date[r["data"]].append(r)
        self.all_dates = sorted(self.by_date.keys())

        # 2. Pattern giorno della settimana
        dow_counter = Counter()
        for d in self.all_dates:
            dt = datetime.strptime(d, "%Y-%m-%d")
            dow_counter[dt.weekday()] += 1
        total = sum(dow_counter.values())
        self.dow_probs = {k: v / total for k, v in dow_counter.items()}

        # 3. Gap tra uscite consecutive
        self.gaps = []
        for i in range(1, len(self.all_dates)):
            d1 = datetime.strptime(self.all_dates[i - 1], "%Y-%m-%d")
            d2 = datetime.strptime(self.all_dates[i], "%Y-%m-%d")
            self.gaps.append((d2 - d1).days)

        # 4. Cadenza recente (ultimi 20 gap)
        self.recent_gaps = self.gaps[-20:] if len(self.gaps) >= 20 else self.gaps

        # 5. Prodotti per mese (frequenza su tutti gli anni)
        self.month_products: dict[int, Counter] = defaultdict(Counter)
        for r in rows:
            self.month_products[r["_dt"].month][r["nome"]] += 1

        # 6. Temi per mese
        self.month_themes: dict[int, Counter] = defaultdict(Counter)
        for r in rows:
            self.month_themes[r["_dt"].month][r["titolo_offerta"]] += 1

        # 7. Temi semplificati per mese
        self.month_theme_simple: dict[int, Counter] = defaultdict(Counter)
        for r in rows:
            m = r["_dt"].month
            t = r["titolo_offerta"].lower()
            if "giardino" in t or "giardinaggio" in t:
                self.month_theme_simple[m]["Giardino"] += 1
            elif "fai da te" in t:
                self.month_theme_simple[m]["Fai da te"] += 1
            elif "auto" in t:
                self.month_theme_simple[m]["Auto"] += 1
            elif "hobby" in t or "ufficio" in t:
                self.month_theme_simple[m]["Hobby/Ufficio"] += 1
            else:
                self.month_theme_simple[m]["Altro"] += 1

        # 8. Prodotti ricorrenti (stesso mese, 3+ anni)
        prod_years: dict[tuple, set] = defaultdict(set)
        for r in rows:
            prod_years[(r["_dt"].month, r["nome"])].add(r["_dt"].year)
        self.recurring = defaultdict(list)
        for (month, name), years in prod_years.items():
            if len(years) >= 3:
                self.recurring[month].append({
                    "nome": name,
                    "anni": sorted(years),
                    "frequenza": len(years),
                })
        for m in self.recurring:
            self.recurring[m].sort(key=lambda x: -x["frequenza"])

        # 9. Numero medio di uscite per mese
        month_date_count: dict[int, set] = defaultdict(set)
        for d in self.all_dates:
            dt = datetime.strptime(d, "%Y-%m-%d")
            month_date_count[dt.month].add(dt.year)
        years_per_month = {m: len(yrs) for m, yrs in month_date_count.items()}

        month_total_dates: dict[int, int] = Counter()
        for d in self.all_dates:
            dt = datetime.strptime(d, "%Y-%m-%d")
            month_total_dates[dt.month] += 1
        self.avg_releases_per_month = {
            m: round(month_total_dates[m] / years_per_month.get(m, 1), 1)
            for m in range(1, 13)
        }

        # 10. Numero medio prodotti per uscita per mese
        month_product_counts = defaultdict(list)
        for d in self.all_dates:
            dt = datetime.strptime(d, "%Y-%m-%d")
            month_product_counts[dt.month].append(len(self.by_date[d]))
        self.avg_products_per_release = {
            m: round(sum(counts) / len(counts), 0)
            for m, counts in month_product_counts.items()
        }

    # ────────────────────────────────────────────
    # Analisi estesa (aggiunta rispetto alla base)
    # ────────────────────────────────────────────

    def _analyze_extended(self):
        """
        Pre-calcola strutture aggiuntive per previsioni per-data:
          - Frequenze mensili pesate per anno (dati recenti contano di più)
          - Frequenze per settimana-del-mese × mese
          - Lista (doy, peso) per ogni prodotto (stagionalità giornaliera)
          - Ultima data vista per ogni prodotto (cooldown)
          - Anni per (prodotto, mese) (ricorrenza annuale)
        """
        if not self.rows:
            return

        max_year = max(r["_dt"].year for r in self.rows)
        self._max_year = max_year

        def yw(year: int) -> float:
            """Peso anno: decay esponenziale 80%/anno — i dati recenti contano di più."""
            return max(0.05, 0.80 ** (max_year - year))

        self._year_weight_fn = yw

        # Frequenza mensile pesata per anno
        self.month_products_w: dict[int, Counter] = defaultdict(Counter)
        # Frequenza per (mese, settimana_del_mese) pesata per anno — settimana = giorno//7 (0..4)
        self.week_products_w: dict[tuple, Counter] = defaultdict(Counter)
        # Per ogni prodotto: lista di (doy, peso) per il segnale stagionale
        self.prod_doy_list: dict[str, list] = defaultdict(list)
        # Per (prodotto, mese): insieme degli anni con almeno un'apparizione
        self.prod_month_years: dict[tuple, set] = defaultdict(set)
        # Ultima data vista per prodotto
        self.last_seen: dict[str, datetime] = {}

        for r in self.rows:
            dt: datetime = r["_dt"]
            name: str = r["nome"]
            m = dt.month
            w = yw(dt.year)
            week = (dt.day - 1) // 7        # 0..4 (settimana del mese)
            doy = dt.timetuple().tm_yday    # 1..366

            self.month_products_w[m][name] += w
            self.week_products_w[(m, week)][name] += w
            self.prod_doy_list[name].append((doy, w))
            self.prod_month_years[(name, m)].add(dt.year)

            if name not in self.last_seen or dt > self.last_seen[name]:
                self.last_seen[name] = dt

    def _seasonal_score(self, product: str, target_doy: int, window: int = 21) -> float:
        """
        Punteggio stagionale: quanto spesso questo prodotto appare
        entro ±window giorni dall'anno (es. ±21 giorni intorno al DOY target).
        Usa cosine weighting con decay per anno.
        """
        entries = self.prod_doy_list.get(product, [])
        score = 0.0
        for doy, w in entries:
            dist = min(abs(doy - target_doy), 365 - abs(doy - target_doy))
            if dist <= window:
                score += math.cos(math.pi * dist / (2 * window)) * w
        return score

    def _score_products_for_date(
        self,
        target_dt: datetime,
        min_relative_score: float = 0.35,
        allow_fallback: bool = True,
    ) -> list[dict]:
        """
        Calcola i punteggi raw per una data specifica.
        Questo e' il motore unico da usare in tutti gli endpoint per evitare incoerenze.
        """
        m = target_dt.month
        target_doy = target_dt.timetuple().tm_yday
        week = (target_dt.day - 1) // 7

        month_w = self.month_products_w.get(m, Counter())
        week_w = self.week_products_w.get((m, week), Counter())

        candidates = set(month_w.keys()) | set(week_w.keys())
        if not candidates:
            return []

        max_mw = max(month_w.values()) if month_w else 1.0
        max_ww = max(week_w.values()) if week_w else 1.0

        stag_map = {n: self._seasonal_score(n, target_doy) for n in candidates}
        max_stag = max(stag_map.values(), default=0.0) or 1.0

        n_years_month = len({
            datetime.strptime(d, "%Y-%m-%d").year
            for d in self.all_dates
            if datetime.strptime(d, "%Y-%m-%d").month == m
        })

        scored = []
        for name in candidates:
            s_month = min(1.0, month_w.get(name, 0) / max_mw)
            s_week = min(1.0, week_w.get(name, 0) / max_ww)
            s_stag = stag_map.get(name, 0.0) / max_stag

            anni = self.prod_month_years.get((name, m), set())
            s_rec = len(anni) / max(n_years_month, 1)
            recent_yrs = {y for y in anni if y >= self._max_year - 2}
            s_recent = min(1.0, len(recent_yrs) / 3.0)

            if name in self.last_seen:
                days_since = (target_dt - self.last_seen[name]).days
                if days_since < 10:
                    cool = 0.05
                elif days_since < 21:
                    cool = 0.05 + 0.95 * ((days_since - 10) / 11.0)
                else:
                    cool = 1.0
            else:
                days_since = None
                cool = 0.85

            score = (
                s_month * 0.18 +
                s_week * 0.17 +
                s_stag * 0.30 +
                s_rec * 0.20 +
                s_recent * 0.15
            ) * cool

            if len(anni) >= 3:
                score *= 1.0 + 0.08 * min(len(anni) - 2, 5)

            # Anti-rumore: rimuove candidati senza segnali forti
            has_strong_signal = (s_week >= 0.25) or (s_stag >= 0.30) or (s_recent >= 0.34)
            if score > 1e-7 and has_strong_signal:
                scored.append({
                    "nome": name,
                    "score_raw": score,
                    "apparizioni_storiche": int(self.month_products[m].get(name, 0)),
                    "anni_nel_mese": sorted(anni),
                    "anni_recenti": sorted(recent_yrs),
                    "days_since_last": days_since,
                    "signals": {
                        "month": round(s_month, 4),
                        "week": round(s_week, 4),
                        "seasonal": round(s_stag, 4),
                        "recurrence": round(s_rec, 4),
                        "recent": round(s_recent, 4),
                    },
                })

        if not scored:
            return []

        scored.sort(key=lambda x: x["score_raw"], reverse=True)
        max_sc = scored[0]["score_raw"]
        min_keep = max_sc * min_relative_score
        filtered = [s for s in scored if s["score_raw"] >= min_keep]

        if filtered:
            return filtered
        return scored[:20] if allow_fallback else []

    def predict_products_for_date(self, target_dt: datetime, top_n: int = 25) -> list[dict]:
        """
        Wrapper del motore di scoring unificato.
        """
        scored = self._score_products_for_date(target_dt)
        if not scored:
            return []

        max_sc = scored[0]["score_raw"]
        return [
            {
                "rank": i + 1,
                "nome": p["nome"],
                "confidenza_pct": round(p["score_raw"] / max_sc * 100, 1),
                "score_raw": round(p["score_raw"], 6),
                "apparizioni_storiche": p["apparizioni_storiche"],
                "anni_nel_mese": p["anni_nel_mese"],
                "anni_recenti": p["anni_recenti"],
            }
            for i, p in enumerate(scored[:top_n])
        ]

    def predict_product_top_dates(
        self,
        product_name: str,
        from_date: Optional[datetime] = None,
        months_ahead: int = 12,
        top_n: int = 10,
    ) -> list[dict]:
        """
        Restituisce le date piu probabili nei prossimi N mesi per un singolo prodotto.

        Il ranking usa il punteggio del prodotto nel giorno specifico e una piccola
        penalita distanza per evitare che date troppo lontane vincano sempre.
        """
        if not product_name:
            return []

        start = (from_date or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=max(30, months_ahead * 31))

        candidates = []
        d = start + timedelta(days=1)
        while d <= end:
            # Storicamente le uscite sono quasi sempre lunedi o giovedi.
            if d.weekday() in (0, 3):
                candidates.append(d)
            d += timedelta(days=1)

        ranked_dates = []
        for dt in candidates:
            ranked = self.predict_products_for_date(dt, top_n=200)
            match = next((p for p in ranked if p["nome"] == product_name), None)
            if not match:
                continue

            days_away = (dt - start).days
            distance_penalty = min(15.0, days_away * 0.03)
            score = max(0.0, match["confidenza_pct"] - distance_penalty)
            ranked_dates.append(
                {
                    "data": dt.strftime("%Y-%m-%d"),
                    "giorno": self.DOW_IT[dt.weekday()],
                    "giorni_da_oggi": days_away,
                    "rank_nel_giorno": match["rank"],
                    "confidenza_prodotto_nel_giorno_pct": match["confidenza_pct"],
                    "score_finale_pct": round(score, 2),
                    "score_raw": match["score_raw"],
                }
            )

        ranked_dates.sort(
            key=lambda x: (
                -x["score_finale_pct"],
                -x["confidenza_prodotto_nel_giorno_pct"],
                x["giorni_da_oggi"],
            )
        )
        return ranked_dates[:top_n]

    def predict_product_best_window(
        self,
        product_name: str,
        from_date: Optional[datetime] = None,
        months_ahead: int = 12,
        alternatives_n: int = 10,
    ) -> dict:
        """
        Restituisce la miglior data nei prossimi 12 mesi e altre 10 date alternative.
        """
        total_needed = max(1, alternatives_n + 1)
        ranked = self.predict_product_top_dates(
            product_name=product_name,
            from_date=from_date,
            months_ahead=months_ahead,
            top_n=total_needed,
        )
        if not ranked:
            return {
                "finestra_mesi": months_ahead,
                "data_piu_probabile_12_mesi": None,
                "altre_10_date_probabili_12_mesi": [],
                "top_date_probabili_12_mesi": [],
            }

        return {
            "finestra_mesi": months_ahead,
            "data_piu_probabile_12_mesi": ranked[0],
            "altre_10_date_probabili_12_mesi": ranked[1 : 1 + alternatives_n],
            "top_date_probabili_12_mesi": ranked,
        }

    # ────────────────────────────────────────────
    # Predizione date future
    # ────────────────────────────────────────────

    def _predict_dates(self, mesi_avanti: int) -> list[dict]:
        """Prevede le date di uscita per i prossimi N mesi."""
        last_date = datetime.strptime(self.all_dates[-1], "%Y-%m-%d")
        today = datetime.now()
        start = max(last_date, today)

        # Data finale
        end_month = start.month + mesi_avanti
        end_year = start.year + (end_month - 1) // 12
        end_month = ((end_month - 1) % 12) + 1
        end_date = datetime(end_year, end_month, 28)

        # Strategia: genera tutti i lunedì e giovedì nel range,
        # poi seleziona quelli più probabili basandosi sulla cadenza
        candidates = []
        d = start + timedelta(days=1)
        while d <= end_date:
            if d.weekday() in (0, 3):  # lunedì=0, giovedì=3
                candidates.append(d)
            d += timedelta(days=1)

        # Seleziona basandosi sul numero atteso di uscite per mese
        predicted = []
        by_month = defaultdict(list)
        for c in candidates:
            by_month[(c.year, c.month)].append(c)

        for (year, month), month_candidates in sorted(by_month.items()):
            n_expected = int(self.avg_releases_per_month.get(month, 3))

            # Strategia di selezione: alterna lunedì e giovedì,
            # privilegiando lunedì (che sono più frequenti nei dati)
            mondays = [c for c in month_candidates if c.weekday() == 0]
            thursdays = [c for c in month_candidates if c.weekday() == 3]

            selected = []

            # Pattern tipico: 2-4 lunedì + 1-2 giovedì al mese
            n_mon = min(len(mondays), max(1, round(n_expected * 0.6)))
            n_thu = min(len(thursdays), max(0, n_expected - n_mon))

            # Distribuisci uniformemente nel mese
            if mondays and n_mon > 0:
                step = max(1, len(mondays) // n_mon)
                selected += mondays[::step][:n_mon]
            if thursdays and n_thu > 0:
                step = max(1, len(thursdays) // n_thu)
                selected += thursdays[::step][:n_thu]

            selected.sort()
            for s in selected:
                themes = self.month_theme_simple.get(month, {})
                top_theme = themes.most_common(1)[0][0] if themes else "Fai da te"

                # Assegna tema in base al giorno
                if s.weekday() == 3 and "Giardino" in themes:
                    theme = "Giardino" if themes["Giardino"] > 0 else top_theme
                else:
                    theme = top_theme

                predicted.append({
                    "data": s.strftime("%Y-%m-%d"),
                    "giorno": self.DOW_IT[s.weekday()],
                    "tema_probabile": theme,
                    "n_prodotti_attesi": int(self.avg_products_per_release.get(month, 20)),
                })

        return predicted

    # ────────────────────────────────────────────
    # Predizione prodotti (aggiornata)
    # ────────────────────────────────────────────

    def _predict_products(self, month: int, top_n: int = 25) -> dict:
        """
        Prevede i prodotti per un dato mese.
        Usa il 15 del mese come data di riferimento (generico mensile).
        """
        now = datetime.now()
        # Calcola anno: se il mese è già passato quest'anno, usa il prossimo
        if month < now.month:
            year = now.year + 1
        elif month == now.month:
            year = now.year
        else:
            year = now.year
        try:
            ref_dt = datetime(year, month, 15)
        except ValueError:
            ref_dt = datetime(year, month, 14)

        scored = self.predict_products_for_date(ref_dt, top_n)

        month_themes = self.month_theme_simple.get(month, Counter())
        total_t = sum(month_themes.values()) or 1

        return {
            "top_per_frequenza": [
                {"nome": p["nome"], "apparizioni_storiche": p["apparizioni_storiche"]}
                for p in scored
            ],
            "ricorrenti_annuali": self.recurring.get(month, [])[:15],
            "temi": [
                {"tema": t, "peso": round(c / total_t * 100, 1)}
                for t, c in month_themes.most_common()
            ],
        }

    # ────────────────────────────────────────────
    # API pubblica
    # ────────────────────────────────────────────

    def predict(self, mesi_avanti: int = 2, top_prodotti: int = 25) -> list[dict]:
        """
        Genera previsioni complete per i prossimi N mesi.

        Args:
            mesi_avanti: quanti mesi nel futuro prevedere
            top_prodotti: quanti prodotti mostrare per mese

        Returns:
            Lista di previsioni mensili con date e prodotti attesi.
        """
        date_previste = self._predict_dates(mesi_avanti)

        # Raggruppa date per mese
        months_seen = {}
        for dp in date_previste:
            dt = datetime.strptime(dp["data"], "%Y-%m-%d")
            key = (dt.year, dt.month)
            if key not in months_seen:
                months_seen[key] = {
                    "mese": self.MESE_IT[dt.month],
                    "anno": dt.year,
                    "date_previste": [],
                    "prodotti": self._predict_products(dt.month, top_prodotti),
                }
            months_seen[key]["date_previste"].append(dp)

        return list(months_seen.values())

    def predict_month(self, mese: int, anno: int, top_prodotti: int = 25) -> dict:
        """
        Previsione per un singolo mese specifico.

        Args:
            mese: numero del mese (1-12)
            anno: anno
            top_prodotti: quanti prodotti mostrare

        Returns:
            Dict con date e prodotti previsti.
        """
        # Genera date per quel mese
        start = datetime(anno, mese, 1)
        end = datetime(anno, mese, 28)

        candidates = []
        d = start
        while d <= end + timedelta(days=3):
            if d.month == mese and d.weekday() in (0, 3):
                candidates.append(d)
            d += timedelta(days=1)

        n_expected = int(self.avg_releases_per_month.get(mese, 3))
        mondays = [c for c in candidates if c.weekday() == 0]
        thursdays = [c for c in candidates if c.weekday() == 3]

        selected = []
        n_mon = min(len(mondays), max(1, round(n_expected * 0.6)))
        n_thu = min(len(thursdays), max(0, n_expected - n_mon))
        if mondays and n_mon > 0:
            step = max(1, len(mondays) // n_mon)
            selected += mondays[::step][:n_mon]
        if thursdays and n_thu > 0:
            step = max(1, len(thursdays) // n_thu)
            selected += thursdays[::step][:n_thu]
        selected.sort()

        themes = self.month_theme_simple.get(mese, {})

        dates = []
        for s in selected:
            top_theme = themes.most_common(1)[0][0] if themes else "Fai da te"
            if s.weekday() == 3 and "Giardino" in themes:
                theme = "Giardino"
            else:
                theme = top_theme

            dates.append({
                "data": s.strftime("%Y-%m-%d"),
                "giorno": self.DOW_IT[s.weekday()],
                "tema_probabile": theme,
                "n_prodotti_attesi": int(self.avg_products_per_release.get(mese, 20)),
            })

        return {
            "mese": self.MESE_IT[mese],
            "anno": anno,
            "date_previste": dates,
            "prodotti": self._predict_products(mese, top_prodotti),
        }

    def get_top_n_dates_most_probable(
        self,
        top_dates: list[dict],
        top_n: int = 5,
    ) -> list[dict]:
        """
        Restituisce le top N date dal ranking fornito.
        Sono già ordinate per probabilità/confidenza.
        """
        if not top_dates:
            return []
        return top_dates[:top_n]

    def get_stats(self) -> dict:
        """Ritorna le statistiche del dataset analizzato."""
        return {
            "totale_prodotti": len(self.rows),
            "totale_date": len(self.all_dates),
            "range": f"{self.all_dates[0]} → {self.all_dates[-1]}",
            "distribuzione_giorni": {
                self.DOW_IT[k]: f"{v:.1%}" for k, v in sorted(self.dow_probs.items())
                if v > 0.005
            },
            "uscite_medie_per_mese": {
                self.MESE_IT[m]: v for m, v in self.avg_releases_per_month.items()
            },
            "prodotti_medi_per_uscita": {
                self.MESE_IT[m]: v for m, v in self.avg_products_per_release.items()
            },
        }

    def __repr__(self):
        return (
            f"LidlPredictor({len(self.rows)} prodotti, "
            f"{len(self.all_dates)} date, "
            f"{self.all_dates[0]}→{self.all_dates[-1]})"
        )

    def __getstate__(self):
        """Rimuove attributi non serializzabili (es. funzioni locali)."""
        state = self.__dict__.copy()
        state.pop("_year_weight_fn", None)
        return state

    def __setstate__(self, state):
        """Ripristina stato e helper opzionali dopo un pickle load."""
        self.__dict__.update(state)
        self._year_weight_fn = None


# ────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Lidl Parkside Predictor — previsioni basate su pattern storici"
    )
    parser.add_argument("--csv", required=True, help="Percorso al CSV dei prodotti Lidl")
    parser.add_argument("--mesi", type=int, default=2, help="Mesi da prevedere (default: 2)")
    parser.add_argument("--mese", type=int, help="Prevedi un mese specifico (1-12)")
    parser.add_argument("--anno", type=int, help="Anno per --mese (default: anno corrente)")
    parser.add_argument("--stats", action="store_true", help="Mostra statistiche dataset")
    parser.add_argument("--json", action="store_true", help="Output in formato JSON")
    args = parser.parse_args()

    p = LidlPredictor(args.csv)

    if args.stats:
        stats = p.get_stats()
        if args.json:
            print(json.dumps(stats, ensure_ascii=False, indent=2))
        else:
            print(f"\n📊 {p}")
            print(f"\n📅 Distribuzione giorni: {stats['distribuzione_giorni']}")
            print(f"\n📦 Uscite medie/mese: {stats['uscite_medie_per_mese']}")
        return

    if args.mese:
        anno = args.anno or datetime.now().year
        result = p.predict_month(args.mese, anno)
        results = [result]
    else:
        results = p.predict(mesi_avanti=args.mesi)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(f"\n{'='*60}")
            print(f"📅 {r['mese'].upper()} {r['anno']}")
            print(f"{'='*60}")

            print("\n🗓️  Date previste:")
            for d in r["date_previste"]:
                print(f"   {d['data']} ({d['giorno']}) — {d['tema_probabile']} — ~{d['n_prodotti_attesi']} prodotti")

            print(f"\n📦 Top prodotti attesi (per frequenza storica):")
            for i, prod in enumerate(r["prodotti"]["top_per_frequenza"][:15], 1):
                print(f"   {i:2d}. {prod['nome']} ({prod['apparizioni_storiche']}x)")

            if r["prodotti"]["ricorrenti_annuali"]:
                print(f"\n🔁 Prodotti ricorrenti ogni anno in {r['mese']}:")
                for prod in r["prodotti"]["ricorrenti_annuali"][:10]:
                    print(f"   • {prod['nome']} (presente in {prod['frequenza']} anni: {prod['anni'][-3:]}...)")

            print(f"\n🏷️  Temi:")
            for t in r["prodotti"]["temi"]:
                bar = "█" * int(t["peso"] / 3)
                print(f"   {t['tema']:15s} {t['peso']:5.1f}% {bar}")


if __name__ == "__main__":
    main()