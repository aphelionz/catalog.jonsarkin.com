---
name: theme-edit
description: Edit the Sarkin-Jeppesen theme (CSS, PHP templates, JS). Use when asked to change item page layout, browse cards, styling, or any frontend display. Skips codebase exploration for familiar files.
---

# Theme Edit

Frontend changes to the Sarkin-Jeppesen Omeka theme. Use this workflow
for CSS, template (PHP), and JavaScript changes.

**Key principle:** Don't re-explore familiar files. Use line-range reads
from the structure maps in `docs/omeka-invariants.md` → "Hot File Structure Maps".

---

## Files

| File | Lines | What it does |
|---|---|---|
| `omeka/volume/themes/sarkin-jeppesen/asset/css/style.css` | 2379 | All CSS (no SCSS) — see section map in omeka-invariants.md |
| `omeka/volume/themes/sarkin-jeppesen/view/omeka/site/item/show.phtml` | 574 | Item detail page template |
| `omeka/volume/themes/sarkin-jeppesen/view/omeka/site/item/browse.phtml` | — | Browse/card grid template |
| `omeka/volume/themes/sarkin-jeppesen/view/omeka/site/item-set/browse.phtml` | — | Item-set browse template |
| `omeka/volume/themes/sarkin-jeppesen/asset/js/sarkin.js` | 315 | Client JS: cite, share, zoom, async sections |

## Steps

### Step 1 — Read only the relevant section

Consult the structure maps in `docs/omeka-invariants.md` to identify which
line range to read. Use `offset` and `limit` parameters — never read an
entire 2000+ line file.

### Step 2 — Start the preview server

```
docker compose down 2>/dev/null
```
Then use `preview_start` with the `catalog` server configuration.

After starting, immediately resize to desktop:
```
preview_resize preset: "desktop"
```

### Step 3 — Navigate to the right page

- Item detail: `/s/catalog/item/{id}` (pick a representative item)
- Browse grid: `/s/catalog/faceted-browse/1`
- Home page: `/s/catalog`

### Step 4 — Edit and verify

1. Edit the source file(s)
2. Use `preview_snapshot` to check text/structure (not `preview_eval`)
3. Use `preview_inspect` with a CSS selector to check computed styles (not `preview_eval` + `getComputedStyle`)
4. Take a `preview_screenshot` only for visual proof to show the user

### Step 5 — Check mobile

```
preview_resize preset: "mobile"
```
Take a screenshot, fix any breakpoint issues, then restore:
```
preview_resize preset: "desktop"
```

### Step 6 — Commit

Stage only the files you changed — not unrelated modifications.

---

## Gotchas

- **No SCSS** — all CSS is hand-written; selector specificity matters
- **Preview default viewport is ~650px** — always resize to desktop first
- **`prepend()` loads in reverse order** — head management stack quirk
- **Site slug is `catalog`** — URLs are `/s/catalog/...`
