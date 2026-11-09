"""
Lecture des réglages du tableau de bord.

    parametres.toml           → réglages de l'application (mode, horaires, JOJ, zones, fichiers SQL)
    .streamlit/secrets.toml   → identifiants de connexion à SQL Server (jamais partagés)

Ce module ne dépend pas de Streamlit : il sert aussi au script verifier_installation.py.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

try:  # Python 3.11 et plus
    import tomllib as _tomllib

    def _lire_toml(texte: str) -> dict:
        return _tomllib.loads(texte)
except ModuleNotFoundError:  # Python 3.9 / 3.10 : paquet « toml », installé avec Streamlit
    import toml as _toml

    def _lire_toml(texte: str) -> dict:
        return _toml.loads(texte)


DOSSIER = Path(__file__).resolve().parent
FICHIER_PARAMETRES = DOSSIER / "parametres.toml"
FICHIER_SECRETS = DOSSIER / ".streamlit" / "secrets.toml"

MODES = ("reel", "demo")
FICHIERS_SQL = {
    "validations": "sql/1_validations.sql",
    "previsions": "sql/2_previsions.sql",
    "gares": "sql/3_gares.sql",
    "gtfs": "sql/4_gtfs_horaires.sql",
}


class ErreurParametres(Exception):
    """Réglage absent ou invalide : le message dit quoi corriger, et où."""


def lire_texte(chemin: Path) -> str:
    """Lit un fichier texte enregistré en UTF-8 (avec ou sans BOM) ou en UTF-16 (SSMS, Bloc-notes)."""
    brut = chemin.read_bytes()
    if brut.startswith((b"\xff\xfe", b"\xfe\xff")):
        return brut.decode("utf-16")
    try:
        return brut.decode("utf-8-sig")
    except UnicodeDecodeError:
        return brut.decode("cp1252")


def _plages_horaires(valeur, ou: str) -> list[tuple[int, int]]:
    if not isinstance(valeur, list) or len(valeur) != 7:
        raise ErreurParametres(f"{ou} : il faut 7 paires [ouverture, fermeture], du lundi au dimanche.")
    plages = []
    for i, p in enumerate(valeur):
        if not (isinstance(p, list) and len(p) == 2 and all(isinstance(x, int) for x in p) and 0 <= p[0] < p[1] <= 24):
            raise ErreurParametres(f"{ou} : la paire n° {i + 1} doit ressembler à [5, 23] (heures entières, 24 = minuit).")
        plages.append((p[0], p[1]))
    return plages


@dataclass
class Parametres:
    # [application]
    mode: str = "demo"
    afficher_pages_simulees: bool = False
    rafraichissement_secondes: int = 60
    alerte_retard_minutes: int = 45
    decalage_utc_heures: float = 0.0
    # [historique]
    semaines_reference: int = 4
    # [horaires]
    horaires_normaux: list = field(default_factory=lambda: [(5, 23)] * 6 + [(6, 22)])
    # [joj]
    joj_afficher: bool = True
    joj_debut: date = date(2026, 10, 31)
    joj_fin: date = date(2026, 11, 13)
    joj_horaires: tuple = (5, 24)
    joj_objectif: int = 0            # objectif de voyageurs cumulés sur les Jeux (0 = aucun)
    # [exploitation]
    parc_rames: int = 8
    capacite_rame: int = 568
    retard_simule_max: int = 4
    # [zones]
    zones: dict = field(default_factory=dict)
    # [base]
    lecture_non_bloquante: bool = True
    # [securite]
    afficher_details_erreurs: bool = False
    # .streamlit/secrets.toml → [sqlserver]
    connexion: dict = field(default_factory=dict)
    connexion_previsions: dict = field(default_factory=dict)
    connexion_gtfs: dict = field(default_factory=dict)
    # Informations de diagnostic
    origine: str = "valeurs par défaut"
    avertissements: list = field(default_factory=list)

    # ── Fichiers SQL ──
    def chemin_sql(self, nom: str) -> Path:
        return DOSSIER / FICHIERS_SQL[nom]

    def lire_sql(self, nom: str) -> str:
        chemin = self.chemin_sql(nom)
        if not chemin.exists():
            raise ErreurParametres(f"Fichier introuvable : {FICHIERS_SQL[nom]}")
        return lire_texte(chemin)

    def signature(self) -> str:
        """Empreinte du mode, de la base visée et des requêtes : change dès qu'un fichier SQL est modifié,
        ce qui vide le cache des données sans redémarrer l'application."""
        h = hashlib.sha1(self.mode.encode())
        for connexion in (self.connexion, self.connexion_previsions, self.connexion_gtfs):
            for cle in ("serveur", "base", "utilisateur"):
                h.update(str(connexion.get(cle, "")).encode())
        for nom in FICHIERS_SQL:
            chemin = self.chemin_sql(nom)
            h.update(chemin.read_bytes() if chemin.exists() else b"-")
        return h.hexdigest()[:16]

    # ── Connexion ──
    def chaine_connexion(self, cible: str = "validations") -> str:
        c = self.connexion_previsions if cible == "previsions" else (self.connexion_gtfs if cible == "gtfs" else self.connexion)
        manquants = [k for k in ("serveur", "base", "utilisateur", "mot_de_passe") if not str(c.get(k, "")).strip()]
        if manquants:
            section = "sqlserver_previsions" if cible == "previsions" else ("sqlserver_gtfs" if cible == "gtfs" else "sqlserver")
            raise ErreurParametres(
                f"Identifiants SQL Server incomplets dans .streamlit/secrets.toml (section [{section}]) : "
                + ", ".join(manquants) + " manquant(s).")

        def brut(v) -> str:  # valeur protégée par des accolades (mot de passe avec ; ou =)
            return "{" + str(v).replace("}", "}}") + "}"

        return (f"DRIVER={brut(c.get('pilote', 'ODBC Driver 17 for SQL Server'))};"
                f"SERVER={c['serveur']};DATABASE={c['base']};"
                f"UID={brut(c['utilisateur'])};PWD={brut(c['mot_de_passe'])};"
                f"Encrypt={c.get('chiffrement', 'no')};"
                f"TrustServerCertificate={c.get('faire_confiance_certificat', 'yes')};"
                "ApplicationIntent=ReadOnly;APP=Tableau de bord frequentation")

    @property
    def delai_connexion(self) -> int:
        return int(self.connexion.get("delai_connexion_secondes", 10))

    @property
    def delai_requete(self) -> int:
        return int(self.connexion.get("delai_requete_secondes", 60))

    def description_connexion(self, cible: str = "validations") -> str:
        c = self.connexion_previsions if cible == "previsions" else (self.connexion_gtfs if cible == "gtfs" else self.connexion)
        return f"serveur {c.get('serveur', '?')}, base {c.get('base', '?')}, utilisateur {c.get('utilisateur', '?')}"


