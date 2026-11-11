"""
Tableau de bord TER Sénégal — Fréquentation des gares · Suivi des trains · Serpent de charge

    Lancement :  streamlit run app.py
    Réglages  :  parametres.toml (mode réel ou démonstration, horaires, JOJ, zones)
                 .streamlit/secrets.toml (identifiants SQL Server)
                 sql/*.sql (requêtes qui décrivent les tables)
"""
from __future__ import annotations

import base64
import hashlib
import html as _html
import json
import logging
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from statistics import median
from datetime import date, datetime, time as dtime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from donnees import ErreurDonnees, SourceDemo, SourceSQL
from gtfs import circulations, construire_reseau
from parametres import ErreurParametres, Parametres, charger_parametres

try:
    PARAM = charger_parametres()
    ERREUR_PARAMETRES = None
except ErreurParametres as _e:  # réglage invalide : la page l'affiche au lieu de planter
    PARAM, ERREUR_PARAMETRES = Parametres(), str(_e)

REEL = PARAM.mode == "reel" and ERREUR_PARAMETRES is None
# Les pages opérationnelles sont disponibles en mode réel : GTFS théorique + positions simulées.
PAGES_SIMULEES = True

st.set_page_config(
    page_title="Pilotage opérationnel TER Dakar–AIBD",
    page_icon="🚆",
    layout="wide",
    initial_sidebar_state="collapsed",
)

ASSETS = Path(__file__).parent / "assets"
STATIC = Path(__file__).parent / "static"
RAFRAICHISSEMENT = f"{PARAM.rafraichissement_secondes if REEL else 30}s"  # rafraîchissement automatique de la page
ACCELERATION = 1 if REEL else 10  # démonstration : trains 10 × plus rapides ; mode réel : horaires GTFS, temps réel

# Polices et carte servies par l'application (dossier static/) quand elles y sont : aucun accès Internet
# n'est alors nécessaire. Sinon (ou dans l'aperçu navigateur), elles viennent des sites publics.
_LOCAL = sys.platform != "emscripten"
POLICE_CSS = ((STATIC / "polices" / "polices.css").read_text(encoding="utf-8")
              if _LOCAL and (STATIC / "polices" / "polices.css").exists() else
              "@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');")
LEAFLET = ("app/static/leaflet" if _LOCAL and (STATIC / "leaflet" / "leaflet.js").exists()
           else "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist")

# ═══════════════════════════════ Réseau ═══════════════════════════════
# (gare, point kilométrique depuis Dakar)
GARES = [
    ("Dakar", 0), ("Thiaroye", 10), ("Rufisque", 17), ("Pikine", 25),
    ("Diamniadio", 42), ("AIBD", 55),
]
NOMS = [n for n, _ in GARES]
PKS = [k for _, k in GARES]

# Tracé simplifié de la voie, de Dakar vers l'AIBD (lat, lon)
TRACE = [
    (14.6732, -17.4293), (14.6830, -17.4370), (14.6925, -17.4418), (14.7040, -17.4378),
    (14.7165, -17.4318), (14.7262, -17.4262), (14.7348, -17.4185), (14.7442, -17.4058),
    (14.7520, -17.3928), (14.7558, -17.3800), (14.7575, -17.3655), (14.7600, -17.3545),
    (14.7612, -17.3430), (14.7578, -17.3310), (14.7512, -17.3205), (14.7440, -17.3080),
    (14.7360, -17.2960), (14.7275, -17.2840), (14.7210, -17.2745), (14.7140, -17.2600),
    (14.7075, -17.2440), (14.7040, -17.2290), (14.7080, -17.2120), (14.7150, -17.1980),
    (14.7205, -17.1840), (14.7160, -17.1650), (14.7070, -17.1460), (14.6960, -17.1250),
    (14.6850, -17.1040), (14.6760, -17.0880), (14.6710, -17.0760),
]
OCEAN = [14.692, -17.362]  # position de l'étiquette « Océan Atlantique »

LIGNES = ["Toutes les lignes", "Dakar → AIBD", "AIBD → Dakar"]


# ═══════════════════════════════ Données ═══════════════════════════════
# Modèle de démonstration cohérent : tous les chiffres affichés en découlent.
# À remplacer par vos sources (GTFS-RT pour les trains, billettique / GTFS pour les validations).
PARC_RAMES = PARAM.parc_rames    # rames disponibles (parametres.toml)
CAPACITE_RAME = PARAM.capacite_rame  # places par rame (parametres.toml)
V_COMMERCIALE = 75             # km/h, arrêts compris
# Horaires (ouverture, fermeture) par jour, lundi → dimanche, et période des JOJ : lus dans parametres.toml
HORAIRES_NORMAUX = list(PARAM.horaires_normaux)
HORAIRES_JOJ = tuple(PARAM.joj_horaires)          # JOJ : 05h → 00h, 7 j/7, dimanches compris
JOJ_DEBUT, JOJ_FIN = PARAM.joj_debut, PARAM.joj_fin  # Jeux Olympiques de la Jeunesse Dakar 2026
JOJ_HAUSSE = {"Dakar": 1.3, "Thiaroye": 1.15, "Rufisque": 1.2, "Pikine": 1.15, "Diamniadio": 1.9, "AIBD": 1.6}
JOJ_JOURS = [1, 1, 1, 1, 1.05, 1.02, .95]  # semaine plus homogène pendant les Jeux (lundi → dimanche)
CROISSANCE = 1.08              # croissance annuelle de la fréquentation hors JOJ (2026 vs 2025)

# (id, couleur, écart en minutes) — trains répartis régulièrement sur l'aller-retour
TRAINS = [("T1", "#0A72FA", 2), ("T2", "#05B079", 0), ("T3", "#FDA800", 3),
          ("T4", "#8429F8", 0), ("T5", "#F20C6F", 0), ("T6", "#0EA5B7", 0)]

# Validations d'un jour ouvré type : gare → (volume journalier d'entrées = de sorties, profil horaire)
VOLUMES = {"Dakar": (10500, "centre"), "Thiaroye": (6800, "peripherie"), "Rufisque": (6200, "peripherie"),
           "Pikine": (5900, "peripherie"), "Diamniadio": (4600, "pole"), "AIBD": (2400, "aeroport")}
ZONES_DEMO = {"Zone Dakar": ["Dakar"], "Zone Pikine": ["Thiaroye", "Pikine"],
              "Zone Rufisque": ["Rufisque", "Diamniadio"], "Zone AIBD": ["AIBD"]}
JOUR_FACTEUR = [1, 1, 1, 1, 1.03, .78, .6]  # lundi → dimanche


def _gauss(h, m, s):
    return math.exp(-((h - m) / s) ** 2)


def est_joj(jour: date) -> bool:
    return JOJ_DEBUT <= jour <= JOJ_FIN


def horaires(jour: date) -> tuple[int, int]:
    return HORAIRES_JOJ if est_joj(jour) else HORAIRES_NORMAUX[jour.weekday()]


def _profil_brut(type_, sens, h, joj):
    m, s, c = _gauss(h, 7.5, 1.3), _gauss(h, 18, 1.6), .32 * _gauss(h, 13, 3.2) + .05
    if type_ == "aeroport":
        v = _gauss(h, 14, 5) + .1
    elif type_ == "pole":
        v = .75 * m + .75 * s + c
    else:  # banlieue : on part le matin, on rentre le soir ; centre (Dakar) : l'inverse
        a, b = (1, .4) if (type_ == "peripherie") == (sens == "e") else (.4, 1)
        v = a * m + b * s + c
    return v + (.35 * _gauss(h, 21, 1.6) if joj else 0)  # JOJ : sessions sportives en soirée


@lru_cache(maxsize=None)
def profil(type_: str, sens: str, ouv: int, ferm: int, joj: bool) -> tuple:
    v = [_profil_brut(type_, sens, h, joj) if ouv <= h < ferm else 0.0 for h in range(24)]
    return tuple(x / sum(v) for x in v)


def _bruit(*cles) -> float:
    """Écart journalier déterministe (± 8 %) entre le réel et la semaine type."""
    n = int(hashlib.md5("|".join(map(str, cles)).encode()).hexdigest()[:8], 16)
    return .92 + .16 * n / 0xFFFFFFFF


def base_h(gare: str, jour_sem: int, h: int, sens: str, joj: bool = False) -> float:
    """Semaine type (normale ou JOJ) : validations pendant l'heure h (« e » = entrées, « s » = sorties)."""
    vol, type_ = VOLUMES[gare]
    if joj:
        return vol * JOJ_HAUSSE[gare] * JOJ_JOURS[jour_sem] * profil(type_, sens, *HORAIRES_JOJ, True)[h]
    return vol * JOUR_FACTEUR[jour_sem] * profil(type_, sens, *HORAIRES_NORMAUX[jour_sem], False)[h]


def attendu_h(gare: str, jour: date, h: int, sens: str) -> float:
    """Valeur attendue un jour donné : semaine type (JOJ ou normale) corrigée de la croissance annuelle."""
    return base_h(gare, jour.weekday(), h, sens, est_joj(jour)) / CROISSANCE ** (2026 - jour.year)


def flux_h(gare: str, jour: date, h: int, sens: str) -> float:
    """Validations réelles pendant l'heure h du jour donné."""
    return attendu_h(gare, jour, h, sens) * _bruit(jour.isoformat(), gare, h, sens)


def _heures_sim(ts: float) -> float:
    return ts * ACCELERATION / 3600


def etat_train(phase: float, ts: float) -> tuple[float, int]:
    """Position (pk) et sens d'un train : aller-retour continu à vitesse commerciale (même calcul que la carte)."""
    L = PKS[-1]
    u = (_heures_sim(ts) * V_COMMERCIALE + phase) % (2 * L)
    return (u, 1) if u < L else (2 * L - u, -1)


def decrire_position(pk: float, sens: int) -> tuple[str, str]:
    devant = [n for n, k in GARES if (k > pk + 1e-6 if sens > 0 else k < pk - 1e-6)]
    derriere = [n for n, k in GARES if (k <= pk + 1e-6 if sens > 0 else k >= pk - 1e-6)]
    prochain = (devant[0] if sens > 0 else devant[-1]) if devant else NOMS[-1 if sens > 0 else 0]
    nom, k = min(GARES, key=lambda g: abs(g[1] - pk))
    if abs(k - pk) < .8:
        return nom, prochain
    return f"Entre {derriere[-1] if sens > 0 else derriere[0]} et {prochain}", prochain


@st.cache_data(ttl=3600, show_spinner=False, max_entries=4)
def d_gtfs_jour(cle: str, jour_iso: str) -> pd.DataFrame:
    """Horaires GTFS d'une date de service : relus une fois par heure (ils changent rarement)."""
    return source().gtfs(date.fromisoformat(jour_iso))


RESEAU_DEMO = dict(gares=list(GARES), trace=list(TRACE), geo=True)


def appliquer_reseau(reseau: dict) -> None:
    """Remplace la ligne de démonstration (6 gares) par la ligne réelle : toutes les pages de trains
    (carte, schéma de ligne, serpent de charge) utilisent alors les gares de Ref_Gares."""
    global GARES, NOMS, PKS, TRACE, CUMUL
    GARES = [(g, k) for g, k in reseau["gares"]]
    NOMS, PKS = [g for g, _ in GARES], [k for _, k in GARES]
    if reseau.get("trace"):
        TRACE = [tuple(p) for p in reseau["trace"]]
    CUMUL = _cumul()


