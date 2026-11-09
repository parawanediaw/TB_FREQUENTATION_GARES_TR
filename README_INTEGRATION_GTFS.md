# Version 4 — Fréquentation, JOJ, trains GTFS

## Pages
| Page | Source | Ce qui est affiché |
|---|---|---|
| Fréquentation | validations (lots de 30 min), prévisions | indicateurs du jour, **gares les plus affluentes** (top entrées / top sorties du dernier lot, tendance, pic du jour), **résumé JOJ**, prévision 24 h, affluence habituelle, flux par gare |
| JOJ Dakar 2026 | validations, prévisions | détail de l'impact des Jeux (voir plus bas) |
| Suivi des trains | GTFS théorique | position de chaque train calculée à l'instant présent, arrêts en gare compris ; retards simulés |
| Serpent de charge | GTFS + validations | charge estimée par tronçon sur la dernière fenêtre de 60 min |
| Écran TV | — | désactivé (code conservé) |

## Analyses JOJ : définitions
- **À date** : chaque comparaison s'arrête à la même heure que la dernière validation reçue.
- **Période normale** : même jour de la semaine, moyenne des 4 semaines avant les Jeux (`[historique] semaines_reference`).
- **2025** : même jour de la semaine un an plus tôt (364 jours), pour neutraliser l'effet du jour de la semaine.
- **Projection fin des JOJ** : réel + prévisions du modèle ; un jour sans prévision prend la moyenne normale (signalé dans la légende).
- **Top gares** : hausse des entrées des jours terminés vs la normale de ces mêmes jours.
- **Profil horaire** (à la place du donut « Produits JOJ ») : la requête des validations ne contient pas le type de titre. Pour le donut, ajouter une colonne `produit` à `sql/1_validations.sql` : il pourra être ajouté.

## Trains (GTFS)
- La ligne est construite à partir de **Ref_Gares** (ordre, coordonnées) : toutes les gares réelles, PK calculés sur les coordonnées.
- Les arrêts GTFS sont reliés aux gares par leur nom (accents, majuscules et « Gare de » ignorés), sinon par leur position (< 1,5 km). Les arrêts non reconnus sont listés sur la page et dans `verifier_installation.py`.
- La carte reçoit les points de passage de chaque train et les interpole elle-même en continu : le train avance en temps réel, s'arrête en gare, et l'écart avec la position théorique (sans retard) est visible.
- Ponctualité : un train est « à l'heure » sous 3 minutes de retard.
- Les services du jour **et de la veille** sont lus (horaires GTFS au-delà de 24:00).
- Non géré : `calendar_dates` (exceptions de service).

## Réglages ajoutés (`parametres.toml`)
`[joj] objectif`, `[exploitation] parc_rames`, `capacite_rame`, `retard_simule_max`.
