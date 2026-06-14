#!/usr/bin/env python3
"""Create the curated "Press" item set and tag its members.

Reads scripts/press_classified.json (id/keep/outlet/slug), then on the LOCAL DB:
  - creates a new "Press" item set (resource + item_set + dcterms:title),
  - adds every keep=true item to it (item_item_set),
  - sets dcterms:publisher (property 5) for keepers with an outlet (guarded).

Idempotent-ish: aborts if a "Press" item set already exists. Prints the new
set id and the distinct outlet→slug map (for the logo build + module config).
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'scripts', 'press_classified.json')
CONT = 'catalogjonsarkincom-db-1'


def mysql(sql, capture=True):
    p = subprocess.run(
        ['docker', 'exec', '-i', CONT, 'mysql', '-N', '-uomeka', '-pomeka', 'omeka'],
        input=sql, text=True, capture_output=True)
    if p.returncode and capture:
        sys.exit('SQL error: ' + p.stderr)
    return p.stdout.strip()


def sq(s):  # escape a SQL string literal body
    return s.replace('\\', '\\\\').replace("'", "''")


rows = json.load(open(DATA))
keeps = [(r['id'], r['outlet']) for r in rows if r['keep']]
PRINT = '--print' in sys.argv  # emit SQL to stdout instead of executing (for prod replay)
if not PRINT:
    print(f'total={len(rows)} keep={len(keeps)} skip={len(rows)-len(keeps)}')
    # Guard: abort if a Press item set already exists.
    existing = mysql(
        "SELECT r.id FROM resource r JOIN value v ON v.resource_id=r.id AND v.property_id=1 "
        "WHERE r.resource_type='Omeka\\\\Entity\\\\ItemSet' AND v.value='Press';")
    if existing:
        sys.exit(f'A "Press" item set already exists (id {existing}); aborting.')

stmts = [
    "INSERT INTO resource (owner_id, is_public, created, modified, resource_type, title) "
    "VALUES (1, 1, NOW(), NOW(), 'Omeka\\\\Entity\\\\ItemSet', 'Press');",
    "SET @setid := LAST_INSERT_ID();",
    "INSERT INTO item_set (id, is_open) VALUES (@setid, 0);",
    "INSERT INTO `value` (resource_id, property_id, type, value, is_public) "
    "VALUES (@setid, 1, 'literal', 'Press', 1);",
]
# membership
vals = ','.join(f'({iid}, @setid)' for iid, _ in keeps)
stmts.append(f"INSERT IGNORE INTO item_item_set (item_id, item_set_id) VALUES {vals};")
# publishers (guarded so a re-run won't duplicate)
for iid, outlet in keeps:
    if not outlet:
        continue
    stmts.append(
        "INSERT INTO `value` (resource_id, property_id, type, value, is_public) "
        f"SELECT {iid}, 5, 'literal', '{sq(outlet)}', 1 FROM DUAL "
        f"WHERE NOT EXISTS (SELECT 1 FROM `value` v WHERE v.resource_id={iid} AND v.property_id=5);")
stmts.append("SELECT @setid;")

if PRINT:
    print('\n'.join(stmts))
    sys.exit(0)

out = mysql('\n'.join(stmts))
set_id = out.splitlines()[-1].strip()
print(f'created Press item set id={set_id}')

# Report distinct outlet→slug map for logos/config
outlets = {}
for r in rows:
    if r['keep'] and r['outlet']:
        outlets[r['outlet']] = r['slug']
print(f'distinct outlets={len(outlets)}')
for name in sorted(outlets):
    print(f"  '{name}' => '{outlets[name]}',")
