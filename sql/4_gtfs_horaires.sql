/*
GTFS THEORIQUE - SQL Server
Paramètre : ? = date de service au format YYYY-MM-DD.
Hypothèses : tables GTFS standard importées dans dbo : calendar, trip, route,
stop_time, stop. Les noms de colonnes sont ceux du standard GTFS.
Si ton import a renommé des colonnes, adapte uniquement ce fichier.
La table gare peut servir de référentiel complémentaire, mais n'est pas nécessaire
pour le calcul des horaires si stop contient stop_name/stop_lat/stop_lon.
*/

DECLARE @service_date DATE = ?;
DECLARE @weekday INT =
    DATEDIFF(DAY, CONVERT(DATE, '19000101'), @service_date) % 7;

SELECT
    CAST(t.trip_id AS VARCHAR(100)) AS trip_id,
    CAST(t.route_id AS VARCHAR(100)) AS route_id,
    CAST(r.route_short_name AS VARCHAR(100)) AS route_short_name,
    CAST(t.trip_headsign AS VARCHAR(200)) AS trip_headsign,
    CAST(st.stop_id AS VARCHAR(100)) AS stop_code,
    CAST(s.stop_name AS VARCHAR(200)) AS stop_name,
    TRY_CONVERT(INT, st.stop_sequence) AS stop_sequence,
    CAST(st.arrival_time AS VARCHAR(20)) AS arrival_time,
    CAST(st.departure_time AS VARCHAR(20)) AS departure_time,
    s.stop_lat AS stop_lat,
    s.stop_lon AS stop_lon
FROM dbo.trip AS t
INNER JOIN dbo.calendar AS c
    ON c.service_id = t.service_id
INNER JOIN dbo.route AS r
    ON r.route_id = t.route_id
INNER JOIN dbo.stop_time AS st
    ON st.trip_id = t.trip_id
INNER JOIN dbo.stop AS s
    ON s.id = st.stop_id
WHERE
    TRY_CONVERT(DATE, c.start_date, 112) <= @service_date
    AND TRY_CONVERT(DATE, c.end_date, 112) >= @service_date
    AND TRY_CONVERT(INT,
        CASE @weekday
            WHEN 0 THEN c.monday
            WHEN 1 THEN c.tuesday
            WHEN 2 THEN c.wednesday
            WHEN 3 THEN c.thursday
            WHEN 4 THEN c.friday
            WHEN 5 THEN c.saturday
            WHEN 6 THEN c.sunday
        END
    ) = 1

ORDER BY
    t.trip_id,
    TRY_CONVERT(INT, st.stop_sequence);
