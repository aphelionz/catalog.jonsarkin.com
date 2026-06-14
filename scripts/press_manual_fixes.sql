-- Press manual fixes — run AFTER apply_press_shopify (id-agnostic; safe on local or prod).
-- 1) Consolidate the four "Accidental Artist" videos into one item (The Star-Ledger series).
-- 2) Attribute the formerly-unidentified placeholder items to their outlets.

-- ── Accidental Artist: keep the pre-existing Sketchbook item, drop the 3 created parts ──
SET @keep := (SELECT v.resource_id FROM `value` v
              WHERE v.property_id=1 AND v.value LIKE 'Accidental Artist 4 of 4%' LIMIT 1);

-- the three create-parts, identified by their YouTube source URLs (the keeper still has Part 4's URL here)
CREATE TEMPORARY TABLE _delparts AS
  SELECT DISTINCT v.resource_id AS id FROM `value` v
  WHERE v.property_id=11 AND v.value IN (
    'https://www.youtube.com/watch?v=km4zYnTWj04',
    'https://www.youtube.com/watch?v=cnlH8PcNiPw',
    'https://www.youtube.com/watch?v=937Ywzu7Ds4');
DELETE FROM `value`         WHERE resource_id IN (SELECT id FROM _delparts);
DELETE FROM item_item_set   WHERE item_id     IN (SELECT id FROM _delparts);
DELETE FROM item            WHERE id          IN (SELECT id FROM _delparts);
DELETE FROM resource        WHERE id          IN (SELECT id FROM _delparts);
DROP TEMPORARY TABLE _delparts;

-- retitle/attribute the keeper and point it at Part 1
UPDATE resource SET title='The Accidental Artist (video series)' WHERE id=@keep;
UPDATE `value` SET value='The Accidental Artist (video series)' WHERE resource_id=@keep AND property_id=1;
DELETE FROM `value` WHERE resource_id=@keep AND property_id IN (5,11,4);
INSERT INTO `value` (resource_id,property_id,type,value,is_public) VALUES
 (@keep,5,'literal','The Star-Ledger',1),
 (@keep,11,'literal','https://www.youtube.com/watch?v=km4zYnTWj04',1),
 (@keep,4,'literal','The Star-Ledger''s four-part video series on Jon Sarkin, from Amy Ellis Nutt''s Pulitzer-finalist project. Watch: https://www.youtube.com/watch?v=km4zYnTWj04',1);

-- ── Placeholder items → outlets (stable ids; guarded so re-runs are safe) ──
INSERT INTO `value` (resource_id,property_id,type,value,is_public)
SELECT * FROM (SELECT 7857 r,5 p,'literal' t,'NPR' v,1 i) x
WHERE NOT EXISTS (SELECT 1 FROM `value` z WHERE z.resource_id=7857 AND z.property_id=5);
INSERT INTO `value` (resource_id,property_id,type,value,is_public)
SELECT * FROM (SELECT 7875,5,'literal','TakePart',1) x
WHERE NOT EXISTS (SELECT 1 FROM `value` z WHERE z.resource_id=7875 AND z.property_id=5);
INSERT INTO `value` (resource_id,property_id,type,value,is_public)
SELECT * FROM (SELECT 7860,5,'literal','Capture the Extraordinary',1) x
WHERE NOT EXISTS (SELECT 1 FROM `value` z WHERE z.resource_id=7860 AND z.property_id=5);
INSERT INTO `value` (resource_id,property_id,type,value,is_public)
SELECT * FROM (SELECT 7957,5,'literal','YouTube',1) x
WHERE NOT EXISTS (SELECT 1 FROM `value` z WHERE z.resource_id=7957 AND z.property_id=5);

-- canonical sources for the two we could pin
DELETE FROM `value` WHERE resource_id=7857 AND property_id=11;
INSERT INTO `value` (resource_id,property_id,type,value,is_public) VALUES
 (7857,11,'literal','https://www.npr.org/2011/07/14/135407818/shadows-bright-as-glass-a-brain-injury-then-art',1);
DELETE FROM `value` WHERE resource_id=7875 AND property_id=11;
INSERT INTO `value` (resource_id,property_id,type,value,is_public) VALUES
 (7875,11,'literal','https://www.youtube.com/watch?v=O8N5ANyfaqs',1);
