# Server upgrade, October 2026

This upgrade moves the catalog to PHP 8.5.12, Omeka S 4.2.1 and current third-party modules. Prod moves to a new VM built from git (Mark, 2026-10-31: "assume this server is rooted"). Branch `claude/busy-chebyshev-34ebda` holds the code. The prod steps are in [prod-rebuild-runbook.md](prod-rebuild-runbook.md).

Status on 2026-10-31:

- Done: code changes, local A/B test on identical data (all checks pass), runbook.
- Waiting on Mark: approval of the runbook and the decisions at the end of this page.

## Versions

| Component | Prod today | Target | Notes |
|---|---|---|---|
| Host OS | Ubuntu, kernel 6.17.0-1022-gcp | Debian 13 (trixie) | Mark, 2026-11-07: move off Ubuntu for faster CVE fixes. Nightly unattended-upgrades (on from step 9) and needrestart. Docker stays on one major; a Docker update restarts the containers. A kernel update reboots at 07:30 UTC. |
| PHP | 8.4.18 | 8.5.12 | 8.4 active support ends 2026-12-31. 8.5 has security support to 2029-12-31. Prod is 9 patch releases behind (8.4.27 is current). |
| Base image | `giocomai/omeka-s-docker:v4.2.0` | `v4.2.1@sha256:9fe00058…` | Pinned by digest. The maintainer pinned PHP 8.5.12 for two PHP CVEs. Apache 2.4.65 to 2.4.69. |
| Omeka S | 4.2.0 | 4.2.1 | Security fixes: `javascript:` URLs in navigation, cross-site search leak of private sites, `has_media=1` leak of private media. Zero schema migrations. |
| Common | 3.4.82 | 3.4.94 | Adds two DB indexes on upgrade (see "Upstream bug"). |
| Log | 3.4.37 | 3.4.42 | Needs Common 3.4.94. |
| BulkEdit | 3.4.39 | 3.4.40 | Needs Common 3.4.93. |
| CustomVocab | 2.0.2 (inactive) | 2.1.0 | Needs Omeka 4.2.0. See decisions. |
| FacetedBrowse (fork) | labeled 1.9.2, based on 1.8.0 | based on 1.10.0 | See next section. |
| Our modules | unchanged versions | explicit nullable types | CollectorSubmission, EnrichItem, Exhibitions, IccThumbnailer, IconographicProfile, Press, RapidEditor, ShieldedRegistryAssets, SimilarPieces, SimpleUpload, SiteLockdown. |

Out of scope, unchanged: MariaDB 10.11, Traefik v2.11.32, Qdrant v1.16, ImageMagick 7.1.1-43, Python 3.13.5.

Third-party module versions now live in `scripts/fetch-omeka-modules.sh` with SHA-256 pins. `make modules` installs them. `make deploy` stops when local versions differ from the pins.

## FacetedBrowse fork

Facts:

- The fork started from upstream 1.8.0 (`cd919d3`). Commit `6dd954b` ported a few 1.9.x fixes and set the label to 1.9.2.
- Because of that label, the upstream 1.9.0 migration never ran. Prod and local DBs lack `faceted_browse_page.thumbnail_type`. Upstream 1.10.0 maps that column. Our `Module::upgrade()` now checks for the column itself when the old version is below 1.10.0, so the module upgrade adds it. Tested both ways on 2026-10-31: a 1.9.2 DB without the column, and a 1.9.2 DB that already had it.
- The theme overrides `page.phtml`, `facets.phtml`, `browse.phtml` and four facet templates. The page loads one module script, `faceted-browse.js`.

Method: a three-way merge with base 1.8.0, ours, and upstream 1.10.0. Eight files conflicted.

| File | Resolution |
|---|---|
| `src/ControllerPlugin/FacetedBrowse.php` | Kept our `buildBaseParams`, `computeFacetCounts`, `computeValueFacetCounts` (GROUP BY counts) and our Year/Size sort options. Took upstream's removal of `getValueValues` and its siblings; upstream moved them into the facet types, and nothing of ours calls them. |
| `src/Controller/Site/PageController.php` | Auto-merged. Our server-side facet counts and filters are intact. |
| facet-render views, `page.js`, `faceted-browse.js`, `module.ini` | Upstream. The theme overrides these views; our a11y edits match upstream's own. |
| `src/FacetType/PropertyExists.php` (ours) | Unchanged. Upstream's new "show all values" table is opt-in per facet type, so it skips this type. |

The fork label is now 1.10.0, so future upstream migrations run. Visible change: with a filter active, selected values keep their place in the list (upstream 1.10.0 behavior). The theme legend now carries `id="facet-legend-N"`, which upstream's Year select references with `aria-labelledby`.

Fixes on top of upstream 1.10.0, from the Copilot review of PR #1. All are upstream bugs, so they are worth reporting there too:

