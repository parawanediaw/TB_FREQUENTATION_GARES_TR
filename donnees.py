"""
Sources de données de la page « Fréquentation des gares ».

Deux sources offrent exactement les mêmes méthodes et renvoient des tableaux (DataFrame) de même forme :

    SourceSQL   lit SQL Server à partir des requêtes du dossier sql/  (mode « reel »)
    SourceDemo  calcule des chiffres simulés                          (mode « demo »)

La page ne sait pas d'où viennent les chiffres : les calculs et l'affichage sont communs aux deux modes.
En mode réel, une erreur n'est jamais remplacée en silence par des chiffres simulés : elle est affichée.

Colonnes renvoyées
    gares()                    gare, ordre, zone, latitude, longitude
    derniere_validation(a, b)  date-heure de la dernière validation reçue dans [a, b[ (ou None)
    fenetres([(nom, a, b)])    fenetre, gare, sens, nb      — comptages exacts sur des intervalles [a, b[
    horaire(a, b)              jour, heure, gare, sens, nb  — par heure
    journalier(a, b)           jour, gare, sens, nb         — par jour
    previsions(a, b)           date_heure, gare, sens, nb, nb_bas, nb_haut (nb_bas / nb_haut facultatifs)
sens vaut « E » (entrée) ou « S » (sortie).
"""
from __future__ import annotations

import math
import re
import time as _time
from contextlib import closing
from datetime import date, datetime, timedelta
from typing import Callable

import pandas as pd

from parametres import FICHIERS_SQL, ErreurParametres, Parametres, nettoyer_requete, preparer_sous_requete

COLONNES = {
    "gares": ["gare", "ordre", "zone", "latitude", "longitude"],
    "fenetres": ["fenetre", "gare", "sens", "nb"],
    "horaire": ["jour", "heure", "gare", "sens", "nb"],
    "journalier": ["jour", "gare", "sens", "nb"],
    "previsions": ["date_heure", "gare", "sens", "nb", "nb_bas", "nb_haut"],
}
ETIQUETTE = "/* tableau de bord frequentation : {} */\n"  # repérable dans les traces SQL Server


class ErreurDonnees(Exception):
    """Erreur affichée telle quelle sur la page : ce qui ne va pas, le message technique, quoi vérifier."""

    def __init__(self, titre: str, detail: str = "", conseil: str = ""):
        super().__init__(titre)
        self.titre, self.detail, self.conseil = titre, detail, conseil


def _vide(nom: str) -> pd.DataFrame:
    return pd.DataFrame(columns=COLONNES[nom])


