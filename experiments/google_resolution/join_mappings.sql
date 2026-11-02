-- DuckDB connection in DBeaver. These are provisional links, never verified merges.
-- Run after preparing the v2 root. Rows accumulate as each source is processed.
CREATE OR REPLACE TEMP VIEW foody_google AS
SELECT * FROM read_parquet(
 'D:/Projects/integration-recomentdation-fnb-data-platform-20261028/output/google-resolution-full-20261030-v2/foody_mapping.parquet'
);
CREATE OR REPLACE TEMP VIEW overture_google AS
SELECT * FROM read_parquet(
 'D:/Projects/integration-recomentdation-fnb-data-platform-20261028/output/google-resolution-full-20261030-v2/overture_mapping.parquet'
);

CREATE OR REPLACE TEMP VIEW foody_google_ids AS
SELECT *, count(*) OVER (PARTITION BY google_id_kind,google_identity_value) AS foody_ids_for_google
FROM foody_google
WHERE status='CAPTURED_FOR_REVIEW' AND google_id_kind IS NOT NULL AND google_identity_value IS NOT NULL;
CREATE OR REPLACE TEMP VIEW overture_google_ids AS
SELECT *, count(*) OVER (PARTITION BY google_id_kind,google_identity_value) AS overture_ids_for_google
FROM overture_google
WHERE status='CAPTURED_FOR_REVIEW' AND google_id_kind IS NOT NULL AND google_identity_value IS NOT NULL;

-- Every typed-ID equality is returned, including same-source collisions flagged for review.
CREATE OR REPLACE TEMP VIEW provisional_google_matches AS
SELECT f.source_id AS foody_id,o.source_id AS overture_id,
       f.source_name AS foody_name,o.source_name AS overture_name,
       f.source_address AS foody_address,o.source_address AS overture_address,
       f.google_id_kind,f.google_identity_value,
       f.google_name AS google_name_from_foody,o.google_name AS google_name_from_overture,
       f.google_address AS google_address_from_foody,o.google_address AS google_address_from_overture,
       f.google_lat AS foody_google_lat,f.google_lon AS foody_google_lon,
       o.google_lat AS overture_google_lat,o.google_lon AS overture_google_lon,
       f.coordinate_distance_m AS foody_distance_m,o.coordinate_distance_m AS overture_distance_m,
       f.foody_ids_for_google,o.overture_ids_for_google,
       f.foody_ids_for_google>1 OR o.overture_ids_for_google>1 AS identity_collision,
       'MATCH_BY_GOOGLE_ID' AS match_status,'PENDING_REVIEW' AS verification_status
FROM foody_google_ids f
JOIN overture_google_ids o USING (google_id_kind,google_identity_value);

SELECT * FROM provisional_google_matches ORDER BY identity_collision,foody_name;

-- Source progress: compare these with 57 Foody / 1531 Overture planned jobs.
SELECT source,status,count(*) AS rows
FROM (SELECT source,status FROM foody_google UNION ALL SELECT source,status FROM overture_google)
GROUP BY source,status ORDER BY source,status;

-- Different ID, absent or pending counterpart is unresolved, not a confirmed different store.
SELECT f.source_id,f.source_name,f.google_id_kind,f.google_identity_value
FROM foody_google_ids f
ANTI JOIN overture_google_ids o USING (google_id_kind,google_identity_value);
