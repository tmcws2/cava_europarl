"""
CavaEuroparl - Bot Bluesky + Telegram suivant les mouvements de collaborateurs
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

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHANNEL = "@cavaparlement"

STATE_FILE   = "state.json"
EP_API_BASE  = "https://data.europarl.europa.eu/api/v2"
EP_SITE_BASE = "https://www.europarl.europa.eu"
CURRENT_TERM = 10

HEADERS = {
    "User-Agent": "CavaEuroparl/1.0 (@cavaeuroparl.bsky.social) - Civic transparency bot",
    "Accept": "text/html,application/xhtml+xml",
}
API_HEADERS = {
    "User-Agent": "CavaEuroparl/1.0 (@cavaeuroparl.bsky.social)",
    "Accept": "application/ld+json",
}

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# ─── Groupes politiques ───────────────────────────────────────────────────────

# Correspondance entre les identifiants de l'API EP et un label lisible
GROUP_LABELS = {
    "PPE":        "PPE",
    "SD":         "S&D",
    "S-D":        "S&D",
    "RENEW":      "Renew Europe",
    "VERTS-ALE":  "Verts/ALE",
    "ECR":        "ECR",
    "THE-LEFT":   "La Gauche",
    "LEFT":       "La Gauche",
    "ESN":        "ESN",
    "PFE":        "Patriotes pour l'Europe",
    "NI":         "Non-inscrit·e",
}

GROUP_EMOJIS = {
    "PPE":        "🔵",
    "SD":         "🔴",
    "S-D":        "🔴",
    "RENEW":      "🟡",
    "VERTS-ALE":  "🟢",
    "ECR":        "🟤",
    "THE-LEFT":   "☭",
    "LEFT":       "☭",
    "ESN":        "⚫",
    "PFE":        "🟠",
    "NI":         "⚪",
}

# ─── Types d'assistants ───────────────────────────────────────────────────────

TYPE_EMOJIS = {
    "accredited assistants":            "🏛️",
    "accredited assistants (grouping)":  "🏛️",
    "local assistants":                 "📍",
    "local assistants (grouping)":      "📍",
    "specialised service providers":    "🔧",
    "paying agents":                    "💶",
    "paying agents (grouping)":         "💶",
    "trainees":                         "🎓",
    "assistants to the vice-presidency/to the quaestorate": "⭐",
}

TYPE_LABELS_FR = {
    "accredited assistants":            "Accrédité·e (Bruxelles/Strasbourg)",
    "accredited assistants (grouping)": "Accrédité·e mutualisé·e",
    "local assistants":                 "Assistant·e local·e (France)",
    "local assistants (grouping)":      "Assistant·e local·e mutualisé·e",
    "specialised service providers":    "Prestataire de services",
    "paying agents":                    "Agent payeur",
    "paying agents (grouping)":         "Agent payeur mutualisé",
    "trainees":                         "Stagiaire",
    "assistants to the vice-presidency/to the quaestorate": "Assistant·e VP/Questeur",
}


# ─── Session HTTP ─────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3, backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    return session

SESSION = make_session()


# ─── Helpers groupes ──────────────────────────────────────────────────────────

def _extract_group_key(item: dict) -> str:
    """
    Extrait la clé du groupe politique depuis un item MEP de l'API EP.
    Essaie plusieurs champs selon la version de l'API.
    """
    # Champ direct (certaines versions de l'API)
    for field in ("ep-core:politicalGroup", "politicalGroup", "hasGroup"):
        val = item.get(field)
        if isinstance(val, str):
            # Peut être une URI ou juste l'acronyme
            return val.rstrip("/").split("/")[-1].upper()
        if isinstance(val, dict):
            return (val.get("notation") or val.get("label") or "").upper()

    # Champ imbriqué "hasMembership" ou "memberOf"
    for field in ("hasMembership", "memberOf", "ep-core:hasMembership"):
        memberships = item.get(field, [])
        if isinstance(memberships, dict):
            memberships = [memberships]
        for m in memberships:
            if isinstance(m, dict):
                role = m.get("role", m.get("ep-core:role", ""))
                if "political" in str(role).lower() or "group" in str(role).lower():
                    org = m.get("organization", m.get("ep-core:organization", {}))
                    if isinstance(org, dict):
                        key = org.get("notation") or org.get("label") or ""
                        return str(key).upper()
                    if isinstance(org, str):
                        return org.rstrip("/").split("/")[-1].upper()

    return ""


def format_group(group_key: str) -> tuple:
    """Retourne (emoji, label) pour un groupe politique."""
    key = group_key.upper().replace("_", "-")
    emoji = GROUP_EMOJIS.get(key, "🏛️")
    label = GROUP_LABELS.get(key, group_key if group_key else "Groupe inconnu")
    return emoji, label


# ─── Récupération des eurodéputés français ────────────────────────────────────

def get_french_meps() -> dict:
    """
    Retourne {mep_id: {"name": str, "group": str}} pour tous les
    eurodéputés français actifs.
    """
    print("-> Récupération des eurodéputés français via EP Open Data API...")

    url = f"{EP_API_BASE}/meps"
    params = {
        "country-of-representation": "FR",
        "format":             "application/ld+json",
        "parliamentary-term": CURRENT_TERM,
        "json-layout":        "framed",
        "limit":              200,
        "offset":             0,
    }

    resp = SESSION.get(url, params=params, headers=API_HEADERS, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    items = data.get("data", [])
    if items:
        first = items[0]
        print(f"  [DEBUG] Clés MEP : {list(first.keys())}")
        print(f"  [DEBUG] Premier item : {json.dumps(first, ensure_ascii=False)[:800]}")

    meps = {}
    for item in items:
        at_id  = item.get("@id", "")
        mep_id = at_id.rstrip("/").split("/")[-1] if at_id else ""
        if not mep_id or not mep_id.isdigit():
            mep_id = str(item.get("identifier", ""))

        mep_name = (
            item.get("label")
            or item.get("foaf:name")
            or item.get("skos:prefLabel")
            or f"{item.get('foaf:givenName', '')} {item.get('foaf:familyName', '')}".strip()
            or f"MEP#{mep_id}"
        )

        group_key = _extract_group_key(item)

        if mep_id and mep_id.isdigit():
            meps[mep_id] = {"name": mep_name, "group": group_key}

    print(f"  {len(meps)} eurodéputés français trouvés")
    # Affiche un échantillon de groupes détectés pour validation
    sample = [(v["name"], v["group"]) for v in list(meps.values())[:5]]
    print(f"  [DEBUG] Échantillon groupes : {sample}")
    return meps


# ─── Récupération des assistants ──────────────────────────────────────────────

def _parse_assistants_table(soup: BeautifulSoup) -> list:
    results = []
    table = soup.find("table")
    if not table:
        return results

    for row in table.find_all("tr")[1:]:
        cols = row.find_all("td")
        if len(cols) < 3:
            continue

        assistant_name = cols[0].get_text(separator=" ", strip=True)
        assistant_type = cols[1].get_text(separator=" ", strip=True)

        mep_ids = []
        for a in cols[2].find_all("a", href=True):
            m = re.search(r"/meps/en/(\d+)", a["href"])
            if m:
                mep_ids.append(m.group(1))

        if assistant_name and mep_ids:
            results.append({"name": assistant_name, "type": assistant_type, "mep_ids": mep_ids})

    return results


def _fetch_assistants_for_letter(letter: str, offset: int = 0):
    url = f"{EP_SITE_BASE}/meps/en/assistants"
    params = {"letter": letter, "searchType": "BY_ASSISTANT", "assistantType": "", "name": ""}
    if offset > 0:
        params["offset"] = offset

    try:
        resp = SESSION.get(url, params=params, headers=HEADERS, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [ERREUR] Lettre {letter} offset {offset} : {e}")
        return [], False

    soup     = BeautifulSoup(resp.text, "html.parser")
    rows     = _parse_assistants_table(soup)
    has_more = bool(soup.find(string=re.compile(r"Load more", re.I)))
    return rows, has_more


def get_all_assistants_by_mep(french_mep_ids: set) -> dict:
    print("-> Scan des assistants (A-Z)...")
    mep_to_assistants = {mid: [] for mid in french_mep_ids}
    seen = set()

    for letter in ALPHABET:
        offset, has_more, page_num = 0, True, 0

        while has_more:
            rows, has_more = _fetch_assistants_for_letter(letter, offset)
            page_num += 1

            for row in rows:
                for mep_id in row["mep_ids"]:
                    if mep_id in french_mep_ids:
                        key = (row["name"], row["type"], mep_id)
                        if key not in seen:
                            seen.add(key)
                            mep_to_assistants[mep_id].append(
                                {"name": row["name"], "type": row["type"]}
                            )

            if not rows or page_num > 30:
                break
            if has_more:
                offset += 10
                time.sleep(0.3)

        time.sleep(0.2)

    total = sum(len(v) for v in mep_to_assistants.values())
    print(f"  {total} entrées trouvées pour les eurodéputés français")
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


# ─── Formatage des posts ──────────────────────────────────────────────────────

def _build_message(change: dict) -> dict:
    """Retourne {bluesky: str, telegram: str} pour un changement."""
    emoji_type  = TYPE_EMOJIS.get(change["assistant_type"].lower(), "👤")
    type_label  = TYPE_LABELS_FR.get(change["assistant_type"].lower(), change["assistant_type"])
    mep_url     = f"{EP_SITE_BASE}/meps/en/{change['mep_id']}/ASSISTANTS"

    group_key         = change.get("mep_group", "")
    group_emoji, group_label = format_group(group_key)

    if change["type"] == "arrival":
        header    = "🇪🇺 Nouvelle arrivée au Parlement européen"
        action_bs = (
            f"{emoji_type} {change['assistant_name']} rejoint l'équipe de "
            f"{change['mep_name']} ({group_emoji} {group_label})"
        )
        action_tg = (
            f"{emoji_type} <b>{change['assistant_name']}</b> rejoint l'équipe de "
            f"<b>{change['mep_name']}</b> ({group_emoji} {group_label})"
        )
    else:
        header    = "🇪🇺 Départ au Parlement européen"
        action_bs = (
            f"{emoji_type} {change['assistant_name']} quitte l'équipe de "
            f"{change['mep_name']} ({group_emoji} {group_label})"
        )
        action_tg = (
            f"{emoji_type} <b>{change['assistant_name']}</b> quitte l'équipe de "
            f"<b>{change['mep_name']}</b> ({group_emoji} {group_label})"
        )

    bluesky = f"{header}\n\n{action_bs}\n📋 {type_label}\n\n➡️ {mep_url}"
    if len(bluesky) > 300:
        bluesky = bluesky[:297] + "..."

    telegram = (
        f"{header}\n\n"
        f"{action_tg}\n"
        f"📋 {type_label}\n\n"
        f'➡️ <a href="{mep_url}">Voir la fiche EP</a>'
    )

    return {"bluesky": bluesky, "telegram": telegram}


# ─── Publication ──────────────────────────────────────────────────────────────

def post_to_bluesky(text: str) -> None:
    client = Client()
    client.login(BLUESKY_HANDLE, BLUESKY_PASSWORD)
    client.send_post(text=text)
    print(f"  ✓ Bluesky ({len(text)} car.)")


def post_to_telegram(html: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHANNEL,
        "text":       html,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    print(f"  ✓ Telegram ({len(html)} car.)")


def publish_change(change: dict) -> None:
    messages = _build_message(change)

    if BLUESKY_PASSWORD:
        try:
            post_to_bluesky(messages["bluesky"])
        except Exception as e:
            print(f"  ✗ Bluesky : {e}")
    else:
        print("  ⚠️  BLUESKY_PASSWORD absent")

    if TELEGRAM_TOKEN:
        try:
            post_to_telegram(messages["telegram"])
        except Exception as e:
            print(f"  ✗ Telegram : {e}")
    else:
        print("  ⚠️  TELEGRAM_TOKEN absent")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  CavaEuroparl — suivi des collaborateurs des MEPs FR")
    print("=" * 55)

    state        = load_state()
    is_first_run = len(state) == 0

    if is_first_run:
        print("⚠️  Premier run : construction de l'état initial, aucun post")

    # 1. Eurodéputés français — {mep_id: {"name": ..., "group": ...}}
    try:
        french_meps = get_french_meps()
    except Exception as e:
        print(f"Erreur fatale (MEPs) : {e}")
        sys.exit(1)

    if not french_meps:
        print("Aucun MEP trouvé — vérifier l'API EP")
        sys.exit(1)

    # 2. Assistants actuels
    try:
        current_by_mep = get_all_assistants_by_mep(set(french_meps.keys()))
    except Exception as e:
        print(f"Erreur fatale (assistants) : {e}")
        sys.exit(1)

    # 3. Comparaison avec l'état précédent
    new_state = {}
    changes   = []

    for mep_id, mep_info in french_meps.items():
        mep_name  = mep_info["name"]
        mep_group = mep_info["group"]
        current_set = {(a["name"], a["type"]) for a in current_by_mep.get(mep_id, [])}

        new_state[mep_id] = {
            "name":       mep_name,
            "group":      mep_group,
            "assistants": [{"name": n, "type": t} for n, t in sorted(current_set)],
        }

        if not is_first_run and mep_id in state:
            prev_set = {(a["name"], a["type"]) for a in state[mep_id].get("assistants", [])}
            # Groupe : prendre celui du nouvel état (peut avoir changé)
            for name, atype in (current_set - prev_set):
                changes.append({
                    "type": "arrival", "mep_id": mep_id, "mep_name": mep_name,
                    "mep_group": mep_group, "assistant_name": name, "assistant_type": atype,
                })
            for name, atype in (prev_set - current_set):
                changes.append({
                    "type": "departure", "mep_id": mep_id, "mep_name": mep_name,
                    "mep_group": mep_group, "assistant_name": name, "assistant_type": atype,
                })

    print(f"\n{'─'*55}")
    print(f"  MEPs suivis : {len(new_state)}")
    print(f"  Changements : {len(changes)}")

    if changes:
        print(f"\nPublication de {len(changes)} changement(s)...")
        for change in changes:
            arrow = "->" if change["type"] == "arrival" else "<-"
            print(f"  [{change['type'].upper()}] {arrow} {change['assistant_name']} | "
                  f"{change['mep_name']} ({change['mep_group']})")
            publish_change(change)
            time.sleep(3)
    else:
        if not is_first_run:
            print("\n  Aucun changement aujourd'hui.")

    save_state(new_state)
    print("\n✅ Terminé")


if __name__ == "__main__":
    main()