def _nettoyer(df: pd.DataFrame, nom: str) -> pd.DataFrame:
    """Uniformise les colonnes : noms en minuscules, gare sans espaces, sens E/S, nombres décimaux."""
    df = df.rename(columns={c: str(c).strip().lower() for c in df.columns})
    if df.empty:
        return _vide(nom) if not len(df.columns) else df
    if "gare" in df:
        df["gare"] = df["gare"].map(lambda v: "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v).strip())
        if nom != "gares":
            df["gare"] = df["gare"].replace("", "(gare non renseignée)")
    if "sens" in df:
        df["sens"] = df["sens"].map(lambda v: "" if v is None else str(v).strip().upper()[:1])
        df = df[df["sens"].isin(["E", "S"])].copy()
    for c in ("nb", "nb_bas", "nb_haut", "ordre", "latitude", "longitude"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "nb" in df:
        df["nb"] = df["nb"].fillna(0.0).astype(float)
    if "heure" in df:
        df["heure"] = pd.to_numeric(df["heure"], errors="coerce").fillna(0).astype(int)
    if "jour" in df:
        df["jour"] = pd.to_datetime(df["jour"]).dt.date
    if "date_heure" in df:
        df["date_heure"] = pd.to_datetime(df["date_heure"])
    return df.reset_index(drop=True)


def _sec(d: datetime) -> datetime:
    """Bornes envoyées à SQL Server : à la seconde près (évite les écarts de précision datetime / datetime2)."""
    return d.replace(microsecond=0)


# ═══════════════════════════════ SQL Server ═══════════════════════════════
class SourceSQL:
    def __init__(self, p: Parametres):
        self.p = p

    # ── Outils ──
    def _sous_requete(self, nom: str) -> str:
        try:
            return preparer_sous_requete(self.p.lire_sql(nom), FICHIERS_SQL[nom])
        except ErreurParametres as e:
            raise ErreurDonnees(f"Requête {FICHIERS_SQL[nom]} à corriger", str(e),
                                "Ouvrir le fichier, le corriger puis recharger la page.") from e

    def _connexion(self, cible: str = "validations"):
        try:
            import pyodbc
        except ImportError as e:
            raise ErreurDonnees("Le module pyodbc n'est pas installé", str(e),
                                "Relancer scripts\\2_installer_sur_le_serveur.bat.") from e
        try:
            chaine = self.p.chaine_connexion(cible)
        except ErreurParametres as e:
            raise ErreurDonnees("Identifiants SQL Server incomplets", str(e),
                                "Compléter .streamlit/secrets.toml puis recharger la page.") from e
        try:
            cn = pyodbc.connect(chaine, timeout=self.p.delai_connexion, autocommit=True)
        except pyodbc.Error as e:
            pilotes = ", ".join(pyodbc.drivers()) or "aucun"
            raise ErreurDonnees(
                "Connexion à SQL Server impossible", _message(e),
                f"Vérifier le serveur, la base, l'utilisateur et le mot de passe dans .streamlit/secrets.toml "
                f"({self.p.description_connexion(cible)}), et que le pilote indiqué est installé "
                f"(pilotes ODBC présents sur ce serveur : {pilotes}).") from e
        cn.timeout = self.p.delai_requete  # délai maximal d'une requête, en secondes
        return cn

    def _executer(self, nom: str, sql: str, params: list | tuple = (), fichier: str = "", cible: str = "validations") -> pd.DataFrame:
        sql = ETIQUETTE.format(nom) + sql
        if sql.count("?") != len(params):  # garde-fou : une requête mal construite ne part pas
            raise ErreurDonnees(f"Requête « {nom} » mal construite", f"{sql.count('?')} paramètres attendus, {len(params)} fournis.")
        import pyodbc
        with closing(self._connexion(cible)) as cn:
            cur = cn.cursor()
            try:
                if self.p.lecture_non_bloquante:
                    cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
                cur.execute(sql, list(params))
                colonnes = [c[0] for c in cur.description]
                lignes = [tuple(r) for r in cur.fetchall()]
            except pyodbc.Error as e:
                conseil = (f"Vérifier {fichier} : il doit renvoyer les colonnes attendues (voir l'en-tête du fichier). "
                           "Le lancer tel quel dans SSMS aide à trouver l'erreur.") if fichier else ""
                raise ErreurDonnees(f"La requête « {nom} » a échoué", _message(e), conseil) from e
        return pd.DataFrame.from_records(lignes, columns=colonnes)

    # ── Méthodes communes aux deux sources ──
    def gares(self) -> pd.DataFrame:
        fichier = FICHIERS_SQL["gares"]
        try:
            sql = nettoyer_requete(self.p.lire_sql("gares"), fichier)
        except ErreurParametres as e:
            raise ErreurDonnees(f"Requête {fichier} à corriger", str(e)) from e
        df = _nettoyer(self._executer("gares", sql, fichier=fichier), "gares")
        if "gare" not in df:
            raise ErreurDonnees(f"{fichier} ne renvoie pas de colonne « gare »",
                                f"Colonnes reçues : {', '.join(df.columns) or 'aucune'}",
                                "Nommer la colonne du libellé de la gare avec « AS gare ».")
        df = df[df["gare"].astype(bool)].drop_duplicates("gare")
        for c in COLONNES["gares"]:
            if c not in df:
                df[c] = None
        if df["ordre"].notna().any():
            df = df.sort_values("ordre", kind="stable")
        return df[COLONNES["gares"]].reset_index(drop=True)

    def derniere_validation(self, debut: datetime, fin: datetime) -> datetime | None:
        src = self._sous_requete("validations")
        sql = (f"SELECT MAX(s.date_heure) AS derniere\nFROM (\n{src}\n) AS s\n"
               "WHERE s.date_heure >= CAST(? AS datetime) AND s.date_heure < CAST(? AS datetime)")
        df = self._executer("derniere_validation", sql, [_sec(debut), _sec(fin)], FICHIERS_SQL["validations"])
        v = df.iloc[0, 0] if len(df) else None
        if v is None or pd.isna(v):
            return None
        return pd.Timestamp(v).to_pydatetime().replace(tzinfo=None)

    def fenetres(self, liste: list[tuple[str, datetime, datetime]]) -> pd.DataFrame:
        src = self._sous_requete("validations")
        blocs, params = [], []
        for nom, a, b in liste:
            if not re.fullmatch(r"[a-z0-9_]+", nom):
                raise ValueError(nom)
            blocs.append(f"SELECT '{nom}' AS fenetre, s.gare, s.sens, SUM(s.nb) AS nb\nFROM (\n{src}\n) AS s\n"
                         "WHERE s.date_heure >= CAST(? AS datetime) AND s.date_heure < CAST(? AS datetime)\n"
                         "GROUP BY s.gare, s.sens")
            params += [_sec(a), _sec(b)]
        df = self._executer("fenetres", "\nUNION ALL\n".join(blocs), params, FICHIERS_SQL["validations"])
        return _nettoyer(df, "fenetres")

    def horaire(self, debut: datetime, fin: datetime) -> pd.DataFrame:
        src = self._sous_requete("validations")
        sql = ("SELECT CAST(s.date_heure AS date) AS jour, DATEPART(HOUR, s.date_heure) AS heure,\n"
               "       s.gare, s.sens, SUM(s.nb) AS nb\n"
               f"FROM (\n{src}\n) AS s\n"
               "WHERE s.date_heure >= CAST(? AS datetime) AND s.date_heure < CAST(? AS datetime)\n"
               "GROUP BY CAST(s.date_heure AS date), DATEPART(HOUR, s.date_heure), s.gare, s.sens")
        return _nettoyer(self._executer("horaire", sql, [_sec(debut), _sec(fin)], FICHIERS_SQL["validations"]), "horaire")

    def journalier(self, debut: datetime, fin: datetime) -> pd.DataFrame:
        src = self._sous_requete("validations")
        sql = ("SELECT CAST(s.date_heure AS date) AS jour, s.gare, s.sens, SUM(s.nb) AS nb\n"
               f"FROM (\n{src}\n) AS s\n"
               "WHERE s.date_heure >= CAST(? AS datetime) AND s.date_heure < CAST(? AS datetime)\n"
               "GROUP BY CAST(s.date_heure AS date), s.gare, s.sens")
        return _nettoyer(self._executer("journalier", sql, [_sec(debut), _sec(fin)], FICHIERS_SQL["validations"]), "journalier")

    def previsions(self, debut: datetime, fin: datetime) -> pd.DataFrame:
        src = self._sous_requete("previsions")
        sql = (f"SELECT p.*\nFROM (\n{src}\n) AS p\n"
               "WHERE p.date_heure >= CAST(? AS datetime) AND p.date_heure < CAST(? AS datetime)")
        df = _nettoyer(
            self._executer("previsions", sql, [_sec(debut), _sec(fin)],
                           FICHIERS_SQL["previsions"], cible="previsions"),
            "previsions")
        manquantes = [c for c in ("date_heure", "gare", "sens", "nb") if c not in df]
        if manquantes:
            raise ErreurDonnees(f"{FICHIERS_SQL['previsions']} : colonne(s) {', '.join(manquantes)} absente(s)",
                                f"Colonnes reçues : {', '.join(df.columns)}",
                                "Nommer les colonnes avec AS date_heure, AS gare, AS sens, AS nb.")
        for c in ("nb_bas", "nb_haut"):
            if c not in df:
                df[c] = float("nan")
        # Plusieurs lignes pour la même heure et la même gare (par ex. deux exécutions du modèle) : on additionne,
        # ce qui se verrait aussitôt — la requête doit ne garder que la dernière exécution.
        return df[COLONNES["previsions"]]


    def gtfs(self, service_date: date) -> pd.DataFrame:
        """Horaires théoriques GTFS d'une date de service (connexion [sqlserver_gtfs])."""
        from parametres import sans_commentaires
        fichier = FICHIERS_SQL["gtfs"]
        try:
            sql = sans_commentaires(self.p.lire_sql("gtfs")).strip()
        except ErreurParametres as e:
            raise ErreurDonnees(f"Requête {fichier} introuvable ou illisible", str(e)) from e
        from parametres import verifier_lecture_seule
        try:
            verifier_lecture_seule(sql, fichier)
        except ErreurParametres as e:
            raise ErreurDonnees(f"Requête {fichier} refusée", str(e)) from e
        if sql.count("?") != 1:
            raise ErreurDonnees(f"Requête {fichier} à corriger",
                                f"Elle doit contenir exactement un « ? » (la date de service) ; elle en contient {sql.count('?')}.")
        # SET NOCOUNT ON : les DECLARE du lot ne renvoient pas de message qui masquerait le résultat du SELECT
        df = self._executer("gtfs_horaires", "SET NOCOUNT ON;\n" + sql, [service_date.isoformat()], fichier, cible="gtfs")
        df = df.rename(columns={c: str(c).strip().lower() for c in df.columns})
        manquantes = [c for c in ("trip_id", "stop_name", "stop_sequence", "arrival_time", "departure_time") if c not in df]
        if manquantes:
            raise ErreurDonnees("Résultat GTFS incomplet", f"Colonnes manquantes : {', '.join(manquantes)}",
                                f"Vérifier les alias de colonnes dans {fichier}.")
        for c in ("route_id", "route_short_name", "trip_headsign", "stop_code", "stop_lat", "stop_lon"):
            if c not in df:
                df[c] = None
        return df[["trip_id", "route_id", "route_short_name", "trip_headsign", "stop_code", "stop_name",
                   "stop_sequence", "arrival_time", "departure_time", "stop_lat", "stop_lon"]]

def _message(e: Exception) -> str:
    """Message d'erreur ODBC lisible (sans les codes répétés)."""
    txt = " ".join(str(a) for a in getattr(e, "args", [e]))
    txt = re.sub(r"\[Microsoft\]\[ODBC[^\]]*\](\[SQL Server\])?", "", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return re.sub(r"^(\w{5}) \[\1\] ", r"[\1] ", txt)  # code d'erreur ODBC répété en tête


# ═══════════════════════════════ Démonstration ═══════════════════════════════
class SourceDemo:
    """Chiffres simulés à partir du modèle de démonstration de l'application.

    flux(gare, jour, heure, sens)    validations « réelles » simulées pendant une heure (sens « e » ou « s »)
    attendu(gare, jour, heure, sens) valeur attendue (sert de prévision)
    """

    def __init__(self, gares: pd.DataFrame, flux: Callable, attendu: Callable, maintenant: Callable[[], datetime]):
        self._gares, self._flux, self._attendu, self._maintenant = gares, flux, attendu, maintenant

    def gares(self) -> pd.DataFrame:
        return self._gares.copy()

    def _noms(self) -> list[str]:
        return list(self._gares["gare"])

    def _compte(self, gare: str, sens: str, a: datetime, b: datetime) -> float:
        """Validations simulées sur [a, b[, en ne comptant rien après « maintenant »."""
        b = min(b, self._maintenant())
        total, h = 0.0, a.replace(minute=0, second=0, microsecond=0)
        while h < b:
            suite = h + timedelta(hours=1)
            part = (min(b, suite) - max(a, h)).total_seconds() / 3600
            if part > 0:
                total += self._flux(gare, h.date(), h.hour, sens) * part
            h = suite
        return total

    def derniere_validation(self, debut: datetime, fin: datetime) -> datetime | None:
        m = self._maintenant()
        return m if debut <= m < fin else None

    def fenetres(self, liste):
        lignes = [(nom, g, s.upper(), self._compte(g, s, a, b)) for nom, a, b in liste for g in self._noms() for s in "es"]
        return pd.DataFrame(lignes, columns=COLONNES["fenetres"])

    def horaire(self, debut, fin):
        lignes, h = [], debut.replace(minute=0, second=0, microsecond=0)
        while h < fin:
            for g in self._noms():
                for s in "es":
                    lignes.append((h.date(), h.hour, g, s.upper(), self._compte(g, s, max(h, debut), min(h + timedelta(hours=1), fin))))
            h += timedelta(hours=1)
        return pd.DataFrame(lignes, columns=COLONNES["horaire"])

    def journalier(self, debut, fin):
        lignes, j = [], debut.date()
        while datetime.combine(j, datetime.min.time()) < fin:
            a = max(datetime.combine(j, datetime.min.time()), debut)
            b = min(datetime.combine(j + timedelta(days=1), datetime.min.time()), fin)
            for g in self._noms():
                for s in "es":
                    lignes.append((j, g, s.upper(), self._compte(g, s, a, b)))
            j += timedelta(days=1)
        return pd.DataFrame(lignes, columns=COLONNES["journalier"])

    def previsions(self, debut, fin):
        lignes, h = [], debut.replace(minute=0, second=0, microsecond=0)
        while h < fin:
            for g in self._noms():
                for s in "es":
                    v = self._attendu(g, h.date(), h.hour, s)
                    lignes.append((h, g, s.upper(), v, v * .92, v * 1.08))
            h += timedelta(hours=1)
        df = pd.DataFrame(lignes, columns=COLONNES["previsions"])
        df["date_heure"] = pd.to_datetime(df["date_heure"])
        return df


def chronometrer(f: Callable, *args):
    """Exécute f(*args) et renvoie (résultat, durée en secondes) — utilisé par le script de vérification."""
    t0 = _time.perf_counter()
    r = f(*args)
    return r, _time.perf_counter() - t0