| File | Fix |
|---|---|
| `src/ColumnType/Value.php` | A single value renders through `asHtml()`. Before, it printed unescaped (stored XSS in the table view). |
| `src/ColumnType/ItemSet.php` | One item set renders with `reset()`. `itemSets()` keys by ID, so `[0]` was null. |
| `view/.../site/page/facets.phtml` and the theme's `facets.phtml`, `page.phtml` | Category and facet names are escaped in `aria-label` and `<legend>`. The theme's `facets.phtml` legend carries `id="facet-legend-N"`, like `page.phtml`. |
| `src/FacetType/Value.php` | Show-all pages past blank labels, so the limit counts usable rows and a blank row cannot hide a cut-off list. Reproduced with one blank title, then fixed. |
| `src/Controller/SiteAdmin/CategoryController.php`, `show-all-table.phtml` | Show-all fetches one row past its 1,000 limit. A cut-off list shows a note and hides "Add all", which would replace the facet's values with a partial list. |
| `src/Api/Representation/FacetedBrowseCategoryRepresentation.php` | The category View link carries `?faceted_browse_category_id=N`, which our server-rendered page reads, plus upstream's fragment. |
| `Module.php` | The column check above. |

Our theme renders neither table column type. The escaping fixes change output only for names that contain HTML characters, and the current names contain none.

## Upstream bug in Common 3.4.94

The module upgrade adds `item_site/idx_site_item` directly. It hands `value/idx_property_value_resource` to a background script. The launcher passes `--batch file`, and the script reads `--batch=file`, so the script exits at once and logs to syslog. The index never appears. The runbook adds it by hand:

```sql
ALTER TABLE value ADD INDEX idx_property_value_resource (property_id, value_resource_id), ALGORITHM=INPLACE, LOCK=NONE;
```

It took 0.15 s on 171k rows locally. Worth reporting upstream (Daniel-KM/Omeka-S-module-Common).

## Local test, 2026-10-31

Two isolated Docker stacks ran on the same pre-upgrade dump of the local DB:

- baseline: old image, old modules, `main` code (port 8891)
- upgraded: this branch (port 8890)

Mark's dev stack stayed untouched. Both stacks used a copy-on-write clone of the local media files. The suite ran twice: first against `main` at `d876246`, then again after merging `main` at `19acfb1` (the security-audit and private-field fixes), with both DBs reloaded and the upgrade steps replayed. Both runs gave the results below.

| Check | Result |
|---|---|
| PHP 8.5 lint of every module and theme file | Zero errors. 16 deprecations in our code, all fixed. |
| Admin upgrade path | DB version bumped to 4.2.1 on its own. Common, Log, BulkEdit, CustomVocab and FacetedBrowse upgraded from Admin > Modules with success messages. |
| 38 public pages (sitemap sample, pages, items, search, API) | Same status on both. 36 byte-identical; 2 differ only in asset `?v=` strings. |
| Faceted browse, 10 filter combinations | Identical facet counts and identical result items on both. |
| Faceted browse UI in Chromium | 10/10 pass on both: checkbox and radio select/deselect, See more/less, Year select, sort, mobile Filters. Screenshots pixel-identical. |
| Admin pages (18, incl. RapidEditor, Enrich Fields, ICC Thumbnailer, Faceted Browse admin, Log, batch edit) | All 200. Zero PHP warnings or deprecations with `error_reporting=E_ALL`. |
| RapidEditor save round trip (full payload, as the editor sends it) | Identical on both: title edited and restored, all 26 values kept. |
| IccThumbnailer | Thumbnails byte-identical to prod's stored derivatives for two originals; ICC profiles (sRGB, Display P3) kept. |
| clip-api: similar works, visual search upload | Identical results on both. |
| PHP in `/files` | `.php`, `.phtml`, `.phar`, `.php8` return Omeka's 404 page through the front controller. |

Left to verify on prod: Apple HDR gain-map re-embedding (2,000 recent originals held zero gain-map samples; the code path, Python and ImageMagick are unchanged), EnrichItem Analyze and batch (cost money), email (SMTP credentials were withheld from the test).

Pre-existing, seen on both stacks: a `$ is not defined` console error on the faceted browse page. EnrichItem adds zero tabs to the item page; the "Enrich tab" note in CLAUDE.md is stale.

## Debian 13 test VM, 2026-11-07

A throwaway `debian-13` VM (e2-standard-2, Shielded VM, zero GCP identity) ran the runbook's step 4 blocks as an operator paste, then got deleted. The fresh image shipped Debian 13.7 with unattended-upgrades already on, and with gcloud, cron and file installed.

