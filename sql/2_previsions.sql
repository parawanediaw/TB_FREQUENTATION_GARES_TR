-- ═══════════════════════════════════════════════════════════════════════════
--  2. PRÉVISIONS : une ligne par heure, par gare et par sens
-- ═══════════════════════════════════════════════════════════════════════════
--  Colonnes à renvoyer (alias AS) :
--
--    date_heure   début de l'heure prévue (type datetime), par exemple 2026-11-03 08:00
--    gare         nom de la gare, écrit comme dans sql/3_gares.sql
--    sens         'E' pour les entrées, 'S' pour les sorties
--    nb           nombre de validations prévu pour cette heure
--
--  Colonnes facultatives :
--    nb_bas, nb_haut   bornes basse et haute de la prévision : elles dessinent la bande
--                      autour de la courbe. Sans elles, pas de bande.
--
--  Règles : une seule instruction SELECT, sans ORDER BY, sans WITH, sans « ; ».
--  Si la table garde plusieurs exécutions du modèle, ne garder que la plus récente
--  (exemple en bas de la requête), sinon les prévisions seraient additionnées.
--
--  Les noms de colonnes marqués « À ADAPTER » sont des EXEMPLES.
-- ═══════════════════════════════════════════════════════════════════════════
SELECT
    f.prediction_time AS date_heure,
    f.GARE AS gare, 
    CASE f.type_evenement                                                       
        WHEN 'entree' THEN 'E'
        WHEN 'sortie' THEN 'S'
	WHEN 'Validation en entrée' THEN 'E'
        WHEN 'Validation en sortie' THEN 'S'
    END AS sens,
    f.predicted_value AS nb,
    lower_bound as nb_bas, 
    upper_bound as nb_haut
FROM dbo.previsions_validations_horaires f
          
-- Plusieurs exécutions du modèle dans la table ? Ne garder que la dernière, par exemple :
-- WHERE f.date_execution = (SELECT MAX(date_execution) FROM dbo.validations_horaires_forecast)
