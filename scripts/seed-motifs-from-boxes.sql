-- Seed motifs (dcterms:subject, property_id=3) from box values (schema:box, property_id=1424)
-- Run on prod: docker compose exec -T db mariadb -uomeka -p'...' omeka < scripts/seed-motifs-from-boxes.sql

-- Step 1: Create temp table mapping box categories to motif tag names
CREATE TEMPORARY TABLE box_motif_map (
  box_category VARCHAR(255) COLLATE utf8mb4_unicode_ci,
  motif_tag VARCHAR(255) COLLATE utf8mb4_unicode_ci
);

INSERT INTO box_motif_map (box_category, motif_tag) VALUES
  ('desert', 'Desert'),
  ('portraits', 'Portraits'),
  ('comic', 'Comic'),
  ('ladies', 'Ladies'),
  ('ladie', 'Ladies'),
  ('creature', 'Creature'),
  ('pop culture', 'Pop Culture'),
  ('super artist', 'Super Artist'),
  ('boat', 'Boat'),
  ('fish', 'Fish'),
  ('bottle/still life', 'Bottle'),
  ('cardboard artist', 'Cardboard Artist'),
  ('words', 'Text Fragments'),
  ('ocean', 'Ocean'),
  ('tree', 'Tree'),
  ('vehicle', 'Vehicle'),
  ('building', 'Building'),
  ('guitar', 'Guitar'),
  ('spiral/mouth', 'Spiral/Mouth'),
  ('nipple', 'Nipple'),
  ('brancusi', 'Brancusi'),
  ('skull', 'Skull'),
  ('window', 'Window'),
  ('clouds', 'Cloud'),
  ('mri', 'MRI'),
  ('heart', 'Heart'),
  ('judith', 'Judith'),
  ('CBM', 'CBM'),
  ('pencil', 'Pencil');

-- Step 2: Extract category from each box value and join to mapping
-- Box value patterns:
--   "desert (box 1) 138"  → strip "(box N)" and trailing number → "desert"
--   "creature 112"        → strip trailing number → "creature"
--   "window 37"           → strip trailing number → "window"
CREATE TEMPORARY TABLE items_to_tag AS
SELECT DISTINCT
  bv.resource_id,
  m.motif_tag
FROM value bv
JOIN box_motif_map m
  ON TRIM(REGEXP_REPLACE(REGEXP_REPLACE(bv.value, '\\(box [0-9]+\\)', ''), '[0-9]+$', '')) = m.box_category
WHERE bv.property_id = 1424
  AND bv.value IS NOT NULL
  AND bv.value != ''
  -- Skip items that already have this exact motif
  AND NOT EXISTS (
    SELECT 1 FROM value ev
    WHERE ev.resource_id = bv.resource_id
      AND ev.property_id = 3
      AND ev.value = m.motif_tag
  );

-- Step 3: Preview what we're about to insert
SELECT motif_tag, COUNT(*) as items FROM items_to_tag GROUP BY motif_tag ORDER BY items DESC;
SELECT COUNT(*) as total_inserts FROM items_to_tag;

-- Step 4: Insert the motif values
INSERT INTO `value` (resource_id, property_id, `type`, `value`, is_public)
SELECT resource_id, 3, 'literal', motif_tag, 1
FROM items_to_tag;

SELECT ROW_COUNT() as rows_inserted;

-- Cleanup
DROP TEMPORARY TABLE box_motif_map;
DROP TEMPORARY TABLE items_to_tag;