| Check | Result |
|---|---|
| Step 4 setup and patching blocks, pasted into an interactive shell | Ran to the end in 3.5 min with zero dialogs. full-upgrade installed kernel 6.12.115 from `trixie-security`. Docker 29.9.3, Compose v5.7.0, Ops Agent installed. Module pins matched. |
| Docker key and major pin | The key check passed. The pin wrote `5:29.*` and `2.*`; every 29.x version shows priority 990. |
| Post-reboot checks | `NEEDRESTART-KSTA: 1`, live-restore `true`, gcloud, crontab and file present, root device `/dev/sda1`. |
| Shielded VM | The reboot onto the new kernel logged a late-boot failure. After `--shielded-learn-integrity-policy`, the next three boots passed. |
| MariaDB stops | `docker compose stop db` shut down cleanly in 1 s with a 60 s stop timeout. A host reboot under write load (343,500 rows) came back with zero crash recovery. |
| Step 9 patching block | Origins list Debian stable and security, `origin=Docker` and `site=packages.cloud.google.com`; Google's repos sit on that site. |

The review on 2026-11-08 changed the key check, the pins and live-restore (now off). Local tests of those changes:

| Check | Result |
|---|---|
| Key check, `debian:trixie` container | Docker's key file passed. Docker's key with a second key appended or prepended, the second key alone, and an HTML page all got deleted. |
| Major pins, same container | Installed Docker 28.5.2, containerd 1.7.29, Compose 2.40.3 and buildx 0.37.1. The pin wrote six lines. Candidates stayed on 28.x, 1.x and 2.x while 29.9.3, 2.4.2 and 5.7.0 sat in the repo. A missing package printed `DOCKER PIN FAILED` and wrote zero pins. |
| Nightly run, same container | unattended-upgrade upgraded buildx to 0.38.1 and a Debian package and held the other Docker packages. The kernel hook's `/var/run/reboot-required` then made it call `shutdown -r 07:30`. needrestart's default config skips `^docker`. |
| Docker update with live-restore off, nested Docker 29.9.3 | `db` from the prod compose file (stop timeout 60 s) under write load. Stopping the daemon shut MariaDB down cleanly; it came back on its restart policy at the same LSN, with zero crash recovery. |

## Prod findings, 2026-10-31 (read-only)

Configuration:

- `db`, `qdrant` and `clip-api` on prod run from the dev `docker-compose.yml`. Only `omeka` and `traefik` use `docker-compose.prod.yml`. Qdrant (6333) and clip-api (8000) are published on 0.0.0.0. The GCP firewall blocked both from the laptop.
- `/opt/catalog` holds rsync copies. Its `.git` is a pointer file to a laptop worktree, so prod has zero git history. `.mcp.json`, `cookies.txt` and `AGENTS.md` sit there from older deploys.
- `make deploy` runs rsync without `--delete`. Prod holds 351 files from older third-party module versions (`vendor/`, `test/`).
- The VM runs as the default compute service account with the `cloud-platform` scope. The backup script depends on it. If that account holds the default Editor role, root on the VM means Editor on the project.
- The host has PHP, Composer and certbot installed from an earlier setup. Uptime was 78 days on kernel 6.17.0-1022-gcp.

Integrity check against known-good sources:

- Omeka core in the running container: 9,244 of 9,244 files match the official 4.2.0 release.
- `docker diff` of the Omeka container since 2026-05-18: PHP session files, the Apache pid, one cache file, and the expected config mounts.
- Our modules and theme on prod: every file matches git `main` or the laptop checkout.
- Third-party modules: every file that also exists on the laptop matches it. The 351 leftover files exist only on prod and remain unverified.

These checks found zero signs of tampering in the application layer. The host OS, the database, the media files and GCP IAM remain unchecked, and a host-level compromise would hide from these checks. The runbook therefore rebuilds from scratch.

## Upgrade order

1. Base image (PHP 8.5.12 + Omeka 4.2.1): rebuild the Omeka image.
2. Omeka core: the DB version bumps on first admin load.
3. Modules in Admin > Modules: Common, Log, BulkEdit, CustomVocab, Faceted Browse. Faceted Browse adds `thumbnail_type`.
4. One-time SQL: Common's `idx_property_value_resource`.

Rollback: restore the dump taken before step 3, then rebuild with the previous Dockerfile and FacetedBrowse, and the old module pins in the current fetch script. The runbook lists the commands and the old checksums.

## Decisions for Mark

- [ ] Approve the assume-breach rebuild in the runbook (new VM, new service account, rotated secrets).
- [ ] CustomVocab: uninstall it (inactive since the 2026-03-22 literal conversion; uninstall drops its tables) or keep it pinned at 2.1.0. Recommendation: uninstall after the rebuild.
- [ ] Qdrant and the search index: the runbook now starts both empty and fills them with "Ingest All Items" after cutover. A restored store would keep points that no audited item backs. Cost: similar works and visual search stay empty until the ingest finishes. Accept that window, or ask for a restore-and-reconcile step instead.
- [ ] The official `mariadb:10.11` image runs Ubuntu 22.04 on any host. Keep it, or move to Debian 13's own MariaDB 11.8 in a Debian image (a major-version upgrade and a Compose change).
- [ ] Report the Common `--batch` bug upstream.
- [ ] After the rebuild, move `make deploy` from laptop rsync to `git checkout <sha>` plus `scripts/fetch-omeka-modules.sh` on the VM, so prod always equals a known commit.
