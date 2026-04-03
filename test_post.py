"""
test_post.py — Envoie un post de test sur @cavaeuroparl.bsky.social
A supprimer après vérification.
"""
import os
from atproto import Client

BLUESKY_HANDLE   = "cavaeuroparl.bsky.social"
BLUESKY_PASSWORD = os.environ.get("BLUESKY_PASSWORD")

if not BLUESKY_PASSWORD:
    print("Erreur : variable BLUESKY_PASSWORD absente")
    exit(1)

TEXT = (
    "🇪🇺 Bonjour ! Je suis @cavaeuroparl.bsky.social\n\n"
    "Je surveille les mouvements de collaborateurs "
    "des eurodéputés français au Parlement européen "
    "et je publie chaque arrivée et chaque départ.\n\n"
    "Bot open source 🤖"
)

print(f"Connexion en tant que {BLUESKY_HANDLE}...")
client = Client()
client.login(BLUESKY_HANDLE, BLUESKY_PASSWORD)

print("Envoi du post...")
client.send_post(text=TEXT)
print(f"✅ Posté ({len(TEXT)} caractères)")
