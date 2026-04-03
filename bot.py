"""
CavaEuroparl - Bot Bluesky suivant les mouvements de collaborateurs
des eurodéputés français au Parlement européen.

Source MEPs  : EP Open Data API (data.europarl.europa.eu/api/v2)
Source staff : EP website (europarl.europa.eu/meps/en/assistants)
"""

import json
import os
import sys
import time
import re

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from atproto import Client

# ─── Configuration ────────────────────────────────────────────────────────────

BLUESKY_HANDLE   = "cavaeuroparl.bsky.social"
BLUESKY_PASSWORD = os.environ.get("BLUESKY_PASSWORD")
STATE_FILE       = "state.json"

EP_API_BASE  = "https://data.europarl.europa.eu/api/v2"
EP_SITE_BASE = "https://www.europarl.europa.eu"
CURRENT_TERM = 10   # législature 2024-2029

HEADERS = {
    "User-Agent": "CavaEuroparl/1.0 (@cavaeuroparl.bsky.social) - Civic transparency bot",
    "Accept": "text/html,application/xhtml+xml",
}
API_HEADERS = {
    "User-Agent": "CavaEuroparl/1.0 (@cavaeuroparl.bsky.social)",
    "Accept": "application/ld+json",
}

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# ─── Session HTTP avec retry automatique ──────────────────────────────────────