def charger_donnees() -> dict:
    """Trains en ligne. Mode réel : horaires théoriques GTFS (veille + jour, pour les trains après minuit),
    positions interpolées à l'instant présent, retard simulé ; mode démonstration : modèle de rotation."""
    maj = maintenant()
    if REEL:
        cle = PARAM.signature()
        jours = [maj.date() - timedelta(days=1), maj.date()]
        horaires_df = pd.concat([d_gtfs_jour(cle, j.isoformat()).assign(service_date=j) for j in jours],
                                ignore_index=True)
        reseau = construire_reseau(d_gares(cle), horaires_df)
        appliquer_reseau(reseau)
        d = circulations(horaires_df, maj, reseau, retard_max=PARAM.retard_simule_max)
        d.update(maj=maj, ts=maj.timestamp(), geo=reseau["geo"], nb_gtfs=horaires_df["trip_id"].nunique())
        return d
    ts, L = maj.timestamp(), PKS[-1]
    trains = []
    for k, (tid, couleur, ecart) in enumerate(TRAINS):
        phase = 5 + k * 2 * L / len(TRAINS)
        pk, sens = etat_train(phase, ts)
        position, prochain = decrire_position(pk, sens)
        d = min(abs(g - pk) for g in PKS)
        trains.append(dict(id=tid, couleur=couleur, phase=phase, pk=pk, sens=sens, position=position,
                           prochain=prochain, ecart=ecart, vitesse=int(min(110, 40 + 25 * d) // 5 * 5)))
    return dict(trains=trains, a_venir=[], departures=[], non_associes=[], maj=maj, ts=ts)


def prochain_depart(d: dict) -> tuple[datetime, str]:
    """Prochain départ théorique de Dakar, calculé à partir du GTFS en mode réel."""
    if REEL:
        futurs = [x for x in d.get("departures", []) if x["direction"] > 0 and x["datetime"] >= d["maj"]]
        candidats = [x for x in futurs if x.get("origine") == NOMS[0]] or futurs
        if candidats:
            x = min(candidats, key=lambda z: z["datetime"])
            num = re.findall(r"\d{2,}", x["id"])
            return x["datetime"], (num[-1] if num else x["id"][-8:])
        return d["maj"], "Aucun départ"
    L = PKS[-1]
    attente = []
    for t in d["trains"]:
        u = (_heures_sim(d["ts"]) * V_COMMERCIALE + t["phase"]) % (2 * L)
        attente.append(((2 * L - u) % (2 * L) / V_COMMERCIALE * 3600 / ACCELERATION, t["id"]))
    s, tid = min(attente)
    return d["maj"] + timedelta(seconds=s), tid


def frequence(departs=None, maj=None, sens=1) -> float:
    """Nombre de départs théoriques par heure et par sens (GTFS), sinon modèle démo."""
    if departs is not None and maj is not None:
        debut = maj - timedelta(hours=1)
        return float(sum(1 for x in departs if x["direction"] == sens and debut <= x["datetime"] <= maj))
    return len(TRAINS) * V_COMMERCIALE / (2 * PKS[-1])


def charge_reseau(maj: datetime, b: dict | None = None, d: dict | None = None) -> dict:
    """Charge estimée à partir des validations du dernier lot et de l'offre GTFS.
    En mode réel, les validations sont espacées de 30 min : on affiche le dernier lot reçu,
    jamais une prétendue mesure instantanée.
    """
    n = len(NOMS)
    if REEL:
        m60 = (b or {}).get("F", {}).get("m60", {})
        E = [float(m60.get(g, {}).get("E", 0)) for g in NOMS]
        S = [float(m60.get(g, {}).get("S", 0)) for g in NOMS]
    else:
        E = [flux_h(g, maj.date(), maj.hour, "e") for g in NOMS]
        S = [flux_h(g, maj.date(), maj.hour, "s") for g in NOMS]
    aller, retour = [0.0] * (n - 1), [0.0] * (n - 1)
    if sum(E) >= 1 and sum(S) >= 1:
        cible = [x * sum(E) / sum(S) for x in S]
        M = [[E[o] * cible[k] if o != k else 0.0 for k in range(n)] for o in range(n)]
        for _ in range(40):
            for o in range(n):
                r = sum(M[o]); M[o] = [x * E[o] / r if r else 0.0 for x in M[o]]
            for k in range(n):
                c = sum(M[o][k] for o in range(n))
                for o in range(n): M[o][k] = M[o][k] * cible[k] / c if c else 0.0
        for i in range(n - 1):
            aller[i] = sum(M[o][k] for o in range(i + 1) for k in range(i + 1, n))
            retour[i] = sum(M[o][k] for o in range(i + 1, n) for k in range(i + 1))
    departs = (d or {}).get("departures")
    # Aligner l'offre GTFS sur la même fenêtre temporelle que les validations reçues.
    reference = (b or {}).get("t_ref") or maj
    fa, fr = frequence(departs, reference, 1), frequence(departs, reference, -1)
    offre_a, offre_r = fa * CAPACITE_RAME, fr * CAPACITE_RAME
    return dict(E=E, S=S, aller=aller, retour=retour, freq=(fa + fr) / 2,
                pct_a=[v / offre_a * 100 if offre_a else 0 for v in aller],
                pct_r=[v / offre_r * 100 if offre_r else 0 for v in retour],
                freq_a=fa, freq_r=fr, reference=reference,
                fraicheur=(b or {}).get("der"), stale=(b or {}).get("en_retard", False))


def charge_train(t: dict, ch: dict) -> tuple[float, float]:
    """Voyageurs à bord estimés d'un train = flux du tronçon parcouru / fréquence."""
    i = min(int(pk_vers_idx(t["pk"])), len(GARES) - 2)
    freq_sens = (ch.get("freq_a", ch["freq"]) if t["sens"] > 0 else ch.get("freq_r", ch["freq"])) or 1
    voy = (ch["aller"] if t["sens"] > 0 else ch["retour"])[i] / freq_sens
    return voy, voy / CAPACITE_RAME * 100


# ═══════════════════════════════ Logique ═══════════════════════════════
def ligne(t): return "Dakar → AIBD" if t["sens"] > 0 else "AIBD → Dakar"
SEUIL_RETARD = 3  # minutes : en dessous, un train est compté « à l'heure » (tolérance usuelle de ponctualité)
def statut(t): return ("En retard", "retard") if t["ecart"] >= SEUIL_RETARD else ("À l'heure", "heure")
def ecart_txt(t): return f"+{t['ecart']} min" if t["ecart"] > 0 else f"{t['ecart']} min"


def pk_vers_idx(pk: float) -> float:
    """Point kilométrique → indice fractionnaire de gare (0 = Dakar … 5 = AIBD)."""
    for i in range(len(PKS) - 1):
        a, b = PKS[i], PKS[i + 1]
        if pk <= b or i == len(PKS) - 2:
            return i + max(0.0, min(1.0, (pk - a) / (b - a)))
    return 0.0


def etats_troncons(t) -> list[str]:
    """État de chaque tronçon gare i → i+1 pour un train : parcouru, en cours ou à venir."""
    q, n = NOMS.index(t["prochain"]), len(GARES) - 1
    if t["sens"] > 0:
        return ["fait" if i < q - 1 else "cours" if i == q - 1 else "avenir" for i in range(n)]
    return ["fait" if i > q else "cours" if i == q else "avenir" for i in range(n)]


def etat_gare(i: int, t) -> str:
    q = NOMS.index(t["prochain"])
    if i == q:
        return "prochain"
    return "fait" if (i < q if t["sens"] > 0 else i > q) else "avenir"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def filtrer(trains, ligne_choisie: str, recherche: str):
    res = [t for t in trains if ligne_choisie == LIGNES[0] or ligne(t) == ligne_choisie]
    q = _norm(recherche or "")
    if q:
        res = [t for t in res if q in _norm(" ".join([t["id"], ligne(t), t["position"], t["prochain"]]))]
    return res


# — Géométrie de la carte —
def _cumul():
    c = [0.0]
    for (a1, o1), (a2, o2) in zip(TRACE, TRACE[1:]):
        c.append(c[-1] + math.hypot(a2 - a1, (o2 - o1) * math.cos(math.radians(a1))))
    return c


CUMUL = _cumul()


def _dist(pk: float) -> float:
    return max(0.0, min(1.0, pk / PKS[-1])) * CUMUL[-1]


def point_pk(pk: float) -> list[float]:
    d = _dist(pk)
    for i in range(1, len(TRACE)):
        if d <= CUMUL[i] or i == len(TRACE) - 1:
            r = (d - CUMUL[i - 1]) / ((CUMUL[i] - CUMUL[i - 1]) or 1)
            (a1, o1), (a2, o2) = TRACE[i - 1], TRACE[i]
            return [round(a1 + (a2 - a1) * r, 6), round(o1 + (o2 - o1) * r, 6)]
    return list(TRACE[-1])


def sous_trace(pk_a: float, pk_b: float):
    da, db = _dist(pk_a), _dist(pk_b)
    milieu = [list(p) for p, c in zip(TRACE, CUMUL) if da < c < db]
    return [point_pk(pk_a), *milieu, point_pk(pk_b)]


# ═══════════════════════════════ Rendu ═══════════════════════════════
VERT, BLEU, ROUGE = "#17A97B", "#1E87FC", "#7D142F"
TRONCON_LIGNE = {"fait": VERT, "cours": BLEU, "avenir": "#B4C7DF"}
TRONCON_CARTE = {"fait": "#13A574", "cours": "#1A7FF5", "avenir": "#87A6CF"}
JOURS = ["Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim."]
MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]

P_TRAIN = ("M12 2c-4 0-8 .5-8 4v9.5C4 17.43 5.57 19 7.5 19L6 20.5v.5h2.23l2-2H14l2 2h2v-.5L16.5 19c1.93 0 "
           "3.5-1.57 3.5-3.5V6c0-3.5-3.58-4-8-4zM7.5 17c-.83 0-1.5-.67-1.5-1.5S6.67 14 7.5 14s1.5.67 1.5 1.5S8.33 "
           "17 7.5 17zm3.5-7H6V6h5v4zm2 0V6h5v4h-5zm3.5 7c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 "
           "1.5-.67 1.5-1.5 1.5z")
P_AVION = "M21 16v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"
P_REGLAGES = ("M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.49.49 "
              "0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.48.48 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l"
              "-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.48.48 0 0 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c"
              "-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5"
              ".38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-"
              ".94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-"
              "3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z")
P_GROUPE = ("M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 "
            "5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 "
            "0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z")


def _svg(inner: str, trait: float | None = None) -> str:
    if trait is None:
        return f'<svg viewBox="0 0 24 24" fill="currentColor">{inner}</svg>'
    return (f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="{trait}" '
            f'stroke-linecap="round" stroke-linejoin="round">{inner}</svg>')


ICO = {
    "train": _svg(f'<path d="{P_TRAIN}"/>'),
    "avion": f'<svg viewBox="0 0 24 24" fill="currentColor" style="transform:rotate(45deg)"><path d="{P_AVION}"/></svg>',
    "accueil": _svg('<path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/>'),
    "groupe": _svg(f'<path d="{P_GROUPE}"/>'),
    "reseau": _svg('<circle cx="12" cy="5.2" r="2.6"/><circle cx="5" cy="18.2" r="2.6"/><circle cx="19" cy="18.2" r="2.6"/>'
                   '<path d="M10.8 7.6 6.3 15.8M13.2 7.6l4.5 8.2M7.7 18.2h8.6"/>', 2.1),
    "reglages": _svg(f'<path d="{P_REGLAGES}"/>'),
    "reveil": _svg('<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2"/><path d="M5 3 2 6"/><path d="m22 6-3-3"/>'
                   '<path d="M6.38 18.7 4 21"/><path d="M17.64 18.67 20 21"/>', 2.2),
    "sirene": ('<svg viewBox="0 0 24 24"><path fill="currentColor" d="M5.8 18v-6.2a6.2 6.2 0 0 1 12.4 0V18z"/>'
               '<rect x="3.6" y="18.4" width="16.8" height="3.6" rx="1.4" fill="currentColor"/>'
               '<path d="M12 8.4v4.4" stroke="#fff" stroke-width="2.1" stroke-linecap="round"/>'
               '<circle cx="12" cy="15.5" r="1.2" fill="#fff"/></svg>'),
    "depart": _svg('<path d="M21 7.5V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3.5"/><path d="M16 2v4"/>'
                   '<path d="M8 2v4"/><path d="M3 10h5"/><path d="M17.5 17.5 16 16.3V14"/><circle cx="16" cy="16" r="6"/>', 2.1),
    "haut": _svg('<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>', 2.6),
    "bas": _svg('<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>', 2.6),
    "horloge": _svg('<circle cx="12" cy="12" r="9.5"/><path d="M12 6.5V12l3.5 2"/>', 2.2),
    "soleil": _svg('<circle cx="12" cy="12" r="4.2"/><path d="M12 2v2.2M12 19.8V22M4.93 4.93l1.55 1.55M17.52 17.52l1.55 1.55'
                   'M2 12h2.2M19.8 12H22M4.93 19.07l1.55-1.55M17.52 6.48l1.55-1.55"/>', 2),
    "chevron": _svg('<path d="m9 18 6-6-6-6"/>', 2.4),
    "tv": _svg('<rect x="2.5" y="4" width="19" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>', 2.1),
    "entree": _svg('<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><path d="m10 17 5-5-5-5"/><path d="M15 12H3"/>', 2.3),
    "sortie": _svg('<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>', 2.3),
    "trophee": _svg('<path d="M8 21h8M12 16.5V21M7 4h10v5a5 5 0 0 1-10 0z"/><path d="M17 5.5h3V7a3 3 0 0 1-3 3M7 5.5H4V7a3 3 0 0 0 3 3"/>', 2),
    "pourcent": _svg('<path d="M19 5 5 19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>', 2.4),
    "info": _svg('<path d="M12 17v-6M12 7.5h.01"/>', 2.8),
    "epingle": _svg('<path d="M12 21s7-6.2 7-11.5A7 7 0 0 0 5 9.5C5 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.6"/>', 2.2),
    "plus": _svg('<path d="M12 5v14M5 12h14"/>', 3),
    "courbe": _svg('<path d="m3 17 6-6 4 4 8-8"/><path d="M15 7h6v6"/>', 2.4),
    "barres": _svg('<path d="M5 20V12M10 20V8M15 20v-6M20 20V4"/>', 2.6),
    "fleches": _svg('<path d="M4 8h13l-4-4M20 16H7l4 4"/>', 2.4),
    "ampoule": _svg('<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.6.5 1 1.2 1 2V16h5.2v-.2c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z"/>', 2),
    "nuage": _svg('<path d="M12 2v2M4.93 4.93l1.41 1.41M20 12h2M19.07 4.93l-1.41 1.41"/><path d="M15.95 12.65A4 4 0 0 0 8.2 10.4"/>'
                   '<path d="M13 22H7a5 5 0 1 1 4.9-6H13a3 3 0 0 1 0 6Z"/>', 1.9),
}

CSS = POLICE_CSS + r"""

/* ── Habillage Streamlit ── */
#MainMenu, footer, header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"], [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"], [data-testid="InputInstructions"] { display: none !important; }
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] { background: #F4F8FD !important; }
.stApp, .stApp input, .stApp button, .stApp textarea, [data-baseweb="popover"] li,
.stApp [data-testid="stMarkdownContainer"], .stApp [data-testid="stMarkdownContainer"] *, .stApp button p,
.stApp [data-baseweb="select"] * { font-family: 'Plus Jakarta Sans', system-ui, sans-serif !important; }
.stApp [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
.block-container, [data-testid="stMainBlockContainer"] { padding: 0 16px 28px 130px !important; max-width: none !important; }
[data-testid="stVerticalBlock"] { gap: 10px; }
[data-testid="stHorizontalBlock"] { gap: 16px !important; }
.element-container:has(.tb-fixe), [data-testid="stElementContainer"]:has(.tb-fixe) { position: absolute !important; }
[data-testid="stMarkdownContainer"] .tb-scope { line-height: 1.4; }
iframe { display: block; }

/* ── Barre latérale ── */
.tb-sidebar { position: fixed; top: 0; left: 0; bottom: 0; width: 110px; z-index: 1000; display: flex; flex-direction: column;
  padding: 16px 0 28px; background: #7D142F; box-shadow: 2px 0 14px rgba(60, 8, 22, .18); overflow: hidden; }
.tb-logo { display: flex; justify-content: center; }
.tb-logo img { display: block; }
.tb-nav { display: flex; flex-direction: column; gap: 7px; margin-top: 17px; padding-left: 6px; }
.tb-nav span { white-space: nowrap; overflow-wrap: normal; word-break: normal; }
.tb-nav div { display: flex; align-items: center; gap: 10px; min-height: 46px; padding: 0 2px 0 10px; border-radius: 9px 0 0 9px;
  color: #FBEEF1; font-size: 9.5px; font-weight: 500; line-height: 1.5; letter-spacing: -.005em; cursor: pointer; transition: background .15s; }
.tb-nav div:hover { background: rgba(255, 255, 255, .06); }
.tb-nav div.actif { background: #A3264A; color: #fff; }
.tb-nav svg { flex: none; width: 18px; height: 18px; color: #F5DCE3; }
.tb-nav .actif svg { color: #fff; }
.tb-logo-bas { margin-top: auto; }

/* ── En-tête ── */
.tb-header { position: relative; display: flex; align-items: center; height: 103px; margin: 0 -16px 4px -20px; padding-left: 23px;
  background: linear-gradient(90deg, #F8FBFF 0%, #F3F8FE 55%, #EBF4FE 100%); border-bottom: 1px solid #E3EBF5;
  box-shadow: 0 8px 18px -14px rgba(16, 42, 90, .25); overflow: hidden; }
.tb-h1 { font-size: 26px; font-weight: 800; color: #0B1F55; letter-spacing: -.02em; line-height: 1.15; white-space: nowrap; }
.tb-sous { margin-top: 8px; font-size: 14px; font-weight: 500; color: #1B3266; white-space: nowrap; }
.tb-live { flex: none; display: flex; flex-direction: column; gap: 9px; margin-left: 72px; padding-top: 6px; }
.tb-live-l { display: flex; align-items: center; gap: 10px; font-size: 11.5px; font-weight: 600; color: #0B1F55; white-space: nowrap; }
.tb-live-d { white-space: nowrap; }
.tb-live-l i { width: 8px; height: 8px; border-radius: 50%; background: #12B76A; box-shadow: 0 0 0 3px rgba(18, 183, 106, .16); }
.tb-live-d { display: flex; align-items: center; gap: 6px; font-size: 10px; color: #3D5585; }
.tb-live-d svg { width: 11px; height: 11px; }
.tb-banniere { flex: 0 1 auto; align-self: flex-end; width: auto; height: 98px; max-width: 48%; min-width: 0; margin: 0 20px 0 auto; object-fit: contain; object-position: right bottom; }

/* ── Cartes communes ── */
.tb-card { background: #fff; border: 1px solid #EBF1F8; border-radius: 12px;
  box-shadow: 0 1px 2px rgba(16, 42, 90, .04), 0 6px 18px rgba(16, 42, 90, .04); }
.tb-ct { font-size: 14px; font-weight: 700; color: #0B1F55; letter-spacing: -.005em; }
.tb-ct span { font-size: 11px; font-weight: 400; color: #22396E; }

/* ── Indicateurs ── */
.tb-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.tb-kpi { display: flex; align-items: flex-start; gap: 16px; min-height: 110px; padding: 8px 12px 14px 17px; }
.tb-kpi-ic { flex: none; display: grid; place-items: center; width: 48px; height: 48px; border-radius: 50%; }
.tb-kpi-ic svg { width: 25px; height: 25px; }
.tb-kpi-t { min-width: 0; padding-top: 8px; }
.tb-kpi-l { font-size: 12.5px; font-weight: 500; color: #0B1F55; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tb-kpi-l em { font-style: normal; font-weight: 400; color: #3D5585; }
.tb-kpi-v { margin: 9px 0; font-size: 25px; font-weight: 800; color: #0B1F55; letter-spacing: -.01em; line-height: 1.1; white-space: nowrap; }
.tb-kpi-d { display: flex; align-items: center; gap: 7px; font-size: 11.5px; color: #22396E; white-space: nowrap; }
.tb-kpi-d .dv { display: flex; align-items: center; gap: 7px; font-weight: 700; }
.tb-kpi-d svg { width: 13px; height: 13px; }

/* ── Ligne Dakar – AIBD ── */
.tb-ligne { padding: 9px 14px 15px; }
.tb-ligne-h { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.tb-ligne-h small { font-size: 10.5px; color: #5B6E93; white-space: nowrap; }
.tb-ligne-h small b { color: #0B1F55; font-weight: 700; }
.tb-rail { position: relative; display: flex; justify-content: space-between; margin-top: 18px; }
.tb-rail-bar { position: absolute; left: 32px; right: 32px; top: 10px; height: 3px; border-radius: 3px; }
.tb-stop { position: relative; z-index: 1; display: flex; flex-direction: column; align-items: center; width: 64px; text-align: center; }
.tb-stop .pt { display: grid; place-items: center; height: 23px; }
.tb-stop .pt i { display: block; width: 13px; height: 13px; border-radius: 50%; background: #fff; border: 2px solid #A9BDD8; box-sizing: border-box; }
.tb-stop.fait .pt i { border-color: #17A97B; }
.tb-stop.prochain .pt i { width: 13px; height: 13px; border: 0; background: #7D142F; box-shadow: 0 0 0 3px rgba(125, 20, 47, .25); }
.tb-stop.prochain.heure .pt i { background: #12B76A; box-shadow: 0 0 0 3px rgba(18, 183, 106, .25); }
.tb-stop .pt b { display: grid; place-items: center; width: 23px; height: 23px; border-radius: 50%; background: #0D3B8C; color: #fff; }
.tb-stop .pt b svg { width: 13px; height: 13px; }
.tb-stop .nm { margin-top: 4px; font-size: 11.5px; color: #0B1F55; white-space: nowrap; }
.tb-stop.term .nm, .tb-stop.prochain .nm { font-weight: 700; }
.tb-stop .km { margin-top: 4px; font-size: 9.5px; color: #4A5F86; white-space: nowrap; }
.tb-rail.dense .tb-stop { width: 34px; }
.tb-rail.dense .tb-rail-bar { left: 17px; right: 17px; }
.tb-rail.dense .tb-stop.decale .nm { margin-top: 30px; }
.tb-rail.dense .tb-stop .nm { font-size: 11px; }

/* ── Tableau ── */
.st-key-tableau { padding: 12px 10px 8px; gap: 2px !important; background: #fff; border: 1px solid #EBF1F8; border-radius: 12px;
  box-shadow: 0 1px 2px rgba(16, 42, 90, .04), 0 6px 18px rgba(16, 42, 90, .04); }
.tb-tab-t { padding: 0 4px; }
table.tb-tab { width: 100%; margin: 12px 0 0 !important; border-collapse: collapse; table-layout: fixed; border: 0 !important; }
.tb-tab th { padding: 9px 8px !important; border: 0 !important; background: #F4F8FD; text-align: left; font-size: 11px; font-weight: 600; color: #0B2A73; }
.tb-tab th:first-child { border-radius: 8px 0 0 8px; padding-left: 10px !important; }
.tb-tab th:last-child { border-radius: 0 8px 8px 0; }
.tb-tab td { height: 34px; padding: 4px 8px !important; border: 0 !important; border-bottom: 1px solid #EAF0F7 !important;
  font-size: 11px; color: #0B1F55; line-height: 1.35; }
.tb-tab td:nth-child(5), .tb-tab td:nth-child(6), .tb-tab td:nth-child(7) { white-space: nowrap; }
.tb-tab td:first-child { padding-left: 10px !important; }
.tb-tab tr:hover td { background: #F9FBFE; }
.tb-tab .lg { font-weight: 600; }
.tb-tag { display: inline-grid; place-items: center; width: 40px; height: 22px; border-radius: 8px; color: #fff; font-size: 11.5px; font-weight: 700; }
.tb-ec { font-weight: 600; }
.tb-ec.retard { color: #7D142F; }
.tb-st { display: inline-flex; align-items: center; gap: 6px; height: 21px; padding: 0 13px 0 9px; border-radius: 11px; font-size: 10.5px; font-weight: 500; }
.tb-st svg { width: 11px; height: 11px; }
.tb-st.retard { background: #F6E5EA; color: #7D142F; }
.tb-st.heure { background: #E1F6EC; color: #0A9A62; }
.st-key-voir_tous { align-self: flex-end; width: auto !important; }
.st-key-voir_tous button { min-height: 0 !important; padding: 4px 6px !important; border: 0 !important; background: transparent !important;
  box-shadow: none !important; color: #0B63F3 !important; }
.st-key-voir_tous button p { font-size: 11.5px !important; font-weight: 500; }
.st-key-voir_tous button:hover { color: #084CC0 !important; text-decoration: underline; }

/* ── Panneau « Liste des trains » ── */
.st-key-panneau { padding: 14px 11px 14px 14px; gap: 12px !important; background: #fff; border: 1px solid #E8EFF8; border-radius: 14px;
  box-shadow: 0 1px 2px rgba(16, 42, 90, .04), 0 8px 24px rgba(16, 42, 90, .05); }
.tb-ptitle { padding-left: 4px; font-size: 18px; font-weight: 800; color: #000A3D; letter-spacing: -.01em; white-space: nowrap; }
:is(.st-key-filtre_ligne, .st-key-charge_ligne, .st-key-charge_train, .st-key-zone) [data-baseweb="select"] > div { min-height: 34px !important; border: 1px solid #DCE6F2 !important; border-radius: 8px !important;
  background: #fff !important; box-shadow: 0 1px 3px rgba(16, 42, 90, .05); }
:is(.st-key-filtre_ligne, .st-key-charge_ligne, .st-key-charge_train, .st-key-zone) [data-baseweb="select"] div { font-size: 11.5px !important; color: #0B1F55 !important; }
:is(.st-key-filtre_ligne, .st-key-charge_ligne, .st-key-charge_train, .st-key-zone) svg { color: #0B1F55 !important; }
.st-key-recherche [data-baseweb="input"] { border: 1px solid #D9E6F3 !important; border-radius: 9px !important; background: #F1F6FC !important; }
.st-key-recherche [data-baseweb="base-input"] { background: transparent !important; }
.st-key-recherche input { height: 36px; padding-left: 36px !important; font-size: 12px !important; color: #0B1F55 !important;
  background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%233F5F8A' stroke-width='2.3' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='m20 20-3.6-3.6'/%3E%3C/svg%3E") no-repeat 12px 50% / 15px !important; }
.st-key-recherche input::placeholder { color: #3F5F8A !important; opacity: 1; }
[class*="st-key-carte_"] { position: relative; gap: 0 !important; }
[class*="st-key-choix_"] { position: absolute !important; inset: 0; z-index: 5; width: auto !important; margin: 0 !important; }
[class*="st-key-choix_"] div, [class*="st-key-choix_"] button { width: 100% !important; height: 100% !important; }
[class*="st-key-choix_"] button { min-height: 0 !important; opacity: 0; cursor: pointer; }
.tb-tc { padding: 14px 14px 15px 11px; background: #fff; border: 1px solid #E3ECF6; border-radius: 11px;
  box-shadow: 0 1px 2px rgba(16, 42, 90, .03), 0 4px 12px rgba(16, 42, 90, .04); transition: border-color .15s, box-shadow .15s; }
[class*="st-key-carte_"]:hover .tb-tc { border-color: #C3D8F4; box-shadow: 0 6px 18px rgba(16, 42, 90, .08); }
[class*="st-key-carte_"]:focus-within .tb-tc { outline: 2px solid #8AB6F7; outline-offset: 1px; }
.tb-tc.sel { border-color: #8AB6F7; box-shadow: 0 0 0 3px rgba(27, 110, 243, .10); }
.tb-tc-h { display: flex; align-items: center; height: 30px; }
.tb-tc-b { flex: none; display: grid; place-items: center; width: 40px; height: 30px; border-radius: 8px; color: #fff; font-size: 13.5px; font-weight: 700;
  box-shadow: 0 2px 6px rgba(16, 42, 90, .12); }
.tb-tc-r { width: 104px; margin: 0 14px 0 18px; font-size: 12.5px; font-weight: 500; color: #0B1F55; white-space: nowrap; }
.tb-pill { flex: none; height: 19px; padding: 0 13px; border-radius: 10px; font-size: 10.5px; font-weight: 600; line-height: 19px; white-space: nowrap; }
.tb-pill.retard { background: #F6E5EA; color: #7D142F; }
.tb-pill.heure { background: #E1F6EC; color: #069A60; }
.tb-tc-c { display: grid; place-items: center; margin-left: auto; color: #0E67FD; }
.tb-tc-c svg { width: 15px; height: 15px; }
.tb-mini { position: relative; height: 12px; margin: 13px 35px 0 9px; }
.tb-mini .ln { position: absolute; left: 0; right: 0; top: 5px; height: 2px; border-radius: 2px; background: #DBE7F5; }
.tb-mini .f { position: absolute; top: 4.5px; height: 3px; border-radius: 3px; }
.tb-mini .d { position: absolute; top: 3px; width: 6px; height: 6px; margin-left: -3px; border-radius: 50%; background: #B8CBE2; }
.tb-mini .d.fait { background: #1D5FC4; }
.tb-mini .d.o { top: 2px; width: 8px; height: 8px; margin-left: -4px; background: #fff; border: 2px solid #17A97B; box-sizing: border-box; }
.tb-mini .m { position: absolute; top: 1px; width: 16px; height: 10px; margin-left: -8px; border-radius: 5px; background: #17A97B;
  border: 1.5px solid #fff; box-shadow: 0 1px 3px rgba(16, 42, 90, .3); box-sizing: border-box; }
.tb-tc-f { display: flex; align-items: center; gap: 9px; margin-top: 17px; padding-left: 5px; font-size: 10.5px; color: #1B3266; white-space: nowrap; overflow: hidden; }
.tb-tc-f .lab { margin-right: 9px; color: #22396E; }
.tb-tc-f i { font-style: normal; color: #C4D1E3; }
.tb-tc-f .retard { color: #7D142F; }
.tb-xl span { overflow-wrap: normal; word-break: normal; hyphens: none; }
@media (max-width: 1100px) { .tb-xl span { font-size: 9.5px; } }
@media (max-width: 1000px) { .tb-xl span { font-size: 9px; } }
.tb-vide { padding: 28px 8px; text-align: center; font-size: 12px; color: #5B6E93; }

/* ── Navigation latérale (boutons Streamlit superposés) ── */
.st-key-sidebar { position: fixed !important; top: 0; left: 0; bottom: 0; z-index: 1000; width: 110px !important; height: 100vh !important;
  padding: 16px 0 20px; gap: 7px !important; flex-wrap: nowrap !important; background: #7D142F; box-shadow: 2px 0 14px rgba(60, 8, 22, .18); overflow: hidden; }
.st-key-sidebar [data-testid="stElementContainer"]:has(.tb-logo-haut) { margin: 8px 0 14px; }
.st-key-sidebar [data-testid="stElementContainer"]:has(.tb-bas) { margin-top: auto; }
[class*="st-key-nav_"] { position: relative; width: auto !important; margin-left: 6px; gap: 0 !important; }
[class*="st-key-navbtn_"] { position: absolute !important; inset: 0; z-index: 5; width: auto !important; margin: 0 !important; }
[class*="st-key-navbtn_"] div, [class*="st-key-navbtn_"] button { width: 100% !important; height: 100% !important; }
[class*="st-key-navbtn_"] button { min-height: 0 !important; opacity: 0; cursor: pointer; }
.tb-navi { display: flex; align-items: center; gap: 10px; min-height: 46px; padding: 0 2px 0 10px; border-radius: 9px 0 0 9px;
  color: #FBEEF1; font-size: 9.5px; font-weight: 500; line-height: 1.5; transition: background .15s; }
.tb-navi span { white-space: nowrap; }
[class*="st-key-nav_"]:hover .tb-navi { background: rgba(255, 255, 255, .08); }
.tb-navi.actif, [class*="st-key-nav_"]:hover .tb-navi.actif { background: #A3264A; color: #fff; }
.tb-navi svg { flex: none; width: 18px; height: 18px; color: #F5DCE3; }
.tb-navi.actif svg { color: #fff; }
.tb-gtfs { display: flex; align-items: flex-start; gap: 7px; padding: 0 0 14px 14px; font-size: 9px; line-height: 1.45; color: #FBEEF1; }
.tb-gtfs i { flex: none; width: 7px; height: 7px; margin-top: 3px; border-radius: 50%; background: #12D483; box-shadow: 0 0 0 3px rgba(18, 212, 131, .2); }

/* ── Pages Fréquentation & Serpent de charge ── */
.tb-g { display: grid; gap: 10px; }
.tb-g4 { grid-template-columns: repeat(3, minmax(0, 1fr)) minmax(0, 1.5fr); }
.tb-g3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.tb-g2 { grid-template-columns: minmax(0, 1.75fr) minmax(0, 1fr); }
.tb-kpi-d { flex-wrap: wrap; row-gap: 2px; }
.tb-kpi-t { flex: 1 1 auto; }
.tb-top-b > div { flex: 1; min-width: 0; }
.tb-top-l { white-space: nowrap; line-height: 1.6; }
:is(.st-key-aff_mode, .st-key-aff_periode, .st-key-vue_charge) button { white-space: nowrap; flex: none !important; width: auto !important; }
:is(.st-key-aff_mode, .st-key-aff_periode, .st-key-vue_charge) button p { white-space: nowrap; overflow: visible !important; text-overflow: clip !important; }
.tb-kpi-d .sm { font-size: 10px; color: #4A5F86; }
.tb-pad { padding: 12px 16px 14px; }
.tb-ch { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.tb-leg { display: flex; align-items: center; gap: 8px; }
.tb-leg span { display: flex; align-items: center; gap: 7px; padding: 4px 11px; border-radius: 8px; background: #F4F8FD; font-size: 10.5px; color: #0B1F55; }
.tb-leg i { width: 8px; height: 8px; border-radius: 50%; }
.tb-top { padding: 11px 14px 12px; }
.tb-top-h { display: flex; align-items: center; gap: 8px; font-size: 12.5px; font-weight: 700; color: #0B1F55; }
.tb-top-h svg { width: 17px; height: 17px; color: #E9A800; }
.tb-top-b { display: flex; align-items: center; gap: 14px; margin-top: 10px; flex-wrap: wrap; }
.tb-top-b img { width: 84px; height: 66px; border-radius: 7px; object-fit: cover; }
.tb-top-n { font-size: 15px; font-weight: 800; color: #0B1F55; }
.tb-top-l { margin-top: 2px; font-size: 11.5px; color: #22396E; }
.tb-top-l b { font-size: 14px; font-weight: 700; color: #0B1F55; }
.tb-badge { align-self: flex-start; padding: 3px 10px; border-radius: 9px; background: #E1F6EC; color: #0A9A62; font-size: 10px; font-weight: 600; white-space: nowrap; }
.tb-rang { display: inline-grid; place-items: center; width: 21px; height: 21px; border-radius: 50%; color: #fff; font-size: 11px; font-weight: 800; }
.tb-barre { position: relative; display: block; height: 7px; border-radius: 4px; background: #E6EEF7; overflow: hidden; }
.tb-barre > span { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 4px; }
.tb-donut { position: relative; width: 150px; height: 150px; margin: 14px auto 12px; }
.tb-donut svg { position: absolute; inset: 0; width: 100%; height: 100%; }
.tb-donut div { position: absolute; inset: 0; display: grid; place-content: center; text-align: center; }
.tb-donut b { font-size: 20px; font-weight: 800; color: #0B1F55; }
.tb-donut small { font-size: 10.5px; color: #4A5F86; }
.tb-dl { display: grid; gap: 7px; padding: 0 8px; }
.tb-dl div { display: flex; align-items: center; gap: 9px; font-size: 11.5px; color: #0B1F55; }
.tb-dl i { width: 9px; height: 9px; border-radius: 50%; }
.tb-dl b { margin-left: auto; font-weight: 700; }
.tb-plot { position: relative; height: 150px; margin: 18px 4px 0 40px; border-bottom: 1px solid #D5E0EE; display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); }
.tb-gl { position: absolute; left: 0; right: 0; border-top: 1px dashed #E6EDF6; }
.tb-gl span { position: absolute; right: calc(100% + 8px); top: -7px; font-size: 9.5px; color: #4A5F86; white-space: nowrap; }
.tb-grp { position: relative; display: flex; align-items: flex-end; justify-content: center; gap: 4px; }
.tb-grp i { width: min(24px, 32%); border-radius: 5px 5px 0 0; }
.tb-xl { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); margin: 6px 4px 0 40px; text-align: center; font-size: 11px; color: #0B1F55; }
.st-key-bloc_affluence, .st-key-bloc_ctrl, .st-key-bloc_info { gap: 10px !important; background: #fff; border: 1px solid #EBF1F8; border-radius: 12px;
  box-shadow: 0 1px 2px rgba(16, 42, 90, .04), 0 6px 18px rgba(16, 42, 90, .04); }
.st-key-bloc_affluence { padding: 12px 16px 14px; }
.st-key-bloc_ctrl { padding: 8px 12px; }
.st-key-bloc_info { padding: 10px 16px; }
.tb-heat { display: grid; grid-template-columns: 34px repeat(19, minmax(0, 1fr)); gap: 3px; align-items: center; }
.tb-heat i { display: block; height: 16px; border-radius: 3px; }
.tb-heat span { font-size: 10px; color: #22396E; }
.tb-heat .hx { text-align: center; font-size: 9.5px; color: #4A5F86; white-space: nowrap; }
.tb-hl { display: flex; gap: 18px; flex-wrap: wrap; margin-top: 12px; font-size: 10.5px; color: #22396E; }
.tb-hl span { display: flex; align-items: center; gap: 7px; }
.tb-hl i { width: 9px; height: 9px; border-radius: 50%; }
.tb-pv-m { display: flex; justify-content: space-between; gap: 10px; margin-top: 10px; }
.tb-pv-m > div { display: flex; align-items: center; gap: 10px; padding: 6px 12px; border-radius: 9px; }
.tb-pv-m .b { background: #F7FAFE; border: 1px solid #E8EFF8; }
.tb-pv-m svg { width: 26px; height: 26px; }
.tb-pv-m small { display: block; font-size: 9.5px; color: #4A5F86; }
.tb-pv-m b { display: block; font-size: 14px; font-weight: 800; color: #0B1F55; }
.tb-pv-l { display: flex; gap: 18px; justify-content: center; margin: 10px 0 2px; font-size: 10px; color: #22396E; }
.tb-pv-l span { display: flex; align-items: center; gap: 7px; }
.tb-pv-l i { width: 18px; border-top: 2px solid #1B6EF3; }
.tb-pv-l i.p { border-top-style: dashed; }
.tb-svg { display: block; width: 100%; height: auto; }
.tb-note { display: flex; align-items: center; gap: 9px; margin-top: 8px; padding: 8px 11px; border-radius: 8px; background: #F4F8FD; font-size: 10px; color: #22396E; line-height: 1.45; }
.tb-note b, .tb-info b { flex: none; display: grid; place-items: center; width: 18px; height: 18px; border-radius: 50%; background: #1B6EF3; color: #fff; }
.tb-note svg { width: 12px; height: 12px; }
.tb-serp { display: grid; grid-template-columns: 96px repeat(5, minmax(0, 1fr)); column-gap: 8px; row-gap: 6px; align-items: center; margin-top: 14px; }
.tb-serp .noms { grid-column: 2 / -1; position: relative; height: 18px; margin: 0 6px; }
.tb-serp .noms.dense { height: 32px; }
.tb-serp .noms span { position: absolute; top: 0; transform: translateX(-50%); font-size: 10.5px; font-weight: 600; color: #0B1F55; white-space: nowrap; }
.tb-serp .noms span:first-child { transform: none; } .tb-serp .noms span:last-child { transform: translateX(-100%); }
.tb-serp .noms span.bas { top: 15px; }
.tb-serp .s { font-size: 10.5px; font-weight: 600; color: #22396E; white-space: nowrap; }
.tb-serp .c { position: relative; padding-top: 2px; }
.tb-serp .c i { display: block; height: 9px; border-radius: 5px; }
.tb-serp .c small { display: block; margin-top: 4px; font-size: 10px; color: #22396E; }
.tb-cg { display: flex; align-items: center; gap: 9px; }
.tb-cg .tb-barre { flex: none; width: 74px; }
.tb-cg b { font-weight: 600; }
.tb-circ { font-weight: 600; color: #7D142F; }
.tb-cgr { display: grid; grid-template-columns: 78px minmax(0, 1fr) 38px; gap: 12px; align-items: center; height: 31px; font-size: 11px; color: #0B1F55; }
.tb-cgr b { text-align: right; font-weight: 600; }
.tb-cgr .tb-barre { height: 8px; }
.tb-info { display: flex; align-items: center; gap: 14px; font-size: 11px; color: #22396E; line-height: 1.5; }
.tb-info b { width: 30px; height: 30px; }
.tb-info svg { width: 17px; height: 17px; }
:is(.st-key-aff_mode, .st-key-aff_periode, .st-key-vue_charge) [data-testid="stButtonGroup"] { gap: 4px; }
.st-key-aff_mode { display: flex; justify-content: flex-start; }
:is(.st-key-aff_mode, .st-key-aff_periode, .st-key-vue_charge) button { min-height: 28px !important; padding: 2px 14px !important; border: 0 !important; border-radius: 8px !important;
  background: #F4F8FD !important; color: #22396E !important; box-shadow: none !important; }
:is(.st-key-aff_mode, .st-key-aff_periode, .st-key-vue_charge) button p { font-size: 11.5px !important; font-weight: 600; }
.st-key-aff_mode [data-testid="stBaseButton-segmented_controlActive"] { background: #E1F6EC !important; color: #0A9A62 !important; }
.st-key-vue_charge [data-testid="stBaseButton-segmented_controlActive"] { background: #1B6EF3 !important; color: #fff !important; }
:is(.st-key-voir_carte, .st-key-ouvrir_tv) { display: flex; justify-content: flex-end; }
:is(.st-key-voir_carte, .st-key-ouvrir_tv) button { min-height: 32px !important; border: 1px solid #DCE6F2 !important; border-radius: 8px !important; background: #fff !important; color: #0B1F55 !important; }
:is(.st-key-voir_carte, .st-key-ouvrir_tv) button p { font-size: 11px !important; font-weight: 600; }
:is(.st-key-voir_carte, .st-key-ouvrir_tv) button:hover { border-color: #8AB6F7 !important; color: #0B63F3 !important; }
@media (max-width: 1200px) { .tb-g4 { grid-template-columns: repeat(2, minmax(0, 1fr)); } .tb-g2, .tb-g3 { grid-template-columns: minmax(0, 1fr); } }

/* ── Fréquentation : gare à l'instant t, prévision ── */
.tb-g4h { grid-template-columns: minmax(0, 1.7fr) repeat(3, minmax(0, 1fr)); }
.tb-g2e { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.tb-t5 { display: grid; grid-template-columns: 40px minmax(92px, 132px) minmax(0, 1fr) minmax(0, 1fr); gap: 18px; align-items: center; min-height: 36px;
  padding: 0 10px; border-bottom: 1px solid #EAF0F7; font-size: 11px; color: #0B1F55; }
.tb-t5:last-child { border-bottom: 0; }
.tb-t5 .nm { font-weight: 600; }
.tb-t5-h { min-height: 32px; margin-top: 12px; border: 0; border-radius: 8px; background: #F4F8FD; font-weight: 600; color: #0B2A73; }
.tb-t5-h em { font-style: normal; font-weight: 400; color: #4A5F86; }
.tb-t5-b { display: flex; align-items: center; gap: 10px; }
.tb-t5-b .tb-barre { flex: 1; height: 9px; }
.tb-t5-b b { flex: none; width: 46px; text-align: right; font-weight: 700; }
.tb-t5-b small { flex: none; width: 30px; font-size: 10px; color: #4A5F86; }
.tb-t5-g { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 28px; }
.tb-t5-col .tb-t5 { grid-template-columns: 34px minmax(80px, 128px) minmax(0, 1fr); gap: 14px; }
.tb-t5-h i { display: inline-block; width: 8px; height: 8px; margin-right: 6px; border-radius: 50%; }
@media (max-width: 900px) { .tb-t5-g { grid-template-columns: minmax(0, 1fr); } }
.tb-ctrl-t { font-size: 10.5px; color: #4A5F86; text-align: right; }
.tb-hero { display: flex; align-items: center; gap: 16px; padding: 14px 16px; border-radius: 12px; background: #7D142F; color: #fff;
  box-shadow: 0 8px 22px rgba(125, 20, 47, .22); }
.tb-hero img, .tb-hero .ph { flex: none; width: 96px; height: 76px; border-radius: 8px; object-fit: cover; }
.tb-hero .ph { display: grid; place-items: center; background: rgba(255, 255, 255, .12); }
.tb-hero .ph svg { width: 34px; height: 34px; color: #fff; }
.tb-hero > div { flex: 1; min-width: 0; }
.tb-hero-k { display: flex; align-items: center; gap: 8px; font-size: 10px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: #F5DCE3; }
.tb-hero-k i { width: 7px; height: 7px; border-radius: 50%; background: #12D483; box-shadow: 0 0 0 3px rgba(18, 212, 131, .25); }
.tb-hero-n { margin-top: 4px; font-size: 24px; font-weight: 800; letter-spacing: -.01em; line-height: 1.15; }
.tb-hero-l { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 14px; margin-top: 4px; font-size: 11px; line-height: 1.35; color: #FBEEF1; }
.tb-hero-l span { flex: none; white-space: nowrap; }
:is(.st-key-filtre_ligne, .st-key-charge_ligne, .st-key-charge_train, .st-key-zone) [data-baseweb="select"] > div > div:first-child { flex: 1 1 auto; min-width: 0; }
:is(.st-key-filtre_ligne, .st-key-charge_ligne, .st-key-charge_train, .st-key-zone) [data-baseweb="select"] > div > div:first-child > div { max-width: none !important; overflow: visible !important; text-overflow: clip !important; white-space: nowrap; }
.tb-hero-l b { font-size: 16px; font-weight: 800; color: #fff; }
.tb-hero-s { margin-top: 6px; font-size: 10.5px; color: #F5DCE3; }
/* Refonte gare la plus affluente : deux classements distincts */
.tb-hero-panel { min-width:0; height:100%; padding:15px; border-radius:14px; color:#fff; background:linear-gradient(145deg,#7D142F,#941B3C 58%,#681128); box-shadow:0 8px 22px rgba(125,20,47,.18); }
.tb-hero-title { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:12px; }
.tb-hero-title b { display:block; font-size:13px; font-weight:800; letter-spacing:.04em; }
.tb-hero-title span { display:block; margin-top:3px; font-size:10px; color:#F5DCE3; }
.tb-hero-title i { font-size:9px; font-style:normal; color:#F5DCE3; letter-spacing:.08em; }
.tb-hero-cards { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
.tb-hero-card { min-width:0; padding:10px; border-radius:11px; background:#fff; color:#0B1F55; box-shadow:0 3px 12px rgba(30,10,20,.12); }
.tb-hero-card-h { display:flex; justify-content:space-between; align-items:center; gap:5px; margin-bottom:8px; }
.tb-hero-pill { padding:4px 7px; border-radius:7px; font-size:9px; font-weight:800; color:#fff; white-space:nowrap; }
.tb-hero-pill.entree { background:#168C68; } .tb-hero-pill.sortie { background:#287BC1; }
.tb-hero-part { font-size:14px; font-weight:800; white-space:nowrap; }
.tb-hero-part small { font-size:8px; font-weight:600; color:#687894; }
.tb-hero-card img,.tb-hero-ph { display:block; width:100%; height:78px; border-radius:7px; object-fit:cover; background:#EEF3F8; }
.tb-hero-ph { display:grid; place-items:center; color:#7D142F; }
.tb-hero-ph svg { width:28px; height:28px; }
.tb-hero-name { margin-top:8px; font-size:18px; font-weight:800; color:#7D142F; }
.tb-hero-value { margin-top:1px; font-size:27px; font-weight:800; line-height:1.15; }
.tb-hero-value small { display:block; font-size:10px; font-weight:600; color:#536783; }
.tb-hero-foot { margin-top:8px; padding-top:7px; border-top:1px solid #E8EDF4; font-size:9px; color:#65758E; }
.tb-hero-note { margin-top:10px; font-size:9px; color:#F5DCE3; }
@media (max-width:700px) { .tb-hero-cards { grid-template-columns:1fr; } }
.tb-fc { padding: 14px 16px; border: 1.5px solid #BBD3FA; background: linear-gradient(180deg, #F3F8FF 0, #fff 110px); }
.tb-fc-h { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.tb-fc-h .tb-pv-l { margin: 0 0 0 auto; }
.tb-fc-tag { padding: 3px 10px; border-radius: 9px; background: #1B6EF3; color: #fff; font-size: 10px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }
.tb-fc-b { display: grid; grid-template-columns: minmax(0, 1fr) 230px; gap: 18px; align-items: center; margin-top: 10px; }
.tb-fc-st { display: grid; gap: 9px; }
.tb-fc-st > div { padding: 9px 12px; border-radius: 9px; background: #fff; border: 1px solid #E3ECF7; }
.tb-fc-st small { display: block; font-size: 10px; color: #4A5F86; }
.tb-fc-st b { display: block; margin: 2px 0 1px; font-size: 18px; font-weight: 800; color: #0B1F55; }
.tb-fc-st b em { font-style: normal; font-size: 10.5px; font-weight: 500; color: #4A5F86; }
.tb-fc-st span { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; font-size: 10.5px; color: #22396E; }
.tb-fc-st .dv { display: inline-flex; align-items: center; gap: 3px; font-weight: 700; }
.tb-fc-st .dv svg { width: 11px; height: 11px; }
.tb-pv-l i.r { border-top-color: #0B1F55; }
.tb-pv-l i.z { width: 14px; height: 8px; border: 0; border-radius: 2px; background: rgba(27, 110, 243, .2); }
.tb-md { display: grid; grid-template-columns: 74px minmax(0, 1fr) minmax(0, 1fr); gap: 12px; align-items: center; min-height: 31px; font-size: 11px; color: #0B1F55; }
.tb-md-t { min-height: 0; margin: 8px 0 2px; font-size: 10px; font-weight: 600; color: #0B2A73; }
.tb-md-c { display: flex; align-items: center; gap: 8px; }
.tb-md-c .tb-barre { flex: 1; height: 8px; }
.tb-md-c b { flex: none; width: 40px; text-align: right; font-weight: 600; }
@media (max-width: 1200px) {
  .tb-g4h { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .tb-g4h > .tb-hero { grid-column: 1 / -1; }
  .tb-g2e, .tb-fc-b { grid-template-columns: minmax(0, 1fr); }
}

/* ── Fréquentation : cartes, JOJ, prévision ── */
.tb-kpi-2 { display: inline-flex; gap: 12px; }
.tb-kpi-2 > span { display: inline-flex; align-items: baseline; gap: 4px; }
.tb-kpi-2 small { font-size: 10.5px; font-weight: 500; color: #4A5F86; letter-spacing: 0; }
.tb-g2j { grid-template-columns: minmax(0, 1fr) minmax(0, 1.32fr); }
.tb-joj { padding: 12px 16px; }
.tb-joj-h { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.tb-joj-tag { padding: 3px 10px; border-radius: 9px; background: #7D142F; color: #fff; font-size: 10px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; white-space: nowrap; }
.tb-joj-d { margin-left: auto; font-size: 10.5px; color: #4A5F86; white-space: nowrap; }
.tb-joj-b { display: grid; grid-template-columns: 178px minmax(0, 1fr); gap: 14px; align-items: center; margin-top: 8px; }
.tb-joj-k small { display: block; font-size: 10px; color: #4A5F86; }
.tb-joj-k > b { display: block; margin: 3px 0 9px; font-size: 24px; font-weight: 800; line-height: 1.1; color: #7D142F; letter-spacing: -.01em; }
.tb-joj-k > b em { font-style: normal; font-size: 11px; font-weight: 500; color: #4A5F86; }
.tb-joj-c { display: grid; gap: 6px; }
.tb-joj-c > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 10px; border-radius: 8px; background: #F4F8FD; font-size: 10.5px; color: #22396E; }
.tb-joj-c .dv { display: inline-flex; align-items: center; gap: 3px; font-size: 12.5px; font-weight: 800; white-space: nowrap; }
.tb-joj-c .dv svg { width: 11px; height: 11px; }
.tb-joj-k .ec { margin-top: 8px; }
.tb-joj-l { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-top: 6px; font-size: 10px; color: #22396E; }
.tb-joj-l span { display: flex; align-items: center; gap: 6px; }
.tb-joj-l i { width: 16px; border-top: 3px solid; border-radius: 2px; }
.tb-joj-l em { font-style: normal; color: #4A5F86; }
.tb-fc-st .pic { padding: 12px 14px; border: 0; background: #7D142F; box-shadow: 0 10px 24px rgba(125, 20, 47, .3); }
.tb-fc-st .pic small { font-size: 11px; font-weight: 800; letter-spacing: .02em; color: #F5DCE3; }
.tb-fc-st .pic .dv { color: #fff !important; }
.tb-fc-st .pic b { font-size: 27px; color: #fff; }
.tb-fc-st .pic b em { color: #F5DCE3; }
.tb-fc-st .pic span { font-size: 12px; font-weight: 700; color: #fff; }
.tb-fc-tag.joj { background: #7D142F; }
.tb-pv-l b.pk { width: 10px; height: 10px; border-radius: 50%; background: #7D142F; }
.tb-heat i.ferme, .tb-hl i.ferme { background: repeating-linear-gradient(135deg, #E3E9F2 0 2px, #F7F9FC 2px 5px); }
.tb-hl i.ferme { border-radius: 2px; }
@media (max-width: 1200px) { .tb-g2j { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 760px) { .tb-joj-b { grid-template-columns: minmax(0, 1fr); } }

/* ── Gares les plus affluentes (dernier lot) ── */
.tb-aff { position: relative; overflow: hidden; display: flex; flex-direction: column; gap: 12px; padding: 16px; border-radius: 14px;
  color: #fff; background: radial-gradient(120% 90% at 100% 0%, #9B2347 0%, #7D142F 45%, #63102A 100%); box-shadow: 0 8px 22px rgba(125,20,47,.18); }
.tb-aff-deco { position: absolute; right: -18px; top: -26px; width: 210px; opacity: .13; filter: brightness(0) invert(1); pointer-events: none; }
.tb-aff-h { position: relative; display: flex; align-items: center; gap: 12px; }
.tb-aff-h .ic { flex: none; display: grid; place-items: center; width: 42px; height: 42px; border-radius: 11px; background: rgba(255,255,255,.14); }
.tb-aff-h .ic svg { width: 24px; height: 24px; }
.tb-aff-h b { display: block; font-size: 15px; font-weight: 800; letter-spacing: .02em; }
.tb-aff-h span { display: block; margin-top: 2px; font-size: 11px; color: #F5DCE3; }
.tb-aff-g { position: relative; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; flex: 1; }
.tb-aff-c { min-width: 0; display: flex; flex-direction: column; border-radius: 12px; background: #fff; color: #0B1F55; overflow: hidden;
  box-shadow: 0 4px 14px rgba(30,10,20,.16); }
.tb-aff-ch { display: flex; align-items: center; justify-content: space-between; gap: 6px; padding: 10px 10px 8px; }
.tb-aff-pill { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px 5px 8px; border-radius: 999px; font-size: 10.5px; font-weight: 800;
  color: #fff; white-space: nowrap; }
.tb-aff-pill svg { width: 14px; height: 14px; }
.tb-aff-pill.entree { background: #126F57; } .tb-aff-pill.sortie { background: #1F6FB8; }
.tb-tend { font-size: 13px; font-weight: 800; white-space: nowrap; }
.tb-tend.up { color: #12915F; } .tb-tend.down { color: #C0392B; } .tb-tend.eq, .tb-tend.nd { color: #5B6E93; }
.tb-aff-b { display: flex; align-items: center; justify-content: space-between; gap: 6px; padding: 6px 12px 10px; }
.tb-aff-n { display: flex; align-items: center; gap: 4px; min-width: 0; font-size: 17px; font-weight: 800; color: #7D142F; }
.tb-aff-n svg { flex: none; width: 16px; height: 16px; color: #0B1F55; }
.tb-aff-n span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tb-aff-v { display: flex; align-items: center; gap: 6px; margin-top: 2px; font-size: 30px; font-weight: 800; line-height: 1.05; letter-spacing: -.02em; }
.tb-aff-v small { font-size: 10px; font-weight: 600; line-height: 1.25; color: #4A5F86; letter-spacing: 0; }
.tb-aff-r { position: relative; flex: none; width: 74px; height: 74px; }
.tb-aff-r > span { position: absolute; inset: 0; display: grid; place-content: center; text-align: center; line-height: 1.05; }
.tb-aff-r b { font-size: 15px; font-weight: 800; color: #7D142F; }
.tb-aff-r small { display: block; font-size: 8px; color: #5B6E93; }
.tb-aff-f { display: flex; align-items: center; justify-content: center; gap: 6px; padding: 8px; border-top: 1px solid #EDF1F7; background: #F8FAFD;
  font-size: 11.5px; color: #22396E; }
.tb-aff-f svg { width: 15px; height: 15px; color: #7D142F; }
.tb-aff-s { flex: 1; display: flex; flex-direction: column; justify-content: space-around; gap: 6px; padding: 10px 12px; border-top: 1px solid #EDF1F7; }
.tb-aff-s > div { display: grid; grid-template-columns: 14px minmax(0, 104px) minmax(0, 1fr) 42px; gap: 8px; align-items: center; font-size: 12.5px; color: #22396E; }
.tb-aff-s .r { font-weight: 700; color: #7F93B3; } .tb-aff-s .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tb-aff-s .tb-barre { height: 6px; } .tb-aff-s b { text-align: right; font-weight: 700; color: #0B1F55; }
.tb-aff-vide { display: grid; place-items: center; flex: 1; min-height: 160px; padding: 12px; font-size: 12px; color: #5B6E93; text-align: center; }
.tb-aff-note { position: relative; display: flex; align-items: center; gap: 7px; font-size: 10.5px; color: #F5DCE3; }
.tb-aff-note svg { flex: none; width: 16px; height: 16px; padding: 2px; border-radius: 50%; background: rgba(255,255,255,.18); color: #fff; }

/* ── Résumé JOJ (page Fréquentation) ── */
.tb-jr { display: flex; flex-direction: column; gap: 12px; padding: 14px 16px; }
.tb-jr-h { display: flex; align-items: center; gap: 12px; }
.tb-jr-tag { flex: none; padding: 7px 14px; border-radius: 999px; background: #7D142F; color: #fff; font-size: 13px; font-weight: 800; letter-spacing: .02em; }
.tb-jr-h .sep { flex: none; width: 2px; height: 28px; background: #D5DEEA; }
.tb-jr-t { flex: 1; min-width: 0; font-size: 15px; font-weight: 800; color: #0B1F55; }
.tb-jr-t span { display: block; margin-top: 1px; font-size: 10.5px; font-weight: 500; color: #5B6E93; }
.tb-jr-emb { flex: none; height: 60px; width: auto; padding: 5px 8px; border-radius: 10px; background: #7D142F; } /* emblème en version négative : texte blanc */
.tb-jr-k { display: grid; grid-template-columns: 1.25fr 1fr 1fr; gap: 10px; }
.tb-jr-k .k { display: flex; align-items: center; gap: 10px; min-width: 0; padding: 10px 12px; border-radius: 11px; background: #F6F8FC; border: 1px solid #EBF0F7; }
.tb-jr-k .k.rose { background: #FBEFF2; border-color: #F5DCE3; }
.tb-jr-k .ic { flex: none; display: grid; place-items: center; width: 34px; height: 34px; color: #7D142F; }
.tb-jr-k .ic svg { width: 30px; height: 30px; }
.tb-jr-k .ic.rond { border-radius: 50%; background: #7D142F; color: #fff; } .tb-jr-k .ic.rond svg { width: 18px; height: 18px; }
.tb-jr-k b { display: block; font-size: 21px; font-weight: 800; color: #7D142F; line-height: 1.1; letter-spacing: -.01em; }
.tb-jr-k .lib { display: block; margin-top: 2px; font-size: 10.5px; line-height: 1.3; color: #22396E; }
.tb-jr-kv { display: flex; align-items: center; gap: 8px; } .tb-jr-kv i { width: 1.5px; height: 20px; background: #D9B6C1; }
.tb-jr-kv .tb-tend { font-size: 17px; }
.tb-jr-c { padding: 10px 12px 6px; border-radius: 11px; border: 1px solid #EBF0F7; }
.tb-jr-ct, .tb-jt { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 800; color: #0B1F55; }
.tb-jr-ct svg, .tb-jt svg { width: 18px; height: 18px; color: #7D142F; }
.tb-jt span { font-size: 11px; font-weight: 500; color: #5B6E93; }
.tb-jl { display: flex; flex-wrap: wrap; justify-content: center; gap: 6px 16px; margin-top: 4px; font-size: 10.5px; color: #22396E; }
.tb-jl i { display: inline-block; width: 8px; height: 8px; margin-right: 6px; border-radius: 50%; vertical-align: 0; }
.tb-jl em { font-style: normal; color: #5B6E93; }
.tb-jr-m { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-radius: 11px; font-size: 11.5px; color: #22396E; }
.tb-jr-m svg { flex: none; width: 22px; height: 22px; }
.tb-jr-m b { flex: none; font-size: 13px; } .tb-jr-m i { flex: none; width: 1.5px; height: 18px; background: currentColor; opacity: .3; }
.tb-jr-m span { flex: 1; font-style: italic; }
.tb-jr-m a { flex: none; font-weight: 700; color: #7D142F; text-decoration: none; white-space: nowrap; }
.tb-jr-m a:hover { text-decoration: underline; }
.tb-jr-m.vert { background: #EAF7F0; } .tb-jr-m.vert svg, .tb-jr-m.vert b { color: #12804F; }
.tb-jr-m.bleu { background: #EAF2FE; } .tb-jr-m.bleu svg, .tb-jr-m.bleu b { color: #1B5FD0; }
.tb-jr-m.rouge { background: #FBEDF0; } .tb-jr-m.rouge svg, .tb-jr-m.rouge b { color: #7D142F; }
.tb-jr-m.seul { background-color: #FBEFF2; } .tb-jr-m.seul svg, .tb-jr-m.seul b { color: #7D142F; }
.tb-jr-m.seul span { font-style: normal; }

/* ── Infobulles des graphiques (CSS pur, sans JavaScript) ── */
.tb-svg .hv .zone { fill: transparent; cursor: crosshair; }
.tb-svg .hv .tip, .tb-svg .hv .gd { opacity: 0; pointer-events: none; transition: opacity .08s; }
.tb-svg .hv:hover .tip, .tb-svg .hv:hover .gd { opacity: 1; }
.tb-svg .hv .tip text { font-size: 11px; }

/* ── Page JOJ ── */
.tb-je-g { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 10px; }
.tb-je { display: flex; flex-direction: column; gap: 2px; min-width: 0; padding: 10px 12px; border-radius: 10px; }
.tb-je small, .tb-jp small { font-size: 11px; font-weight: 700; color: #0B1F55; }
.tb-je b { font-size: 26px; font-weight: 800; letter-spacing: -.01em; line-height: 1.15; }
.tb-je span, .tb-jp span { font-size: 10.5px; color: #4A5F86; }
.tb-spark { display: block; width: 100%; height: 38px; margin-top: 4px; }
.tb-jp { display: flex; flex-direction: column; gap: 4px; min-width: 0; padding: 10px 12px; border-radius: 10px; background: #F6F8FC; }
.tb-jp b { font-size: 21px; font-weight: 800; color: #0B1F55; }
.tb-jp .tb-barre { height: 7px; margin-top: 4px; }
.tb-jg { display: grid; grid-template-columns: 18px minmax(0, 1fr) auto; grid-template-areas: "r nm p" ". br br" ". sm sm"; column-gap: 8px;
  padding: 7px 0; border-bottom: 1px solid #EEF2F8; font-size: 12px; color: #0B1F55; }
.tb-jg:last-child { border-bottom: 0; }
.tb-jg .r { grid-area: r; font-weight: 700; } .tb-jg .nm { grid-area: nm; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tb-jg b { grid-area: p; font-weight: 800; } .tb-jg b.up { color: #12915F; } .tb-jg b.down { color: #7D142F; }
.tb-jg .tb-barre { grid-area: br; height: 6px; margin-top: 4px; } .tb-jg small { grid-area: sm; margin-top: 2px; font-size: 10px; color: #5B6E93; }
.tb-jn { margin-top: 8px; font-size: 10.5px; line-height: 1.5; color: #5B6E93; }
@media (max-width: 900px) { .tb-jr-k, .tb-aff-g { grid-template-columns: minmax(0, 1fr); } .tb-jr-m { flex-wrap: wrap; } }

/* ── Fréquentation : données réelles (erreurs, nombreuses gares, absence de prévision) ── */
.tb-err { padding: 16px 18px; border: 1.5px solid #F2C4CF; background: #FFF7F9; }
.tb-err-h { display: flex; align-items: center; gap: 10px; font-size: 15px; font-weight: 800; color: #7D142F; }
.tb-err-h b { flex: none; display: grid; place-items: center; width: 24px; height: 24px; border-radius: 50%; background: #7D142F; color: #fff; font-size: 14px; }
.tb-err-t { margin-top: 10px; font-size: 12.5px; font-weight: 700; color: #0B1F55; }
.tb-err pre { margin: 8px 0 0; padding: 10px 12px; border-radius: 8px; background: #fff; border: 1px solid #F0D9DF;
  font: 11px/1.5 ui-monospace, Consolas, monospace; color: #4A1424; white-space: pre-wrap; word-break: break-word; }
.tb-err-c { margin-top: 10px; font-size: 11.5px; line-height: 1.55; color: #22396E; }
.tb-sim { display: flex; align-items: center; gap: 9px; padding: 8px 12px; border-radius: 9px; background: #FFF4E5; border: 1px solid #F8D9A8;
  font-size: 11.5px; color: #7A4A00; }
.tb-sim b { flex: none; display: grid; place-items: center; width: 18px; height: 18px; border-radius: 50%; background: #C2570C; color: #fff; font-size: 11px; }
.tb-t5 .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tb-fh { display: grid; gap: 2px; margin-top: 12px; }
.tb-fh-r { display: grid; grid-template-columns: minmax(84px, 128px) minmax(0, 1fr) minmax(0, 1fr); gap: 14px; align-items: center;
  min-height: 25px; font-size: 11px; color: #0B1F55; }
.tb-fh-r .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
.tb-fh-r > div { display: flex; align-items: center; gap: 8px; }
.tb-fh-r .tb-barre { flex: 1; height: 8px; }
.tb-fh-r b { flex: none; width: 46px; text-align: right; font-weight: 700; }
.tb-fh-h { min-height: 24px; border-radius: 8px; padding: 0 0 0 2px; background: #F4F8FD; font-size: 10px; font-weight: 600; color: #0B2A73; }
.tb-fc-vide { padding: 14px; border-radius: 9px; background: #fff; border: 1px dashed #A9C5F2; font-size: 11.5px; line-height: 1.5; color: #22396E; }
.tb-vide-b { padding: 26px 8px; text-align: center; font-size: 11.5px; line-height: 1.5; color: #4A5F86; }

/* ── Adaptatif ── */
@media (max-width: 1320px) { .tb-live { margin-left: 40px; } }
@media (max-width: 1200px) {
  .tb-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  [data-testid="stHorizontalBlock"]:has(.st-key-panneau) { flex-wrap: wrap !important; }
  [data-testid="stHorizontalBlock"]:has(.st-key-panneau) > [data-testid="stColumn"],
  [data-testid="stHorizontalBlock"]:has(.st-key-panneau) > [data-testid="column"] {
    flex: 1 1 100% !important; width: 100% !important; min-width: 100% !important; }
  .st-key-panneau [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; }
  .st-key-panneau [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
  .st-key-panneau [data-testid="stHorizontalBlock"] > [data-testid="column"] { min-width: 0 !important; width: auto !important; flex: 1 1 0 !important; }
}
@media (max-width: 1120px) { .tb-banniere { display: none; } }
@media (max-width: 900px) { .tb-live { display: none; } }
@media (max-width: 760px) {
  .tb-sidebar { display: none; }
  .block-container, [data-testid="stMainBlockContainer"] { padding-left: 16px !important; }
  .tb-header { margin-left: -16px; }
}
"""


def _h(s: str) -> str:
    """HTML compacté sur une ligne (évite l'interprétation Markdown)."""
    return re.sub(r"\n\s*", "", s.strip())


def md(html: str, conteneur=st):
    conteneur.markdown(_h(html), unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def _img(nom: str) -> str:
    try:
        return base64.b64encode((ASSETS / nom).read_bytes()).decode()
    except OSError:
        return ""


def _img_tag(nom: str, cls: str = "", largeur: int | None = None) -> str:
    b64 = _img(nom)
    if not b64:
        return ""
    w = f' width="{largeur}"' if largeur else ""
    return f'<img class="{cls}" src="data:image/png;base64,{b64}"{w} alt="">'


def date_fr(d: datetime) -> str:
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]} {d.year}"


ETAT_DEMO = ("Données de démonstration", "#1B6EF3")
ETAT_SIMULE = ("Données simulées", "#C2570C")


def html_entete(maj: datetime, titre: str = "Suivi des trains",
                sous_titre: str = "Horaires théoriques GTFS · positions simulées",
                etat: tuple[str, str] | None = None) -> str:
    """En-tête de page. « etat » = (libellé, couleur) de l'indicateur de fraîcheur des données."""
    libelle, coul = etat or (ETAT_SIMULE if REEL else ETAT_DEMO)
    return f"""
<div class="tb-header">
  <div><div class="tb-h1">{titre}</div><div class="tb-sous">{sous_titre}</div></div>
  <div class="tb-live">
    <div class="tb-live-l"><i style="background:{coul};box-shadow:0 0 0 3px {coul}29"></i>{libelle}</div>
    <div class="tb-live-d"><span>{date_fr(maj)}</span><span>•</span>{ICO["horloge"]}<span>{maj:%H:%M}</span></div>
  </div>
  {_img_tag("banniere_dakar2026_fondu.png", "tb-banniere")}
</div>"""


def html_bandeau_gtfs(d: dict) -> str:
    """Rappel de la nature des positions (et des arrêts GTFS non reconnus, s'il y en a)."""
    if not REEL:
        return html_bandeau_simule()
    txt = ("Horaires théoriques du GTFS : la position de chaque train est calculée à l'instant présent "
           "(arrêts en gare compris). Les retards sont simulés : aucune donnée GPS ni GTFS-RT n'est connectée.")
    if d.get("non_associes"):
        txt += (" Arrêts GTFS non reconnus dans Ref_Gares (ignorés) : " + ", ".join(d["non_associes"][:6])
                + ("…" if len(d["non_associes"]) > 6 else "") + ".")
    return f'<div class="tb-sim"><b>i</b><span>{esc(txt)}</span></div>'


def html_bandeau_simule() -> str:
    """Bandeau des pages sans vraie source, quand l'application tourne en mode réel."""
    if not REEL:
        return ""
    return ('<div class="tb-sim"><b>!</b><span>Page de démonstration : chiffres simulés, en attente d\'une source réelle '
            '(positions des trains). Seule la page <strong>Fréquentation</strong> lit les vraies données.</span></div>')


def _delta(v: int, suffixe: str = "", moins_est_mieux: bool = False) -> str:
    if v == 0:
        return f'<span class="dv" style="color:#4A5F86">= 0{suffixe}</span>'
    bon = v < 0 if moins_est_mieux else v > 0
    return (f'<span class="dv" style="color:{"#0FA96D" if bon else ROUGE}">'
            f'{ICO["haut" if v > 0 else "bas"]}<span>{v:+d}{suffixe}</span></span>')


def carte_kpi(ic, fond, coul, label, valeur, bas) -> str:
    return (f'<div class="tb-card tb-kpi"><div class="tb-kpi-ic" style="background:{fond};color:{coul}">{ICO[ic]}</div>'
            f'<div class="tb-kpi-t"><div class="tb-kpi-l">{label}</div><div class="tb-kpi-v">{valeur}</div>'
            f'<div class="tb-kpi-d">{bas}</div></div></div>')


def dec(x: float) -> str:
    s = f"{x:.1f}".replace(".", ",")
    return s[:-2] if s.endswith(",0") else s


def html_kpis(d: dict) -> str:
    trains = d["trains"]
    n = len(trains)
    ok = sum(t["ecart"] < SEUIL_RETARD for t in trains)
    retards = [t["ecart"] for t in trains if t["ecart"] >= SEUIL_RETARD]
    dep, tid = prochain_depart(d)
    moyen = dec(sum(retards) / len(retards)) if retards else "0"
    return '<div class="tb-kpis">' + "".join([
        carte_kpi("train", "#D9F5EA", "#12B97A", "Trains en circulation", f"{n} / {PARC_RAMES}",
                  f'<span class="sm">{PARC_RAMES - n} rame(s) en réserve</span>' if n <= PARC_RAMES else
                  f'<span class="sm">selon le GTFS · plus que le parc déclaré ({PARC_RAMES})</span>'),
        carte_kpi("reveil", "#E3F0FE", "#0A6CF5", "Ponctualité", f"{round(ok / n * 100)} %" if n else "—",
                  f'<span class="sm">{ok} sur {n} à l\'heure (retard &lt; {SEUIL_RETARD} min)</span>'),
        carte_kpi("sirene", "#F6E5EA", ROUGE, "Retard moyen", f"{moyen} min",
                  f'<span class="sm">sur {len(retards)} train(s) en retard</span>'),
        carte_kpi("depart", "#E3F0FE", "#0A66F0", "Prochain départ <em>(Dakar)</em>", f"{dep:%H:%M}",
                  f'<span class="sm">{tid} · Dakar → AIBD</span>'),
    ]) + "</div>"


def html_ligne(t: dict) -> str:
    etats = etats_troncons(t)
    n = len(etats)
    degrade = ",".join(f"{TRONCON_LIGNE[e]} {i / n * 100:.2f}% {(i + 1) / n * 100:.2f}%" for i, e in enumerate(etats))
    arrets = []
    for i, (nom, km) in enumerate(GARES):
        term = i in (0, len(GARES) - 1)
        cls = (f'tb-stop {etat_gare(i, t)}{" term" if term else ""}{" heure" if t["ecart"] < SEUIL_RETARD else ""}'
               f'{" decale" if len(GARES) > 8 and i % 2 else ""}')
        pt = f'<b>{ICO["avion" if i else "train"]}</b>' if term else "<i></i>"
        arrets.append(f'<div class="{cls}"><div class="pt">{pt}</div><div class="nm">{nom}</div><div class="km">{dec(km)} km</div></div>')
    return f"""
<div class="tb-card tb-ligne">
  <div class="tb-ligne-h"><div class="tb-ct">Ligne Dakar – AIBD</div><small>Train suivi : <b>{t["id"]}</b> · {ligne(t)}</small></div>
  <div class="tb-rail{" dense" if len(GARES) > 8 else ""}"><div class="tb-rail-bar" style="background:linear-gradient(90deg,{degrade})"></div>{"".join(arrets)}</div>
</div>"""


def html_tableau(trains: list[dict]) -> str:
    lignes = []
    for t in trains:
        s, c = statut(t)
        lignes.append(
            f'<tr><td><span class="tb-tag" style="background:{t["couleur"]}">{t["id"]}</span></td>'
            f'<td class="lg">{ligne(t)}</td><td>{t["position"]}</td><td>{t["prochain"]}</td>'
            f'<td><span class="tb-ec {c}">{ecart_txt(t)}</span></td>'
            f'<td><span class="tb-st {c}">{ICO["train"]}{s}</span></td><td>{t["vitesse"]} km/h</td></tr>')
    cols = "".join(f'<col style="width:{w}%">' for w in (8.8, 14.7, 18.1, 16.6, 14.9, 15.8, 11.1))
    return f"""
<div class="tb-tab-t">
  <div class="tb-ct">Détail des trains <span>(positions simulées depuis le GTFS)</span></div>
  <table class="tb-tab"><colgroup>{cols}</colgroup>
    <thead><tr><th>N° train</th><th>Ligne</th><th>Position actuelle</th><th>Prochain arrêt</th><th>Écart</th><th>Statut</th><th>Vitesse</th></tr></thead>
    <tbody>{"".join(lignes)}</tbody>
  </table>
</div>"""


def html_carte_train(t: dict, selection: bool) -> str:
    s, c = statut(t)
    n = len(GARES) - 1
    p = pk_vers_idx(t["pk"]) / n
    q = NOMS.index(t["prochain"])
    if t["sens"] > 0:
        dernier = max(q - 1, 0) / n
        vert, bleu, origine = (0, dernier), (dernier, max(p, dernier)), 0
        passe = lambda i: i / n <= dernier
    else:
        dernier = min(q + 1, n) / n
        vert, bleu, origine = (dernier, 1), (min(p, dernier), dernier), n
        passe = lambda i: i / n >= dernier
    barres = "".join(f'<span class="f" style="left:{a * 100:.2f}%;width:{(b - a) * 100:.2f}%;background:{coul}"></span>'
                     for (a, b), coul in ((vert, VERT), (bleu, BLEU)) if b > a)
    points = "".join(f'<span class="d{" o" if i == origine else " fait" if passe(i) else ""}" style="left:{i / n * 100:.2f}%"></span>'
                     for i in range(n + 1))
    return f"""
<div class="tb-tc{" sel" if selection else ""}">
  <div class="tb-tc-h">
    <span class="tb-tc-b" style="background:{t["couleur"]}">{t["id"]}</span>
    <span class="tb-tc-r">{ligne(t).replace("→", "&nbsp;→&nbsp;")}</span>
    <span class="tb-pill {c}">{s}</span>
    <span class="tb-tc-c">{ICO["chevron"]}</span>
  </div>
  <div class="tb-mini"><span class="ln"></span>{barres}{points}<span class="m" style="left:{p * 100:.2f}%"></span></div>
  <div class="tb-tc-f"><span class="lab">Position</span><span>{t["position"]}</span><i>|</i><span class="{c}">{ecart_txt(t)}</span><i>|</i><span>{t["vitesse"]} km/h</span></div>
</div>"""


CARTE_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="__LEAFLET__/leaflet.css">
<script src="__LEAFLET__/leaflet.js"></script>
<style>
__POLICE__
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:transparent;font-family:'Plus Jakarta Sans',system-ui,sans-serif;color:#0B1F55}
.card{position:absolute;inset:0;background:#fff;border:1px solid #EBF1F8;border-radius:12px;overflow:hidden}
.head{position:absolute;left:14px;top:0;height:36px;display:flex;align-items:center;font-size:14px;font-weight:700;letter-spacing:-.005em}
#map{position:absolute;left:4px;right:4px;top:35px;bottom:4px;border-radius:9px;background:#CFE6F7}
.legend{position:absolute;top:17px;right:6px;z-index:1000;display:flex;align-items:center;gap:17px;height:40px;padding:0 15px;background:#fff;border-radius:9px;box-shadow:0 4px 16px rgba(16,42,90,.12);font-size:9.5px;font-weight:500;white-space:nowrap}
.legend span{display:flex;align-items:center;gap:7px}
.legend i{width:8px;height:8px;border-radius:50%}
.ctl{position:absolute;right:16px;z-index:1000;background:#fff;border-radius:8px;box-shadow:0 2px 10px rgba(16,42,90,.16)}
.compass{top:68px;width:34px;height:40px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;cursor:pointer;font-size:9px;font-weight:800}
.compass svg{width:12px;height:12px;fill:#0B2A73}
.zoom{top:142px;width:34px;display:flex;flex-direction:column}
.zoom button{all:unset;display:grid;place-items:center;width:34px;height:36px;cursor:pointer;color:#0B3D91;font:400 20px/1 'Plus Jakarta Sans',system-ui,sans-serif}
.zoom button+button{border-top:1px solid #E6EDF6}
.zoom button:hover,.compass:hover{background:#F3F7FD}
.leaflet-container{font-family:inherit;background:#CFE6F7}
.leaflet-tooltip.lbl{background:none;border:0;box-shadow:none;padding:0;font-size:12.5px;font-weight:700;color:#0B1F55;text-shadow:0 0 2px #fff,0 0 4px #fff,0 0 7px #fff}
.leaflet-tooltip.lbl.t{font-size:13.5px;font-weight:800}
.leaflet-tooltip.lbl:before{display:none}
.leaflet-tooltip.tip{font-family:inherit;font-size:11px;line-height:1.45;color:#0B1F55;border:0;border-radius:8px;padding:6px 9px;box-shadow:0 6px 18px rgba(16,42,90,.2)}
.gare{width:13px;height:13px;border-radius:50%;background:#fff;border:3px solid #0D3A86;box-shadow:0 1px 3px rgba(0,0,0,.25)}
.tr{width:100%;height:100%;border-radius:50%;border:2px solid #fff;display:grid;place-items:center;box-shadow:0 2px 6px rgba(9,30,66,.35)}
.tr svg{width:56%;height:56%;fill:#fff}
.tr.sel{animation:pulse 1.8s ease-out infinite}
.tr.theo{background:#1B82FD;opacity:.92}
.num{position:absolute;left:50%;transform:translateX(-50%);padding:1px 6px;border-radius:6px;background:#fff;font-size:10.5px;font-weight:800;line-height:15px;white-space:nowrap;box-shadow:0 1px 4px rgba(9,30,66,.25);pointer-events:none}
.num.n{bottom:calc(100% + 3px)}
.num.s{top:calc(100% + 3px)}
@keyframes pulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--c) 55%,transparent),0 2px 6px rgba(9,30,66,.35)}100%{box-shadow:0 0 0 12px color-mix(in srgb,var(--c) 0%,transparent),0 2px 6px rgba(9,30,66,.35)}}
.term{width:34px;height:34px;border-radius:50%;background:#0B63D6;border:3px solid #fff;box-shadow:0 0 0 2px #0B63D6,0 3px 8px rgba(9,30,66,.35);display:grid;place-items:center}
.term svg{width:50%;height:50%;fill:#fff}
.ocean{font-style:italic;font-size:15px;font-weight:500;color:#4C9FE6;white-space:nowrap;letter-spacing:.01em}
.leaflet-control-attribution{font-size:8.5px;background:rgba(255,255,255,.7)!important;border-radius:6px 0 0 0}
.tv .head{left:22px;height:58px;font-size:24px;font-weight:800}
.tv #map{top:58px;left:6px;right:6px;bottom:6px}
.tv .legend{top:24px;right:10px;height:56px;gap:26px;padding:0 22px;font-size:15px;border-radius:12px}
.tv .legend i{width:12px;height:12px}
.tv .ctl{display:none}
.tv .leaflet-tooltip.lbl{font-size:19px}
.tv .leaflet-tooltip.lbl.t{font-size:22px}
.tv .num{font-size:16px;line-height:22px;padding:1px 9px;border-radius:8px}
.tv .ocean{font-size:22px}
.tv .gare,.tv .term{border-width:4px}
</style></head><body class="__CLASSE__">
<div class="card">
  <div class="head">__TITRE__</div>
  <div id="map"></div>
  <div class="legend">__LEGENDE__</div>
  <div class="ctl compass" id="nord" title="Recentrer la carte"><b>N</b><svg viewBox="0 0 24 24"><path d="M12 2 19.5 21 12 16.8 4.5 21z"/></svg></div>
  <div class="ctl zoom"><button id="zp" title="Zoom avant">+</button><button id="zm" title="Zoom arrière">−</button></div>
</div>
<script>
const D = __DATA__;
const IC = {
  train: '<svg viewBox="0 0 24 24"><path d="' + D.p_train + '"/></svg>',
  avion: '<svg viewBox="0 0 24 24" style="transform:rotate(45deg)"><path d="' + D.p_avion + '"/></svg>'
};
const map = L.map('map', {zoomControl: false, scrollWheelZoom: false, zoomSnap: 0.1, zoomDelta: 0.5});
map.attributionControl.setPrefix(false);
L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager_nolabels/{z}/{x}/{y}{r}.png',
  {subdomains: 'abcd', maxZoom: 18, attribution: '© OpenStreetMap · © CARTO'}).addTo(map);
const bornes = L.latLngBounds(D.trace);
const cadrer = () => map.fitBounds(bornes, {paddingTopLeft: [56 * D.k, 40 * D.k], paddingBottomRight: [64 * D.k, 22 * D.k]});
cadrer();
const icone = (html, w, h, ax, ay) => L.divIcon({html, className: '', iconSize: [w, h], iconAnchor: [ax ?? w / 2, ay ?? h / 2]});
L.marker(D.ocean, {icon: icone('<div class="ocean">Océan Atlantique</div>', 140, 20), interactive: false, keyboard: false}).addTo(map);
D.gares.forEach(g => {
  const s = Math.round((g.term ? 34 : 13) * D.k), dim = ' style="width:' + s + 'px;height:' + s + 'px"';
  const html = g.term ? '<div class="term"' + dim + '>' + (g.avion ? IC.avion : IC.train) + '</div>' : '<div class="gare"' + dim + '></div>';
  const m = L.marker(g.ll, {icon: icone(html, s, s), keyboard: false, zIndexOffset: g.term ? 400 : 0}).addTo(map);
  const gauche = g.dir === 'left', bas = g.dir === 'bottom';
  m.bindTooltip(g.nom, {permanent: true, direction: gauche ? 'left' : (bas ? 'bottom' : 'top'),
    offset: gauche ? [-s / 2 - 2, -12] : (bas ? [0, s / 2 + 8] : [0, -(s / 2 + 8)]),
    className: 'lbl' + (g.term ? ' t' : '')});
});
const cum = [0];
for (let i = 1; i < D.trace.length; i++) {
  const [a1, o1] = D.trace[i - 1], [a2, o2] = D.trace[i];
  cum.push(cum[i - 1] + Math.hypot(a2 - a1, (o2 - o1) * Math.cos(a1 * Math.PI / 180)));
}
const pkLL = pk => {
  const d = Math.max(0, Math.min(1, pk / D.pk_max)) * cum[cum.length - 1];
  for (let i = 1; i < cum.length; i++) if (d <= cum[i] || i === cum.length - 1) {
    const r = (d - cum[i - 1]) / ((cum[i] - cum[i - 1]) || 1), [a1, o1] = D.trace[i - 1], [a2, o2] = D.trace[i];
    return [a1 + (a2 - a1) * r, o1 + (o2 - o1) * r];
  }
};
// Deux voies : sens Dakar → AIBD côté nord, sens AIBD → Dakar côté sud (décalage en pixels)
const OFF = 4.5 * D.k, cote = sens => sens > 0 ? -OFF : OFF;
const normale = (a, b, q, px) => { const dx = b.x - a.x, dy = b.y - a.y, l = Math.hypot(dx, dy) || 1;
  return map.layerPointToLatLng(L.point(q.x - dy / l * px, q.y + dx / l * px)); };
const decale = (lls, px) => { const p = lls.map(ll => map.latLngToLayerPoint(ll));
  return p.map((q, i) => normale(p[Math.max(0, i - 1)], p[Math.min(p.length - 1, i + 1)], q, px)); };
const surVoie = (pk, sens) => { const lp = k => map.latLngToLayerPoint(pkLL(Math.max(0, Math.min(D.pk_max, k))));
  return normale(lp(pk - .3), lp(pk + .3), lp(pk), cote(sens)); };
const fonds = [1, -1].map(sens => ({sens, l: L.polyline([], {color: '#fff', weight: D.poids + 3.8, opacity: .9, interactive: false}).addTo(map)}));
const voies = [1, -1].map(sens => ({sens, segs: D.troncons.map((t, i) => L.polyline([], {color: (sens > 0 ? D.cv.a : D.cv.r)[i],
  weight: D.poids, lineCap: 'round', lineJoin: 'round', interactive: false}).addTo(map))}));
const tracer = () => {
  fonds.forEach(f => f.l.setLatLngs(decale(D.trace, cote(f.sens))));
  voies.forEach(v => v.segs.forEach((s, i) => s.setLatLngs(decale(D.troncons[i].pts, cote(v.sens)))));
};
tracer();
map.on('zoomend', tracer);
const iconeTrain = (t, sens) => { const s = Math.round((t.sel ? 30 : 24) * D.k);
  return icone('<div class="tr' + (t.sel ? ' sel' : '') + '" style="background:' + t.c + ';--c:' + t.c + '">' + IC.train + '</div>' +
    '<span class="num ' + (sens > 0 ? 'n' : 's') + '" style="color:' + t.c + '">' + t.id + '</span>', s, s); };
// Mode réel : points de passage GTFS [secondes depuis l'affichage, PK], interpolés en continu
// (arrêts en gare compris) ; démonstration : aller-retour à vitesse commerciale, horloge accélérée × D.accel.
const T0 = performance.now(), ecoule = () => (performance.now() - T0) / 1000;
const interp = (wp, s) => { if (!wp.length || s < wp[0][0] || s > wp[wp.length - 1][0]) return null;
  for (let i = 1; i < wp.length; i++) if (s <= wp[i][0]) { const a = wp[i - 1], b = wp[i];
    return a[1] + (b[1] - a[1]) * ((s - a[0]) / ((b[0] - a[0]) || 1)); }
  return wp[wp.length - 1][1]; };
const rotation = ph => { const L = D.pk_max, u = ((Date.now() / 3.6e6 * D.accel * D.v + ph) % (2 * L) + 2 * L) % (2 * L);
  return u < L ? [u, 1] : [2 * L - u, -1]; };
const etat = t => { if (!t.wp) return rotation(t.phase); const pk = interp(t.wp, ecoule()); return pk === null ? null : [pk, t.sens]; };
const theorique = t => { if (!t.wp) return null; const pk = interp(t.wp, ecoule() + t.retard_s); return pk === null ? null : [pk, t.sens]; };
const mobiles = D.trains.map(t => {
  const [pk, sens] = etat(t) || [t.wp ? t.wp[0][1] : 0, t.sens || 1];
  const o = {t, sens};
  o.theo = t.theo_ecart ? L.marker(surVoie(pk, sens), {icon: icone('<div class="tr theo">' + IC.train + '</div>', 20 * D.k, 20 * D.k),
    interactive: false, keyboard: false, zIndexOffset: 850}).addTo(map) : null;
  o.m = L.marker(surVoie(pk, sens), {icon: iconeTrain(t, sens), keyboard: false, zIndexOffset: t.sel ? 1200 : 900}).addTo(map)
    .bindTooltip(() => '<b>' + t.id + '</b> · ' + (o.sens > 0 ? 'Dakar → AIBD' : 'AIBD → Dakar') +
      '<br>Écart : <b style="color:' + t.c + '">' + t.ecart + '</b>', {direction: 'right', offset: [16, 0], className: 'tip'});
  return o;
});
const anim = () => {
  mobiles.forEach(o => {
    const e = etat(o.t);
    o.m.setOpacity(e ? 1 : 0);
    if (!e) { if (o.theo) o.theo.setOpacity(0); return; }
    const [pk, sens] = e;
    if (sens !== o.sens) { o.sens = sens; o.m.setIcon(iconeTrain(o.t, sens)); }
    o.m.setLatLng(surVoie(pk, sens));
    if (o.theo) {
      const th = o.t.wp ? theorique(o.t) : [Math.max(0, Math.min(D.pk_max, pk + sens * o.t.theo_ecart)), sens];
      o.theo.setOpacity(th ? 1 : 0);
      if (th) o.theo.setLatLng(surVoie(th[0], th[1]));
    }
  });
  requestAnimationFrame(anim);
};
requestAnimationFrame(anim);
document.getElementById('nord').onclick = cadrer;
document.getElementById('zp').onclick = () => map.zoomIn();
document.getElementById('zm').onclick = () => map.zoomOut();
addEventListener('resize', () => { map.invalidateSize(); cadrer(); });
</script></body></html>"""


LEG_SUIVI = ('<span><i style="background:#05A86F"></i>Position réelle</span><span><i style="background:#1B82FD"></i>Position théorique</span>'
             '<span><i style="background:#7D142F"></i>En retard</span><span><i style="background:#05A86F"></i>À l\'heure</span>'
             '<span><i style="background:#95B1C9"></i>Terminé</span>')
LEG_CHARGE = ('<span><i style="background:#fff;border:2px solid #0D3A86"></i>Gare</span>'
              '<span><i style="background:#05B877"></i>Train (position réelle)</span>'
              '<span><i style="background:#1B82FD"></i>Train (position théorique)</span>'
              '<span>Charge <b style="display:inline-block;width:64px;height:6px;border-radius:3px;'
              'background:linear-gradient(90deg,#2AA5E0,#22B07D,#F5A524,#F2711C,#7D142F)"></b></span>')


def html_carte(trains: list[dict], selection: str | None, couleurs: dict | None = None, legende: str = LEG_SUIVI,
               titre: str = "Réseau et position des trains", poids: float = 3.2,
               echelle: float = 1.0, a_venir: list[dict] | None = None) -> str:
    t_sel = next((t for t in trains if t["id"] == selection), None)
    if couleurs is None:
        etats = etats_troncons(t_sel) if t_sel else ["cours"] * (len(GARES) - 1)
        actif, gris = [TRONCON_CARTE[e] for e in etats], ["#9DB4D3"] * len(etats)
        s = t_sel["sens"] if t_sel else 1
        couleurs = {"a": actif if s > 0 else gris, "r": actif if s < 0 else gris}
    donnees = dict(
        p_train=P_TRAIN, p_avion=P_AVION, ocean=OCEAN, pk_max=PKS[-1], accel=ACCELERATION,
        trace=[list(p) for p in TRACE], cv=couleurs, poids=poids, k=echelle,
        troncons=[dict(pts=sous_trace(PKS[i], PKS[i + 1])) for i in range(len(GARES) - 1)],
        gares=[dict(nom=esc(n), ll=point_pk(k), term=i in (0, len(GARES) - 1), avion=i == len(GARES) - 1 and "aibd" in _norm(n),
                    dir="left" if i == 0 else ("top" if i % 2 or len(GARES) <= 8 else "bottom")) for i, (n, k) in enumerate(GARES)],
        v=V_COMMERCIALE,
        trains=[dict(id=esc(t["id"]), phase=t.get("phase", 0), sel=t["id"] == selection, c=ROUGE if t["ecart"] >= SEUIL_RETARD else "#05B877",
                     ecart=ecart_txt(t), wp=t.get("wp"), sens=t["sens"], retard_s=t["ecart"] * 60,
                     theo_ecart=t["ecart"] * V_COMMERCIALE / 60 if t["id"] == selection and t["ecart"] > 0 else 0)
                for t in list(trains) + list(a_venir or [])],
    )
    return (CARTE_HTML.replace("__LEAFLET__", LEAFLET).replace("__POLICE__", POLICE_CSS)
            .replace("__TITRE__", titre).replace("__LEGENDE__", legende)
            .replace("__CLASSE__", "tv" if echelle > 1 else "")
            .replace("__DATA__", json.dumps(donnees, ensure_ascii=False).replace("</", "<\\/")))


# — Fréquentation —
RANGS = ["#17A97B", "#2563C9", "#F5A524", "#8429F8", "#E0306E"]
JOURS_C = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
ECHELLE = [(0, (126, 211, 160)), (.42, (246, 213, 92)), (.72, (245, 140, 60)), (1, (125, 20, 47))]
TOUTES = "Toutes les gares"
PREFIXE_GARE = "Gare · "
VERT_OK, ORANGE, GRIS = "#12B76A", "#C2570C", "#7F93B3"


def nb(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def pct(a: float, b: float) -> int:
    return round((a / b - 1) * 100) if b else 0


def graduation(vmax: float, n: int = 4) -> float:
    brut = max(vmax, 1) / n
    mag = 10 ** math.floor(math.log10(brut))
    return next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= brut)


def couleur_echelle(v: float) -> str:
    for (a, ca), (b, cb) in zip(ECHELLE, ECHELLE[1:]):
        if v <= b:
            r = (v - a) / (b - a)
            return "#" + "".join(f"{round(x + (y - x) * r):02x}" for x, y in zip(ca, cb))
    return "#7d142f"


def niveau(p: float) -> str:
    for seuil, c in ((85, ROUGE), (70, "#F2711C"), (55, "#F5A524"), (45, "#B9C23A"), (30, "#22B07D")):
        if p >= seuil:
            return c
    return "#2AA5E0"


def statut_joj(jour: date) -> str:
    if jour < JOJ_DEBUT:
        return f"J-{(JOJ_DEBUT - jour).days}"
    if jour <= JOJ_FIN:
        return f"Jour {(jour - JOJ_DEBUT).days + 1}/{(JOJ_FIN - JOJ_DEBUT).days + 1}"
    return "Terminés"


def compact(v: float) -> str:
    if v >= 1e6:
        return dec(v / 1e6) + " M"
    return f"{v / 1e3:.0f} k" if v >= 1e3 else nb(v)


def hh(d: datetime) -> str:
    return f"{d:%H}h{d:%M}"


def esc(s) -> str:
    return _html.escape(str(s))


# — Fréquentation : accès aux données (communs aux modes réel et démonstration) —
def maintenant() -> datetime:
    """Heure locale de Dakar (UTC + décalage de parametres.toml), sans fuseau, à la seconde.
    Pour les essais, la variable d'environnement FREQ_HEURE_SIMULEE (« 2026-11-03 08:15:00 ») fixe l'heure."""
    simulee = os.environ.get("FREQ_HEURE_SIMULEE")
    if simulee:
        return datetime.fromisoformat(simulee).replace(microsecond=0)
    return (datetime.now(timezone.utc) + timedelta(hours=PARAM.decalage_utc_heures)).replace(tzinfo=None, microsecond=0)


def minuit(j: date) -> datetime:
    return datetime.combine(j, dtime())


def _gares_demo() -> pd.DataFrame:
    zone_de = {g: z for z, lst in ZONES_DEMO.items() for g in lst}
    return pd.DataFrame([dict(gare=n, ordre=i + 1, zone=zone_de.get(n), latitude=point_pk(k)[0], longitude=point_pk(k)[1])
                         for i, (n, k) in enumerate(GARES)])


def source():
    """Source des chiffres : SQL Server en mode réel, modèle simulé en mode démonstration."""
    if REEL:
        return SourceSQL(PARAM)
    return SourceDemo(_gares_demo(), flux_h, attendu_h, maintenant)


# Mise en cache : « cle » change avec le mode et le contenu des fichiers SQL (cache vidé dès qu'ils changent).
# Une erreur n'est jamais mise en cache : la requête est retentée au rafraîchissement suivant.
@st.cache_data(ttl=3600, show_spinner=False, max_entries=8)
def d_gares(cle: str) -> pd.DataFrame:
    return source().gares()


@st.cache_data(ttl=60, show_spinner=False, max_entries=8)
def d_derniere(cle: str, jour: date) -> datetime | None:
    """Dernière validation des 3 derniers jours (pour dire « hier à 23h58 » quand rien n'est encore arrivé aujourd'hui)."""
    return source().derniere_validation(minuit(jour - timedelta(days=3)), maintenant() + timedelta(hours=1))


@st.cache_data(ttl=300, show_spinner=False, max_entries=48)
def d_fenetres(cle: str, fenetres: tuple) -> pd.DataFrame:
    return source().fenetres(list(fenetres))


@st.cache_data(ttl=300, show_spinner=False, max_entries=48)
def d_horaire_recent(cle: str, debut: datetime, fin: datetime) -> pd.DataFrame:
    return source().horaire(debut, fin)


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=8)
def d_horaire_historique(cle: str, debut: datetime, fin: datetime) -> pd.DataFrame:
    return source().horaire(debut, fin)


@st.cache_data(ttl=3600, show_spinner=False, max_entries=32)
def d_journalier(cle: str, debut: datetime, fin: datetime) -> pd.DataFrame:
    return source().journalier(debut, fin)


@st.cache_data(ttl=1800, show_spinner=False, max_entries=16)
def d_previsions(cle: str, debut: datetime, fin: datetime) -> pd.DataFrame:
    return source().previsions(debut, fin)


def _par_gare(df: pd.DataFrame, colonne: str) -> dict:
    """{valeur de « colonne » : {gare : {"E": n, "S": n}}}"""
    out: dict = {}
    for k, g, s, n in zip(df[colonne], df["gare"], df["sens"], df["nb"]):
        out.setdefault(k, {}).setdefault(g, {"E": 0.0, "S": 0.0})[s] += float(n)
    return out


def _somme(par_gare: dict, gares, sens: str = "ES") -> float:
    return sum(par_gare.get(g, {}).get(s, 0.0) for g in gares for s in sens)


def _par_jour(df: pd.DataFrame, gares, sens: str = "E") -> dict:
    """{jour : total} sur les gares et le sens demandés (gares=None : toutes)."""
    if df.empty:
        return {}
    m = df["sens"].isin(list(sens))
    if gares is not None:
        m &= df["gare"].isin(list(gares))
    return {j: float(v) for j, v in df[m].groupby("jour")["nb"].sum().items()}


def en_service(d: datetime) -> bool:
    o, f = horaires(d.date())
    return o <= d.hour + d.minute / 60 < f


def perimetres(gares_df: pd.DataFrame, vus: list[str]) -> tuple[dict, list[str]]:
    """Choix du filtre : toutes les gares, les zones, puis chaque gare. Renvoie aussi les avertissements."""
    connues = list(gares_df["gare"])
    noms = connues + [g for g in vus if g not in set(connues)]  # gares présentes dans les validations seulement
    opts, zones, avert = {TOUTES: noms}, {}, []
    for g, z in zip(gares_df["gare"], gares_df["zone"]):
        if isinstance(z, str) and z.strip():
            zones.setdefault(z.strip(), []).append(g)
    for z, lst in PARAM.zones.items():  # zones de parametres.toml (prioritaires)
        inconnues = [g for g in lst if g not in noms]
        if inconnues:
            avert.append(f"parametres.toml, zone « {z} » : gare inconnue " + ", ".join(f"« {g} »" for g in inconnues)
                         + ". Écrire les noms exactement comme dans sql/3_gares.sql.")
        zones[z] = [g for g in noms if g in lst]
    for z, lst in zones.items():
        if lst:
            opts[z if z.lower().startswith("zone") else f"Zone {z}"] = lst
    if len(noms) > 1:
        opts.update({PREFIXE_GARE + g: [g] for g in noms})
    return opts, avert


def freq_base() -> dict:
    """Chiffres communs à toute la page, quel que soit le périmètre choisi.

    Heure de référence = dernière validation reçue aujourd'hui (les validations arrivent par lots toutes
    les 30 minutes) : les comparaisons « à la même heure » se font toutes à cette heure-là.
    """
    cle, now = PARAM.signature(), maintenant()
    J, N = now.date(), PARAM.semaines_reference
    gares_df = d_gares(cle)
    der = d_derniere(cle, J)
    du_jour = der is not None and der.date() == J
    if du_jour:
        t_ref = min(der, now)
        t_sup = t_ref + timedelta(seconds=1)  # fin (exclue) des intervalles : la dernière validation est comptée
    else:  # aucune validation aujourd'hui : on raisonne sur l'heure actuelle (arrondie à la minute pour le cache)
        t_ref = now
        t_sup = now.replace(second=0) + timedelta(minutes=1)
    m0 = minuit(J)
    fen = (("jour", m0, t_sup), ("veille", m0 - timedelta(days=1), t_sup - timedelta(days=1)),
           ("m30", t_sup - timedelta(minutes=30), t_sup), ("m60", t_sup - timedelta(minutes=60), t_sup),
           *((f"s{k}", t_sup - timedelta(days=7 * k, minutes=60), t_sup - timedelta(days=7 * k)) for k in range(1, N + 1)),
           # même lot de 30 min, les mêmes jours des semaines précédentes : tendance de chaque gare
           *((f"q{k}", t_sup - timedelta(days=7 * k, minutes=30), t_sup - timedelta(days=7 * k)) for k in range(1, N + 1)))
    F = _par_gare(d_fenetres(cle, fen), "fenetre")
    opts, avert = perimetres(gares_df, sorted({g for d in F.values() for g in d}))
    ouv, ferm = horaires(J)
    service = en_service(now)
    ouvert_depuis = (now - (m0 + timedelta(hours=ouv))).total_seconds() / 60
    age = (now - t_ref).total_seconds() / 60 if du_jour else None
    en_retard = REEL and service and ouvert_depuis > PARAM.alerte_retard_minutes and (
        age is None or age > PARAM.alerte_retard_minutes)
    return dict(cle=cle, now=now, J=J, N=N, der=der, du_jour=du_jour, t_ref=t_ref, t_sup=t_sup, F=F,
                options=opts, avert=avert, ouv=ouv, ferm=ferm, service=service, en_retard=en_retard)


def etat_donnees(b: dict | None, erreur: bool = False) -> tuple[str, str]:
    """Libellé et couleur de l'indicateur de fraîcheur affiché dans l'en-tête."""
    if not REEL:
        return ETAT_DEMO
    if erreur or b is None:
        return "Lecture des données impossible", ROUGE
    der, now = b["der"], b["now"]
    if der is None:
        quand = "aucune validation reçue aujourd'hui"
    elif der.date() == now.date():
        quand = f"dernière validation reçue à {hh(der)}"
    elif der.date() == now.date() - timedelta(days=1):
        quand = f"dernière validation reçue hier à {hh(der)}"
    else:
        quand = f"dernière validation reçue le {der:%d/%m} à {hh(der)}"
    if b["en_retard"]:
        return f"Données en retard · {quand}", ORANGE
    if not b["service"]:
        return f"Service fermé · {quand}", GRIS
    return quand[0].upper() + quand[1:], VERT_OK


JOURNAL = logging.getLogger("frequentation")


def html_erreur(titre: str, detail: str = "", conseil: str = "", entete: str = "Lecture des données impossible") -> str:
    """Carte d'erreur. Sécurité : le détail technique (serveur, base, message SQL) n'est montré aux visiteurs
    que si [securite] afficher_details_erreurs = true ; il est toujours écrit dans le journal du serveur."""
    JOURNAL.error("%s | %s | %s", titre, detail, conseil)
    if not PARAM.afficher_details_erreurs:
        detail, conseil = "", ("Le détail est enregistré dans le journal du serveur "
                            "(Linux : journalctl -u frequentation-gares ; Windows : logs\\streamlit.log).")
    pre = f"<pre>{esc(detail).replace(chr(10), '<br>')}</pre>" if detail else ""  # sauts de ligne gardés par md()
    cons = f'<div class="tb-err-c">{esc(conseil)}</div>' if conseil else ""
    return (f'<div class="tb-card tb-err"><div class="tb-err-h"><b>!</b>{esc(entete)}</div>'
            f'<div class="tb-err-t">{esc(titre)}</div>{pre}{cons}'
            f'<div class="tb-err-c">Les chiffres concernés ne s\'affichent pas tant que ce problème n\'est pas réglé : '
            f'l\'application ne remplace jamais les vraies données par des chiffres simulés. '
            f'Le script <b>verifier_installation.py</b> aide à trouver la cause.</div></div>')


def cible_txt(perimetre: str, G: list[str]) -> str:
    if perimetre == TOUTES:
        return "toutes les gares"
    return perimetre[len(PREFIXE_GARE):] if perimetre.startswith(PREFIXE_GARE) else perimetre


def html_freq_cartes(b: dict, G: list[str], perimetre: str) -> str:
    F = b["F"]
    jour, veille = F.get("jour", {}), F.get("veille", {})
    E, S = _somme(jour, G, "E"), _somme(jour, G, "S")
    e15, s15 = _somme(F.get("m30", {}), G, "E"), _somme(F.get("m30", {}), G, "S")
    rythme = _somme(F.get("m60", {}), G, "E")  # voyageurs = entrées (une sortie est le même voyageur)
    habituel = semaine_habituelle(b, G)
    cumul = f"cumul depuis {b['ouv']:02d}h00"
    rouge = lambda v: f'<span style="color:{ROUGE}">{v}</span>'

    def vs_hier(v: float, ref: float) -> str:
        if ref <= 0:  # pas de validations hier à la même heure : aucune comparaison à faire
            return '<span class="sm">pas de données hier à cette heure</span>'
        return _delta(pct(v, ref), " %") + '<span class="sm">vs. hier à la même heure</span>'

    comp = (_delta(pct(rythme, habituel), " %") + '<span class="sm">vs. semaine habituelle</span>'
            if habituel else '<span class="sm">pas de référence habituelle à cette heure</span>')
    return '<div class="tb-kpis">' + "".join([
        carte_kpi("entree", "#D9F5EA", "#12B97A", f"Entrées <em>({cumul})</em>", rouge(nb(E)), vs_hier(E, _somme(veille, G, "E"))),
        carte_kpi("sortie", "#E3F0FE", "#0A6CF5", f"Sorties <em>({cumul})</em>", rouge(nb(S)), vs_hier(S, _somme(veille, G, "S"))),
        carte_kpi("horloge", "#F6E5EA", ROUGE, "Dernier lot reçu <em>(fenêtre 30 min)</em>",
                  f'<span class="tb-kpi-2"><span>{nb(e15)}<small>entrées</small></span><span>{nb(s15)}<small>sorties</small></span></span>',
                  f'<span class="sm">{esc(cible_txt(perimetre, G))} · jusqu\'à {hh(b["t_ref"])}</span>'),
        carte_kpi("groupe", "#E3F0FE", "#0A66F0", "Voyageurs <em>(dernière heure reçue)</em>", nb(rythme),
                  comp + f'<span class="sm"> · entrées jusqu\'à {hh(b["t_ref"])}</span>'),
    ]) + "</div>"


def semaine_habituelle(b: dict, G: list[str]) -> float | None:
    """Voyageurs (entrées) des 60 mêmes minutes, les mêmes jours des semaines précédentes (médiane).
    Une semaine sans aucune validation sur tout le réseau (données absentes) est écartée."""
    vals = [_somme(b["F"].get(f"s{k}", {}), G, "E") for k in range(1, b["N"] + 1)
            if _somme(b["F"].get(f"s{k}", {}), b["options"][TOUTES]) > 0]
    return median(vals) if vals else None


def plage_previsions(J: date) -> tuple[datetime, datetime]:
    """Une seule plage de prévisions pour toute la page (même requête en cache pour les deux blocs)."""
    debut, fin = J, J + timedelta(days=2)
    if PARAM.joj_afficher:
        debut, fin = min(debut, JOJ_DEBUT), max(fin, JOJ_FIN + timedelta(days=1))
    return minuit(debut), minuit(fin)


def previsions_par_heure(b: dict, G: list[str], sens: str = "E") -> dict:
    """{début d'heure : (nb, nb_bas, nb_haut)} sommés sur les gares du périmètre."""
    df = d_previsions(b["cle"], *plage_previsions(b["J"]))
    if df.empty:
        return {}
    d = df[df["sens"].isin(list(sens)) & df["gare"].isin(list(G))]
    if d.empty:
        return {}
    g = d.groupby("date_heure")[["nb", "nb_bas", "nb_haut"]].sum(min_count=1)
    return {pd.Timestamp(t).to_pydatetime(): (float(r.nb), r.nb_bas, r.nb_haut) for t, r in g.iterrows()}


def survol(x: float, demi: float, haut: float, bas: float, W: float, titre: str, lignes: list[tuple]) -> str:
    """Zone de survol d'un graphique SVG, sans JavaScript : une bande verticale invisible ; au survol,
    un trait repère et une bulle avec les valeurs apparaissent (règles .tb-svg .hv dans la feuille de style).
    lignes = [(couleur, libellé, valeur), …] ; une valeur None est omise."""
    lignes = [(c, l, v) for c, l, v in lignes if v is not None]
    if not lignes:
        return ""
    larg = max([len(titre)] + [len(l) + len(v) + 3 for _, l, v in lignes]) * 6.2 + 26
    h = 22 + 16 * len(lignes)
    bx = x + 12 if x + 12 + larg <= W - 2 else max(2.0, x - 12 - larg)
    by = haut + 2
    corps = "".join(
        f'<circle cx="{bx + 14:.1f}" cy="{by + 16 + 16 * (i + 1) - 4:.1f}" r="4" fill="{c}" stroke="#fff" stroke-width="1"/>'
        f'<text x="{bx + 24:.1f}" y="{by + 16 + 16 * (i + 1):.1f}" fill="#fff">{esc(l)} : '
        f'<tspan font-weight="800">{esc(v)}</tspan></text>' for i, (c, l, v) in enumerate(lignes))
    return (f'<g class="hv"><rect class="zone" x="{x - demi:.1f}" y="{haut}" width="{2 * demi:.1f}" height="{bas - haut:.1f}"/>'
            f'<line class="gd" x1="{x:.1f}" x2="{x:.1f}" y1="{haut}" y2="{bas:.1f}" stroke="#0B1F55" stroke-dasharray="2 2"/>'
            f'<g class="tip"><rect x="{bx:.1f}" y="{by:.1f}" width="{larg:.1f}" height="{h}" rx="7" fill="#0B1F55" opacity=".94"/>'
            f'<text x="{bx + 12:.1f}" y="{by + 16:.1f}" fill="#fff" font-weight="800">{esc(titre)}</text>{corps}</g></g>')


def anneau(part: float, coul: str, taille: int = 74) -> str:
    """Jauge circulaire : part (0–100) d'un total."""
    r, ep = 30, 7
    c = 2 * math.pi * r
    p = max(0.0, min(100.0, part))
    return (f'<svg class="tb-ring" width="{taille}" height="{taille}" viewBox="0 0 74 74" aria-hidden="true">'
            f'<circle cx="37" cy="37" r="{r}" fill="none" stroke="#E6ECF4" stroke-width="{ep}"/>'
            f'<circle cx="37" cy="37" r="{r}" fill="none" stroke="{coul}" stroke-width="{ep}" stroke-linecap="round" '
            f'stroke-dasharray="{c * p / 100:.1f} {c:.1f}" transform="rotate(-90 37 37)"/></svg>')


def tendance(v: float | None, suffixe: str = " %") -> str:
    if v is None:
        return '<span class="tb-tend nd">—</span>'
    cls, fl = ("up", "↗") if v > 0 else (("down", "↘") if v < 0 else ("eq", "→"))
    return f'<span class="tb-tend {cls}">{fl} {"+" if v > 0 else ""}{v}{suffixe}</span>'


def gare_affluente(b: dict, G: list[str], s: str, n_suivantes: int = 5) -> dict | None:
    """Gare la plus affluente du dernier lot reçu (30 min) dans le sens s (« E » ou « S ») :
    gare, val, part du flux (%), tend (% vs le même lot des semaines précédentes), autres [(gare, nb)], pic du jour.
    None s'il n'y a aucune validation dans le lot."""
    F, N = b["F"], b["N"]
    lot = F.get("m30", {})
    vals = {g: _somme(lot, [g], s) for g in G}
    total = sum(vals.values())
    if total < 1:
        return None
    g = max(G, key=vals.get)
    refs = [_somme(F.get(f"q{k}", {}), [g], s) for k in range(1, N + 1)
            if _somme(F.get(f"q{k}", {}), b["options"][TOUTES]) > 0]
    hab = median(refs) if refs else 0
    pic = "—"
    df_j = d_horaire_recent(b["cle"], minuit(b["J"]), b["t_sup"])
    if not df_j.empty:
        d = df_j[(df_j["gare"] == g) & (df_j["sens"] == s)]
        if not d.empty:
            pic = f'{int(d.groupby("heure")["nb"].sum().idxmax()):02d}h00'
    autres = [(x, vals[x]) for x in sorted((x for x in G if x != g), key=vals.get, reverse=True)[:n_suivantes] if vals[x] > 0]
    return dict(gare=g, val=vals[g], part=vals[g] / total * 100, tend=pct(vals[g], hab) if hab else None, autres=autres, pic=pic)


def html_hero(b: dict, G: list[str], perimetre: str) -> str:
    """Gares les plus affluentes sur le dernier lot reçu (30 min) : un classement en entrée, un en sortie (sans photo de gare).
    Tendance = même lot de 30 min, les mêmes jours des semaines précédentes ; pic = heure la plus chargée du jour."""
    N = b["N"]

    def carte(s: str) -> str:
        entree = s == "E"
        coul, pill = ("#168C68", "entree") if entree else ("#287BC1", "sortie")
        titre = "TOP ENTRÉES" if entree else "TOP SORTIES"
        x = gare_affluente(b, G, s, 5)
        if x is None:
            return (f'<div class="tb-aff-c"><div class="tb-aff-ch"><span class="tb-aff-pill {pill}">{ICO[pill]}{titre}</span></div>'
                    f'<div class="tb-aff-vide">Aucune validation dans le dernier lot</div></div>')
        g, part = x["gare"], x["part"]
        suivantes = ('<div class="tb-aff-s">' + "".join(
            f'<div><span class="r">{k + 2}</span><span class="nm" title="{esc(n)}">{esc(n)}</span>'
            f'<span class="tb-barre"><span style="width:{v / x["val"] * 100:.0f}%;background:{coul}"></span></span>'
            f'<b>{nb(v)}</b></div>' for k, (n, v) in enumerate(x["autres"])) + "</div>") if x["autres"] else ""
        return f"""
<div class="tb-aff-c">
  <div class="tb-aff-ch"><span class="tb-aff-pill {pill}">{ICO[pill]}{titre}</span>
    <span title="vs. le même lot de 30 min, les mêmes jours des {N} semaines précédentes">{tendance(x["tend"])}</span></div>
  <div class="tb-aff-b">
    <div><div class="tb-aff-n">{ICO["epingle"]}<span>{esc(g)}</span></div>
      <div class="tb-aff-v" style="color:{coul if not entree else '#0B1F55'}">{nb(x["val"])}<small>voyageurs<br>{"entrants" if entree else "sortants"}</small></div></div>
    <div class="tb-aff-r">{anneau(part, ROUGE if entree else coul)}<span><b>{round(part)} %</b><small>du flux<br>{"entrant" if entree else "sortant"}</small></span></div>
  </div>
  {suivantes}
  <div class="tb-aff-f">{ICO["horloge"]}Pic du jour : <b>{x["pic"]}</b></div>
</div>"""

    cible = cible_txt(perimetre, G)
    return f"""
<div class="tb-aff">
  {_img_tag("train_illustration.png", "tb-aff-deco")}
  <div class="tb-aff-h"><span class="ic">{ICO["train"]}</span>
    <div><b>GARES LES PLUS AFFLUENTES</b><span>Entrées / Sorties — dernier lot reçu (30 min)</span></div></div>
  <div class="tb-aff-g">{carte("E")}{carte("S")}</div>
  <div class="tb-aff-note">{ICO["info"]}Validations reçues par lots de 30 minutes · dernier lot jusqu'à {hh(b["t_ref"])} · {esc(cible)}</div>
</div>"""


# — JOJ : analyses « à date » —
ALIGN_2025 = timedelta(days=364)  # même jour de semaine un an plus tôt


def joj_analyse(b: dict, G: list[str]) -> dict:
    """Toutes les analyses JOJ du périmètre G, comparées « à date » (jusqu'à la dernière validation reçue).

    réel     validations reçues (jours terminés + aujourd'hui jusqu'à la dernière validation)
    normal   même jour de semaine, moyenne des N semaines avant les Jeux (jours avec données)
    2025     même jour de semaine un an plus tôt (364 jours), arrêté à la même heure aujourd'hui
    prévu    prévisions du modèle (validations_horaires_forecast), à défaut la moyenne normale
    """
    cle, J, N, t_ref = b["cle"], b["J"], b["N"], b["t_ref"]
    d0, d1, un = JOJ_DEBUT, JOJ_FIN, timedelta(days=1)
    jours = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
    ref0 = d0 - timedelta(days=7 * N)
    contexte = [d0 - timedelta(days=i) for i in range(14, 0, -1)]
    fin_reel = min(J, d1 + un)
    h0 = t_ref.replace(minute=0, second=0, microsecond=0)
    frac = (t_ref - h0).total_seconds() / 3600
    dans_jeux = d0 <= J <= d1
    tous = b["options"][TOUTES]

    jr = d_journalier(cle, minuit(min(ref0, contexte[0])), minuit(max(fin_reel, d0)))
    j25 = d_journalier(cle, minuit(contexte[0] - ALIGN_2025), minuit(d1 + un - ALIGN_2025))
    h_norm = d_horaire_historique(cle, minuit(ref0), minuit(d0))
    h_joj = d_horaire_recent(cle, minuit(d0), min(b["t_sup"], minuit(d1 + un))) if J >= d0 else _vide_h()
    h25 = d_horaire_historique(cle, minuit(J - ALIGN_2025), minuit(J - ALIGN_2025 + un)) if dans_jeux else _vide_h()
    P_e, P_s = previsions_par_heure(b, G, "E"), previsions_par_heure(b, G, "S")

    jours_ok_norm = set(_par_jour(jr[(jr["jour"] >= ref0) & (jr["jour"] < d0)] if not jr.empty else jr, tous, "ES"))
    jours_h_norm = set(h_norm["jour"]) if not h_norm.empty else set()

    def par_sens(s: str) -> dict:
        reel = _par_jour(jr, G, s)
        a25 = _par_jour(j25, G, s)
        # Normale = MÉDIANE du même jour de semaine sur les semaines de référence : un jour anormal
        # (panne, fichier incomplet) ne la tire pas vers le bas
        normal = {j: _normale(reel, jours_ok_norm, j.weekday()) for j in jours}
        # profil horaire « normal » du jour de semaine (pour arrêter la comparaison à la même heure)
        hn: dict = {}
        if not h_norm.empty:
            d = h_norm[h_norm["gare"].isin(list(G)) & (h_norm["sens"] == s)]
            hn = {(j, int(h)): float(v) for (j, h), v in d.groupby(["jour", "heure"])["nb"].sum().items()}
        jours_h_js = {js: [j for j in jours_h_norm if j.weekday() == js] for js in range(7)}
        prof = lambda js, h: median([hn.get((j, h), 0.0) for j in jours_h_js[js]]) if jours_h_js[js] else 0.0
        aujourd_hui = _somme(b["F"].get("jour", {}), G, s)
        norm_partiel = (sum(prof(J.weekday(), h) for h in range(h0.hour)) + prof(J.weekday(), h0.hour) * frac) if dans_jeux else 0.0
        a25_partiel = 0.0
        if dans_jeux and not h25.empty:
            d = h25[h25["gare"].isin(list(G)) & (h25["sens"] == s)]
            hs = d.groupby("heure")["nb"].sum()
            a25_partiel = float(hs[hs.index < h0.hour].sum() + hs.get(h0.hour, 0.0) * frac)
        Ps = P_e if s == "E" else P_s
        prev_j: dict = {}
        for t_, (v, _, _) in Ps.items():
            prev_j[t_.date()] = prev_j.get(t_.date(), 0.0) + v
        repli = False
        valeurs, statut_j = [], []
        for j in jours:
            if j < J:
                valeurs.append(reel.get(j, 0.0)); statut_j.append("reel")
            elif j == J:
                reste = sum(v for t_, (v, _, _) in Ps.items() if t_.date() == J and t_ > h0) + Ps.get(h0, (0.0,))[0] * (1 - frac)
                if any(t_.date() == J for t_ in Ps):
                    valeurs.append(aujourd_hui + reste)
                else:
                    repli = True
                    valeurs.append(max(aujourd_hui, normal[j]))
                statut_j.append("jour")
            else:
                if j in prev_j:
                    valeurs.append(prev_j[j])
                else:
                    repli = True
                    valeurs.append(normal[j])
                statut_j.append("prevu")
        faits = [j for j in jours if j < J]
        R = sum(reel.get(j, 0.0) for j in faits) + (aujourd_hui if dans_jeux else 0.0)
        Nn = sum(normal[j] for j in faits) + norm_partiel
        A = sum(a25.get(j - ALIGN_2025, 0.0) for j in faits) + a25_partiel
        a_2025 = sum(a25.values()) > 0
        a_normal = sum(normal.values()) > 0
        return dict(reel=reel, a25=a25, normal=normal, valeurs=valeurs, statut=statut_j, R=R, N=Nn, A=A,
                    a_2025=a_2025, a_normal=a_normal, repli=repli, projection=sum(valeurs),
                    normal_total=sum(normal.values()), vs_normal=pct(R, Nn) if (Nn and R) else None,
                    vs_2025=pct(R, A) if (A and R and a_2025) else None, ecart_2025=(R - A) if (a_2025 and R) else None)

    E, S = par_sens("E"), par_sens("S")

    # Pics horaires pendant les Jeux, et profil horaire JOJ vs normal (entrées + sorties)
    def profil_horaire(df: pd.DataFrame, sens: str) -> dict:
        if df.empty:
            return {}
        d = df[df["gare"].isin(list(G)) & df["sens"].isin(list(sens))]
        n_j = max(len(set(d["jour"])), 1)
        return {int(h): float(v) / n_j for h, v in d.groupby("heure")["nb"].sum().items()}

    pics_h = {}
    for s in "ES":
        p = profil_horaire(h_joj, s)
        if p:
            h = max(p, key=p.get)
            pics_h[s] = (h, p[h] / (sum(p.values()) or 1) * 100)
    prof_joj, prof_norm = profil_horaire(h_joj, "ES"), profil_horaire(h_norm, "ES")

    # Gares : hausse des entrées pendant les Jeux vs normal (jours terminés ; sinon aujourd'hui à date)
    hausses = []
    faits = [j for j in jours if j < J]
    for g in G:
        rg = _par_jour(jr, [g], "E")
        ng = sum(_normale(rg, jours_ok_norm, j.weekday()) for j in faits)
        vg = sum(rg.get(j, 0.0) for j in faits)
        if ng > 0 and vg > 0:
            hausses.append((g, pct(vg, ng), vg - ng))
    hausses.sort(key=lambda x: -x[1])

    # Ratios quotidiens réel / normal (petites courbes entrées et sorties)
    ratios = {s: [(X["reel"].get(j, 0.0) / X["normal"][j] if X["normal"][j] else None) for j in faits]
              for s, X in (("E", E), ("S", S))}
    return dict(jours=jours, contexte=contexte, J=J, nr=len(faits), dans_jeux=dans_jeux, E=E, S=S, pics=pics_h,
                prof_joj=prof_joj, prof_norm=prof_norm, hausses=hausses, ratios=ratios,
                reel_contexte={s: _par_jour(jr, G, s) for s in "ES"}, h0=h0)


def _normale(par_jour: dict, jours_ref: set, jour_semaine: int) -> float:
    """Médiane des valeurs d'un jour de la semaine sur les jours de référence (0 si aucun)."""
    vals = [par_jour.get(j, 0.0) for j in jours_ref if j.weekday() == jour_semaine]
    return median(vals) if vals else 0.0


def _vide_h() -> pd.DataFrame:
    return pd.DataFrame(columns=["jour", "heure", "gare", "sens", "nb"])


def joj_message(a: dict) -> tuple[str, str, str]:
    """Titre, phrase et couleur du message « À retenir », calculés à partir des chiffres."""
    E = a["E"]
    v = E["vs_normal"]
    if a["J"] < JOJ_DEBUT:
        return ("Jeux à venir", f"Début des JOJ dans {(JOJ_DEBUT - a['J']).days} jour(s) · fréquentation prévue sur la période : "
                f"{compact(E['projection'])} voyageurs.", "bleu")
    if v is None:
        return ("Pas encore de comparaison", "La période de référence ne contient pas encore assez de validations.", "bleu")
    titre, coul = (("Fréquentation supérieure à la normale", "vert") if v >= 5 else
                   ("Fréquentation proche de la normale", "bleu") if v > -5 else ("Fréquentation inférieure à la normale", "rouge"))
    morceaux = [f"{'+' if v > 0 else ''}{v} % d'entrées vs une période normale"]
    if E["vs_2025"] is not None:
        morceaux.append(f"{'+' if E['ecart_2025'] >= 0 else '−'}{nb(abs(E['ecart_2025']))} voyageurs vs 2025")
    if "E" in a["pics"]:
        h = a["pics"]["E"][0]
        morceaux.append(f"pic des entrées à {h:02d}h–{(h + 1) % 24:02d}h")
    if a["hausses"]:
        g, p, _ = a["hausses"][0]
        morceaux.append(f"plus forte hausse à {g} ({'+' if p > 0 else ''}{p} %)")
    return titre, " · ".join(morceaux) + ".", coul


def svg_cumul(a: dict, W: int = 560, H: int = 190, points: bool = True) -> str:
    """Fréquentation cumulée (entrées) : JOJ 2026 (réel puis prévu en pointillés), période normale, 2025."""
    E, jours, J = a["E"], a["jours"], a["J"]
    n, nr = len(jours), a["nr"]

    def cumuler(v):
        out, s = [], 0.0
        for x in v:
            s += x
            out.append(s)
        return out

    cj = cumuler(E["valeurs"])
    cn = cumuler([E["normal"][j] for j in jours]) if E["a_normal"] else None
    c1 = cumuler([E["a25"].get(j - ALIGN_2025, 0.0) for j in jours]) if E["a_2025"] else None
    g0, d0, t0, b0 = 46, 58, 14, 24
    pas = graduation(max(cj[-1], cn[-1] if cn else 0, c1[-1] if c1 else 0, PARAM.joj_objectif) * 1.06)
    ymax = pas * 4
    X = lambda i: g0 + i * (W - g0 - d0) / max(n - 1, 1)
    Y = lambda v: t0 + (1 - v / ymax) * (H - t0 - b0)
    chemin = lambda vs, i0=0: "M" + " L".join(f"{X(i0 + i):.1f},{Y(v):.1f}" for i, v in enumerate(vs))
    svg = "".join(f'<line x1="{g0}" x2="{W - d0}" y1="{Y(i * pas):.1f}" y2="{Y(i * pas):.1f}" stroke="#E9EFF7"/>'
                  f'<text x="{g0 - 7}" y="{Y(i * pas) + 4:.1f}" text-anchor="end">{compact(i * pas) if i else "0"}</text>' for i in range(5))
    svg += "".join(f'<text x="{X(i):.1f}" y="{H - 6}" text-anchor="middle">{jours[i]:%d/%m}</text>'
                   for i in sorted({0, min(4, n - 1), min(9, n - 1), n - 1}))
    if PARAM.joj_objectif:
        yo = Y(PARAM.joj_objectif)
        svg += (f'<line x1="{g0}" x2="{W - d0}" y1="{yo:.1f}" y2="{yo:.1f}" stroke="#C99A2E" stroke-dasharray="3 3"/>'
                f'<text x="{g0 + 4}" y="{yo - 4:.1f}" fill="#9A6F12" font-weight="700">Objectif</text>')
    if c1:
        svg += f'<path d="{chemin(c1)}" fill="none" stroke="#9AABC6" stroke-width="2" stroke-dasharray="5 4"/>'
        svg += "".join(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="2.4" fill="#9AABC6"/>' for i, v in enumerate(c1) if points and i % 2 == 0)
    if cn:
        svg += f'<path d="{chemin(cn)}" fill="none" stroke="#1B6EF3" stroke-width="2.2"/>'
        svg += "".join(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="2.6" fill="#1B6EF3"/>' for i, v in enumerate(cn) if points and i % 2 == 0)
    if nr:
        svg += f'<path d="{chemin(cj[:nr])}" fill="none" stroke="{ROUGE}" stroke-width="3" stroke-linejoin="round"/>'
    if nr < n:
        i0 = max(nr - 1, 0)
        svg += f'<path d="{chemin(cj[i0:], i0)}" fill="none" stroke="{ROUGE}" stroke-width="3" stroke-dasharray="6 4" stroke-linejoin="round"/>'
    svg += "".join(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="{3.2 if i < nr else 2.6}" fill="{ROUGE if i < nr else "#fff"}" '
                   f'stroke="{ROUGE}" stroke-width="1.6"/>' for i, v in enumerate(cj) if points and (i < nr or i == n - 1))
    if a["dans_jeux"]:
        xa = X((J - JOJ_DEBUT).days)
        svg += (f'<line x1="{xa:.1f}" x2="{xa:.1f}" y1="{t0}" y2="{H - b0}" stroke="{ROUGE}" stroke-dasharray="3 3"/>'
                f'<rect x="{xa - 34:.1f}" y="{t0 - 2}" width="68" height="16" rx="4" fill="{ROUGE}"/>'
                f'<text x="{xa:.1f}" y="{t0 + 9.5}" text-anchor="middle" fill="#fff" font-weight="700" font-size="10">Aujourd\'hui</text>')
    fins = sorted([(Y(cj[-1]), ROUGE, cj[-1])] + ([(Y(cn[-1]), "#1B6EF3", cn[-1])] if cn else [])
                  + ([(Y(c1[-1]), "#7F93B3", c1[-1])] if c1 else []))
    ys: list[float] = []
    for y, _, _ in fins:
        ys.append(max(y, ys[-1] + 14) if ys else y)
    svg += "".join(f'<text x="{W - d0 + 8}" y="{yy + 4:.1f}" fill="{c}" font-weight="800" font-size="12">{compact(v)}</text>'
                   for yy, (_, c, v) in zip(ys, fins))
    for i, j in enumerate(jours):
        svg += survol(X(i), (W - g0 - d0) / max(n - 1, 1) / 2, t0, H - b0, W, f"{JOURS_C[j.weekday()]} {j:%d/%m} · cumul", [
            (ROUGE, "JOJ 2026" + (" (réel)" if i < nr else " (prévu)"), nb(cj[i])),
            ("#1B6EF3", "Normale", nb(cn[i]) if cn else None),
            ("#9AABC6", "2025", nb(c1[i]) if c1 else None)])
    return (f'<svg class="tb-svg" viewBox="0 0 {W} {H}" font-size="10.5" fill="#4A5F86" '
            f'font-family="Plus Jakarta Sans, sans-serif" role="img" aria-label="Fréquentation cumulée pendant les JOJ">{svg}</svg>')


def legende_cumul(a: dict) -> str:
    E = a["E"]
    prevu = "" if a["nr"] == len(a["jours"]) else (" <em>(prévu en pointillés)</em>" if not E["repli"]
                                                  else " <em>(prévu : prévisions, complétées par la moyenne normale)</em>")
    return (f'<div class="tb-jl"><span><i style="background:{ROUGE}"></i>JOJ 2026{prevu}</span>'
            + ('<span><i style="background:#1B6EF3"></i>Période normale</span>' if E["a_normal"] else "")
            + ('<span><i style="background:#9AABC6"></i>Même période 2025</span>' if E["a_2025"] else "") + "</div>")


def html_joj(b: dict, G: list[str], perimetre: str) -> str:
    """Résumé JOJ de la page Fréquentation : 3 indicateurs, courbe cumulée, message « À retenir »."""
    a = joj_analyse(b, G)
    E = a["E"]
    titre, phrase, coul = joj_message(a)
    if a["J"] < JOJ_DEBUT:
        k1 = (f'<b>{nb(E["projection"])}</b><span class="lib">voyageurs prévus sur les Jeux</span>')
    else:
        k1 = (f'<div class="tb-jr-kv"><b>{nb(E["R"])}</b>{"<i></i>" + tendance(E["vs_normal"]) if E["vs_normal"] is not None else ""}</div>'
              f'<span class="lib">voyageurs depuis le début des JOJ{" · vs. normal, à date" if E["vs_normal"] is not None else ""}</span>')
    if E["ecart_2025"] is not None:
        k2 = (f'<b>{"+" if E["ecart_2025"] >= 0 else "−"}{nb(abs(E["ecart_2025"]))}</b>'
              f'<span class="lib">voyageurs {"supplémentaires" if E["ecart_2025"] >= 0 else "en moins"} vs 2025 ({"+" if E["vs_2025"] > 0 else ""}{E["vs_2025"]} %)</span>')
    else:
        k2 = '<b>—</b><span class="lib">comparaison 2025 indisponible</span>'
    if PARAM.joj_objectif:
        atteint = round(E["R"] / PARAM.joj_objectif * 100)
        k3 = (f'<b>{compact(PARAM.joj_objectif)}</b><span class="lib">objectif cumulé · {atteint} % atteint, '
              f'{compact(E["projection"])} projetés</span>')
    else:
        vs = pct(E["projection"], E["normal_total"]) if E["normal_total"] else None
        k3 = (f'<b>{compact(E["projection"])}</b><span class="lib">projection fin des JOJ'
              f'{f" ({chr(43) if vs > 0 else chr(8722)}{abs(vs)} % vs normal)" if vs is not None else ""}</span>')
    ic3 = "trophee"
    return f"""
<div class="tb-card tb-jr">
  <div class="tb-jr-h">
    <span class="tb-jr-tag">JOJ DAKAR 2026</span><i class="sep"></i>
    <div class="tb-jr-t">Impact sur la fréquentation du TER<span>{statut_joj(a["J"])} · {JOJ_DEBUT:%d/%m} → {JOJ_FIN:%d/%m/%Y} · entrées · {esc(cible_txt(perimetre, G))}</span></div>
    {_img_tag("embleme_dakar2026.png", "tb-jr-emb")}
  </div>
  <div class="tb-jr-k">
    <div class="k rose"><span class="ic">{ICO["groupe"]}</span><div>{k1}</div></div>
    <div class="k"><span class="ic rond">{ICO["plus"]}</span><div>{k2}</div></div>
    <div class="k"><span class="ic">{ICO[ic3]}</span><div>{k3}</div></div>
  </div>
  <div class="tb-jr-c"><div class="tb-jr-ct">{ICO["courbe"]}Évolution cumulée de la fréquentation</div>
    {svg_cumul(a)}{legende_cumul(a)}</div>
  <div class="tb-jr-m {coul}">{ICO["barres"]}<b>{titre}</b><i></i><span>{esc(phrase)}</span>
    <a href="?page=joj" target="_self">Voir le détail →</a></div>
</div>"""


# — Page JOJ (détail) —
def svg_quotidien(a: dict, W: int = 760, H: int = 250) -> str:
    """Entrées par jour : 2 semaines avant les Jeux puis toute la période ; bande rose = période des JOJ."""
    E = a["E"]
    jours = a["contexte"] + a["jours"]
    n, J = len(jours), a["J"]
    v26 = [a["reel_contexte"]["E"].get(j) for j in a["contexte"]] + [
        (E["valeurs"][i] if s == "reel" else None) for i, s in enumerate(E["statut"])]
    # prévu (aujourd'hui compris, journée pas finie), relié au dernier jour réel
    prev = [None] * len(a["contexte"]) + [(E["valeurs"][i] if s != "reel" or (i + 1 < len(E["statut"]) and E["statut"][i + 1] != "reel")
                                           else None) for i, s in enumerate(E["statut"])]
    v25 = [E["a25"].get(j - ALIGN_2025) for j in jours] if E["a_2025"] else [None] * n
    vn = [None] * len(a["contexte"]) + [E["normal"][j] for j in a["jours"]] if E["a_normal"] else [None] * n
    vals = [x for s in (v26, prev, v25, vn) for x in s if x]
    pas = graduation(max(vals + [1]) * 1.1)
    ymax = pas * 4
    g0, d0, t0, b0 = 48, 14, 26, 26
    X = lambda i: g0 + (i + .5) * (W - g0 - d0) / n
    Y = lambda v: t0 + (1 - v / ymax) * (H - t0 - b0)

    def courbe(s, coul, ep=2.4, tiret=""):
        segs, cur = [], []
        for i, v in enumerate(s):
            if v is None:
                if len(cur) > 1:
                    segs.append(cur)
                cur = []
            else:
                cur.append((X(i), Y(v)))
        if len(cur) > 1:
            segs.append(cur)
        return "".join(f'<path d="M{" L".join(f"{x:.1f},{y:.1f}" for x, y in sg)}" fill="none" stroke="{coul}" '
                       f'stroke-width="{ep}" stroke-linejoin="round"{tiret}/>' for sg in segs)

    i0 = len(a["contexte"])
    x0, x1 = X(i0) - (W - g0 - d0) / n / 2, X(n - 1) + (W - g0 - d0) / n / 2
    svg = (f'<rect x="{x0:.1f}" y="{t0}" width="{x1 - x0:.1f}" height="{H - t0 - b0}" fill="#FBE7EC" opacity=".75"/>'
           f'<rect x="{(x0 + x1) / 2 - 42:.1f}" y="{t0 + 6}" width="84" height="18" rx="5" fill="#fff" stroke="#F3C3CF"/>'
           f'<text x="{(x0 + x1) / 2:.1f}" y="{t0 + 18.5}" text-anchor="middle" fill="{ROUGE}" font-weight="700">Période JOJ</text>')
    svg += "".join(f'<line x1="{g0}" x2="{W - d0}" y1="{Y(i * pas):.1f}" y2="{Y(i * pas):.1f}" stroke="#E9EFF7"/>'
                   f'<text x="{g0 - 8}" y="{Y(i * pas) + 4:.1f}" text-anchor="end">{compact(i * pas) if i else "0"}</text>' for i in range(5))
    svg += "".join(f'<text x="{X(i):.1f}" y="{H - 7}" text-anchor="middle">{jours[i]:%d/%m}</text>' for i in range(0, n, 4))
    svg += courbe(v25, "#AFBDD3", 2) + courbe(vn, "#1B6EF3", 2.4) + courbe(prev, ROUGE, 2.6, ' stroke-dasharray="6 4"') + courbe(v26, ROUGE, 2.8)
    if J in jours:
        xa = X(jours.index(J))
        svg += f'<line x1="{xa:.1f}" x2="{xa:.1f}" y1="{t0}" y2="{H - b0}" stroke="{ROUGE}" stroke-dasharray="2 3"/>'
    for i, j in enumerate(jours):
        reel_i = v26[i]
        prev_i = prev[i] if reel_i is None else None
        svg += survol(X(i), (W - g0 - d0) / n / 2, t0, H - b0, W, f"{JOURS_C[j.weekday()]} {j:%d/%m/%Y}", [
            (ROUGE, "2026 (réel)" if j != J else "Aujourd’hui (en cours)", nb(reel_i) if reel_i is not None and j != J else None),
            (ROUGE, "2026 (prévu)" if j != J else "Aujourd’hui (estimé)", nb(prev_i) if prev_i is not None else None),
            ("#1B6EF3", "Normale", nb(vn[i]) if vn[i] is not None else None),
            ("#AFBDD3", "2025", nb(v25[i]) if v25[i] is not None else None)])
    return (f'<svg class="tb-svg" viewBox="0 0 {W} {H}" font-size="10.5" fill="#4A5F86" '
            f'font-family="Plus Jakarta Sans, sans-serif" role="img" aria-label="Entrées par jour">{svg}</svg>')


def svg_profil(a: dict, W: int = 380, H: int = 210) -> str:
    """Profil horaire moyen (entrées + sorties) : jour moyen des Jeux vs jour moyen normal."""
    HE = heures_affichees()
    pj = [a["prof_joj"].get(h, 0.0) for h in HE]
    pn = [a["prof_norm"].get(h, 0.0) for h in HE]
    pas = graduation(max(pj + pn + [1]) * 1.1)
    ymax = pas * 4
    g0, d0, t0, b0 = 40, 8, 10, 24
    X = lambda i: g0 + i * (W - g0 - d0) / max(len(HE) - 1, 1)
    Y = lambda v: t0 + (1 - v / ymax) * (H - t0 - b0)
    trace = lambda s: "M" + " L".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(s))
    svg = "".join(f'<line x1="{g0}" x2="{W - d0}" y1="{Y(i * pas):.1f}" y2="{Y(i * pas):.1f}" stroke="#E9EFF7"/>'
                  f'<text x="{g0 - 6}" y="{Y(i * pas) + 4:.1f}" text-anchor="end">{compact(i * pas) if i else "0"}</text>' for i in range(5))
    svg += "".join(f'<text x="{X(i):.1f}" y="{H - 6}" text-anchor="middle">{h}h</text>' for i, h in enumerate(HE) if h % 3 == 0)
    aire = trace(pj) + f" L{X(len(HE) - 1):.1f},{Y(0):.1f} L{X(0):.1f},{Y(0):.1f} Z"
    svg += f'<path d="{aire}" fill="rgba(125,20,47,.10)"/>'
    svg += f'<path d="{trace(pn)}" fill="none" stroke="#1B6EF3" stroke-width="2.2"/>'
    svg += f'<path d="{trace(pj)}" fill="none" stroke="{ROUGE}" stroke-width="2.6" stroke-linejoin="round"/>'
    for i, h in enumerate(HE):
        svg += survol(X(i), (W - g0 - d0) / max(len(HE) - 1, 1) / 2, t0, H - b0, W, f"{h:02d}h–{(h + 1) % 24:02d}h · jour moyen", [
            (ROUGE, "JOJ", nb(pj[i])), ("#1B6EF3", "Normale", nb(pn[i])),
            ("#9AABC6", "Écart", f"{'+' if pj[i] >= pn[i] else ''}{pct(pj[i], pn[i])} %" if pn[i] else None)])
    return (f'<svg class="tb-svg" viewBox="0 0 {W} {H}" font-size="10" fill="#4A5F86" '
            f'font-family="Plus Jakarta Sans, sans-serif" role="img" aria-label="Profil horaire moyen">{svg}</svg>')


def sparkline(ratios: list, coul: str, W: int = 150, H: int = 38) -> str:
    v = [r for r in ratios if r is not None]
    if len(v) < 2:
        return ""
    lo, hi = min(v + [1]) * .97, max(v + [1]) * 1.03
    X = lambda i: 2 + i * (W - 4) / (len(v) - 1)
    Y = lambda r: H - 3 - (r - lo) / ((hi - lo) or 1) * (H - 6)
    return (f'<svg class="tb-spark" viewBox="0 0 {W} {H}" aria-hidden="true">'
            f'<line x1="0" x2="{W}" y1="{Y(1):.1f}" y2="{Y(1):.1f}" stroke="#C9D6E8" stroke-dasharray="3 3"/>'
            f'<path d="M{" L".join(f"{X(i):.1f},{Y(r):.1f}" for i, r in enumerate(v))}" fill="none" stroke="{coul}" stroke-width="2.2"/></svg>')


def html_joj_kpis(a: dict) -> str:
    E, S = a["E"], a["S"]
    tot = E["R"] + S["R"]
    pic = a["pics"].get("E")
    vs_n = E["vs_normal"]
    proj_vs = pct(E["projection"], E["normal_total"]) if E["normal_total"] else None
    signe = lambda v: f'{"+" if v > 0 else ""}{v} %'
    return '<div class="tb-kpis">' + "".join([
        carte_kpi("groupe", "#F6E5EA", ROUGE, ("Voyageurs sur toute la période" if a["J"] > JOJ_FIN else "Voyageurs depuis le début")
                  + " <em>(entrées)</em>",
                  f'<span style="color:{ROUGE}">{nb(E["R"])}</span>',
                  (_delta(vs_n, " %") + '<span class="sm">vs. période normale, à date</span>') if vs_n is not None
                  else f'<span class="sm">{nb(tot)} validations entrées + sorties</span>'),
        carte_kpi("courbe", "#D9F5EA", "#12B97A", "Écart vs 2025 <em>(à date)</em>",
                  signe(E["vs_2025"]) if E["vs_2025"] is not None else "—",
                  f'<span class="sm">{"+" if (E["ecart_2025"] or 0) >= 0 else "−"}{nb(abs(E["ecart_2025"]))} voyageurs</span>'
                  if E["ecart_2025"] is not None else '<span class="sm">pas de données 2025</span>'),
        carte_kpi("horloge", "#F6E5EA", ROUGE, "Pic de fréquentation <em>(entrées)</em>",
                  f'{pic[0]:02d}h – {(pic[0] + 1) % 24:02d}h' if pic else "—",
                  f'<span class="sm">≈ {round(pic[1])} % des entrées du jour, pendant les JOJ</span>' if pic else '<span class="sm">pas encore de données</span>'),
        carte_kpi("barres", "#E3F0FE", "#0A66F0", "Projection fin des JOJ",
                  f'<span style="color:{ROUGE}">{compact(E["projection"])}</span>',
                  f'<span class="sm">{signe(proj_vs) + " vs normal · " if proj_vs is not None else ""}réel + prévisions</span>'),
    ]) + "</div>"


def html_joj_es(a: dict) -> str:
    def bloc(s, lib, coul, fond):
        X = a[s]
        v = X["vs_normal"]
        return (f'<div class="tb-je" style="background:{fond}"><small>{lib}</small>'
                f'<b style="color:{coul}">{("+" if v > 0 else "") + str(v) + " %" if v is not None else "—"}</b>'
                f'<span>vs normal, à date · {nb(X["R"])}</span>{sparkline(a["ratios"][s], coul)}</div>')
    return (f'<div class="tb-card tb-pad"><div class="tb-jt">{ICO["fleches"]}Entrées vs sorties</div>'
            f'<div class="tb-je-g">{bloc("E", "Entrées", "#12915F", "#F0FAF5")}{bloc("S", "Sorties", "#1B6EF3", "#F0F6FE")}</div>'
            f'<div class="tb-jn">Courbes : chaque jour des Jeux rapporté à un jour normal (pointillé = normal).</div></div>')


def html_joj_pics(a: dict) -> str:
    def bloc(s, lib, coul):
        p = a["pics"].get(s)
        if not p:
            return f'<div class="tb-jp"><small>{lib}</small><b>—</b></div>'
        return (f'<div class="tb-jp"><small>{lib}</small><b>{p[0]:02d}h – {(p[0] + 1) % 24:02d}h</b>'
                f'<span>≈ {round(p[1])} % du total du jour</span>'
                f'<span class="tb-barre"><span style="width:{min(100, p[1] * 4):.0f}%;background:{coul}"></span></span></div>')
    return (f'<div class="tb-card tb-pad"><div class="tb-jt">{ICO["horloge"]}Pics de fréquentation <span>(jour moyen des JOJ)</span></div>'
            f'<div class="tb-je-g">{bloc("E", "Entrées", "#17A97B")}{bloc("S", "Sorties", "#1E87FC")}</div></div>')


def html_joj_gares(a: dict) -> str:
    h = a["hausses"]
    if not h:
        return (f'<div class="tb-card tb-pad"><div class="tb-jt">{ICO["trophee"]}Top gares <span>(hausse des entrées)</span></div>'
                '<div class="tb-vide-b">Disponible dès la fin du premier jour des Jeux.</div></div>')
    top = h[:5]
    mx = max(abs(p) for _, p, _ in top) or 1
    lignes = "".join(
        f'<div class="tb-jg"><span class="r">{i + 1}.</span><span class="nm" title="{esc(g)}">{esc(g)}</span>'
        f'<b class="{"up" if p >= 0 else "down"}">{"+" if p > 0 else ""}{p} %</b>'
        f'<span class="tb-barre"><span style="width:{abs(p) / mx * 100:.0f}%;background:{"#D46A86" if p >= 0 else "#9AABC6"}"></span></span>'
        f'<small>{"+" if v >= 0 else "−"}{nb(abs(v))} voyageurs</small></div>' for i, (g, p, v) in enumerate(top))
    return (f'<div class="tb-card tb-pad"><div class="tb-jt">{ICO["trophee"]}Top gares <span>(hausse des entrées vs normal)</span></div>'
            f'{lignes}</div>')


@st.fragment(run_every=RAFRAICHISSEMENT)
def page_joj():
    titre, sous = "JOJ Dakar 2026", "Impact sur la fréquentation du TER"
    try:
        b = freq_base()
    except ErreurDonnees as e:
        md(html_entete(maintenant(), titre, sous, etat_donnees(None, erreur=True)))
        md(html_erreur(e.titre, e.detail, e.conseil))
        return
    md(html_entete(b["now"], titre, sous, etat_donnees(b)))
    opts = b["options"]
    if ss.get("zone_joj") not in opts:
        ss["zone_joj"] = TOUTES
    with st.container(key="bloc_ctrl"):
        c1, c2 = st.columns([1.3, 2.1], vertical_alignment="center")
        perimetre = c1.selectbox("Périmètre", list(opts), key="zone_joj", label_visibility="collapsed")
        G = opts[perimetre]
        md(f'<div class="tb-ctrl-t">Période des Jeux : {JOJ_DEBUT:%d/%m} → {JOJ_FIN:%d/%m/%Y} · {statut_joj(b["J"])} · '
           f'comparaisons « à date », arrêtées à la dernière validation reçue ({hh(b["t_ref"])})</div>', c2)
    try:
        a = joj_analyse(b, G)
    except ErreurDonnees as e:
        md(html_erreur(e.titre, e.detail, e.conseil))
        return
    md(html_joj_kpis(a))
    g, d = st.columns([2.05, 1])
    md(f'<div class="tb-card tb-pad"><div class="tb-ch"><div class="tb-jt">{ICO["barres"]}Évolution des validations '
       f'<span>(entrées par jour · {esc(cible_txt(perimetre, G))})</span></div>'
       f'<div class="tb-jl"><span><i style="background:{ROUGE}"></i>JOJ 2026</span>'
       f'<span><i style="background:{ROUGE};opacity:.45"></i>Prévu</span>'
       + (f'<span><i style="background:#1B6EF3"></i>Période normale</span>' if a["E"]["a_normal"] else "")
       + (f'<span><i style="background:#AFBDD3"></i>2025</span>' if a["E"]["a_2025"] else "")
       + f'</div></div>{svg_quotidien(a)}</div>', g)
    md(f'<div class="tb-card tb-pad"><div class="tb-jt">{ICO["horloge"]}Profil horaire <span>(jour moyen, entrées + sorties)</span></div>'
       f'{svg_profil(a)}<div class="tb-jl"><span><i style="background:{ROUGE}"></i>Pendant les JOJ</span>'
       f'<span><i style="background:#1B6EF3"></i>Période normale</span></div></div>', d)
    c1, c2, c3 = st.columns(3)
    md(html_joj_es(a), c1)
    md(html_joj_pics(a), c2)
    md(html_joj_gares(a), c3)
    titre_m, phrase, coul = joj_message(a)
    md(f'<div class="tb-jr-m {coul} seul">{ICO["ampoule"]}<b>À retenir</b><i></i><span>{esc(titre_m)} : {esc(phrase)}</span></div>')
    md('<div class="tb-jn">Période normale : même jour de la semaine, moyenne des '
       f'{b["N"]} semaines avant les Jeux. 2025 : même jour de la semaine un an plus tôt. '
       'Toutes les comparaisons s\'arrêtent à la même heure que la dernière validation reçue.</div>')


def pics(vals: list[float], seuil: float = .45, ecart: int = 4, maxi: int = 4) -> list[int]:
    """Indices des pics locaux significatifs, espacés d'au moins « ecart » points."""
    m = max(vals, default=0) or 1
    cand = [i for i in range(1, len(vals) - 1) if vals[i] >= vals[i - 1] and vals[i] > vals[i + 1] and vals[i] >= seuil * m]
    garde: list[int] = []
    for i in sorted(cand, key=lambda i: -vals[i]):
        if all(abs(i - j) >= ecart for j in garde):
            garde.append(i)
    return sorted(garde[:maxi])


def html_prevision(b: dict, G: list[str], perimetre: str) -> str:
    """Réel des 12 dernières heures, rythme des 60 dernières minutes, puis prévision des 24 heures suivantes.
    Chaque point est placé au milieu de l'heure qu'il représente."""
    t_ref, J = b["t_ref"], b["J"]
    h0 = t_ref.replace(minute=0, second=0, microsecond=0)
    frac = (t_ref - h0).total_seconds() / 3600
    at = lambda k: h0 + timedelta(hours=k)
    df_h = d_horaire_recent(b["cle"], at(-12), h0)
    Hr = {}
    if not df_h.empty:
        d = df_h[(df_h["sens"] == "E") & df_h["gare"].isin(list(G))]
        for (j, h), v in d.groupby(["jour", "heure"])["nb"].sum().items():
            Hr[minuit(j) + timedelta(hours=int(h))] = float(v)
    hist = [Hr.get(at(k), 0.0) for k in range(-12, 0)]
    rythme = _somme(b["F"].get("m60", {}), G, "E")
    P = previsions_par_heure(b, G)
    prev = [P.get(at(k)) for k in range(1, 25)]
    dispo = [(k, p) for k, p in zip(range(1, 25), prev) if p is not None]

    pts = [(k + .5, v, False) for k, v in zip(range(-12, 0), hist)] + [(frac - .5, rythme, False)]
    pts += [(k + .5, p[0], True) for k, p in dispo]
    bande = [(k + .5, p[1], p[2]) for k, p in dispo if pd.notna(p[1]) and pd.notna(p[2])]
    vmax = max([v for _, v, _ in pts] + [h for _, _, h in bande] + [1])
    pas = graduation(vmax * 1.15)
    ymax = pas * 4
    W, H, g0, b0, t0 = 760, 232, 50, 26, 34
    X = lambda x: g0 + (x + 12) * (W - g0 - 14) / 37
    Y = lambda v: t0 + (1 - v / ymax) * (H - t0 - b0)
    trace = lambda ps: "M" + " L".join(f"{X(x):.1f},{Y(v):.1f}" for x, v in ps)
    axes = "".join(f'<line x1="{g0}" x2="{W - 14}" y1="{Y(i * pas):.1f}" y2="{Y(i * pas):.1f}" stroke="#E9EFF7"/>'
                   f'<text x="{g0 - 8}" y="{Y(i * pas) + 4:.1f}" text-anchor="end">{nb(i * pas)}</text>' for i in range(5))
    xs = "".join(f'<text x="{X(k):.1f}" y="{H - 6}" text-anchor="middle">{at(k):%H}h</text>' for k in range(-12, 25, 6))
    reel_pts = [(x, v) for x, v, f in pts if not f]
    svg = ""
    # Prévision en tronçons d'heures consécutives (une heure sans prévision coupe la courbe et la bande) ;
    # le premier tronçon part du point « maintenant ».
    troncons, cur, k_prec = [], [], 0
    for k, p in dispo:
        if cur and k != k_prec + 1:
            troncons.append(cur)
            cur = []
        cur.append((k, p))
        k_prec = k
    if cur:
        troncons.append(cur)
    for i, tr in enumerate(troncons):
        bornes = [(k + .5, p[1], p[2]) for k, p in tr if pd.notna(p[1]) and pd.notna(p[2])]
        if len(bornes) > 1:
            poly = (trace([(x, hb) for x, _, hb in bornes]) + " L"
                    + " L".join(f"{X(x):.1f},{Y(bb):.1f}" for x, bb, _ in reversed(bornes)) + " Z")
            svg += f'<path d="{poly}" fill="rgba(27,110,243,.12)" stroke="none"/>'
        ligne = ([reel_pts[-1]] if i == 0 and tr[0][0] == 1 else []) + [(k + .5, p[0]) for k, p in tr]
        if len(ligne) > 1:
            svg += (f'<path d="{trace(ligne)}" fill="none" stroke="#1B6EF3" stroke-width="2.6" stroke-dasharray="7 5" '
                    f'stroke-linejoin="round"/>')
    svg += f'<path d="{trace(reel_pts)}" fill="none" stroke="#0B1F55" stroke-width="2.2" stroke-linejoin="round"/>'
    xm = X(frac)
    mot = "Dernières données" if b["en_retard"] else "Maintenant"
    svg += (f'<line x1="{xm:.1f}" x2="{xm:.1f}" y1="{t0 - 6}" y2="{H - b0}" stroke="#7D142F" stroke-width="1.5"/>'
            f'<circle cx="{X(frac - .5):.1f}" cy="{Y(rythme):.1f}" r="4.5" fill="#7D142F" stroke="#fff" stroke-width="2"/>'
            f'<text x="{xm + 6:.1f}" y="{H - b0 - 6}" fill="#7D142F" font-weight="700" stroke="#fff" stroke-width="4" '
            f'stroke-linejoin="round" paint-order="stroke">{mot}</text>')
    for i in pics([v for _, v, _ in pts]):
        x, v, futur = pts[i]
        heure = at(math.floor(x)) if i != 12 else t_ref
        txt = f"{heure:%H}h · {nb(v)}"
        w = len(txt) * 6.4 + 14
        rx, ry = min(max(X(x) - w / 2, g0), W - 14 - w), max(2, Y(v) - 29)
        coul = ROUGE if futur else "#0B1F55"
        svg += (f'<circle cx="{X(x):.1f}" cy="{Y(v):.1f}" r="4.5" fill="{coul}" stroke="#fff" stroke-width="2"/>'
                f'<rect x="{rx:.1f}" y="{ry:.1f}" width="{w:.1f}" height="19" rx="6" fill="{coul}"/>'
                f'<text x="{rx + w / 2:.1f}" y="{ry + 13.5:.1f}" text-anchor="middle" fill="#fff" font-weight="700">{txt}</text>')
    bornes_h = {round(k + .5, 2): (p[1], p[2]) for k, p in dispo}
    for i, (x, v, futur) in enumerate(pts):
        if i == 12:
            titre_b, lib = f"Dernière heure reçue (jusqu'à {hh(t_ref)})", "Réel"
        else:
            h_ = at(math.floor(x))
            titre_b = f"{'Demain' if h_.date() > J else 'Aujourd’hui' if h_.date() == J else 'Hier'} {h_:%H}h–{h_ + timedelta(hours=1):%H}h"
            lib = "Prévision" if futur else "Réel"
        lo, hi = bornes_h.get(round(x, 2), (None, None)) if futur else (None, None)
        svg += survol(X(x), (W - g0 - 14) / 37 / 2, t0 - 6, H - b0, W, titre_b, [
            (ROUGE if futur else "#0B1F55", lib, f"{nb(v)} entrées"),
            ("#9DBBF5", "Intervalle", f"{nb(lo)} – {nb(hi)}" if futur and pd.notna(lo) and pd.notna(hi) else None)])
    cible = cible_txt(perimetre, G)
    tag_joj = '<span class="tb-fc-tag joj">JOJ</span>' if any(est_joj(at(k).date()) for k in range(1, 25)) else ""
    leg_bande = '<span><i class="z"></i>Intervalle de prévision</span>' if bande else ""
    if dispo:
        k1, p1 = dispo[0]
        kp, pp = max(dispo, key=lambda kp_: kp_[1][0])
        jour_pic = "aujourd'hui" if at(kp).date() == J else "demain"
        total = sum(p[0] for _, p in dispo)
        # Heures de service sans prévision (les heures de fermeture n'en ont pas besoin)
        manque = [k for k in range(1, 25) if prev[k - 1] is None and en_service(at(k))]
        couv = f'<em> · {len(manque)} h de service sans prévision</em>' if manque else ""
        prevu_auj = sum(v for t, (v, _, _) in P.items() if t.date() == J and t < h0) + P.get(h0, (0.0,))[0] * frac
        reel_auj = _somme(b["F"].get("jour", {}), G, "E")
        ecart = (f'réel du jour : {pct(reel_auj, prevu_auj):+d} % vs. prévu' if prevu_auj > 0
                 else "pas de prévision pour la journée écoulée")
        cote = f"""
      <div class="pic"><small>Prochaine heure ({at(k1):%H}h–{at(k1 + 1):%H}h)</small><b>{nb(p1[0])} <em>entrées</em></b><span>{_delta(pct(p1[0], rythme), " %")} vs. rythme actuel</span></div>
      <div class="pic"><small>Pic prévu</small><b>{nb(pp[0])} <em>entrées/h</em></b><span>{jour_pic} à {at(kp):%H}h</span></div>
      <div class="pic"><small>Total prévu sur 24 h</small><b>{nb(total)} <em>entrées</em></b><span>{ecart}{couv}</span></div>"""
    else:
        cote = ('<div class="tb-fc-vide"><b>Aucune prévision</b> pour les 24 prochaines heures.<br>'
                'Vérifier que la table des prévisions couvre cette période (requête sql/2_previsions.sql).</div>')
    return f"""
<div class="tb-card tb-fc">
  <div class="tb-fc-h"><span class="tb-fc-tag">Prévision</span>{tag_joj}
    <div class="tb-ct">Fréquentation des 24 prochaines heures <span>(entrées par heure · {esc(cible)})</span></div>
    <div class="tb-pv-l"><span><i class="r"></i>Réel</span><span><i class="p"></i>Prévision</span>{leg_bande}<span><b class="pk"></b>Pics</span></div></div>
  <div class="tb-fc-b">
    <svg class="tb-svg" viewBox="0 0 {W} {H}" font-size="11" fill="#4A5F86" font-family="Plus Jakarta Sans, sans-serif">
      {axes}{xs}{svg}
    </svg>
    <div class="tb-fc-st">{cote}
    </div>
  </div>
</div>"""


def html_top5(b: dict, G: list[str]) -> str:
    """Deux classements distincts du cumul du jour : top 5 en entrées, top 5 en sorties,
    chacun trié par son propre nombre de validations (barre = part de la gare n° 1)."""
    jour = b["F"].get("jour", {})

    def classement(sens: str, libelle: str, coul: str) -> str:
        vals = {g: _somme(jour, [g], sens) for g in G}
        total = sum(vals.values()) or 1
        top = sorted(G, key=lambda g: vals[g], reverse=True)[:5]
        mx = vals[top[0]] if top and vals[top[0]] else 1
        lignes = "".join(
            f'<div class="tb-t5"><span class="tb-rang" style="background:{RANGS[i]}">{i + 1}</span>'
            f'<span class="nm" title="{esc(g)}">{esc(g)}</span>'
            f'<div class="tb-t5-b"><span class="tb-barre"><span style="width:{vals[g] / mx * 100:.0f}%;background:{coul}"></span></span>'
            f'<b>{nb(vals[g])}</b><small>{round(vals[g] / total * 100)} %</small></div></div>'
            for i, g in enumerate(top))
        return (f'<div class="tb-t5-col"><div class="tb-t5 tb-t5-h"><span>Rang</span><span>Gare</span>'
                f'<span><i style="background:{coul}"></i>{libelle} <em>· part du jour</em></span></div>{lignes}</div>')

    titre = "Top 5 des gares" if len(G) > 5 else "Classement des gares"
    return f"""
<div class="tb-card tb-pad">
  <div class="tb-ch"><div class="tb-ct">{titre} <span>(cumul du jour · trié par nombre de validations)</span></div></div>
  <div class="tb-t5-g">{classement("E", "Entrées", "#17A97B")}{classement("S", "Sorties", "#1E87FC")}</div>
</div>"""


def html_flux(b: dict, G: list[str]) -> str:
    m60 = b["F"].get("m60", {})
    e = {g: _somme(m60, [g], "E") for g in G}
    s = {g: _somme(m60, [g], "S") for g in G}
    entete = (f'<div class="tb-ch"><div class="tb-ct">Flux par gare <span>(60 dernières minutes, jusqu\'à {hh(b["t_ref"])})</span></div>'
              '<div class="tb-leg"><span><i style="background:#17A97B"></i>Entrées</span>'
              '<span><i style="background:#1E87FC"></i>Sorties</span></div></div>')
    if len(G) > 8:  # nombreuses gares : barres horizontales, une ligne par gare (noms lisibles)
        mx = max(list(e.values()) + list(s.values()) + [1])
        barre = lambda v, c: (f'<div><span class="tb-barre"><span style="width:{v / mx * 100:.1f}%;background:{c}"></span></span>'
                              f'<b>{nb(v)}</b></div>')
        lignes = "".join(f'<div class="tb-fh-r"><span class="nm" title="{esc(g)}">{esc(g)}</span>'
                         f'{barre(e[g], "#17A97B")}{barre(s[g], "#1E87FC")}</div>' for g in G)
        return (f'<div class="tb-card tb-pad">{entete}<div class="tb-fh">'
                '<div class="tb-fh-r tb-fh-h"><span>Gare</span><span>Entrées</span><span>Sorties</span></div>'
                f'{lignes}</div></div>')
    pas = graduation(max(list(e.values()) + list(s.values()) + [1]) * 1.1)
    haut = pas * 4
    grille = "".join(f'<div class="tb-gl" style="bottom:{i * 25}%"><span>{nb(i * pas)}</span></div>' for i in range(5))
    cols = f"grid-template-columns:repeat({len(G)},minmax(0,1fr))"
    barres = "".join(
        f'<div class="tb-grp"><i style="height:{e[g] / haut * 100:.1f}%;background:#17A97B" title="{esc(g)} · entrées : {nb(e[g])}"></i>'
        f'<i style="height:{s[g] / haut * 100:.1f}%;background:#1E87FC" title="{esc(g)} · sorties : {nb(s[g])}"></i></div>' for g in G)
    noms = "".join(f'<span>{esc(g)}</span>' for g in G)
    return f"""
<div class="tb-card tb-pad">
  {entete}
  <div class="tb-plot" style="{cols}">{grille}{barres}</div>
  <div class="tb-xl" style="{cols}">{noms}</div>
</div>"""


def texte_horaires(joj: bool) -> str:
    hx = lambda h: f"{h % 24:02d}h"
    if joj:
        o, f = HORAIRES_JOJ
        return f"Horaires JOJ : {hx(o)} – {hx(f)}, 7 j/7, dimanches compris"
    (o1, f1), (o7, f7) = HORAIRES_NORMAUX[0], HORAIRES_NORMAUX[6]
    return f"Horaires habituels : {hx(o1)} – {hx(f1)} du lundi au samedi, {hx(o7)} – {hx(f7)} le dimanche"


def heures_affichees() -> list[int]:
    o = min([o for o, _ in HORAIRES_NORMAUX] + ([HORAIRES_JOJ[0]] if PARAM.joj_afficher else []))
    f = max([f for _, f in HORAIRES_NORMAUX] + ([HORAIRES_JOJ[1]] if PARAM.joj_afficher else []))
    return list(range(o, f))


def html_heatmap(b: dict, G: list[str], mode: str) -> str:
    """Affluence moyenne par jour de la semaine et par heure, sur les semaines de référence.
    La moyenne d'un jour de semaine ne compte que les jours où des validations ont été reçues."""
    J, N, now = b["J"], b["N"], b["now"]
    df = d_horaire_historique(b["cle"], minuit(J - timedelta(days=7 * N)), minuit(J))
    if df.empty:
        return f'<div class="tb-vide-b">Pas encore de validations sur les {N} dernières semaines.</div>'
    sens = {"Entrées": "E", "Sorties": "S"}.get(mode, "ES")
    unite = {"Entrées": "entrées", "Sorties": "sorties"}.get(mode, "passages")
    n_js = Counter(j.weekday() for j in set(df["jour"]))
    d = df[df["sens"].isin(list(sens)) & df["gare"].isin(list(G))]
    tot = Counter()
    for (j, h), v in d.groupby(["jour", "heure"])["nb"].sum().items():
        tot[(j.weekday(), int(h))] += float(v)
    HE = heures_affichees()
    M = [[tot[(js, h)] / n_js[js] if n_js[js] else 0.0 for h in HE] for js in range(7)]
    mx = max(max(r) for r in M) or 1
    cases = ""
    for js, jour in enumerate(JOURS_C):
        o, f = HORAIRES_NORMAUX[js]
        cases += f"<span>{jour}</span>"
        for h, v in zip(HE, M[js]):
            if v < .5 and not o <= h < f:
                cases += f'<i class="ferme" title="{jour} {h}h · fermé"></i>'
                continue
            vif = ";outline:2px solid #0B1F55;outline-offset:1px" if (js, h) == (now.weekday(), now.hour) else ""
            cases += f'<i style="background:{couleur_echelle(v / mx)}{vif}" title="{jour} {h}h · {nb(v)} {unite}/h en moyenne"></i>'
    cases += "<span></span>" + "".join(f'<span class="hx">{h}h</span>' if h % 2 else "<span></span>" for h in HE)
    leg = "".join(f'<span><i style="background:{couleur_echelle(v)}"></i>{n}</span>'
                  for n, v in (("Faible", .1), ("Modérée", .45), ("Élevée", .75), ("Très élevée", 1)))
    leg += '<span><i class="ferme"></i>Fermé</span>'
    leg += '<span><i style="background:#fff;outline:2px solid #0B1F55;border-radius:2px"></i>Maintenant</span>'
    grille = f"grid-template-columns:34px repeat({len(HE)},minmax(0,1fr))"
    return f'<div class="tb-heat" style="{grille}">{cases}</div><div class="tb-hl">{leg}</div>'


# — Serpent de charge —
def html_charge_kpis(trains: list[dict], charges: dict, ch: dict, sens_vis: tuple) -> str:
    n = len(trains)
    bord = sum(charges[t["id"]][0] for t in trains)
    taux = bord / (n * CAPACITE_RAME) * 100 if n else 0
    segs = []
    for i in range(len(NOMS) - 1):
        if 1 in sens_vis:
            segs.append((f"{NOMS[i]} → {NOMS[i + 1]}", ch["pct_a"][i], ch["aller"][i]))
        if -1 in sens_vis:
            segs.append((f"{NOMS[i + 1]} → {NOMS[i]}", ch["pct_r"][i], ch["retour"][i]))
    nom, p, f = max(segs, key=lambda x: x[1])
    return '<div class="tb-kpis">' + "".join([
        carte_kpi("groupe", "#D9F5EA", "#12B97A", "Voyageurs à bord <em>(estimés)</em>", nb(bord),
                  f'<span class="sm">dans {n} train(s) en ligne</span>'),
        carte_kpi("pourcent", "#E3F0FE", "#0A6CF5", "Taux de charge moyen", f"{round(taux)} %",
                  f'<span class="sm">{CAPACITE_RAME} places par rame</span>'),
        carte_kpi("reseau", "#F6E5EA", ROUGE, "Tronçon le plus chargé", f"{round(p)} %",
                  f'<span class="sm">{nom} · {nb(f)} voy./h</span>'),
        carte_kpi("train", "#E3F0FE", "#0A66F0", "Trains en circulation", f"{n}",
                  f'<span class="sm">{dec(ch["freq"])} trains/h par sens (GTFS) · parc {PARC_RAMES} rames</span>'),
    ]) + "</div>"


def html_serpent(ch: dict, sens_vis: tuple) -> str:
    n = len(NOMS) - 1
    dense = n > 6
    etiquettes = "".join(
        f'<span class="{"bas" if dense and i % 2 else ""}" style="left:{i / n * 100:.2f}%">{esc(nom)}</span>'
        for i, nom in enumerate(NOMS))

    def rangee(lib, pcts, flux):
        return f'<div class="s">{lib}</div>' + "".join(
            f'<div class="c" title="{nb(v)} voyageurs/h · {round(p)} %"><i style="background:{niveau(p)}"></i>'
            f'<small>{round(p)} %{"" if dense else f" · {nb(v)} voy./h"}</small></div>'
            for p, v in zip(pcts, flux))

    rangs = ((rangee("Dakar → AIBD", ch["pct_a"], ch["aller"]) if 1 in sens_vis else "")
             + (rangee("AIBD → Dakar", ch["pct_r"], ch["retour"]) if -1 in sens_vis else ""))
    grille = f"grid-template-columns:96px repeat({n},minmax(0,1fr))"
    return f"""
<div class="tb-card tb-pad">
  <div class="tb-ch"><div class="tb-ct">Charge par tronçon <span>(dernière fenêtre validée · 60 min)</span></div>
    <span class="tb-ctrl-t">Offre : {dec(ch["freq"])} trains/h par sens × {CAPACITE_RAME} places{" · survoler un tronçon pour les voyageurs/h" if dense else ""}</span></div>
  <div class="tb-serp" style="{grille}"><div></div><div class="noms{" dense" if dense else ""}">{etiquettes}</div>{rangs}</div>
</div>"""


def html_detail_charge(trains: list[dict], charges: dict) -> str:
    lignes = ""
    for t in trains:
        voy, p = charges[t["id"]]
        s, c = statut(t)
        lignes += (f'<tr><td><span class="tb-tag" style="background:{t["couleur"]}">{t["id"]}</span></td><td class="lg">{ligne(t)}</td>'
                   f'<td>{t["position"]}</td><td>{t["prochain"]}</td><td style="font-weight:600">{nb(voy)}</td>'
                   f'<td><div class="tb-cg"><span class="tb-barre"><span style="width:{min(p, 100):.0f}%;background:{niveau(p)}"></span></span>'
                   f'<b>{round(p)} %</b></div></td><td><span class="tb-st {c}">{ICO["train"]}{s}</span></td></tr>')
    if not trains:
        lignes = '<tr><td colspan="7" style="text-align:center;color:#5B6E93">Aucun train ne correspond au filtre.</td></tr>'
    cols = "".join(f'<col style="width:{w}%">' for w in (9, 15, 21, 14, 11, 17, 13))
    return f"""
<div class="tb-card tb-pad">
  <div class="tb-ct">Détail par train</div>
  <table class="tb-tab"><colgroup>{cols}</colgroup>
    <thead><tr><th>Train</th><th>Ligne</th><th>Position</th><th>Prochain arrêt</th><th>Voyageurs (est.)</th><th>Charge</th><th>Statut</th></tr></thead>
    <tbody>{lignes}</tbody></table>
</div>"""


def html_montees(ch: dict) -> str:
    mx = max(ch["E"] + ch["S"]) or 1
    barre = lambda v, c: (f'<div class="tb-md-c"><span class="tb-barre"><span style="width:{v / mx * 100:.0f}%;background:{c}"></span></span>'
                          f'<b>{nb(v)}</b></div>')
    rangs = "".join(f'<div class="tb-md"><span>{n}</span>{barre(e, "#17A97B")}{barre(s, "#1E87FC")}</div>'
                    for n, e, s in zip(NOMS, ch["E"], ch["S"]))
    return ('<div class="tb-card tb-pad"><div class="tb-ct">Montées / descentes <span>(fenêtre validée de 60 min)</span></div>'
            '<div class="tb-md tb-md-t"><span></span><span>Montées</span><span>Descentes</span></div>' + rangs + '</div>')


# — Écran TV (fréquentation en grand, pour les écrans muraux) —
CSS_TV = r"""
.block-container, [data-testid="stMainBlockContainer"] { padding: 0 !important; max-width: none !important; }
[data-testid="stAppViewContainer"], [data-testid="stMain"] { overflow: hidden; }
.st-key-quitter_tv { position: fixed !important; right: 10px; bottom: 6px; z-index: 1001; width: auto !important; opacity: .25; transition: opacity .15s; }
.st-key-quitter_tv:hover { opacity: 1; }
.st-key-quitter_tv button { min-height: 0 !important; padding: 3px 10px !important; border: 1px solid #DCE6F2 !important; border-radius: 8px !important;
  background: #fff !important; color: #4A5F86 !important; }
.st-key-quitter_tv button p { font-size: 11px !important; font-weight: 600; }
/* Écran conçu pour 1920 × 1080. Le petit script AJUSTE_TV calcule le zoom pour qu'il tienne en entier
   dans la fenêtre (largeur ET hauteur) ; les règles ci-dessous ne servent que si le script ne s'exécute pas. */
.stApp, [data-testid="stAppViewContainer"] { background: #F4F8FD !important; }
[data-testid="stIFrame"], .stElementContainer:has(> iframe[height="0"]), .element-container:has(iframe[height="0"]) { display: none !important; }
.tvx-w { margin-top: -20px; display: flex; justify-content: center; }
.tvx { width: 1920px; height: 1080px; overflow: hidden; background: #F4F8FD; color: #0B1F55; zoom: .66; }
@media (min-width: 1360px) { .tvx { zoom: .708; } }
@media (min-width: 1440px) { .tvx { zoom: .75; } }
@media (min-width: 1536px) { .tvx { zoom: .8; } }
@media (min-width: 1600px) { .tvx { zoom: .833; } }
@media (min-width: 1680px) { .tvx { zoom: .875; } }
@media (min-width: 1920px) { .tvx { zoom: 1; } }
@media (min-width: 2560px) { .tvx { zoom: 1.333; } }
@media (min-width: 3840px) { .tvx { zoom: 2; } }
@media (max-height: 1000px) and (min-width: 1680px) { .tvx { zoom: .9; } }
@media (max-height: 900px) and (min-width: 1440px) { .tvx { zoom: .8; } }
@media (max-height: 800px) { .tvx { zoom: .7; } }
@media (max-height: 700px) { .tvx { zoom: .6; } }
.tvx * { box-sizing: border-box; }
.tvx .n { font-weight: 600; font-variant-numeric: tabular-nums; letter-spacing: -.01em; }
/* En-tête : même charte que les autres pages (fond clair, titre bleu nuit, bannière Ayo à droite) */
.tvx-h { position: relative; display: flex; align-items: center; gap: 28px; height: 112px; padding: 0 0 0 30px; overflow: hidden;
  background: linear-gradient(90deg, #F8FBFF 0%, #F3F8FE 55%, #EBF4FE 100%); border-bottom: 1px solid #E3EBF5; box-shadow: 0 8px 18px -14px rgba(16,42,90,.25); }
.tvx-logo { flex: none; display: flex; align-items: center; }
.tvx-logo img { display: block; width: auto; height: 46px; }
.tvx-sep { flex: none; width: 1px; height: 58px; background: #D5E0EE; }
.tvx-t b { display: block; font-size: 30px; font-weight: 800; letter-spacing: -.02em; line-height: 1.15; white-space: nowrap; }
.tvx-t span { display: block; margin-top: 6px; font-size: 16px; font-weight: 500; color: #1B3266; white-space: nowrap; }
.tvx-live { flex: none; display: flex; flex-direction: column; gap: 10px; }
.tvx-live-l { display: flex; align-items: center; gap: 10px; font-size: 15px; font-weight: 600; white-space: nowrap; }
.tvx-live-l i { width: 10px; height: 10px; border-radius: 50%; }
.tvx-live-d { display: flex; align-items: center; gap: 8px; font-size: 14px; color: #3D5585; white-space: nowrap; }
.tvx-live-d svg { width: 15px; height: 15px; }
.tvx-joj { flex: none; padding: 8px 16px; border-radius: 999px; background: #7D142F; color: #fff; font-size: 15px; font-weight: 700; letter-spacing: .02em; white-space: nowrap; }
.tvx-clock { flex: none; margin-left: auto; text-align: right; }
.tvx-clock b { display: block; font-size: 42px; font-weight: 600; line-height: 1; letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
.tvx-clock span { font-size: 14px; color: #3D5585; }
.tvx-h img.banniere { flex: none; align-self: flex-end; width: auto; height: 108px; margin-right: 18px; }
.tvx-p { display: grid; grid-template-rows: 172px minmax(0, 1fr) 104px; gap: 16px; height: 968px; padding: 18px 24px 20px; }
.tvx-carte { background: #fff; border: 1px solid #EBF1F8; border-radius: 16px; box-shadow: 0 1px 2px rgba(16,42,90,.04), 0 6px 18px rgba(16,42,90,.05); }
.tvx-ct { display: flex; align-items: baseline; gap: 12px; font-size: 20px; font-weight: 700; white-space: nowrap; }
.tvx-ct small { font-size: 14px; font-weight: 400; color: #22396E; }
/* 4 indicateurs : pastille ronde colorée + valeur + mini-courbe, comme les cartes de la page Fréquentation */
.tvx-k { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.tvx-kt { position: relative; display: flex; gap: 18px; padding: 22px 24px; min-width: 0; overflow: hidden; }
.tvx-vague { position: absolute; right: 0; bottom: 0; width: 100%; height: 100%; pointer-events: none; }
.tvx-ic { flex: none; display: grid; place-items: center; width: 58px; height: 58px; border-radius: 50%; }
.tvx-ic svg { width: 30px; height: 30px; }
.tvx-kx { min-width: 0; flex: 1; }
.tvx-kl { font-size: 16px; font-weight: 500; white-space: nowrap; } .tvx-kl em { font-style: normal; color: #3D5585; }
.tvx-kv { margin: 6px 0 10px; font-size: 50px; line-height: 1.05; white-space: nowrap; }
.tvx-kv u { text-decoration: none; margin-left: 8px; font-size: 18px; color: #4A5F86; }
.tvx-kd { display: flex; align-items: center; gap: 8px; font-size: 15px; color: #22396E; white-space: nowrap; overflow: hidden; }
.tvx-kd .ch { padding: 3px 10px; border-radius: 999px; font-weight: 600; }
.tvx-sp { position: absolute; right: 22px; top: 62px; }
.ch.up { background: #E3F6EE; color: #12915F; } .ch.down { background: #FBE9ED; color: #7D142F; } .ch.eq { background: #EEF2F8; color: #4A5F86; }
.tvx .up { color: #12915F; } .tvx .down { color: #7D142F; } .tvx .eq { color: #4A5F86; }
.tvx-kt.fort { border: 0; color: #fff; background: radial-gradient(120% 120% at 100% 0%, #9B2347 0%, #7D142F 45%, #63102A 100%); box-shadow: 0 10px 26px rgba(125,20,47,.22); }
.tvx-kt.fort .tvx-ic { background: rgba(255,255,255,.16); color: #fff; } 
.tvx-kt > .tvx-ic, .tvx-kt > .tvx-kx { position: relative; }
.tvx-kt.fort .tvx-kl em, .tvx-kt.fort .tvx-kd { color: #F5DCE3; } .tvx-kt.fort .tvx-kv u { color: #F5DCE3; }
.tvx-m { display: grid; grid-template-columns: 1.46fr .8fr 1.2fr; gap: 16px; min-height: 0; }
/* Gares les plus affluentes : même bloc que la page Fréquentation */
.tvx-bloc { position: relative; overflow: hidden; display: flex; flex-direction: column; min-height: 0; padding: 18px 20px 16px; border-radius: 18px; color: #fff;
  background: radial-gradient(120% 90% at 100% 0%, #9B2347 0%, #7D142F 45%, #63102A 100%); box-shadow: 0 8px 22px rgba(125,20,47,.18); }
.tvx-deco { position: absolute; right: -24px; top: -34px; width: 300px; opacity: .13; filter: brightness(0) invert(1); pointer-events: none; }
.tvx-bt { position: relative; display: flex; align-items: center; gap: 14px; }
.tvx-bt .ic { flex: none; display: grid; place-items: center; width: 50px; height: 50px; border-radius: 13px; background: rgba(255,255,255,.14); }
.tvx-bt .ic svg { width: 28px; height: 28px; }
.tvx-bt b { display: block; font-size: 19px; font-weight: 800; letter-spacing: .02em; }
.tvx-bt span { display: block; margin-top: 3px; font-size: 14px; color: #F5DCE3; }
.tvx-bg { position: relative; flex: 1; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin-top: 14px; min-height: 0; }
.tvx-ac { display: flex; flex-direction: column; min-width: 0; background: #fff; border-radius: 14px; color: #0B1F55; overflow: hidden; box-shadow: 0 4px 14px rgba(30,10,20,.16); }
.tvx-ah { display: flex; justify-content: space-between; align-items: center; padding: 14px 14px 0; }
.tvx-tag { display: inline-flex; align-items: center; gap: 7px; padding: 7px 14px 7px 11px; border-radius: 999px; color: #fff; font-size: 13px; font-weight: 800; }
.tvx-tag svg { width: 17px; height: 17px; }
.tvx-ah em { font-style: normal; font-size: 17px; font-weight: 600; }
.tvx-at { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 6px 16px 10px; }
.tvx-ag { display: flex; align-items: center; gap: 6px; font-size: 22px; font-weight: 800; color: #7D142F; white-space: nowrap; overflow: hidden; max-width: 250px; }
.tvx-ag i { flex: none; display: grid; place-items: center; width: 26px; height: 26px; margin-right: 6px; border-radius: 50%; background: #7D142F;
  font-style: normal; font-size: 13px; font-weight: 700; color: #fff; } .tvx-ag span { overflow: hidden; text-overflow: ellipsis; }
.tvx-an { display: flex; align-items: center; gap: 10px; margin-top: 2px; }
.tvx-an b { font-size: 44px; line-height: 1.05; white-space: nowrap; }
.tvx-an span { font-size: 13px; font-weight: 600; color: #4A5F86; line-height: 1.25; }
.tvx-at svg.anneau { flex: none; }
.tvx-als { flex: 1; display: flex; flex-direction: column; justify-content: space-around; padding: 6px 16px; border-top: 1px solid #EDF1F7; }
.tvx-al { display: grid; grid-template-columns: 18px minmax(0, 1fr) 92px 52px; gap: 10px; align-items: center; font-size: 17px; color: #22396E; }
.tvx-al i { font-style: normal; color: #7F93B3; font-weight: 700; }
.tvx-al span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tvx-al div { height: 8px; border-radius: 4px; background: #EEF3F9; overflow: hidden; } .tvx-al u { display: block; height: 100%; border-radius: 4px; }
.tvx-al b { text-align: right; color: #0B1F55; }
.tvx-ap { display: flex; align-items: center; justify-content: center; gap: 8px; padding: 11px; background: #F8FAFD; border-top: 1px solid #EDF1F7; font-size: 15px; color: #22396E; }
.tvx-ap svg { width: 18px; height: 18px; color: #7D142F; }
.tvx-vide { flex: 1; display: grid; place-items: center; padding: 20px; font-size: 17px; color: #5B6E93; text-align: center; }
/* Top 5 du jour */
.tvx-cum { display: flex; flex-direction: column; min-height: 0; padding: 18px 20px 12px; }
.tvx-cu-t { display: flex; align-items: center; gap: 8px; margin-top: 8px; padding-top: 10px; border-top: 1px solid #EDF1F7; font-size: 14px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: #22396E; }
.tvx-cu-t svg { width: 18px; height: 18px; }
.tvx-cu { flex: 1; display: grid; grid-template-columns: 28px minmax(0, 1fr) auto 40px; column-gap: 10px; align-items: center; align-content: center; font-size: 17px; }
.tvx-cu i { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 50%; font-style: normal; font-size: 13px; font-weight: 700; }
.tvx-cu span { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tvx-cu small { font-size: 13px; color: #4A5F86; text-align: right; }
.tvx-cu div { grid-column: 2 / 5; height: 5px; margin-top: 3px; border-radius: 3px; background: #EEF3F9; overflow: hidden; }
.tvx-cu u { display: block; height: 100%; border-radius: 3px; }
/* Réel vs prévision : mêmes couleurs que le graphique de prévision de la page Fréquentation */
.tvx-cb { display: flex; flex-direction: column; min-height: 0; padding: 18px 20px 16px; }
.tvx-lg { display: flex; gap: 16px; margin-left: auto; font-size: 13px; font-weight: 500; color: #22396E; }
.tvx-lg span { display: flex; align-items: center; gap: 7px; } .tvx-lg u { width: 26px; height: 0; border-top: 3px solid; }
.tvx-gr { flex: 1; min-height: 0; margin-top: 6px; }
.tvx-gr svg { display: block; width: 100%; height: 100%; }
.tvx-pv { display: grid; grid-template-columns: 1.25fr 1fr 1fr; gap: 10px; margin-top: 8px; }
.tvx-pv > div { min-width: 0; padding: 10px 14px; border-radius: 12px; background: #F3F8FE; border: 1px solid #E1ECFA; }
.tvx-pv small { display: block; font-size: 12.5px; font-weight: 600; color: #1B3266; white-space: nowrap; }
.tvx-pv b { display: block; margin: 2px 0; font-size: 28px; line-height: 1.1; color: #0B1F55; white-space: nowrap; }
.tvx-pv span { display: block; font-size: 12.5px; color: #4A5F86; white-space: nowrap; }
.tvx-pv .ok { display: flex; align-items: center; gap: 12px; padding: 4px 12px 4px 4px; background: #EEF9F4; border-color: #CDEEDF; }
.tvx-pv .ok svg { flex: none; } .tvx-pv .ok small { font-size: 14px; color: #0B7A55; } .tvx-pv .ok span { font-size: 13px; line-height: 1.35; }
/* À retenir */
.tvx-r { position: relative; display: flex; align-items: stretch; overflow: hidden; background: linear-gradient(90deg, #fff 60%, #FBF1F4 100%); }
.tvx-r > :not(.tvx-vague) { position: relative; }
.tvx-et { flex: none; display: flex; flex-direction: column; justify-content: center; gap: 2px; width: 220px; padding: 0 40px 0 26px; color: #fff;
  background: radial-gradient(120% 140% at 0% 0%, #9B2347 0%, #7D142F 55%, #63102A 100%); clip-path: polygon(0 0, 100% 0, 86% 100%, 0 100%); }
.tvx-et small { font-size: 12px; font-weight: 600; letter-spacing: .12em; color: #F5DCE3; } .tvx-et b { font-size: 20px; font-weight: 800; }
.tvx-f { flex: 1; min-width: 0; display: flex; align-items: center; gap: 16px; padding: 0 24px; }
.tvx-f + .tvx-f { border-left: 1px solid #EBF1F8; }
.tvx-f .ic { flex: none; display: grid; place-items: center; width: 50px; height: 50px; border-radius: 50%; }
.tvx-f .ic svg { width: 26px; height: 26px; }
.tvx-f b { font-size: 36px; white-space: nowrap; }
.tvx-f span { min-width: 0; font-size: 15px; line-height: 1.35; color: #22396E; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tvx-f span strong { display: block; font-size: 17px; font-weight: 700; color: #0B1F55; overflow: hidden; text-overflow: ellipsis; }
.tvx-err { padding: 24px; zoom: 1.5; }
"""

CAL_TV = _svg('<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>', 2)
TV_ENTREE, TV_SORTIE = "#126F57", "#1F6FB8"
# Ajuste le zoom de l'écran TV à la fenêtre : zoom = min(largeur / 1920, hauteur disponible / 1080).
# Exécuté dans un cadre invisible de la même origine ; il agit sur la page et se réapplique à chaque
# rafraîchissement des données et à chaque redimensionnement de la fenêtre.
AJUSTE_TV = """<script>
(function () {
  const w = window.parent, d = w.document;
  if (w.__tvxAjuste) { w.__tvxAjuste(); return; }
  w.__tvxAjuste = function () {
    d.querySelectorAll('.tvx').forEach(function (el) {
      el.style.zoom = 1;
      const haut = Math.max(el.getBoundingClientRect().top, 0);
      const z = Math.min(w.innerWidth / 1920, (w.innerHeight - haut - 4) / 1080);
      el.style.zoom = Math.max(z, .3).toFixed(4);
    });
  };
  w.addEventListener('resize', w.__tvxAjuste);
  new w.MutationObserver(function () { if (d.querySelector('.tvx:not([style])')) w.__tvxAjuste(); })
    .observe(d.body, {childList: true, subtree: true});
  w.__tvxAjuste();
})();
</script>"""
# Vagues décoratives (carte « Effet JOJ » et bandeau « À retenir »)
VAGUE_TV = ('<svg class="tvx-vague" viewBox="0 0 460 170" preserveAspectRatio="none">'
            '<path d="M160 170 C260 120 330 150 460 40 L460 170 Z" fill="#fff" opacity=".10"/>'
            '<path d="M240 170 C320 140 380 150 460 90 L460 170 Z" fill="#fff" opacity=".12"/>'
            '<path d="M120 170 C240 110 300 120 460 10" fill="none" stroke="#fff" stroke-opacity=".28" stroke-width="1.5"/></svg>')
VAGUE_BAS = ('<svg class="tvx-vague" viewBox="0 0 1870 104" preserveAspectRatio="none">'
             '<path d="M1500 104 C1640 80 1720 60 1870 0 L1870 104 Z" fill="#7D142F" opacity=".10"/>'
             '<path d="M1620 104 C1720 88 1790 70 1870 40 L1870 104 Z" fill="#7D142F" opacity=".14"/>'
             '<path d="M1420 104 C1600 70 1700 40 1870 -10" fill="none" stroke="#7D142F" stroke-opacity=".18" stroke-width="1.5"/></svg>')  # mêmes verts / bleus que les pastilles « Top entrées / Top sorties »


def html_tv_entete(maj: datetime, etat: tuple[str, str] | None = None) -> str:
    """Bandeau de l'écran TV, à la charte des autres pages ; « etat » = (libellé, couleur) de la fraîcheur des données."""
    libelle, coul = etat or ("En direct", VERT_OK)
    return f"""
<div class="tvx-h">
  <div class="tvx-logo">{_img_tag("logo_seter_rouge.png")}</div><div class="tvx-sep"></div>
  <div class="tvx-t"><b>Fréquentation des gares</b><span>TER Dakar ↔ AIBD · l'essentiel en temps réel</span></div>
  <div class="tvx-live"><div class="tvx-live-l"><i style="background:{coul};box-shadow:0 0 0 4px {coul}29"></i>{esc(libelle)}</div>
    <div class="tvx-live-d">{CAL_TV}<span>{date_fr(maj)}</span></div></div>
  {f'<div class="tvx-joj">JOJ Dakar 2026 · {statut_joj(maj.date())}</div>' if PARAM.joj_afficher else ""}
  <div class="tvx-clock"><b>{maj:%H:%M}</b><span>heure de mise à jour</span></div><div class="tvx-sep"></div>
  {_img_tag("banniere_dakar2026_fondu.png", "banniere")}
</div>"""


def _tv_var(v: int | None, texte: str) -> str:
    if v is None:
        return f"<span>{texte}</span>"
    cls, fl = ("up", "↑") if v > 0 else (("down", "↓") if v < 0 else ("eq", "→"))
    return f'<span class="ch {cls}">{fl} {"+" if v > 0 else ("−" if v < 0 else "")}{abs(v)} %</span><span>{texte}</span>'


def _tv_spark(vals: list[float], coul: str, W: int = 132, H: int = 44) -> str:
    """Mini-courbe de tendance (aire dégradée), affichée à droite d'un indicateur."""
    if len(vals) < 2 or max(vals) <= 0:
        return ""
    m = max(vals)
    p = " ".join(f"{i * W / (len(vals) - 1):.1f},{H - 3 - v / m * (H - 8):.1f}" for i, v in enumerate(vals))
    gid = "sp" + coul.lstrip("#")
    return (f'<svg class="tvx-sp" width="{W}" height="{H}" viewBox="0 0 {W} {H}"><defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{coul}" stop-opacity=".28"/><stop offset="1" stop-color="{coul}" stop-opacity="0"/></linearGradient></defs>'
            f'<polygon points="0,{H} {p} {W},{H}" fill="url(#{gid})"/><polyline points="{p}" fill="none" stroke="{coul}" stroke-width="2.5" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')


def _tv_horaire(b: dict, G: list[str]) -> dict:
    """{heure : (entrées, sorties)} du jour, sur les gares G (même requête en cache que le graphique de prévision)."""
    df = d_horaire_recent(b["cle"], minuit(b["J"]), b["t_sup"])
    if df.empty:
        return {}
    d = df[df["gare"].isin(list(G))]
    if "jour" in d.columns:
        d = d[d["jour"] == b["J"]]
    out = {}
    for (h, s), v in d.groupby(["heure", "sens"])["nb"].sum().items():
        e, so = out.get(int(h), (0.0, 0.0))
        out[int(h)] = (e + float(v), so) if s == "E" else (e, so + float(v))
    return out


def _tv_anneau(p: float, coul: str, taille: int, txt: str, sous: str, fond: str = "#E6EEF7") -> str:
    c = 2 * math.pi * 40
    return (f'<svg class="anneau" width="{taille}" height="{taille}" viewBox="0 0 100 100"><circle cx="50" cy="50" r="40" fill="none" stroke="{fond}" stroke-width="10"/>'
            f'<circle cx="50" cy="50" r="40" fill="none" stroke="{coul}" stroke-width="10" stroke-linecap="round" stroke-dasharray="{c * max(0, min(p, 100)) / 100:.0f} {c:.0f}" transform="rotate(-90 50 50)"/>'
            f'<text x="50" y="54" text-anchor="middle" font-size="22" font-weight="600" fill="{coul}">{txt}</text>'
            f'<text x="50" y="69" text-anchor="middle" font-size="9.5" fill="#5B6E93">{sous}</text></svg>')


def html_tv_kpis(b: dict, G: list[str], a: dict | None) -> str:
    """Les 4 indicateurs : entrées, sorties, voyageurs de la dernière heure (entrées seules), effet JOJ."""
    F = b["F"]
    jour, veille = F.get("jour", {}), F.get("veille", {})
    E, S = _somme(jour, G, "E"), _somme(jour, G, "S")
    vE, vS = _somme(veille, G, "E"), _somme(veille, G, "S")
    rythme = _somme(F.get("m60", {}), G, "E")
    habituel = semaine_habituelle(b, G)
    depuis = f"depuis {b['ouv']:02d}h00"
    hor = _tv_horaire(b, G)
    heures = sorted(h for h in hor if h >= b["ouv"])
    cumul = lambda k: [sum(hor[x][k] for x in heures[: i + 1]) for i in range(len(heures))]

    def tuile(ic, fond, coul, lib, prec, val, bas, spark="", fort=False):
        deco = VAGUE_TV if fort else ""
        return (f'<div class="tvx-carte tvx-kt{" fort" if fort else ""}" >{deco}'
                f'<div class="tvx-ic" style="background:{fond};color:{coul}">{ICO[ic]}</div>'
                f'<div class="tvx-kx"><div class="tvx-kl">{lib} <em>{prec}</em></div><div class="tvx-kv n">{val}</div>'
                f'<div class="tvx-kd">{bas}</div></div>{spark}</div>')

    hier = lambda v, ref: _tv_var(pct(v, ref), "vs hier à la même heure") if ref > 0 else "<span>pas de données hier à cette heure</span>"
    tuiles = [
        tuile("entree", "#D9F5EA", "#12B97A", "Entrées", depuis, f'<span style="color:{ROUGE}">{nb(E)}</span>', hier(E, vE),
              _tv_spark([0.0] + cumul(0), "#12B97A")),
        tuile("sortie", "#E3F0FE", "#0A6CF5", "Sorties", depuis, f'<span style="color:{ROUGE}">{nb(S)}</span>', hier(S, vS),
              _tv_spark([0.0] + cumul(1), "#0A6CF5")),
        tuile("groupe", "#E3F0FE", "#0A66F0", "Voyageurs", "dernière heure reçue", nb(rythme),
              _tv_var(pct(rythme, habituel), "vs semaine habituelle") if habituel else "<span>pas de référence habituelle</span>",
              _tv_spark([hor[h][0] for h in heures] + [rythme], "#0A66F0"))]
    Ej = a["E"] if a else None
    if Ej and a["dans_jeux"] and Ej["vs_normal"] is not None:
        v = Ej["vs_normal"]
        tuiles.append(tuile("trophee", "", "", "Effet JOJ", f"depuis le {JOJ_DEBUT:%d/%m}", f"{'+' if v > 0 else ('−' if v < 0 else '')}{abs(v)} %",
                            f"<span>{nb(Ej['R'])} voyageurs · vs période normale</span>", fort=True))
    else:  # hors JOJ (ou sans référence) : le dernier lot reçu
        e30, s30 = _somme(F.get("m30", {}), G, "E"), _somme(F.get("m30", {}), G, "S")
        tuiles.append(tuile("horloge", "", "", "Dernier lot reçu", "30 min", f"{nb(e30)}<u>entrées</u>",
                            f"<span>{nb(s30)} sorties · jusqu'à {hh(b['t_ref'])}</span>", fort=True))
    return '<div class="tvx-k">' + "".join(tuiles) + "</div>"


def html_tv_gares(b: dict, G: list[str]) -> str:
    """Bloc « Gares les plus affluentes » (dernier lot de 30 min) : même présentation que la page Fréquentation, en grand."""
    def carte(s: str) -> str:
        entree = s == "E"
        coul, fort = (TV_ENTREE, ROUGE) if entree else (TV_SORTIE, TV_SORTIE)
        tag = f'<span class="tvx-tag" style="background:{coul}">{ICO["entree" if entree else "sortie"]}{"TOP ENTRÉES" if entree else "TOP SORTIES"}</span>'
        x = gare_affluente(b, G, s, 5)
        if x is None:
            return f'<div class="tvx-ac"><div class="tvx-ah">{tag}</div><div class="tvx-vide">Aucune validation dans le dernier lot</div></div>'
        t = x["tend"]
        var = "" if t is None else f'<em class="{"up" if t > 0 else ("down" if t < 0 else "eq")}">{"↗" if t > 0 else ("↘" if t < 0 else "→")} {"+" if t > 0 else ""}{t} %</em>'
        barre = "#168C68" if entree else "#287BC1"
        lignes = "".join(f'<div class="tvx-al"><i>{k + 2}</i><span>{esc(g)}</span><div><u style="width:{v / x["val"] * 100:.0f}%;background:{barre}"></u></div>'
                         f'<b class="n">{nb(v)}</b></div>' for k, (g, v) in enumerate(x["autres"]))
        return f"""
<div class="tvx-ac"><div class="tvx-ah">{tag}{var}</div>
  <div class="tvx-at"><div style="min-width:0"><div class="tvx-ag"><i style="background:{fort}">1</i><span>{esc(x["gare"])}</span></div>
    <div class="tvx-an"><b class="n" style="color:{"#0B1F55" if entree else fort}">{nb(x["val"])}</b><span>voyageurs<br>{"entrants" if entree else "sortants"}</span></div></div>
    {_tv_anneau(x["part"], fort, 100, f"{round(x['part'])} %", "du flux")}</div>
  <div class="tvx-als">{lignes}</div><div class="tvx-ap">{ICO["horloge"]}Pic du jour : <b class="n">{x["pic"]}</b></div></div>"""

    return (f'<div class="tvx-bloc">{_img_tag("train_illustration.png", "tvx-deco")}<div class="tvx-bt"><span class="ic">{ICO["train"]}</span>'
            f'<div><b>GARES LES PLUS AFFLUENTES</b><span>Entrées / Sorties — dernier lot reçu (30 min) · jusqu\'à {hh(b["t_ref"])}</span></div></div>'
            f'<div class="tvx-bg">{carte("E")}{carte("S")}</div></div>')


def html_tv_cumul(b: dict, G: list[str]) -> str:
    """Top 5 du cumul du jour : un classement en entrées, un en sorties, chacun trié par son propre nombre."""
    jour = b["F"].get("jour", {})

    def classement(s: str, lib: str, coul: str, ic: str) -> str:
        vals = {g: _somme(jour, [g], s) for g in G}
        total = sum(vals.values()) or 1
        top = [g for g in sorted(G, key=vals.get, reverse=True)[:5] if vals[g] > 0]
        mx = vals[top[0]] if top else 1
        return f'<div class="tvx-cu-t" style="color:{coul}">{ICO[ic]}{lib}</div>' + ("".join(
            f'<div class="tvx-cu"><i style="background:{coul if k == 0 else "#EEF2F8"};color:{"#fff" if k == 0 else "#22396E"}">{k + 1}</i>'
            f'<span>{esc(g)}</span><b class="n">{nb(vals[g])}</b><small>{round(vals[g] / total * 100)} %</small>'
            f'<div><u style="width:{vals[g] / mx * 100:.0f}%;background:{coul}"></u></div></div>' for k, g in enumerate(top))
            or '<div class="tvx-vide">Aucune validation aujourd\'hui</div>')

    return (f'<div class="tvx-carte tvx-cum"><div class="tvx-ct">Top 5 du jour<small>cumul depuis {b["ouv"]:02d}h00</small></div>'
            f'{classement("E", "Entrées", TV_ENTREE, "entree")}{classement("S", "Sorties", TV_SORTIE, "sortie")}</div>')


def html_tv_prevision(b: dict, G: list[str]) -> str:
    """Entrées du jour heure par heure : le réel superposé à la prévision, pour juger le modèle.
    Précision = 100 % − (somme des écarts |réel − prévu| des heures terminées) / (total réel de ces heures)."""
    t_ref, J = b["t_ref"], b["J"]
    h0 = t_ref.replace(minute=0, second=0, microsecond=0)
    ouv, ferm = b["ouv"], b["ferm"] if b["ferm"] > b["ouv"] else 24
    heures = list(range(ouv, ferm))
    df = d_horaire_recent(b["cle"], minuit(J), b["t_sup"])
    reel = {}
    if not df.empty:
        d = df[(df["sens"] == "E") & df["gare"].isin(list(G))]
        if "jour" in d.columns:
            d = d[d["jour"] == J]
        reel = {int(h): float(v) for h, v in d.groupby("heure")["nb"].sum().items()}
    P = previsions_par_heure(b, G)
    prev = {h: P[minuit(J) + timedelta(hours=h)][0] for h in heures if minuit(J) + timedelta(hours=h) in P}
    finies = [h for h in heures if h < h0.hour] if h0.date() == J else heures  # heures entièrement reçues
    # Trou de chargement : heure sans aucune validation alors que le modèle en attend ; ses voisines sont
    # probablement incomplètes (lot de 30 min manquant). Elles sont exclues de la précision et grisées.
    manque = {h for h in finies if reel.get(h, 0) <= 0 and prev.get(h, 0) >= 50}
    douteuses = manque | {h + d for h in manque for d in (-1, 1) if h + d in finies}
    r_pts = [(h + .5, reel.get(h, 0.0)) for h in finies if h not in manque]
    rythme = _somme(b["F"].get("m60", {}), G, "E")
    if h0.date() == J and ouv <= h0.hour < ferm and b.get("du_jour"):
        r_pts.append(((t_ref - minuit(J)).total_seconds() / 3600 - .5, rythme))  # dernière heure glissante reçue
    p_pts = [(h + .5, v) for h, v in sorted(prev.items())]
    vmax = max([v for _, v in r_pts + p_pts] + [1])
    pas = graduation(vmax * 1.12, 3)
    ymax = pas * 3
    W, H, g0, d0, t0, b0 = 600, 400, 50, 16, 40, 30
    X = lambda x: g0 + (x - ouv) / max(ferm - ouv, 1) * (W - g0 - d0)
    Y = lambda v: H - b0 - v / ymax * (H - t0 - b0)
    ligne = lambda ps: " ".join(f"{X(x):.1f},{Y(v):.1f}" for x, v in ps)
    svg = "".join(f'<line x1="{g0}" x2="{W - d0}" y1="{Y(i * pas):.1f}" y2="{Y(i * pas):.1f}" stroke="#E6EEF7"/>'
                  f'<text x="{g0 - 10}" y="{Y(i * pas) + 5:.1f}" text-anchor="end" font-size="13" fill="#5B6E93">{compact(i * pas) if i else 0}</text>'
                  for i in range(4))
    svg += "".join(f'<text x="{X(h):.1f}" y="{H - 8}" text-anchor="middle" font-size="13" fill="#5B6E93">{h % 24:02d}h</text>'
                   for h in range(ouv, ferm + 1, 3))
    communes = [h for h in finies if h in prev and h not in douteuses]
    for h in sorted(douteuses):  # heures non comparables (données manquantes ou incomplètes) : bande grise
        svg += f'<rect x="{X(h):.1f}" y="{t0}" width="{X(h + 1) - X(h):.1f}" height="{H - b0 - t0}" fill="#EEF2F8"/>'
    if manque:
        xs = [X(h + .5) for h in sorted(manque)]
        svg += (f'<text x="{(xs[0] + xs[-1]) / 2:.1f}" y="{t0 + 16}" text-anchor="middle" font-size="12" fill="#5B6E93">données</text>'
                f'<text x="{(xs[0] + xs[-1]) / 2:.1f}" y="{t0 + 31}" text-anchor="middle" font-size="12" fill="#5B6E93">manquantes</text>')
    # morceaux continus (le réel est interrompu là où des données manquent)
    def morceaux(hs):
        out, cur = [], []
        for h in hs:
            if cur and h != cur[-1] + 1:
                out.append(cur); cur = []
            cur.append(h)
        return out + ([cur] if cur else [])
    for m in morceaux(communes):  # zone d'écart entre réel et prévision, sur les heures comparables
        if len(m) > 1:
            svg += (f'<polygon points="{ligne([(h + .5, reel.get(h, 0.0)) for h in m])} '
                    f'{ligne([(h + .5, prev[h]) for h in reversed(m)])}" fill="#1B6EF3" opacity=".16"/>')
    segs = [[(h + .5, reel.get(h, 0.0)) for h in m] for m in morceaux([h for h in finies if h not in manque])]
    if len(r_pts) > sum(len(s) for s in segs):  # la dernière heure glissante prolonge le dernier morceau
        (segs[-1] if segs and segs[-1][-1][0] >= r_pts[-1][0] - 1.5 else segs.append([]) or segs[-1]).append(r_pts[-1])
    for s in segs:
        if len(s) > 1:
            svg += f'<polygon points="{X(s[0][0]):.1f},{Y(0):.1f} {ligne(s)} {X(s[-1][0]):.1f},{Y(0):.1f}" fill="#0B1F55" opacity=".06"/>'
    if len(p_pts) > 1:
        svg += f'<polyline points="{ligne(p_pts)}" fill="none" stroke="#1B6EF3" stroke-width="3.2" stroke-dasharray="9 7" stroke-linecap="round"/>'
    for s in segs:
        if len(s) > 1:
            svg += f'<polyline points="{ligne(s)}" fill="none" stroke="#0B1F55" stroke-width="3.6" stroke-linejoin="round" stroke-linecap="round"/>'
    svg += "".join(f'<circle cx="{X(x):.1f}" cy="{Y(v):.1f}" r="3.2" fill="#0B1F55"/>' for x, v in r_pts)
    if h0.date() == J and ouv <= h0.hour < ferm:
        xm = X((t_ref - minuit(J)).total_seconds() / 3600)
        svg += (f'<line x1="{xm:.1f}" x2="{xm:.1f}" y1="{t0 - 8}" y2="{H - b0}" stroke="{ROUGE}" stroke-width="2"/>'
                f'<rect x="{min(max(xm - 52, 0), W - 104):.1f}" y="{t0 - 32}" width="104" height="24" rx="7" fill="{ROUGE}"/>'
                f'<text x="{min(max(xm, 52), W - 52):.1f}" y="{t0 - 15}" text-anchor="middle" font-size="13" font-weight="600" fill="#fff">Maintenant</text>')
    if finies and any(reel.get(h, 0) > 0 for h in finies):
        hp = max(finies, key=lambda h: reel.get(h, 0.0))
        txt = f"Pic du jour · {hp % 24:02d}h · {nb(reel[hp])}"
        lw = len(txt) * 7.2 + 16
        xp, yp = X(hp + .5), Y(reel[hp])
        gauche = xp + 14 + lw > W - d0
        svg += (f'<circle cx="{xp:.1f}" cy="{yp:.1f}" r="6.5" fill="#0B1F55" stroke="#fff" stroke-width="2.5"/>'
                f'<rect x="{xp - 14 - lw if gauche else xp + 14:.1f}" y="{yp - 14:.1f}" width="{lw:.0f}" height="26" rx="7" fill="#0B1F55"/>'
                f'<text x="{(xp - 14 - lw / 2) if gauche else (xp + 14 + lw / 2):.1f}" y="{yp + 4:.1f}" text-anchor="middle" font-size="13" font-weight="500" fill="#fff">{txt}</text>')
    # Écart moyen entre réel et prévu sur les heures terminées et comparables
    tot = sum(reel.get(h, 0.0) for h in communes)
    if communes and tot > 0:
        err = sum(abs(reel.get(h, 0.0) - prev[h]) for h in communes) / tot * 100
        biais = round((sum(prev[h] for h in communes) - tot) / tot * 100)  # < 0 : le modèle prévoit trop peu
        sens_b = "juste en moyenne" if abs(biais) < 3 else f"prévision trop {'basse' if biais < 0 else 'haute'} de {abs(biais)} %"
        t_prec = (f'<div><small>Écart moyen · heures écoulées</small><b class="n">{round(err)} %</b><span>{sens_b}</span></div>')
    else:
        t_prec = '<div><small>Écart moyen</small><b class="n">—</b><span>pas encore mesurable</span></div>'
    suiv = P.get(h0 + timedelta(hours=1))
    t_suiv = (f'<div><small>Prochaine heure · {(h0.hour + 1) % 24:02d}h</small><b class="n">{nb(suiv[0])}</b><span>entrées prévues</span></div>'
              if suiv else '<div><small>Prochaine heure</small><b class="n">—</b><span>pas de prévision</span></div>')
    futur = {t: v[0] for t, v in P.items() if h0 < t <= h0 + timedelta(hours=24)}
    if futur:
        tp = max(futur, key=futur.get)
        quand = ("aujourd'hui" if tp.date() == J else "demain") + f" {tp:%H}h"
        t_pic = f'<div><small>Prochain pic</small><b class="n">{nb(futur[tp])}</b><span>{quand}</span></div>'
    else:
        t_pic = '<div><small>Prochain pic</small><b class="n">—</b><span>pas de prévision</span></div>'
    return f"""
<div class="tvx-carte tvx-cb"><div class="tvx-ct">Réel vs prévision<small>entrées par heure</small>
    <div class="tvx-lg"><span><u style="border-color:#0B1F55"></u>Réel</span><span><u style="border-color:#1B6EF3;border-top-style:dashed"></u>Prévision</span>
      <span><u style="border-color:#1B6EF3;opacity:.25;border-top-width:9px"></u>Écart</span></div></div>
  <div class="tvx-gr"><svg viewBox="0 0 {W} {H}" role="img" aria-label="Entrées réelles et prévues, heure par heure">{svg}</svg></div>
  <div class="tvx-pv">{t_prec}{t_suiv}{t_pic}</div>
</div>"""


def html_tv_retenir(b: dict, G: list[str], a: dict | None) -> str:
    """Bandeau « À retenir » : jusqu'à 3 faits calculés (hausse JOJ, tronçon le plus chargé, projection, sinon écart 2025 / gare n° 1)."""
    faits = []
    if a and a.get("hausses") and a["hausses"][0][1] > 0:
        g, p, _ = a["hausses"][0]
        faits.append(("courbe", "#E3F6EE", "#12915F", f'<b class="n up">+{p} %</b>', g, "plus forte hausse (JOJ)"))
    try:  # tronçon le plus chargé : facultatif (pas d'erreur affichée si les horaires GTFS sont indisponibles)
        d = charger_donnees()
        ch = charge_reseau(d["maj"], b if REEL else None, d)
        segs = ([(f"{NOMS[i]} → {NOMS[i + 1]}", ch["pct_a"][i]) for i in range(len(NOMS) - 1)]
                + [(f"{NOMS[i + 1]} → {NOMS[i]}", ch["pct_r"][i]) for i in range(len(NOMS) - 1)])
        nom, p = max(segs, key=lambda x: x[1])
        if p > 0:
            faits.append(("reseau", "#FDF1E3", "#C2570C", f'<b class="n" style="color:{niveau(p)}">{round(p)} %</b>', nom, "tronçon le plus chargé"))
    except ErreurDonnees as e:
        JOURNAL.warning("Écran TV : tronçon le plus chargé non affiché (%s)", e.titre)
    if a and a["J"] <= JOJ_FIN and a["E"]["projection"]:
        E = a["E"]
        vs = f"{'+' if pct(E['projection'], E['normal_total']) > 0 else ''}{pct(E['projection'], E['normal_total'])} % vs période normale" if E["normal_total"] else "entrées sur la période"
        faits.append(("trophee", "#F6E5EA", ROUGE, f'<b class="n">{compact(E["projection"])}</b>', "Projection fin des JOJ", vs))
    if a and a["E"]["ecart_2025"] is not None and len(faits) < 3:
        e = a["E"]["ecart_2025"]
        faits.append(("groupe", "#E3F0FE", "#0A66F0", f'<b class="n {"up" if e >= 0 else "down"}">{"+" if e >= 0 else "−"}{compact(abs(e))}</b>', "Voyageurs vs 2025", "même période, à date"))
    if len(faits) < 3:
        jour = b["F"].get("jour", {})
        vals = {g: _somme(jour, [g], "E") for g in G}
        if vals and max(vals.values()) > 0:
            g = max(vals, key=vals.get)
            faits.append(("epingle", "#F6E5EA", ROUGE, f'<b class="n">{nb(vals[g])}</b>', g, "1re gare du jour (entrées)"))
    if not faits:
        faits.append(("info", "#EEF2F8", "#4A5F86", '<b class="n">—</b>', "Pas encore de fait marquant", "en attente de validations"))
    return ('<div class="tvx-carte tvx-r">' + VAGUE_BAS + '<div class="tvx-et"><small>› À RETENIR</small><b>Faits du jour</b></div>' + "".join(
        f'<div class="tvx-f"><span class="ic" style="background:{fond};color:{coul}">{ICO[ic]}</span>{v}<span><strong>{esc(t)}</strong>{esc(s)}</span></div>'
        for ic, fond, coul, v, t, s in faits[:3]) + "</div>")


# ═══════════════════════════════ Pages ═══════════════════════════════
# En mode réel, seules les pages qui ont une vraie source sont proposées (sauf choix contraire dans parametres.toml).
PAGE_DEFAUT = "frequentation" if REEL else "suivi"
NAV_TV = [("tv", "Écran<br>TV", "tv", "tv")]  # plein écran pour les écrans muraux (adresse directe : ?page=tv)
NAV_JOJ = [("joj", "JOJ<br>Dakar 2026", "trophee", "joj")] if PARAM.joj_afficher else []
if PAGES_SIMULEES:
    NAV = [("frequentation", "Fréquentation", "groupe", "frequentation"), *NAV_JOJ,
           ("suivi", "Suivi des<br>trains", "train", "suivi"), ("charge", "Serpent<br>de charge", "reseau", "charge"), *NAV_TV]
else:
    NAV = [("frequentation", "Fréquentation", "groupe", "frequentation"), *NAV_JOJ, *NAV_TV]
PAGES = tuple(cle for cle, *_ in NAV)

ss = st.session_state
_demande = st.query_params.get("page")
if _demande in PAGES and _demande != ss.get("page") and not ss.get("_nav_interne"):
    ss.page = _demande  # lien « ?page=joj » (bouton « Voir le détail » du résumé JOJ)
if ss.get("page") not in PAGES:
    ss.page = PAGE_DEFAUT
ss["_nav_interne"] = False
ss.setdefault("train_sel", "T2")
ss.setdefault("tous", False)


def aller(page: str):
    ss.page = page
    ss["_nav_interne"] = True
    try:
        st.query_params["page"] = page
    except Exception:
        pass


def choisir(tid: str):
    ss.train_sel = tid


def basculer_tous():
    ss.tous = not ss.tous


def segment(label: str, options: list[str], key: str, conteneur=st) -> str:
    if hasattr(conteneur, "segmented_control"):
        v = conteneur.segmented_control(label, options, key=key, default=options[0], label_visibility="collapsed")
    else:
        v = conteneur.radio(label, options, key=key, horizontal=True, label_visibility="collapsed")
    return v or options[0]


def barre_laterale():
    with st.container(key="sidebar"):
        md(f'<div class="tb-logo tb-logo-haut">{_img_tag("logo_seter.png", largeur=92)}</div>')
        for cle, lib, ic, cible in NAV:
            with st.container(key=f"nav_{cle}"):
                md(f'<div class="tb-navi{" actif" if cle == ss.page else ""}">{ICO[ic]}<span>{lib}</span></div>')
                st.button(lib.replace("<br>", " "), key=f"navbtn_{cle}", on_click=aller, args=(cible,))
        source_txt = ('<i></i><span>Données en<br>temps réel</span>' if REEL else
                      '<i style="background:#8FB8FF;box-shadow:0 0 0 3px rgba(143,184,255,.25)"></i>'
                      '<span>Données de<br>démonstration</span>')
        md(f'<div class="tb-bas"><div class="tb-gtfs">{source_txt}</div>'
           f'<div class="tb-logo">{_img_tag("embleme_dakar2026.png", largeur=78)}</div></div>')


@st.fragment(run_every=RAFRAICHISSEMENT)
def page_suivi():
    try:
        d = charger_donnees()
    except ErreurDonnees as e:
        md(html_entete(maintenant(), "Suivi des trains", "Horaires GTFS et positions simulées", ("GTFS indisponible", ROUGE)))
        md(html_erreur(e.titre, e.detail, e.conseil)); return
    trains = d["trains"]
    md(html_entete(d["maj"], "Suivi des trains", "Horaires GTFS · positions calculées à l'instant présent"))
    md(html_bandeau_gtfs(d))
    if not trains:
        dep, tid = prochain_depart(d)
        md(html_kpis(d))
        components.html(html_carte([], None, a_venir=d.get("a_venir")), height=326)
        md(f'<div class="tb-card tb-pad"><b>Aucun train en ligne à {d["maj"]:%Hh%M} selon les horaires GTFS.</b> '
           f'<span class="sm">Prochain départ de {esc(NOMS[0])} : {dep:%Hh%M} (train {esc(tid)}).</span></div>')
        return
    if ss.train_sel not in {t["id"] for t in trains}:
        ss.train_sel = trains[0]["id"]
    t_sel = next(t for t in trains if t["id"] == ss.train_sel)

    gauche, droite = st.columns([2.74, 1])

    with gauche:
        md(html_kpis(d))
        components.html(html_carte(trains, ss.train_sel, a_venir=d.get("a_venir")), height=326)
        md(html_ligne(t_sel))
        with st.container(key="tableau"):
            md(html_tableau(trains if ss.tous else trains[:5]))
            st.button("Réduire la liste  ↑" if ss.tous else "Voir tous les trains  →", key="voir_tous", on_click=basculer_tous)

    with droite, st.container(key="panneau"):
        c1, c2 = st.columns([1.2, 1], vertical_alignment="center")
        md('<div class="tb-ptitle">Liste des trains</div>', c1)
        choix_ligne = c2.selectbox("Ligne", LIGNES, key="filtre_ligne", label_visibility="collapsed")
        recherche = st.text_input("Recherche", key="recherche", placeholder="Rechercher un train, une gare…",
                                  label_visibility="collapsed")
        liste = filtrer(trains, choix_ligne, recherche)
        if not liste:
            md('<div class="tb-vide">Aucun train ne correspond à votre recherche.</div>')
        for t in liste:
            with st.container(key=f"carte_{t['id']}"):
                md(html_carte_train(t, t["id"] == ss.train_sel))
                st.button(f"Suivre le train {t['id']}", key=f"choix_{t['id']}", on_click=choisir, args=(t["id"],))


def sur(fonction, *args) -> str:
    """Rend un bloc ; si ses données sont illisibles, affiche l'erreur à sa place sans bloquer les autres blocs."""
    try:
        return fonction(*args)
    except ErreurDonnees as e:
        return html_erreur(e.titre, e.detail, e.conseil)


@st.fragment(run_every=RAFRAICHISSEMENT)
def page_frequentation():
    titre, sous = "Fréquentation des gares", "Entrées et sorties · dernier lot disponible (30 min)"
    try:
        b = freq_base()
    except ErreurDonnees as e:  # connexion ou requête des validations en échec : rien d'autre à montrer
        md(html_entete(maintenant(), titre, sous, etat_donnees(None, erreur=True)))
        md(html_erreur(e.titre, e.detail, e.conseil))
        return
    md(html_entete(b["now"], titre, sous, etat_donnees(b)))
    opts = b["options"]
    if ss.get("zone") not in opts:
        ss["zone"] = TOUTES
    with st.container(key="bloc_ctrl"):
        a, c = st.columns([1.3, 2.1], vertical_alignment="center")
        perimetre = a.selectbox("Périmètre", list(opts), key="zone", label_visibility="collapsed")
        G = opts[perimetre]
        noms = ", ".join(G) if len(G) <= 6 else f"{len(G)} gares"
        if not REEL:
            maj = f"mis à jour à {b['now']:%H:%M}"
        elif b["du_jour"]:
            maj = f"dernière validation reçue à {hh(b['der'])}"
        else:
            maj = "aucune validation reçue aujourd'hui"
        md(f'<div class="tb-ctrl-t">Gares : {esc(noms)} · cumul depuis l\'ouverture ({b["ouv"]:02d}h00) · {maj}</div>', c)
    for message in b["avert"]:
        md(f'<div class="tb-sim"><b>!</b><span>{esc(message)}</span></div>')
    md(html_freq_cartes(b, G, perimetre))
    hero = sur(html_hero, b, G, perimetre)
    md(f'<div class="tb-g tb-g2j">{hero}{sur(html_joj, b, G, perimetre)}</div>' if PARAM.joj_afficher else hero)
    md(html_top5(b, G))
    md(sur(html_prevision, b, G, perimetre))
    g, dr = st.columns([1.25, 1])
    with g, st.container(key="bloc_affluence"):
        md(f'<div class="tb-ct">Affluence habituelle <span>(moyenne des {b["N"]} dernières semaines)</span></div>')
        mode = segment("Mesure", ["Entrées", "Sorties", "Total"], "aff_mode")
        md(f'<div class="tb-ctrl-t" style="text-align:left">{texte_horaires(est_joj(b["J"]))}</div>')
        md(sur(html_heatmap, b, G, mode))
    md(html_flux(b, G), dr)


@st.fragment(run_every=RAFRAICHISSEMENT)
def page_charge():
    try:
        d = charger_donnees()
    except ErreurDonnees as e:
        md(html_entete(maintenant(), "Serpent de charge", "GTFS théorique + fréquentation", ("GTFS indisponible", ROUGE)))
        md(html_erreur(e.titre, e.detail, e.conseil)); return
    trains, maj = d["trains"], d["maj"]
    try:
        b_flux = freq_base() if REEL else None
    except ErreurDonnees as e:
        b_flux = None
        md(html_erreur(e.titre, e.detail, e.conseil))
    ch = charge_reseau(maj, b_flux, d)
    md(html_entete(maj, "Serpent de charge", "Flux voyageurs (dernier lot) et charge estimée"))
    ref_txt = f"validations jusqu’à {hh(b_flux['t_ref'])}" if b_flux and b_flux.get("der") else "aucune validation récente disponible"
    md(f'<div class="tb-sim"><b>i</b><span>Horaires GTFS théoriques · charge estimée sur la dernière fenêtre de 60 min ({esc(ref_txt)}) · collecte des validations toutes les 30 min · positions simulées.</span></div>')
    zone_kpi = st.empty()
    with st.container(key="bloc_ctrl"):
        a, b, _, e = st.columns([1.8, 1, .9, 1], vertical_alignment="center")
        vue = segment("Vue", ["Vue globale", "Par train"], "vue_charge", a)
        sel = b.selectbox("Train", [t["id"] for t in trains], key="charge_train",
                          label_visibility="collapsed") if vue == "Par train" and trains else None
        choix = e.selectbox("Ligne", LIGNES, key="charge_ligne", label_visibility="collapsed")
    sens_vis = (1, -1) if choix == LIGNES[0] else ((1,) if choix == LIGNES[1] else (-1,))
    liste = [t for t in trains if t["sens"] in sens_vis and (sel is None or t["id"] == sel)]
    charges = {t["id"]: charge_train(t, ch) for t in trains}
    md(html_charge_kpis(liste, charges, ch, sens_vis), zone_kpi)
    gris = ["#C9D6E8"] * (len(NOMS) - 1)
    couleurs = {"a": [niveau(p) for p in ch["pct_a"]] if 1 in sens_vis else gris,
                "r": [niveau(p) for p in ch["pct_r"]] if -1 in sens_vis else gris}
    components.html(html_carte(liste, sel, couleurs, LEG_CHARGE, "Réseau et charge des trains", 4.5,
                               a_venir=d.get("a_venir") if sel is None else None), height=336)
    md(html_serpent(ch, sens_vis))
    g, dr = st.columns([1.6, 1])
    md(html_detail_charge(liste, charges), g)
    md(html_montees(ch), dr)
    with st.container(key="bloc_info"):
        i1, i2 = st.columns([4.2, 1], vertical_alignment="center")
        md(f'<div class="tb-info"><b>{ICO["info"]}</b><span>Le serpent de charge estime l\'occupation de chaque tronçon à partir des '
           f'entrées et sorties de l\'heure en cours, de l\'offre en trains et de la capacité des rames.</span></div>', i1)
        if i2.button("Voir la carte détaillée  →", key="voir_carte"):
            aller("suivi")
            st.rerun()


@st.fragment(run_every=RAFRAICHISSEMENT)
def page_tv():
    """Écran mural synthétique, un seul affichage 1920 × 1080 sans défilement, toutes les gares, sans commandes :
    4 grands chiffres · gares les plus affluentes (dernier lot) · top 5 du jour (cumul) · réel vs prévision · à retenir.
    Adresse directe : ?page=tv"""
    try:
        b = freq_base()
    except ErreurDonnees as e:
        md('<div class="tvx-w"><div class="tvx">' + html_tv_entete(maintenant(), etat_donnees(None, erreur=True))
           + '<div class="tvx-err">' + html_erreur(e.titre, e.detail, e.conseil) + "</div></div></div>")
        return
    G = b["options"][TOUTES]
    a = None
    if PARAM.joj_afficher:
        try:
            a = joj_analyse(b, G)
        except ErreurDonnees as e:
            JOURNAL.warning("Écran TV : analyses JOJ non affichées (%s)", e.titre)
    md('<div class="tvx-w"><div class="tvx">' + html_tv_entete(b["now"], etat_donnees(b)) + '<div class="tvx-p">'
       + html_tv_kpis(b, G, a)
       + f'<div class="tvx-m">{sur(html_tv_gares, b, G)}{html_tv_cumul(b, G)}{sur(html_tv_prevision, b, G)}</div>'
       + html_tv_retenir(b, G, a) + "</div></div></div>")


st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)
if ERREUR_PARAMETRES:  # réglage invalide : on le dit clairement, sans afficher de chiffres
    barre_laterale()
    md(html_entete(maintenant(), "Fréquentation des gares", "Entrées et sorties · dernier lot disponible (30 min)", ("Réglages à corriger", ROUGE)))
    md(html_erreur("Réglage invalide", ERREUR_PARAMETRES, "Corriger le fichier indiqué puis recharger la page (touche F5).",
                   entete="Réglages à corriger"))
elif ss.page == "tv":
    st.markdown(f"<style>{CSS_TV}</style>", unsafe_allow_html=True)
    components.html(AJUSTE_TV, height=0)
    st.button("Quitter l'affichage TV", key="quitter_tv", on_click=aller, args=("frequentation",))
    page_tv()
else:
    barre_laterale()
    {"suivi": page_suivi, "frequentation": page_frequentation, "charge": page_charge, "joj": page_joj}[ss.page]()
