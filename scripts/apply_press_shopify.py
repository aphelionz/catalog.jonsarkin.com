#!/usr/bin/env python3
"""Apply the Shopify press list to the catalog (reconcile + create).

Reads scripts/press_reconcile.json and, on the LOCAL DB, in one session:
  - create:       new template-3 press item (title/date/publisher/source/description) + add to "Press" set
  - add_existing: add matched item to the "Press" set, backfill publisher, set canonical source
  - in_set:       set the matched item's dcterms:source to the canonical URL

Property ids: title 1, description 4, publisher 5, date 7, source 11.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'scripts', 'press_reconcile.json')
CONT = 'catalogjonsarkincom-db-1'
RES_TYPE = "Omeka\\\\Entity\\\\Item"  # -> Omeka\\Entity\\Item in SQL -> Omeka\Entity\Item stored


def sq(s):
    return str(s).replace('\\', '\\\\').replace("'", "''")


def pretty_date(iso):
    try:
        import datetime
        return datetime.datetime.strptime(iso, '%Y-%m-%d').strftime('%B %-d, %Y')
    except Exception:
        return iso


VERB = {'video': 'Watch at', 'broadcast': 'Watch at', 'podcast': 'Listen at',
        'pdf': 'Read the PDF at', 'archive': 'View at', 'aggregator': 'View at',
        'article': 'Read the original at'}


def describe(outlet, url, typ):
    return f"Coverage of Jon Sarkin in {outlet}. {VERB.get(typ, 'Read at')} {url}"


rows = json.load(open(DATA))
stmts = [
    "SET @setid := (SELECT r.id FROM resource r JOIN item_set s ON s.id=r.id "
    "JOIN `value` v ON v.resource_id=r.id AND v.property_id=1 AND v.value='Press' LIMIT 1);"
]
n_create = n_add = n_inset = 0
for r in rows:
    act = r['action']
    url = sq(r['url'])
    outlet = r.get('outlet') or ''
    if act == 'create':
        n_create += 1
        title = sq(r['title'])
        desc = sq(describe(r['outlet'], r['url'], r['type']))
        stmts.append(
            "INSERT INTO resource (owner_id,is_public,created,modified,resource_type,resource_template_id,title) "
            f"VALUES (1,1,NOW(),NOW(),'{RES_TYPE}',3,'{title}');")
        stmts.append("SET @id := LAST_INSERT_ID();")
        stmts.append("INSERT INTO item (id) VALUES (@id);")
        stmts.append(
            "INSERT INTO `value` (resource_id,property_id,type,value,is_public) VALUES "
            f"(@id,1,'literal','{title}',1),"
            f"(@id,7,'literal','{sq(r['date'])}',1),"
            f"(@id,5,'literal','{sq(outlet)}',1),"
            f"(@id,11,'literal','{url}',1),"
            f"(@id,4,'literal','{desc}',1);")
        stmts.append("INSERT IGNORE INTO item_item_set (item_id,item_set_id) VALUES (@id,@setid);")
    elif act == 'add_existing':
        n_add += 1
        mid = int(r['matched_id'])
        stmts.append(f"INSERT IGNORE INTO item_item_set (item_id,item_set_id) VALUES ({mid},@setid);")
        stmts.append(
            f"INSERT INTO `value` (resource_id,property_id,type,value,is_public) "
            f"SELECT {mid},5,'literal','{sq(outlet)}',1 FROM DUAL "
            f"WHERE NOT EXISTS (SELECT 1 FROM `value` v WHERE v.resource_id={mid} AND v.property_id=5);")
        stmts.append(f"DELETE FROM `value` WHERE resource_id={mid} AND property_id=11;")
        stmts.append(f"INSERT INTO `value` (resource_id,property_id,type,value,is_public) VALUES ({mid},11,'literal','{url}',1);")
    elif act == 'in_set':
        n_inset += 1
        mid = int(r['matched_id'])
        stmts.append(f"DELETE FROM `value` WHERE resource_id={mid} AND property_id=11;")
        stmts.append(f"INSERT INTO `value` (resource_id,property_id,type,value,is_public) VALUES ({mid},11,'literal','{url}',1);")

stmts.append("SELECT (SELECT COUNT(*) FROM item_item_set WHERE item_set_id=@setid) AS press_members;")
sql = '\n'.join(stmts)

if '--print' in sys.argv:
    print(sql); sys.exit(0)

p = subprocess.run(['docker', 'exec', '-i', CONT, 'mysql', '-uomeka', '-pomeka', 'omeka'],
                   input=sql, text=True, capture_output=True)
print(p.stdout.strip())
if p.returncode:
    print('ERROR:', p.stderr[-800:]); sys.exit(1)
print(f"create={n_create} add_existing={n_add} in_set={n_inset}")
