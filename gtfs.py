"""
Horaires théoriques GTFS → circulations simulées « en temps réel ».

    construire_reseau(gares, arrets)    la ligne réelle : gares de Ref_Gares, dans l'ordre, avec leur point
                                        kilométrique (PK) calculé sur leurs coordonnées
    circulations(horaires, maintenant)  les trains en ligne à l'instant donné, interpolés entre les heures
                                        d'arrivée et de départ du GTFS (arrêts en gare compris)

Le « temps réel » est simulé : chaque circulation reçoit un petit retard stable (le même à chaque
rafraîchissement), aucune donnée GPS ou GTFS-Realtime n'est utilisée.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from datetime import datetime, time, timedelta

import pandas as pd

COULEURS = ["#0A72FA", "#05B079", "#FDA800", "#8429F8", "#F20C6F", "#0EA5B7", "#D04B72", "#5B6CF0"]
COLONNES = ["trip_id", "route_id", "route_short_name", "trip_headsign", "stop_code", "stop_name",
            "stop_sequence", "arrival_time", "departure_time", "stop_lat", "stop_lon"]


def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(gare|station|halte|de|du|des|la|le|ter)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def secondes_gtfs(v) -> int | None:
    """« 25:10:00 » → 90600 s (le GTFS dépasse 24:00 pour les trains après minuit)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if hasattr(v, "hour") and not isinstance(v, str):
        return v.hour * 3600 + v.minute * 60 + v.second
    m = re.fullmatch(r"\s*(\d{1,3}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s*", str(v))
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3) or 0)


def _km(a, b) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def _coord(la, lo):
    try:
        la, lo = float(la), float(lo)
    except (TypeError, ValueError):
        return None
    if math.isnan(la) or math.isnan(lo) or not (-90 <= la <= 90) or (la == 0 and lo == 0):
        return None
    return la, lo


def construire_reseau(gares: pd.DataFrame, arrets: pd.DataFrame | None = None) -> dict:
    """Ligne réelle à partir de Ref_Gares (ordre des lignes = ordre sur la ligne).

    Coordonnées : celles de Ref_Gares, sinon celles de l'arrêt GTFS de même nom ; une gare sans
    coordonnées est placée entre ses voisines. Sans coordonnées du tout, les gares sont espacées
    régulièrement (geo=False) et la carte garde le tracé dessiné de l'application.
    """
    noms = [str(g) for g in gares["gare"]]
    coords = {g: _coord(la, lo) for g, la, lo in zip(noms, gares.get("latitude", [None] * len(noms)),
                                                      gares.get("longitude", [None] * len(noms)))}
    if arrets is not None and not arrets.empty:
        par_nom = {}
        for n, la, lo in zip(arrets["stop_name"], arrets["stop_lat"], arrets["stop_lon"]):
            c = _coord(la, lo)
            if c:
                par_nom.setdefault(norm(n), c)
        for g in noms:
            if not coords[g]:
                coords[g] = par_nom.get(norm(g))
    connus = [i for i, g in enumerate(noms) if coords[g]]
    if len(connus) < 2:
        return dict(gares=[(g, 3.0 * i) for i, g in enumerate(noms)], trace=None, geo=False,
                    coords={g: coords[g] for g in noms})
    pts = []
    for i, g in enumerate(noms):  # interpolation des gares sans coordonnées
        if coords[g]:
            pts.append(coords[g])
            continue
        av = max((k for k in connus if k < i), default=None)
        ap = min((k for k in connus if k > i), default=None)
        if av is None or ap is None:
            pts.append(coords[noms[av if ap is None else ap]])
        else:
            r = (i - av) / (ap - av)
            (a1, o1), (a2, o2) = coords[noms[av]], coords[noms[ap]]
            pts.append((a1 + (a2 - a1) * r, o1 + (o2 - o1) * r))
    pk = [0.0]
    for a, b in zip(pts, pts[1:]):
        pk.append(pk[-1] + max(_km(a, b), .05))
    return dict(gares=[(g, round(k, 2)) for g, k in zip(noms, pk)], trace=[list(p) for p in pts], geo=True,
                coords=dict(zip(noms, pts)))


def associer_arrets(arrets: pd.DataFrame, reseau: dict) -> dict:
    """{stop_name GTFS : indice de la gare du réseau} — par le nom, sinon par la position (< 1,5 km)."""
    noms = [g for g, _ in reseau["gares"]]
    cles = [norm(g) for g in noms]
    out = {}
    for n, la, lo in arrets[["stop_name", "stop_lat", "stop_lon"]].drop_duplicates("stop_name").itertuples(index=False):
        k = norm(n)
        idx = next((i for i, c in enumerate(cles) if c == k), None)
        if idx is None:
            idx = next((i for i, c in enumerate(cles) if len(c) >= 4 and len(k) >= 4 and (c in k or k in c)), None)
        c = _coord(la, lo)
        if idx is None and c and reseau["geo"]:
            d, i = min((_km(c, reseau["coords"][g]), i) for i, g in enumerate(noms))
            idx = i if d < 1.5 else None
        if idx is not None:
            out[str(n)] = idx
    return out


def _libelle(trip_id: str, deja: set) -> str:
    """Numéro court lisible : « 123 » pour « TER_123_semaine » ; l'identifiant complet sinon."""
    groupes = re.findall(r"\d{2,}", str(trip_id))
    lib = groupes[-1] if groupes else str(trip_id)[-8:]
    if lib in deja:
        lib = str(trip_id)[-10:]
    deja.add(lib)
    return lib


