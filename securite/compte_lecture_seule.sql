/* Compte SQL en LECTURE SEULE pour le tableau de bord.
   A executer par un administrateur SQL Server, sur CHAQUE serveur (validations, previsions, GTFS) :
   ne garder que le bloc de la base concernee. Remplacer le mot de passe (12 caracteres mini). */

USE master;
CREATE LOGIN tdb_lecture WITH PASSWORD = 'A_REMPLACER_Mot2Passe!', CHECK_POLICY = ON, DEFAULT_DATABASE = DWHBO;
GO

/* ---- Base des validations et des gares ---- */
USE DWHBO;
CREATE USER tdb_lecture FOR LOGIN tdb_lecture;
GRANT SELECT ON dbo.Validations_load_30mn TO tdb_lecture;
GRANT SELECT ON dbo.Ref_Gares            TO tdb_lecture;
DENY INSERT, UPDATE, DELETE, ALTER, EXECUTE TO tdb_lecture;   -- aucun droit d'ecriture
GO

/* ---- Base des previsions (autre serveur : adapter le nom de base) ----
USE PREV;
CREATE USER tdb_lecture FOR LOGIN tdb_lecture;
GRANT SELECT ON dbo.previsions_validations_horaires TO tdb_lecture;
DENY INSERT, UPDATE, DELETE, ALTER, EXECUTE TO tdb_lecture;
GO */

/* ---- Base GTFS (autre serveur : adapter le nom de base) ----
USE GTFS;
CREATE USER tdb_lecture FOR LOGIN tdb_lecture;
GRANT SELECT ON dbo.trip TO tdb_lecture;  GRANT SELECT ON dbo.calendar  TO tdb_lecture;
GRANT SELECT ON dbo.route TO tdb_lecture; GRANT SELECT ON dbo.stop_time TO tdb_lecture;
GRANT SELECT ON dbo.stop TO tdb_lecture;
DENY INSERT, UPDATE, DELETE, ALTER, EXECUTE TO tdb_lecture;
GO */

/* Verification : doit renvoyer uniquement des lignes SELECT */
-- EXECUTE AS LOGIN = 'tdb_lecture'; SELECT * FROM fn_my_permissions(NULL, 'DATABASE'); REVERT;
