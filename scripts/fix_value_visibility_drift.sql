-- Restore public visibility on public-item values hidden by value-visibility drift.
-- Cause: Omeka's ValueHydrator reuses value rows by position and changes is_public only
-- when the payload sends it. RapidEditor and EnrichItem sent o:is_public (ignored), so
-- private flags from schema:box / curation:* moved onto other properties (fixed in 616cf9b).
-- Idempotent: only touches rows that are still private. Re-run to see the check output.
-- Run after scripts/private_fields.sql:
--   docker compose exec -T db mariadb -u root -proot omeka < scripts/fix_value_visibility_drift.sql
-- Afterwards: Admin > Settings > "Index full-text search" (bulk SQL leaves the index stale).
--
-- Left private on purpose: schema:box, curation:category, curation:note (internal fields,
-- Private on the template). Left private for Mark: bibo:owner (one prod row names a private
-- collection's state).

CREATE TEMPORARY TABLE _drift_term (
  term VARCHAR(190) COLLATE utf8mb4_unicode_ci PRIMARY KEY
);

INSERT INTO _drift_term (term) VALUES
  ('dcterms:title'), ('dcterms:date'), ('dcterms:type'), ('dcterms:subject'),
  ('dcterms:medium'), ('dcterms:format'), ('dcterms:provenance'),
  ('bibo:content'), ('bibo:presentedAt'),
  ('schema:creator'), ('schema:itemCondition'), ('schema:mentions'),
  ('schema:artworkSurface'), ('schema:width'), ('schema:distinguishingSign'), ('schema:creditText');

-- dcterms:spatial is public (Mark, 2026-10-30), but on Artwork it is "Location": where the
-- work is now. Each drifted row was judged on its own, so these are value ids. Restored:
-- "Unknown"/"Unknow" (33), "Private Collection" (1), Addison Gallery, Andover (2), Centre
-- Pompidou (2). Left private for Mark: the place of a privately held work (Perugia 9,
-- Vermont 2, New Jersey 2, Rockport 1, Gloucester 1); owner names are public, so these
-- locate named collectors.
CREATE TEMPORARY TABLE _drift_spatial (id INT PRIMARY KEY);

INSERT INTO _drift_spatial (id) VALUES
  (139095), (139121), (152272), (152288), (152304), (152320), (152336), (152350),
  (152402), (152420), (152438), (165217), (165239), (183681), (183736), (183750),
  (183763), (183779), (183796), (183819), (183841), (185844), (185873), (185887),
  (185932), (185946), (185960), (185997), (188397), (188416), (188436), (188457),
  (188499), (188521), (188541), (188564), (188584), (188597);

-- Check: private values on public items, by property. fixed_here = 1 rows are drift this script restores.
SELECT 'before' AS phase, CONCAT(vo.prefix, ':', p.local_name) AS property,
       dt.term IS NOT NULL OR ds.id IS NOT NULL AS fixed_here, COUNT(*) AS private_values
FROM `value` v
JOIN item i ON i.id = v.resource_id
JOIN resource r ON r.id = v.resource_id
JOIN property p ON p.id = v.property_id
JOIN vocabulary vo ON vo.id = p.vocabulary_id
LEFT JOIN _drift_term dt ON dt.term = CONCAT(vo.prefix, ':', p.local_name)
LEFT JOIN _drift_spatial ds ON ds.id = v.id AND CONCAT(vo.prefix, ':', p.local_name) = 'dcterms:spatial'
WHERE r.is_public = 1 AND v.is_public = 0
GROUP BY property, fixed_here
ORDER BY fixed_here DESC, private_values DESC;

UPDATE `value` v
JOIN item i ON i.id = v.resource_id
JOIN resource r ON r.id = v.resource_id
JOIN property p ON p.id = v.property_id
JOIN vocabulary vo ON vo.id = p.vocabulary_id
LEFT JOIN _drift_term dt ON dt.term = CONCAT(vo.prefix, ':', p.local_name)
LEFT JOIN _drift_spatial ds ON ds.id = v.id AND CONCAT(vo.prefix, ':', p.local_name) = 'dcterms:spatial'
SET v.is_public = 1
WHERE r.is_public = 1 AND v.is_public = 0 AND (dt.term IS NOT NULL OR ds.id IS NOT NULL);

SELECT ROW_COUNT() AS values_made_public;

SELECT 'after' AS phase, CONCAT(vo.prefix, ':', p.local_name) AS property,
       dt.term IS NOT NULL OR ds.id IS NOT NULL AS fixed_here, COUNT(*) AS private_values
FROM `value` v
JOIN item i ON i.id = v.resource_id
JOIN resource r ON r.id = v.resource_id
JOIN property p ON p.id = v.property_id
JOIN vocabulary vo ON vo.id = p.vocabulary_id
LEFT JOIN _drift_term dt ON dt.term = CONCAT(vo.prefix, ':', p.local_name)
LEFT JOIN _drift_spatial ds ON ds.id = v.id AND CONCAT(vo.prefix, ':', p.local_name) = 'dcterms:spatial'
WHERE r.is_public = 1 AND v.is_public = 0
GROUP BY property, fixed_here
ORDER BY fixed_here DESC, private_values DESC;

-- Spatial rows still private on public items: Mark decides these one by one.
SELECT v.id AS value_id, v.resource_id AS item_id, v.value AS location
FROM `value` v
JOIN item i ON i.id = v.resource_id
JOIN resource r ON r.id = v.resource_id
JOIN property p ON p.id = v.property_id
JOIN vocabulary vo ON vo.id = p.vocabulary_id
WHERE CONCAT(vo.prefix, ':', p.local_name) = 'dcterms:spatial' AND r.is_public = 1 AND v.is_public = 0
ORDER BY v.value, v.resource_id;