def charger_parametres() -> Parametres:
    """Lit parametres.toml (et les identifiants en mode réel). Lève ErreurParametres avec un message clair."""
    p = Parametres()
    if not FICHIER_PARAMETRES.exists():
        p.avertissements.append("parametres.toml introuvable : l'application tourne en mode démonstration.")
        return p
    try:
        d = _lire_toml(lire_texte(FICHIER_PARAMETRES))
    except Exception as e:  # erreur de syntaxe TOML
        raise ErreurParametres(f"parametres.toml n'est pas lisible : {e}") from e
    p.origine = "parametres.toml"

    app = d.get("application", {})
    p.mode = str(app.get("mode", p.mode)).strip().lower().replace("é", "e")
    if p.mode not in MODES:
        raise ErreurParametres(f"parametres.toml, [application] mode = « {app.get('mode')} » : valeurs possibles « reel » ou « demo ».")
    p.afficher_pages_simulees = bool(app.get("afficher_pages_simulees", p.afficher_pages_simulees))
    p.rafraichissement_secondes = max(15, int(app.get("rafraichissement_secondes", p.rafraichissement_secondes)))
    p.alerte_retard_minutes = max(5, int(app.get("alerte_retard_minutes", p.alerte_retard_minutes)))
    p.decalage_utc_heures = float(app.get("decalage_utc_heures", p.decalage_utc_heures))

    p.semaines_reference = min(12, max(1, int(d.get("historique", {}).get("semaines_reference", p.semaines_reference))))

    hor = d.get("horaires", {})
    if "normaux" in hor:
        p.horaires_normaux = _plages_horaires(hor["normaux"], "parametres.toml, [horaires] normaux")

    joj = d.get("joj", {})
    p.joj_afficher = bool(joj.get("afficher", p.joj_afficher))
    for cle in ("debut", "fin"):
        if cle in joj:
            v = joj[cle]
            if not isinstance(v, date):
                raise ErreurParametres(f"parametres.toml, [joj] {cle} : écrire la date sans guillemets, par exemple 2026-10-31.")
            setattr(p, f"joj_{cle}", v)
    if p.joj_fin < p.joj_debut:
        raise ErreurParametres("parametres.toml, [joj] : la date de fin précède la date de début.")
    if "horaires" in joj:
        h = joj["horaires"]
        if not (isinstance(h, list) and len(h) == 2 and 0 <= h[0] < h[1] <= 24):
            raise ErreurParametres("parametres.toml, [joj] horaires : écrire par exemple [5, 24] (24 = minuit).")
        p.joj_horaires = (int(h[0]), int(h[1]))

    if "objectif" in joj:
        p.joj_objectif = max(0, int(joj["objectif"]))
    ex = d.get("exploitation", {})
    p.parc_rames = max(1, int(ex.get("parc_rames", p.parc_rames)))
    p.capacite_rame = max(1, int(ex.get("capacite_rame", p.capacite_rame)))
    p.retard_simule_max = min(30, max(0, int(ex.get("retard_simule_max", p.retard_simule_max))))

    for nom, gares in d.get("zones", {}).items():
        if not (isinstance(gares, list) and gares and all(isinstance(g, str) for g in gares)):
            raise ErreurParametres(f"parametres.toml, [zones] « {nom} » : écrire une liste de noms de gares, "
                                   'par exemple "Zone Dakar" = ["Dakar", "Colobane"].')
        p.zones[str(nom)] = [g.strip() for g in gares]

    p.lecture_non_bloquante = bool(d.get("base", {}).get("lecture_non_bloquante", p.lecture_non_bloquante))
    p.afficher_details_erreurs = bool(d.get("securite", {}).get("afficher_details_erreurs", p.afficher_details_erreurs))

    if p.mode == "reel":
        if not FICHIER_SECRETS.exists():
            raise ErreurParametres("Mode réel : le fichier .streamlit/secrets.toml est introuvable. "
                                   "Copier .streamlit/secrets.toml.exemple sous ce nom et y saisir les identifiants SQL Server.")
        try:
            s = _lire_toml(lire_texte(FICHIER_SECRETS))
        except Exception as e:
            raise ErreurParametres(f".streamlit/secrets.toml n'est pas lisible : {e}") from e
        if "sqlserver" not in s:
            raise ErreurParametres(".streamlit/secrets.toml : la section [sqlserver] est absente.")
        p.connexion = dict(s["sqlserver"])
        if "sqlserver_previsions" not in s:
            raise ErreurParametres(
                "Mode réel : la section [sqlserver_previsions] est absente de .streamlit/secrets.toml. "
                "Elle doit contenir les identifiants du serveur qui héberge la table de prévisions.")
        p.connexion_previsions = dict(s["sqlserver_previsions"])
        # Si le GTFS est dans la base principale, la section dédiée est facultative.
        p.connexion_gtfs = dict(s.get("sqlserver_gtfs", s["sqlserver"]))
    return p


