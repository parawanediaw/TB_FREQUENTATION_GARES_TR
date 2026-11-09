"""
Télécharge dans static/ la police Plus Jakarta Sans et la bibliothèque de carte Leaflet 1.9.4.

L'application les sert alors elle-même : les postes qui l'affichent n'ont besoin d'aucun accès Internet
(sauf pour le fond de carte des pages « Suivi des trains » et « Serpent de charge »).
À lancer sur un PC connecté à Internet (appelé par scripts\\1_preparer_sur_un_pc_avec_internet.bat).
N'utilise que la bibliothèque standard de Python ; respecte le proxy du système.
"""
from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
# Un navigateur récent obtient le format woff2 (le plus léger)
NAVIGATEUR = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
URL_POLICE = "https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap"
URL_LEAFLET = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/"


def lire(url: str) -> bytes:
    requete = urllib.request.Request(url, headers={"User-Agent": NAVIGATEUR})
    with urllib.request.urlopen(requete, timeout=60) as r:
        return r.read()


def polices() -> None:
    css = lire(URL_POLICE).decode("utf-8")
    dossier = STATIC / "polices"
    dossier.mkdir(parents=True, exist_ok=True)
    adresses = list(dict.fromkeys(re.findall(r"url\((https://[^)]+)\)", css)))
    if not adresses:
        raise RuntimeError("aucun fichier de police trouvé dans la réponse de Google Fonts")
    for i, url in enumerate(adresses):
        nom = f"plus-jakarta-sans-{i + 1}.woff2"
        (dossier / nom).write_bytes(lire(url))
        css = css.replace(url, f"app/static/polices/{nom}")  # chemin servi par l'application
    (dossier / "polices.css").write_text(css, encoding="utf-8")
    print(f"   ✓ police Plus Jakarta Sans : {len(adresses)} fichier(s) dans static/polices")


def leaflet() -> None:
    dossier = STATIC / "leaflet"
    dossier.mkdir(parents=True, exist_ok=True)
    for nom in ("leaflet.js", "leaflet.css"):
        (dossier / nom).write_bytes(lire(URL_LEAFLET + nom))
    print("   ✓ Leaflet 1.9.4 (carte) dans static/leaflet")


def telecharger_ressources() -> int:
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    erreurs = 0
    for etape in (polices, leaflet):
        try:
            etape()
        except Exception as e:
            erreurs += 1
            print(f"   ✗ {etape.__name__} : {e}")
    return 1 if erreurs else 0


if __name__ == "__main__":
    sys.exit(telecharger_ressources())