def make_session() -> requests.Session:
    """Crée une session requests avec retry exponentiel (3 tentatives)."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    return session

SESSION = make_session()

# Emojis par type d'assistant
TYPE_EMOJIS = {
    "accredited assistants":           "🏛️",
    "accredited assistants (grouping)": "🏛️",
    "local assistants":                "📍",
    "local assistants (grouping)":     "📍",
    "specialised service providers":   "🔧",
    "paying agents":                   "💶",
    "paying agents (grouping)":        "💶",
    "trainees":                        "🎓",
    "assistants to the vice-presidency/to the quaestorate": "⭐",
}

# Labels français par type
TYPE_LABELS_FR = {
    "accredited assistants":           "Accrédité·e (Bruxelles/Strasbourg)",
    "accredited assistants (grouping)": "Accrédité·e mutualisé·e",
    "local assistants":                "Assistant·e local·e (France)",
    "local assistants (grouping)":     "Assistant·e local·e mutualisé·e",
    "specialised service providers":   "Prestataire de services",
    "paying agents":                   "Agent payeur",
    "paying agents (grouping)":        "Agent payeur mutualisé",
    "trainees":                        "Stagiaire",
    "assistants to the vice-presidency/to the quaestorate": "Assistant·e VP/Questeur",
}


# ─── Récupération des eurodéputés français ────────────────────────────────────

def get_french_meps() -> dict:
    """
    Retourne un dict {mep_id (str): mep_name (str)} pour tous les
    eurodéputés français du terme en cours.
    """
    print("→ Récupération des eurodéputés français via EP Open Data API...")

    url = f"{EP_API_BASE}/meps"
    params = {
        "country-of-representation": "FR",
        "format":                    "application/ld+json",
        "parliamentary-term":        CURRENT_TERM,
        "json-layout":               "framed",
        "limit":                     200,
        "offset":                    0,
    }

    resp = SESSION.get(url, params=params, headers=API_HEADERS, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    # DEBUG (premier run) – affiche la structure pour valider le parsing
    items = data.get("data", [])
    if items:
        first = items[0]
        print(f"  [DEBUG] Clés d'un item MEP : {list(first.keys())}")
        print(f"  [DEBUG] Premier item : {json.dumps(first, ensure_ascii=False)[:600]}")

    meps = {}
    for item in items:
        # ID numérique — extrait de l'URI @id ou du champ identifier
        at_id  = item.get("@id", "")
        mep_id = at_id.rstrip("/").split("/")[-1] if at_id else ""
        if not mep_id or not mep_id.isdigit():
            mep_id = str(item.get("identifier", ""))

        # Nom — plusieurs champs possibles selon la version de l'API
        mep_name = (
            item.get("label")
            or item.get("foaf:name")
            or item.get("skos:prefLabel")
            or (
                f"{item.get('foaf:givenName', '')} "
                f"{item.get('foaf:familyName', '')}".strip()
            )
            or f"MEP#{mep_id}"
        )

        if mep_id and mep_id.isdigit():
            meps[mep_id] = mep_name

    print(f"  {len(meps)} eurodéputés français trouvés")
    return meps


# ─── Récupération des assistants (scraping EP website) ────────────────────────

def _parse_assistants_table(soup: BeautifulSoup) -> list[dict]:
    """
    Parse le tableau standard de la page /meps/en/assistants.
    Retourne une liste de {assistant_name, assistant_type, mep_ids}.
    """
    results = []
    table = soup.find("table")
    if not table:
        return results

    rows = table.find_all("tr")
    for row in rows[1:]:   # on passe le header
        cols = row.find_all("td")
        if len(cols) < 3:
            continue

        assistant_name = cols[0].get_text(separator=" ", strip=True)
        assistant_type = cols[1].get_text(separator=" ", strip=True)

        # Colonne 3 : liens vers les profils MEP → extraction des IDs
        mep_links = cols[2].find_all("a", href=True)
        mep_ids = []
        for a in mep_links:
            m = re.search(r"/meps/en/(\d+)", a["href"])
            if m:
                mep_ids.append(m.group(1))

        if assistant_name and mep_ids:
            results.append({
                "name":    assistant_name,
                "type":    assistant_type,
                "mep_ids": mep_ids,
            })

    return results


def _fetch_assistants_for_letter(letter: str, offset: int = 0) -> list[dict]:
    """
    Récupère une page de résultats pour une lettre donnée.
    Retourne la liste parsée + un bool indiquant s'il y a une page suivante.
    """
    url = f"{EP_SITE_BASE}/meps/en/assistants"
    params = {
        "letter":      letter,
        "searchType":  "BY_ASSISTANT",
        "assistantType": "",
        "name":        "",
    }
    # On tente un offset si ce n'est pas la première page
    if offset > 0:
        params["offset"] = offset

    try:
        resp = SESSION.get(url, params=params, headers=HEADERS, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [ERREUR] Lettre {letter} offset {offset} : {e}")
        return [], False

    soup   = BeautifulSoup(resp.text, "html.parser")
    rows   = _parse_assistants_table(soup)

    # Détecte si un bouton "Load more" est présent
    has_more = bool(soup.find(string=re.compile(r"Load more", re.I)))

    return rows, has_more


def get_all_assistants_by_mep(french_mep_ids: set) -> dict:
    """
    Scanne toutes les lettres A-Z de la page assistants et retourne
    un dict {mep_id: [{name, type}, ...]} filtré aux seuls eurodéputés français.
    """
    print("→ Scan des assistants (A-Z)...")
    mep_to_assistants = {mid: [] for mid in french_mep_ids}
    seen = set()   # évite les doublons (name, type, mep_id)

    for letter in ALPHABET:
        offset   = 0
        has_more = True
        page_num = 0

        while has_more:
            rows, has_more = _fetch_assistants_for_letter(letter, offset)
            page_num += 1

            for row in rows:
                for mep_id in row["mep_ids"]:
                    if mep_id in french_mep_ids:
                        key = (row["name"], row["type"], mep_id)
                        if key not in seen:
                            seen.add(key)
                            mep_to_assistants[mep_id].append({
                                "name": row["name"],
                                "type": row["type"],
                            })

            if not rows:
                break

            # Pagination : on essaie d'avancer par tranches de 10
            if has_more:
                offset += 10
                time.sleep(0.3)

            # Protection anti-boucle infinie
            if page_num > 30:
                print(f"  [WARN] Lettre {letter} : pagination stoppée après 30 pages")
                break

        time.sleep(0.2)

    total = sum(len(v) for v in mep_to_assistants.values())
    print(f"  {total} entrées assistants trouvées pour les eurodéputés français")
    return mep_to_assistants


# ─── State ────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE) and os.path.getsize(STATE_FILE) > 5:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print(f"  State sauvegardé ({len(state)} MEPs)")


# ─── Bluesky ──────────────────────────────────────────────────────────────────

def format_post(change: dict) -> str:
    emoji      = TYPE_EMOJIS.get(change["assistant_type"].lower(), "👤")
    type_label = TYPE_LABELS_FR.get(change["assistant_type"].lower(), change["assistant_type"])
    mep_url    = f"{EP_SITE_BASE}/meps/en/{change['mep_id']}/ASSISTANTS"

    if change["type"] == "arrival":
        text = (
            f"🇪🇺 Nouvelle arrivée au Parlement européen\n\n"
            f"{emoji} {change['assistant_name']} rejoint l'équipe de "
            f"{change['mep_name']}\n"
            f"📋 {type_label}\n\n"
            f"➡️ {mep_url}"
        )
    else:
        text = (
            f"🇪🇺 Départ au Parlement européen\n\n"
            f"{emoji} {change['assistant_name']} quitte l'équipe de "
            f"{change['mep_name']}\n"
            f"📋 {type_label}\n\n"
            f"➡️ {mep_url}"
        )

    if len(text) > 300:
        text = text[:297] + "..."
    return text


def post_to_bluesky(text: str) -> None:
    client = Client()
    client.login(BLUESKY_HANDLE, BLUESKY_PASSWORD)
    client.send_post(text=text)
    print(f"  ✓ Posté ({len(text)} car.)")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  CavaEuroparl — suivi des collaborateurs des MEPs FR")
    print("=" * 55)

    state        = load_state()
    is_first_run = len(state) == 0

    if is_first_run:
        print("⚠️  Premier run : construction de l'état initial, aucun post Bluesky")

    # 1. Eurodéputés français
    try:
        french_meps = get_french_meps()   # {mep_id: mep_name}
    except Exception as e:
        print(f"Erreur fatale lors de la récupération des MEPs : {e}")
        sys.exit(1)

    if not french_meps:
        print("Aucun MEP trouvé — vérifier l'API EP")
        sys.exit(1)

    # 2. Assistants actuels
    try:
        current_by_mep = get_all_assistants_by_mep(set(french_meps.keys()))
    except Exception as e:
        print(f"Erreur fatale lors du scan des assistants : {e}")
        sys.exit(1)

    # 3. Construction du nouvel état + détection des changements
    new_state = {}
    changes   = []

    for mep_id, mep_name in french_meps.items():
        current_assistants = current_by_mep.get(mep_id, [])
        current_set        = {(a["name"], a["type"]) for a in current_assistants}

        new_state[mep_id] = {
            "name":       mep_name,
            "assistants": [{"name": n, "type": t} for n, t in sorted(current_set)],
        }

        if not is_first_run and mep_id in state:
            prev_set = {(a["name"], a["type"]) for a in state[mep_id].get("assistants", [])}

            for name, atype in (current_set - prev_set):
                changes.append({
                    "type":           "arrival",
                    "mep_id":         mep_id,
                    "mep_name":       mep_name,
                    "assistant_name": name,
                    "assistant_type": atype,
                })

            for name, atype in (prev_set - current_set):
                changes.append({
                    "type":           "departure",
                    "mep_id":         mep_id,
                    "mep_name":       mep_name,
                    "assistant_name": name,
                    "assistant_type": atype,
                })

    # 4. Résumé
    print(f"\n{'─'*55}")
    print(f"  MEPs suivis   : {len(new_state)}")
    print(f"  Changements   : {len(changes)}")

    # 5. Posts Bluesky
    if changes:
        if not BLUESKY_PASSWORD:
            print("\n⚠️  BLUESKY_PASSWORD absent — affichage seul :")
            for c in changes:
                arrow = "→" if c["type"] == "arrival" else "←"
                print(f"  [{c['type'].upper()}] {arrow} {c['assistant_name']} "
                      f"({c['assistant_type']}) | {c['mep_name']}")
        else:
            print(f"\nPublication de {len(changes)} changement(s) sur Bluesky...")
            for change in changes:
                post_text = format_post(change)
                try:
                    post_to_bluesky(post_text)
                    time.sleep(3)
                except Exception as e:
                    print(f"  Erreur lors du post : {e}")
    else:
        if not is_first_run:
            print("\n  Aucun changement aujourd'hui.")

    # 6. Sauvegarde
    save_state(new_state)
    print("\n✅ Terminé")


if __name__ == "__main__":
    main()
