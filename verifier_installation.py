"""
Vérification de l'installation du tableau de bord « Fréquentation des gares ».

Contrôle, dans l'ordre : les réglages, le pilote ODBC, la connexion à SQL Server,
puis chacune des trois requêtes du dossier sql/ (colonnes, valeurs, durée).

    python verifier_installation.py              vérification complète
    python verifier_installation.py --colonnes   liste les colonnes des tables (aide pour écrire les requêtes)
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

# Accents et symboles lisibles même si la sortie est redirigée vers un fichier
for flux in (sys.stdout, sys.stderr):
    try:
        flux.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

LARGEUR = 72
OK, KO, AV = "✓", "✗", "⚠"
SEUIL_LENT = 3.0  # secondes
TABLES = ("Validations_load_30mn", "Ref_Gares", "validations_horaires_forecast")


def bandeau(titre: str):
    print("=" * LARGEUR)
    print(f"  {titre}")
    print("=" * LARGEUR)


class Bilan:
    def __init__(self):
        self.erreurs, self.alertes = 0, 0

    def ok(self, msg):
        print(f"   {OK} {msg}")

    def ko(self, msg, conseil=""):
        self.erreurs += 1
        print(f"   {KO} {msg}")
        if conseil:
            print(f"     → {conseil}")

    def av(self, msg, conseil=""):
        self.alertes += 1
        print(f"   {AV} {msg}")
        if conseil:
            print(f"     → {conseil}")


def nb(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def lister_colonnes(source) -> int:
    bandeau("COLONNES DES TABLES")
    marques = ", ".join("?" * len(TABLES))
    sql = ("SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
           f"WHERE TABLE_NAME IN ({marques}) ORDER BY TABLE_NAME, ORDINAL_POSITION")
    df = source._executer("colonnes", sql, list(TABLES))
    if df.empty:
        print(f"   {AV} Aucune des tables {', '.join(TABLES)} n'est visible avec ce compte, dans cette base.")
        return 1
    for (schema, table), d in df.groupby(["TABLE_SCHEMA", "TABLE_NAME"], sort=False):
        print(f"\n   {schema}.{table}")
        for col, typ in zip(d["COLUMN_NAME"], d["DATA_TYPE"]):
            print(f"      - {col:<36} {typ}")
    print()
    return 0


def main(args: list[str]) -> int:
    from donnees import ErreurDonnees, SourceSQL, chronometrer
    from parametres import FICHIERS_SQL, ErreurParametres, charger_parametres

    bandeau("VÉRIFICATION — Tableau de bord Fréquentation des gares")
    b = Bilan()

    # ── 1. Réglages ──
    print("\n[1/7] Réglages")
    try:
        p = charger_parametres()
    except ErreurParametres as e:
        b.ko(str(e))
        return fin(b)
    b.ok(f"parametres.toml lu (mode : {p.mode})")
    for a in p.avertissements:
        b.av(a)
    if p.mode != "reel":
        b.av("Mode démonstration : les requêtes SQL sont vérifiées quand même, avec .streamlit/secrets.toml.",
             'Pour afficher les vraies données, mettre mode = "reel" dans parametres.toml.')
        from parametres import FICHIER_SECRETS, _lire_toml, lire_texte
        if FICHIER_SECRETS.exists():
            p.connexion = dict(_lire_toml(lire_texte(FICHIER_SECRETS)).get("sqlserver", {}))
    try:
        p.chaine_connexion()
        b.ok(f".streamlit/secrets.toml lu ({p.description_connexion()})")
    except ErreurParametres as e:
        b.ko(str(e), "Copier .streamlit/secrets.toml.exemple en secrets.toml et le remplir.")
        return fin(b)

    # ── 2. Pilote ODBC ──
    print("\n[2/7] Pilote ODBC")
    try:
        import pyodbc
    except ImportError:
        b.ko("Le module pyodbc n'est pas installé.", "Relancer scripts\\2_installer_sur_le_serveur.bat.")
        return fin(b)
    pilote = p.connexion.get("pilote", "ODBC Driver 17 for SQL Server")
    pilotes = pyodbc.drivers()
    if pilote in pilotes:
        b.ok(f"« {pilote} » est installé")
    else:
        b.ko(f"« {pilote} » n'est pas installé sur ce serveur.",
             f"Pilotes présents : {', '.join(pilotes) or 'aucun'}. Corriger « pilote » dans secrets.toml "
             "ou installer le pilote Microsoft ODBC pour SQL Server.")
        return fin(b)

    # ── 3. Connexion ──
    print("\n[3/7] Connexion à SQL Server")
    source = SourceSQL(p)
    try:
        df, duree = chronometrer(source._executer, "connexion",
                                 "SELECT @@SERVERNAME AS serveur, DB_NAME() AS base, "
                                 "CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(32)) AS version, GETDATE() AS heure")
    except ErreurDonnees as e:
        b.ko(f"{e.titre} : {e.detail}", e.conseil)
        return fin(b)
    r = df.iloc[0]
    b.ok(f"Connecté à {r['serveur']} / {r['base']} (SQL Server {r['version']}) en {duree:.1f} s")
    heure_sql = r["heure"].to_pydatetime() if hasattr(r["heure"], "to_pydatetime") else r["heure"]
    ecart = abs((datetime.now() - heure_sql).total_seconds())
    if ecart > 120:
        b.av(f"Les horloges du serveur d'application et de SQL Server diffèrent de {ecart / 60:.0f} min.",
             "Les mentions « en retard » en dépendent : régler l'heure des serveurs.")

    if "--colonnes" in args:
        return lister_colonnes(source)

    # ── 4. Gares ──
    print(f"\n[4/7] Gares ({FICHIERS_SQL['gares']})")
    try:
        gares, duree = chronometrer(source.gares)
    except ErreurDonnees as e:
        b.ko(f"{e.titre} : {e.detail}", e.conseil or "Lancer « python verifier_installation.py --colonnes » pour voir les colonnes.")
        gares = None
    if gares is not None:
        if gares.empty:
            b.ko("La requête ne renvoie aucune gare.")
        else:
            noms = list(gares["gare"])
            b.ok(f"{len(noms)} gares : {', '.join(noms[:8])}{' …' if len(noms) > 8 else ''}")
            if gares["ordre"].isna().all():
                b.ok("ordre d'affichage : celui des lignes renvoyées (pas de colonne « ordre »)")
            if gares["zone"].notna().any():
                b.ok(f"zones lues dans la requête : {', '.join(sorted(set(gares['zone'].dropna().astype(str))))}")
            for z, lst in p.zones.items():
                inconnues = [g for g in lst if g not in noms]
                if inconnues:
                    b.av(f"parametres.toml, zone « {z} » : gare(s) inconnue(s) {', '.join(inconnues)}",
                         "Écrire les noms exactement comme dans la liste ci-dessus.")

    # ── 5. Validations ──
    print(f"\n[5/7] Validations ({FICHIERS_SQL['validations']})")
    maint = datetime.now().replace(microsecond=0)
    minuit = maint.replace(hour=0, minute=0, second=0)
    try:
        der, duree = chronometrer(source.derniere_validation, minuit - timedelta(days=3), maint + timedelta(hours=1))
        b.ok(f"requête valide ({duree:.1f} s)")
        if der is None:
            b.av("Aucune validation sur les 3 derniers jours.",
                 "Vérifier le filtre de la requête (état « ok ») et que le chargement des validations tourne.")
        else:
            retard = (maint - der).total_seconds() / 60
            msg = f"dernière validation : {der:%d/%m/%Y %H:%M:%S} (il y a {retard:.0f} min)"
            (b.ok if retard <= 90 else b.av)(msg)
        jour, duree = chronometrer(source.fenetres, [("jour", minuit, maint + timedelta(seconds=1))])
        e, s = jour.loc[jour["sens"] == "E", "nb"].sum(), jour.loc[jour["sens"] == "S", "nb"].sum()
        b.ok(f"aujourd'hui : {nb(e)} entrées, {nb(s)} sorties ({duree:.1f} s)")
        if e > 0 and s == 0:
            b.av("Aucune sortie comptée.", "Vérifier la valeur des sorties dans le CASE de la colonne « sens ».")
        if s > 0 and e == 0:
            b.av("Aucune entrée comptée.", "Vérifier la valeur des entrées dans le CASE de la colonne « sens ».")
        if gares is not None and not jour.empty:
            hors_liste = sorted(set(jour["gare"]) - set(gares["gare"]))
            if hors_liste:
                b.av(f"Gares présentes dans les validations mais absentes de la liste des gares : {', '.join(hors_liste[:10])}",
                     "Vérifier la jointure avec Ref_Gares (code de gare) ou compléter Ref_Gares.")
        semaines, duree = chronometrer(source.horaire, minuit - timedelta(days=7 * p.semaines_reference), minuit)
        jours = semaines["jour"].nunique() if not semaines.empty else 0
        b.ok(f"historique des {p.semaines_reference} dernières semaines : {jours} jour(s) avec des validations ({duree:.1f} s)")
        if duree > SEUIL_LENT:
            b.av(f"Requête lente ({duree:.1f} s pour {p.semaines_reference} semaines).",
                 "Un index sur la colonne date-heure de la table des validations accélère toutes les requêtes "
                 "(à voir avec l'administrateur de la base).")
        an = maint.replace(year=maint.year - 1)
        un_an, _ = chronometrer(source.journalier, an - timedelta(days=1), an + timedelta(days=1))
        (b.ok if not un_an.empty else b.av)(
            f"historique de l'an dernier ({an:%d/%m/%Y}) : {'présent' if not un_an.empty else 'absent'}"
            + ("" if not un_an.empty else " — les comparaisons avec 2025 seront masquées"))
    except ErreurDonnees as e:
        b.ko(f"{e.titre} : {e.detail}", e.conseil or "Lancer « python verifier_installation.py --colonnes ».")

    # ── 6. Prévisions ──
    print(f"\n[6/7] Prévisions ({FICHIERS_SQL['previsions']})")
    try:
        prev, duree = chronometrer(source.previsions, minuit, minuit + timedelta(days=2))
        b.ok(f"requête valide ({duree:.1f} s)")
        if prev.empty:
            b.av("Aucune prévision pour aujourd'hui et demain.",
                 "Le bloc « Prévision » affichera « Aucune prévision ». Vérifier que la table couvre cette période.")
        else:
            b.ok(f"{len(prev)} lignes pour aujourd'hui et demain, de {prev['date_heure'].min():%d/%m %Hh} "
                 f"à {prev['date_heure'].max():%d/%m %Hh}")
            doublons = prev.duplicated(["date_heure", "gare", "sens"]).sum()
            if doublons:
                b.av(f"{doublons} prévisions en double (même heure, même gare, même sens).",
                     "La table garde sans doute plusieurs exécutions du modèle : ne garder que la dernière dans la requête.")
            if prev["nb_bas"].isna().all():
                b.ok("pas de colonnes nb_bas / nb_haut : le graphique n'aura pas de bande d'intervalle")
            sans_sortie = not (prev["sens"] == "S").any()
            if sans_sortie:
                b.ok("prévisions des entrées seulement (suffisant pour la page)")
    except ErreurDonnees as e:
        b.ko(f"{e.titre} : {e.detail}", e.conseil)

    # ── 7. GTFS ──
    print(f"\n[7/7] Horaires GTFS ({FICHIERS_SQL['gtfs']})")
    try:
        from gtfs import circulations, construire_reseau
        jour = maint.date()
        gt, duree = chronometrer(source.gtfs, jour)
        if gt.empty:
            b.av(f"Aucun horaire GTFS pour le {jour:%d/%m/%Y}.",
                 "Vérifier les dates et jours actifs de calendar (et calendar_dates s'il y a des exceptions).")
        else:
            b.ok(f"{gt['trip_id'].nunique()} circulations, {len(gt)} passages en gare ({duree:.1f} s)")
            if gares is not None and not gares.empty:
                reseau = construire_reseau(gares, gt)
                d = circulations(gt.assign(service_date=jour), maint, reseau)
                (b.ok if reseau["geo"] else b.av)(
                    "ligne tracée avec les coordonnées des gares" if reseau["geo"] else
                    "aucune coordonnée de gare : la carte garde le tracé dessiné (positions approximatives)")
                if d["non_associes"]:
                    b.av(f"Arrêts GTFS non reconnus dans Ref_Gares : {', '.join(d['non_associes'][:8])}",
                         "Ces arrêts sont ignorés. Harmoniser les noms ou compléter les coordonnées de Ref_Gares.")
                b.ok(f"en ce moment : {len(d['trains'])} train(s) en ligne selon les horaires")
    except ErreurDonnees as e:
        b.ko(f"{e.titre} : {e.detail}", e.conseil or "Vérifier [sqlserver_gtfs] dans .streamlit/secrets.toml.")
    return fin(b)


def fin(b: Bilan) -> int:
    print()
    print("=" * LARGEUR)
    if b.erreurs:
        print(f"  RÉSULTAT : {KO} {b.erreurs} problème(s) à corriger" + (f", {b.alertes} alerte(s)" if b.alertes else ""))
    elif b.alertes:
        print(f"  RÉSULTAT : {OK} prêt, avec {b.alertes} alerte(s) à lire ci-dessus")
    else:
        print(f"  RÉSULTAT : {OK} tout est prêt — lancer scripts\\3_lancer.bat")
    print("=" * LARGEUR)
    return 1 if b.erreurs else 0


def run_verification(args: list[str] | None = None) -> int:
    return main(list(sys.argv[1:] if args is None else args))


if __name__ == "__main__":
    sys.exit(run_verification())
