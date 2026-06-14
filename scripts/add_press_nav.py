#!/usr/bin/env python3
"""Insert a 'Press' nav item after 'Exhibitions' in the catalog site navigation.

Reads site.navigation (site id 5) from the local Omeka DB, inserts the Press
node into the 'Catalog Raisonné' dropdown right after 'Exhibitions', and writes
it back via a hex literal (escaping-proof). Idempotent.
"""
import json
import subprocess
import sys

DB = ["docker", "exec", "-i", "catalogjonsarkincom-db-1",
      "mysql", "-uomeka", "-pomeka", "omeka"]


def mysql(sql, raw=False):
    args = ["docker", "exec", "catalogjonsarkincom-db-1", "mysql",
            "-N", "-uomeka", "-pomeka", "omeka", "-e", sql]
    if raw:
        args.insert(5, "-r")
    return subprocess.run(args, capture_output=True, text=True).stdout


nav = json.loads(mysql("SELECT navigation FROM site WHERE id=5;", raw=True))

PRESS = {"type": "url",
         "data": {"label": "Press", "url": "/s/catalog/press", "target_blank": "0"},
         "links": []}

done = False
for node in nav:
    if node.get("data", {}).get("label") == "Catalog Raisonné":
        links = node.setdefault("links", [])
        if any(l.get("data", {}).get("label") == "Press" for l in links):
            print("Press already present, nothing to do.")
            sys.exit(0)
        idx = next((i for i, l in enumerate(links)
                    if l.get("data", {}).get("label") == "Exhibitions"), None)
        if idx is None:
            links.append(PRESS)
        else:
            links.insert(idx + 1, PRESS)
        done = True
        break

if not done:
    print("ERROR: 'Catalog Raisonné' node not found in navigation.")
    sys.exit(1)

hexstr = json.dumps(nav, ensure_ascii=False).encode("utf-8").hex()
subprocess.run(DB, input=f"UPDATE site SET navigation = 0x{hexstr} WHERE id=5;",
               text=True, capture_output=True)
print("Press nav item inserted after Exhibitions.")
