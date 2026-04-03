# 🏛️ Ça va Parlement européen ?

Bot de transparence parlementaire qui surveille les **mouvements de collaborateurs des eurodéputés français** au Parlement européen et publie chaque arrivée et départ en temps réel.

🔵 Bluesky : [@cavaeuroparl.bsky.social](https://bsky.app/profile/cavaeuroparl.bsky.social)
📬 Telegram : [@cavaparlement](https://t.me/cavaparlement)

> Projet citoyen open source — dans la lignée de [@cavasenat.bsky.social](https://bsky.app/profile/cavasenat.bsky.social) et [@cavaassemblee.bsky.social](https://bsky.app/profile/cavaassemblee.bsky.social)

---

## Ce que fait le bot

Chaque jour, le bot :

1. Récupère la liste des 81 eurodéputés français actifs via l'**EP Open Data API**
2. Scanne la page des assistants du site officiel du Parlement européen (A-Z)
3. Compare avec l'état de la veille (stocké dans `state.json`)
4. Publie chaque changement sur Bluesky et Telegram

### Types de collaborateurs suivis

| Type | Description |
|------|-------------|
| 🏛️ Accrédité·e | Basé·e à Bruxelles ou Strasbourg, sous contrat direct avec le PE |
| 🏛️ Accrédité·e mutualisé·e | Partagé·e entre plusieurs eurodéputés |
| 📍 Assistant·e local·e | Basé·e en France, sous contrat de droit français |
| 🔧 Prestataire de services | Personne morale ou physique sous contrat de service |
| 💶 Agent payeur | Gère les aspects fiscaux et sociaux des assistants locaux |
| 🎓 Stagiaire | Stage au sein du cabinet d'un eurodéputé |

---

## Sources de données

| Source | Usage |
|--------|-------|
| [EP Open Data API v2](https://data.europarl.europa.eu/api/v2) | Liste des eurodéputés français, groupe politique |
| [europarl.europa.eu/meps/en/assistants](https://www.europarl.europa.eu/meps/en/assistants) | Liste complète des assistants avec MEP associé |

Les données sont publiques et mises à disposition sous licence [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) par le Parlement européen.

---

## Projets liés

| Bot | Assemblée nationale | Sénat | Parlement européen |
|-----|--------------------:|------:|-------------------:|
| Bluesky | [@cavaassemblee.bsky.social](https://bsky.app/profile/cavaassemblee.bsky.social) | [@cavasenat.bsky.social](https://bsky.app/profile/cavasenat.bsky.social) | [@cavaeuroparl.bsky.social](https://bsky.app/profile/cavaeuroparl.bsky.social) |
| Telegram | [@cavaparlement](https://t.me/cavaparlement) | [@cavaparlement](https://t.me/cavaparlement) | [@cavaparlement](https://t.me/cavaparlement) |

---

## Licence

MIT — contributions bienvenues.
