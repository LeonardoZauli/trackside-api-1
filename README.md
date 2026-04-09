# Lidl Parkside Predictor API — Production v6

API REST per previsioni prodotti Lidl Parkside, pronta per il deploy su **Render.com**.

---

## Struttura

```
production/
├── main.py               # Entry point FastAPI (lifespan, middleware, exception handlers)
├── config.py             # Tutte le impostazioni (env vars / .env)
├── logger.py             # Logger strutturato
├── model_manager.py      # Carica il .pkl all'avvio, reload a caldo
├── security.py           # HMAC-SHA256, rate limiter, middleware, admin key
├── helpers.py            # Confidenza date/prodotti, fuzzy search, dedup
├── lidl_predictor.py     # Classe predictor (necessaria per deserializzare il pkl)
├── routes/
│   ├── health.py         # GET /health, GET /health/full
│   ├── predictions.py    # Route pubbliche (firmate HMAC)
│   └── admin.py          # Route admin (protette da X-Admin-Key)
├── models/
│   └── lidl_predictor_v4.pkl   # Modello pre-analizzato
├── data/
│   └── lidl_prodotti.csv       # Dataset storico (fallback)
├── requirements.txt
├── .env.example
├── Dockerfile
└── render.yaml
```

---

## Deploy su Render.com (5 minuti)

### 1. Prepara il repo

```bash
# Copia la cartella production/ nella root del tuo repo GitHub
# oppure crea un repo dedicato con solo il contenuto di production/
git add .
git commit -m "feat: production API v6"
git push
```

### 2. Crea il Web Service su Render

1. Vai su [render.com](https://render.com) → **New → Web Service**
2. Connetti il tuo repo GitHub
3. Render rileva il `Dockerfile` automaticamente
4. Se usi `render.yaml` nella root: **New → Blueprint** e Render configura tutto

### 3. Imposta le variabili d'ambiente sensibili

Nel pannello Render → **Environment**, aggiungi:

| Variabile | Valore |
|---|---|
| `API_SECRET` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_API_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ALLOWED_ORIGINS` | `https://tuosito.com,https://www.tuosito.com` |

Le altre variabili sono già nel `render.yaml`.

### 4. Deploy

Render fa il build del Docker container e avvia l'app. Controlla i log:
- ✅ `Modello caricato — API pronta` → tutto ok
- ❌ `Avvio fallito` → controlla che `models/lidl_predictor_v4.pkl` sia nel repo

---

## Route

### Pubbliche (richiedono firma HMAC)

| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/health` | Health check base (usato da Render) |
| GET | `/health/full` | Info dettagliate modello + stato |
| GET | `/api/v1/prossime-uscite?n=3` | Prossime N date di uscita previste |
| GET | `/api/v1/prodotti?cerca=trapano` | Catalogo con ricerca fuzzy |
| GET | `/api/v1/previsione-giorno/2026-05-12` | Prodotti per una data specifica |
| GET | `/api/v1/prodotto/Tosaerba elettrico` | Date future per prodotto singolo |

### Admin (header `X-Admin-Key: <ADMIN_API_KEY>`)

| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/admin/status` | Uptime, modello, sicurezza |
| POST | `/admin/reload` | Ricarica modello pkl a caldo |
| GET | `/admin/model` | Info e statistiche predictor |
| GET | `/admin/rate-limits` | Rate limit per ogni IP |
| DELETE | `/admin/rate-limits/{ip}` | Resetta un IP |
| DELETE | `/admin/rate-limits` | Resetta tutti gli IP |
| GET | `/admin/config` | Configurazione attiva (no secrets) |

---

## Firma HMAC (per il tuo backend/frontend)

Le route pubbliche richiedono due header firmati. Il tuo backend deve generarli:

### Python
```python
import hmac, hashlib, time, requests

API_SECRET = "la-tua-chiave-segreta"
API_BASE   = "https://tua-app.onrender.com"

def call_api(path: str) -> dict:
    ts = str(int(time.time()))
    message = f"{ts}:GET:{path}"
    sig = hmac.new(API_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
    r = requests.get(
        f"{API_BASE}{path}",
        headers={"X-Signature": sig, "X-Timestamp": ts},
    )
    return r.json()

call_api("/api/v1/prossime-uscite?n=3")
call_api("/api/v1/prodotto/Tosaerba elettrico")
```

### Node.js
```javascript
const crypto = require('crypto');

const API_SECRET = 'la-tua-chiave-segreta';
const API_BASE   = 'https://tua-app.onrender.com';

async function callApi(path) {
  const ts = Math.floor(Date.now() / 1000).toString();
  const sig = crypto.createHmac('sha256', API_SECRET)
    .update(`${ts}:GET:${path}`)
    .digest('hex');
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'X-Signature': sig, 'X-Timestamp': ts },
  });
  return res.json();
}
```

### Admin
```bash
curl -X POST https://tua-app.onrender.com/admin/reload \
  -H "X-Admin-Key: la-tua-admin-key"

curl https://tua-app.onrender.com/admin/status \
  -H "X-Admin-Key: la-tua-admin-key"
```

---

## Sviluppo locale

```bash
cd production

# Crea l'ambiente virtuale
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Installa dipendenze
pip install -r requirements.txt

# Copia e configura le variabili
cp .env.example .env
# Modifica .env: imposta APP_ENV=development, ENABLE_DOCS=true

# Avvia
uvicorn main:app --reload --port 8000

# Docs disponibili su http://localhost:8000/docs
```

In modalità `development`:
- La firma HMAC è **opzionale** (puoi chiamare le API direttamente)
- I docs Swagger sono **abilitati** su `/docs`
- L'origin check è **disabilitato**

---

## Note sul modello

Il `LidlPredictor` viene serializzato come `.pkl` per un caricamento istantaneo all'avvio (~0.1s vs ~2s dal CSV).

Se aggiorni il CSV con nuovi dati, rigenera il pkl:
```python
from lidl_predictor import LidlPredictor
import pickle

p = LidlPredictor("data/lidl_prodotti.csv")
with open("models/lidl_predictor_v4.pkl", "wb") as f:
    pickle.dump(p, f)
print("PKL aggiornato!")
```

Poi chiama `POST /admin/reload` senza riavviare Render.