# ── Contrôle des requêtes fournies par l'utilisateur ──
def sans_commentaires(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


INTERDITS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|GRANT|REVOKE|DENY|BACKUP|RESTORE|"
    r"SHUTDOWN|RECONFIGURE|BULK|INTO|OPENROWSET|OPENDATASOURCE|OPENQUERY|xp_\w+|sp_\w+)\b", re.I)


def verifier_lecture_seule(code: str, fichier: str) -> None:
    """Sécurité : les requêtes du dossier sql/ ne doivent que LIRE. Toute instruction d'écriture ou
    d'exécution (INSERT, UPDATE, DELETE, EXEC, SELECT … INTO, xp_…) est refusée avant l'envoi."""
    m = INTERDITS.search(sans_commentaires(code))
    if m:
        raise ErreurParametres(f"{fichier} contient « {m.group(0)} » : seules les lectures (SELECT) sont autorisées.")


def nettoyer_requete(sql: str, fichier: str) -> str:
    """Retire les commentaires, les lignes GO et les « ; » de fin. Refuse le caractère « ? »,
    réservé aux paramètres que l'application envoie à SQL Server."""
    lignes = [l for l in sans_commentaires(sql).splitlines() if l.strip().upper() != "GO"]
    code = "\n".join(l.rstrip() for l in lignes if l.strip()).strip()
    while code.endswith(";"):
        code = code[:-1].rstrip()
    if not code:
        raise ErreurParametres(f"{fichier} est vide.")
    if "?" in code:
        raise ErreurParametres(f"{fichier} contient le caractère « ? » : le retirer (il est réservé aux paramètres de l'application).")
    verifier_lecture_seule(code, fichier)
    return code


def preparer_sous_requete(sql: str, fichier: str) -> str:
    """Prépare une requête destinée à être utilisée comme sous-requête (FROM ( … ) AS s)."""
    code = nettoyer_requete(sql, fichier)
    if re.match(r"\s*WITH\b", code, flags=re.I):
        raise ErreurParametres(f"{fichier} commence par WITH : écrire une seule instruction SELECT "
                               "(ou créer une vue et faire SELECT … FROM la_vue).")
    if re.search(r"\bORDER\s+BY\b", code, flags=re.I) and not re.search(r"\b(TOP|OFFSET)\b", code, flags=re.I):
        raise ErreurParametres(f"{fichier} contient ORDER BY : le retirer, la requête est utilisée comme sous-requête.")
    if ";" in code:
        raise ErreurParametres(f"{fichier} contient plusieurs instructions (« ; ») : n'en garder qu'une, un SELECT.")
    return code
