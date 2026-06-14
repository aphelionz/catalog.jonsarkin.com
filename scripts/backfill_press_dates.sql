-- Backfill dcterms:date (property 7) for press/Writing items (resource_template_id 3)
-- from the ISO date embedded in schema:creditText (property 1343), e.g.
--   Jon Sarkin, "Title", 2009-05-27. Published on jsarkin.com.
-- Idempotent: only inserts where no dcterms:date row already exists.
-- Item 8019 ("photos") has no date in creditText and is skipped (needs manual date).

INSERT INTO `value` (resource_id, property_id, type, value, is_public)
SELECT v.resource_id, 7, 'literal',
       REGEXP_SUBSTR(v.value, '[0-9]{4}-[0-9]{2}-[0-9]{2}'), 1
FROM `value` v
JOIN resource r ON r.id = v.resource_id
WHERE v.property_id = 1343
  AND r.resource_template_id = 3
  AND r.is_public = 1
  AND v.value REGEXP '[0-9]{4}-[0-9]{2}-[0-9]{2}'
  AND NOT EXISTS (
      SELECT 1 FROM `value` v2
      WHERE v2.resource_id = v.resource_id AND v2.property_id = 7
  );

SELECT COUNT(*) AS dated_press_items
FROM `value` v JOIN resource r ON r.id = v.resource_id
WHERE r.resource_template_id = 3 AND r.is_public = 1 AND v.property_id = 7;
