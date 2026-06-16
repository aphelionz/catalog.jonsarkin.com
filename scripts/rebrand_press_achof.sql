-- Rebrand the 2011 Guster "Easy Wonderful" UnCovered interview from "Rock Pop
-- Gallery" to "Album Cover Hall of Fame" (Mike Goldstein's current brand) and
-- repoint the now-defunct typepad URL to the live site.
-- id-agnostic and idempotent; safe to run on local or prod.

SET @id := (SELECT v.resource_id FROM `value` v
            WHERE v.property_id = 11
              AND v.value LIKE 'https://rockpopgallery.typepad.com/%uncovered-interview-jon-sa%'
            LIMIT 1);

UPDATE `value` SET value = 'Album Cover Hall of Fame'
  WHERE resource_id = @id AND property_id = 5 AND value = 'Rock Pop Gallery';

UPDATE `value` SET value = 'https://www.albumcoverhalloffame.com'
  WHERE resource_id = @id AND property_id = 11
    AND value LIKE 'https://rockpopgallery.typepad.com/%';
