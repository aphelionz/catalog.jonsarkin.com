-- Make the internal catalog fields private: schema:box, curation:category, curation:note.
-- Idempotent. Run once on prod after `make deploy`, then tick
-- Admin > Settings > "Index full-text search" and save (bulk SQL leaves the index stale).
-- See docs/omeka-invariants.md#private-fields.

-- 1. Template flag: the source of truth. RapidEditor enforces it on every item save.
UPDATE resource_template_property rtp
JOIN property p ON p.id = rtp.property_id
JOIN vocabulary v ON v.id = p.vocabulary_id
SET rtp.is_private = 1
WHERE CONCAT(v.prefix, ':', p.local_name) IN ('schema:box', 'curation:category', 'curation:note');

-- 2. Existing values.
UPDATE value val
JOIN property p ON p.id = val.property_id
JOIN vocabulary v ON v.id = p.vocabulary_id
SET val.is_public = 0
WHERE CONCAT(v.prefix, ':', p.local_name) IN ('schema:box', 'curation:category', 'curation:note')
  AND val.is_public = 1;

-- 3. Check: still_public must be 0 on every row.
SELECT CONCAT(v.prefix, ':', p.local_name) AS term, COUNT(*) AS total, SUM(val.is_public) AS still_public
FROM value val
JOIN property p ON p.id = val.property_id
JOIN vocabulary v ON v.id = p.vocabulary_id
WHERE CONCAT(v.prefix, ':', p.local_name) IN ('schema:box', 'curation:category', 'curation:note')
GROUP BY term;