def _retard(trip_id: str, jour, retard_max: int) -> int:
    """Retard simulé, stable pour une circulation : 6 trains sur 10 à l'heure, les autres de 1 à retard_max min."""
    h = hashlib.md5(f"{trip_id}|{jour}".encode()).digest()
    if retard_max <= 0 or h[0] % 10 < 6:
        return 0
    return 1 + h[1] % retard_max


def _interp(wp: list, t: datetime):
    for (t1, k1), (t2, k2) in zip(wp, wp[1:]):
        if t1 <= t <= t2:
            r = (t - t1).total_seconds() / max((t2 - t1).total_seconds(), 1)
            return k1 + (k2 - k1) * r, (t2 - t1).total_seconds(), abs(k2 - k1)
    return None


def circulations(horaires: pd.DataFrame, maintenant: datetime, reseau: dict, retard_max: int = 4,
                 horizon_s: int = 180) -> dict:
    """Trains en ligne à « maintenant » + trains qui partent dans les horizon_s secondes (pour la carte).

    horaires : colonnes COLONNES + service_date (date de service GTFS de la ligne).
    Chaque train porte « wp » : ses points de passage [secondes depuis maintenant, PK], que la carte
    interpole elle-même entre deux rafraîchissements.
    """
    noms = [g for g, _ in reseau["gares"]]
    pks = [k for _, k in reseau["gares"]]
    vide = dict(trains=[], a_venir=[], departures=[], non_associes=[], maj=maintenant)
    if horaires is None or horaires.empty:
        return vide
    idx = associer_arrets(horaires, reseau)
    non_associes = sorted(set(map(str, horaires["stop_name"])) - set(idx))
    trains, a_venir, departs, libelles = [], [], [], set()
    horaires = horaires.assign(_seq=pd.to_numeric(horaires["stop_sequence"], errors="coerce"))
    for (jour, trip), lignes in horaires.groupby(["service_date", "trip_id"], sort=False):
        base = datetime.combine(jour, time.min)
        arrets = []
        for r in lignes.sort_values("_seq").itertuples(index=False):
            i = idx.get(str(r.stop_name))
            a, d = secondes_gtfs(r.arrival_time), secondes_gtfs(r.departure_time)
            if i is None or (a is None and d is None):
                continue
            a, d = (d if a is None else a), (a if d is None else d)
            if arrets and arrets[-1][0] == i:
                continue
            arrets.append((i, base + timedelta(seconds=a), base + timedelta(seconds=max(a, d))))
        if len(arrets) < 2:
            continue
        sens = 1 if pks[arrets[-1][0]] > pks[arrets[0][0]] else -1
        retard = _retard(trip, jour, retard_max)
        dt = timedelta(minutes=retard)
        wp = []
        for i, a, d in arrets:
            wp += [(a + dt, pks[i]), (d + dt, pks[i])]
        wp = wp[1:-1] if len(wp) > 2 else wp  # pas d'attente avant le départ ni après le terminus
        r0 = lignes.iloc[0]
        dest = noms[arrets[-1][0]]
        depart_theo = arrets[0][2]
        departs.append(dict(datetime=depart_theo, id=str(trip), direction=sens, origine=noms[arrets[0][0]],
                            headsign=str(r0.get("trip_headsign") or dest), retard=retard))
        debut, fin = wp[0][0], wp[-1][0]
        if fin < maintenant or debut > maintenant + timedelta(seconds=horizon_s):
            continue
        lib = _libelle(trip, libelles)
        rel = [[round((t - maintenant).total_seconds(), 1), round(k, 3)] for t, k in wp]
        couleur = COULEURS[int(hashlib.md5(str(trip).encode()).hexdigest(), 16) % len(COULEURS)]
        if debut > maintenant:
            a_venir.append(dict(id=lib, trip_id=str(trip), sens=sens, couleur=couleur, ecart=retard, wp=rel))
            continue
        pk, duree, dist = _interp(wp, maintenant)
        vitesse = int(round(dist / duree * 3600 / 5) * 5) if dist > 0 else 0
        devant = [i for i, _, _ in arrets if (pks[i] > pk + 1e-3 if sens > 0 else pks[i] < pk - 1e-3)]
        prochain = noms[devant[0]] if devant else dest
        proche = min(range(len(pks)), key=lambda i: abs(pks[i] - pk))
        if vitesse == 0 or abs(pks[proche] - pk) < .15:
            position = noms[proche]
        else:
            avant = [i for i, _, _ in arrets if (pks[i] <= pk if sens > 0 else pks[i] >= pk)]
            position = f"Entre {noms[avant[-1]] if avant else noms[arrets[0][0]]} et {prochain}"
        trains.append(dict(id=lib, trip_id=str(trip), couleur=couleur, pk=float(pk), sens=sens, position=position,
                           prochain=prochain, ecart=retard, vitesse=vitesse, destination=dest,
                           headsign=str(r0.get("trip_headsign") or dest), depart=depart_theo, wp=rel, phase=0.0))
    departs.sort(key=lambda x: x["datetime"])
    trains.sort(key=lambda t: (t["sens"] < 0, t["pk"] if t["sens"] > 0 else -t["pk"]))
    return dict(trains=trains, a_venir=a_venir, departures=departs, non_associes=non_associes, maj=maintenant)
