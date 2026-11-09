-- ═══════════════════════════════════════════════════════════════════════════
--  3. GARES : une ligne par gare
-- ═══════════════════════════════════════════════════════════════════════════
--  Colonne obligatoire :
--    gare        nom affiché, écrit exactement comme dans les requêtes 1 et 2
--
--  Colonnes facultatives (supprimer les lignes inutiles) :
--    ordre       position de la gare sur la ligne (1 = Dakar, …) ;
--                sans elle, l'ordre des lignes renvoyées est gardé
--    zone        zone de la gare ; sans elle, les zones viennent de parametres.toml
--    latitude, longitude
--
--  Ici, ORDER BY est permis.
--  Les noms de colonnes marqués « À ADAPTER » sont des EXEMPLES.
-- ═══════════════════════════════════════════════════════════════════════════
SELECT
    g.ID_GARE AS ordre,
    g.GARE      AS gare,        
    g.latitude     AS Latitude,    
    g.longitude    AS Longitude    
FROM dbo.Ref_Gares AS g
ORDER BY g.ID_GARE             
