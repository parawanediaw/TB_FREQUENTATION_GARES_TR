-- ═══════════════════════════════════════════════════════════════════════════
--  1. VALIDATIONS : une ligne par validation (ou par groupe de validations)
-- ═══════════════════════════════════════════════════════════════════════════
--  La requête doit renvoyer ces 4 colonnes, avec exactement ces noms (alias AS) :
--
--    date_heure   date et heure de la validation (type datetime ou datetime2)
--    gare         nom de la gare, écrit comme dans sql/3_gares.sql
--    sens         'E' pour une entrée, 'S' pour une sortie (toute autre valeur est ignorée)
--    nb           nombre de validations de la ligne (1 si une ligne = une validation)
--
--  Règles :
--    - une seule instruction SELECT, sans ORDER BY, sans WITH, sans « ; » ;
--    - ne garder que les validations à compter (état « ok ») ;
--    - ne pas filtrer sur les dates : l'application ajoute elle-même ce filtre.
--
--  Les noms de colonnes marqués « À ADAPTER » sont des EXEMPLES.
--  Pour vérifier : lancer la requête telle quelle dans SSMS (ajouter TOP 100 après SELECT).
--
--  Si la date est stockée en texte (NVARCHAR), la convertir, par exemple :
--      TRY_CONVERT(datetime2(0), v.date_validation, 120) AS date_heure   -- texte « 2026-11-03 10:42:15 »
--      TRY_CONVERT(datetime2(0), v.date_validation, 103) AS date_heure   -- texte « 03/11/2026 10:42:15 »
--  (Les requêtes seront alors plus lentes : un index sur une vraie colonne date-heure est préférable.)
-- ═══════════════════════════════════════════════════════════════════════════

SELECT
    v.ValidationDate                                   AS date_heure,  
    COALESCE(g.GARE, CONCAT('Code ', v.Code_Stop))   AS gare,        
    CASE v.SENS                                              
        WHEN 'entree' THEN 'E' 
        WHEN 'sortie' THEN 'S' 
    END                                                 AS SENS,
    1                                                   AS nb
FROM dbo.Validations_load_30mn AS v
LEFT JOIN dbo.Ref_Gares AS g
       ON g.GARE = v.GARE 
WHERE v.Statut = 'Ok'
