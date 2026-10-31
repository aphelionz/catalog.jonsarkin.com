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
-- Private on the template). dcterms:spatial ("Location") and bibo:owner are public, places
-- of privately held works included (Mark, 2026-10-31).

CREATE TEMPORARY TABLE _drift_term (
  term VARCHAR(190) COLLATE utf8mb4_unicode_ci PRIMARY KEY
);

INSERT INTO _drift_term (term) VALUES
  ('dcterms:title'), ('dcterms:date'), ('dcterms:type'), ('dcterms:subject'),
  ('dcterms:medium'), ('dcterms:format'), ('dcterms:provenance'), ('dcterms:spatial'),
  ('bibo:content'), ('bibo:presentedAt'), ('bibo:owner'),
  ('schema:creator'), ('schema:itemCondition'), ('schema:mentions'),
  ('schema:artworkSurface'), ('schema:width'), ('schema:distinguishingSign'), ('schema:creditText');

-- Check: private values on public items, by property. fixed_here = 1 rows are drift this script restores.
SELECT 'before' AS phase, CONCAT(vo.prefix, ':', p.local_name) AS property,
       dt.term IS NOT NULL AS fixed_here, COUNT(*) AS private_values
FROM `value` v
JOIN item i ON i.id = v.resource_id
JOIN resource r ON r.id = v.resource_id
JOIN property p ON p.id = v.property_id
JOIN vocabulary vo ON vo.id = p.vocabulary_id
LEFT JOIN _drift_term dt ON dt.term = CONCAT(vo.prefix, ':', p.local_name)
WHERE r.is_public = 1 AND v.is_public = 0
GROUP BY property, fixed_here
ORDER BY fixed_here DESC, private_values DESC;

UPDATE `value` v
JOIN item i ON i.id = v.resource_id
JOIN resource r ON r.id = v.resource_id
JOIN property p ON p.id = v.property_id
JOIN vocabulary vo ON vo.id = p.vocabulary_id
JOIN _drift_term dt ON dt.term = CONCAT(vo.prefix, ':', p.local_name)
SET v.is_public = 1
WHERE r.is_public = 1 AND v.is_public = 0;

SELECT ROW_COUNT() AS values_made_public;

SELECT 'after' AS phase, CONCAT(vo.prefix, ':', p.local_name) AS property,
       dt.term IS NOT NULL AS fixed_here, COUNT(*) AS private_values
FROM `value` v
JOIN item i ON i.id = v.resource_id
JOIN resource r ON r.id = v.resource_id
JOIN property p ON p.id = v.property_id
JOIN vocabulary vo ON vo.id = p.vocabulary_id
LEFT JOIN _drift_term dt ON dt.term = CONCAT(vo.prefix, ':', p.local_name)
WHERE r.is_public = 1 AND v.is_public = 0
GROUP BY property, fixed_here
ORDER BY fixed_here DESC, private_values DESC;
