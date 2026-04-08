-- Normalize medium values: split composite strings into atomic multi-value rows
-- Property ID 26 = dcterms:medium
-- Run inside: docker compose exec -T db mariadb -u root -proot omeka < scripts/normalize_medium.sql

-- Phase 1: Build expansion table (resource_id → atomic medium)
DROP TABLE IF EXISTS _medium_expand;
CREATE TEMPORARY TABLE _medium_expand (
  resource_id INT,
  atomic VARCHAR(64) COLLATE utf8mb4_unicode_ci
);

INSERT INTO _medium_expand (resource_id, atomic)
SELECT v.resource_id, 'Ink' FROM `value` v WHERE v.property_id = 26 AND v.`value` IN (
  'Ink and colored pencil', 'Ink and marker', 'Ink and graphite', 'Ink and watercolor',
  'Ink and collage', 'Ink and acrylic paint', 'Ink and ballpoint pen',
  'Ink, marker, and colored pencil', 'Ink, colored pencil, and marker',
  'Ink, colored pencil, and watercolor', 'Ink, colored pencil, and graphite',
  'Ink, colored pencil, and ballpoint pen', 'Ink, watercolor, and colored pencil',
  'Ink, marker, and watercolor',
  'Colored pencil and ink', 'Graphite and ink', 'Oil pastel and ink',
  'Marker and ink', 'Colored pencil, marker, and ink', 'Marker, colored pencil, and ink',
  'Acrylic paint, ink, and marker'
);

INSERT INTO _medium_expand (resource_id, atomic)
SELECT v.resource_id, 'Marker' FROM `value` v WHERE v.property_id = 26 AND v.`value` IN (
  'Ink and marker', 'Marker and ink', 'Marker and colored pencil', 'Marker and pen',
  'Marker and collage', 'Marker and acrylic paint', 'Marker and crayon', 'Marker, colored pencil',
  'Ink, marker, and colored pencil', 'Ink, colored pencil, and marker',
  'Colored pencil, marker, and ink', 'Marker, colored pencil, and ink',
  'Colored pencil and marker', 'Oil pastel and marker', 'Acrylic paint and marker',
  'Crayon and marker', 'Acrylic paint, marker, and collage', 'Acrylic paint, ink, and marker',
  'Ink, marker, and watercolor'
);

INSERT INTO _medium_expand (resource_id, atomic)
SELECT v.resource_id, 'Colored pencil' FROM `value` v WHERE v.property_id = 26 AND v.`value` IN (
  'Ink and colored pencil', 'Marker and colored pencil', 'Colored pencil and ink',
  'Colored pencil and marker', 'Colored pencil and graphite', 'Colored pencil and acrylic paint',
  'Ink, marker, and colored pencil', 'Ink, colored pencil, and marker',
  'Colored pencil, marker, and ink', 'Marker, colored pencil, and ink', 'Marker, colored pencil',
  'Ink, colored pencil, and watercolor', 'Ink, colored pencil, and graphite',
  'Ink, colored pencil, and ballpoint pen', 'Ink, watercolor, and colored pencil',
  'Oil pastel and colored pencil', 'Colred pencil and graphite'
);

INSERT INTO _medium_expand (resource_id, atomic)
SELECT v.resource_id, 'Graphite' FROM `value` v WHERE v.property_id = 26 AND v.`value` IN (
  'Colored pencil and graphite', 'Ink and graphite', 'Oil pastel and graphite',
  'Ink, colored pencil, and graphite', 'Graphite and ink', 'Colred pencil and graphite'
);

INSERT INTO _medium_expand (resource_id, atomic)
SELECT v.resource_id, 'Oil pastel' FROM `value` v WHERE v.property_id = 26 AND v.`value` IN (
  'Oil pastel and marker', 'Oil pastel and acrylic paint', 'Oil pastel and colored pencil',
  'Oil pastel and graphite', 'Oil pastel and ink', 'Paint and oil pastel'
);

INSERT INTO _medium_expand (resource_id, atomic)
SELECT v.resource_id, 'Pen' FROM `value` v WHERE v.property_id = 26 AND v.`value` IN (
  'Marker and pen', 'Ink and ballpoint pen', 'Ink, colored pencil, and ballpoint pen'
);

INSERT INTO _medium_expand (resource_id, atomic)
SELECT v.resource_id, 'Watercolor' FROM `value` v WHERE v.property_id = 26 AND v.`value` IN (
  'Ink and watercolor', 'Ink, colored pencil, and watercolor',
  'Ink, watercolor, and colored pencil', 'Ink, marker, and watercolor'
);

INSERT INTO _medium_expand (resource_id, atomic)
SELECT v.resource_id, 'Collage' FROM `value` v WHERE v.property_id = 26 AND v.`value` IN (
  'Ink and collage', 'Marker and collage', 'Acrylic paint, marker, and collage'
);

INSERT INTO _medium_expand (resource_id, atomic)
SELECT v.resource_id, 'Acrylic paint' FROM `value` v WHERE v.property_id = 26 AND v.`value` IN (
  'Ink and acrylic paint', 'Acrylic paint and marker', 'Oil pastel and acrylic paint',
  'Colored pencil and acrylic paint', 'Marker and acrylic paint',
  'Acrylic paint, marker, and collage', 'Acrylic paint, ink, and marker'
);

INSERT INTO _medium_expand (resource_id, atomic)
SELECT v.resource_id, 'Crayon' FROM `value` v WHERE v.property_id = 26 AND v.`value` IN (
  'Marker and crayon', 'Crayon and marker'
);

INSERT INTO _medium_expand (resource_id, atomic)
SELECT v.resource_id, 'Paint' FROM `value` v WHERE v.property_id = 26 AND v.`value` IN (
  'Paint and oil pastel'
);

-- Phase 2: Delete ALL composite values (keep only already-atomic ones)
DELETE FROM `value` WHERE property_id = 26 AND `value` NOT IN (
  'Marker', 'Ink', 'Crayon', 'Colored pencil', 'Acrylic paint', 'Oil pastel', 'Mixed media',
  'Spoken word, live music, video projection, animation, medical analysis, comedy'
);

-- Phase 3: Insert expanded atomic values (deduplicated per item)
INSERT INTO `value` (resource_id, property_id, type, `value`, is_public)
SELECT DISTINCT e.resource_id, 26, 'literal', e.atomic, 1
FROM _medium_expand e
WHERE NOT EXISTS (
  SELECT 1 FROM `value` v2
  WHERE v2.resource_id = e.resource_id AND v2.property_id = 26 AND v2.`value` = e.atomic
);

-- Cleanup
DROP TABLE IF EXISTS _medium_expand;

-- Verify
SELECT `value`, COUNT(*) AS cnt FROM `value` WHERE property_id = 26 GROUP BY `value` ORDER BY cnt DESC;
