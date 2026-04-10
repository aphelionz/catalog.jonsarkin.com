# Jon Sarkin Estate — Shopify Theme Reference

## What this is
jonsarkin.com is the estate's public front door + commerce layer. It is NOT a store — it is an institutional site that happens to sell works. The companion site catalog.jonsarkin.com is the scholarly catalog raisonné.

## Design principles
- **Dark gallery aesthetic** — near-black background (`#111`), off-white text (`#e8e8e8`), artwork is the only color
- **Gallery language** — never "Shop", "Buy", "Add to Cart". Use "Available Works", "Acquire This Work", "Inquire"
- **Don't assert, let people infer** — no sales copy, no superlatives. Metadata tells the story
- **Two-site contrast** — jonsarkin.com is dark/atmospheric (gallery); catalog.jonsarkin.com is light/clean (library). The contrast is intentional

## Two-site architecture
- Every Shopify product links to its catalog entry ("View in the Catalog Raisonné →")
- Catalog inquiry links route back to Shopify product pages
- Shared DNA: same wordmark treatment, same typeface class, same ARS copyright line, same contact sentence

## Product metafields (artwork namespace)
All product pages are driven by `artwork.*` metafields — not the description field:

| Key | Example value |
|-----|--------------|
| `artwork.catalog_number` | `JS-2017-T8764` |
| `artwork.catalog_url` | `https://catalog.jonsarkin.com/s/catalog/item/8764` |
| `artwork.medium` | `Oil pastel` |
| `artwork.support` | `Cardboard album sleeve` |
| `artwork.dimensions` | `12.5" x 12.5"` |
| `artwork.year` | `2017` |
| `artwork.signed` | `↘` |
| `artwork.condition` | `Good` |
| `artwork.provenance` | `Estate of the artist` |
| `artwork.content_excerpt` | (text fragment from the work) |
| `artwork.original_image_url` | (full-res image URL from catalog) |

Leave `descriptionHtml` empty — the template ignores it in favor of metafields. The framing line ("Ships framed with 99% UV conservation glass") is rendered by the theme template for any product whose `artwork.support` contains "album sleeve".

## SEO titles
The theme auto-generates SEO titles from metafields:
`{medium}, {year} — Jon Sarkin ({catalog_number})`

When adding new products, also set the Shopify admin SEO title field to the same value as a fallback (the `global.title_tag` metafield).

## Price routing
- `product.price >= 1000000` (Liquid cents, i.e. ≥ $10,000) → "Inquire About This Work" button
- Below that threshold + in stock → "Acquire This Work" button
- Out of stock → "Acquired" (disabled)

## Files not to touch
- Checkout templates (Shopify controls these)
- `config/settings_data.json` without `--only` flag (theme editor may own it)
- Shopify Payments / shipping settings
- Domain configuration

## Footguns
- **Always `cd shopify/` before `npx shopify theme push/pull`** — pushing from project root uploads CLAUDE.md, docker-compose.yml, etc. as theme files
- **`image_tag` and JS image switching** — Shopify's `image_tag` generates `srcset`, so `.src` swaps via JS don't work. Use plain `<img src>` when JS needs to swap images
- **`settings_data.json` caching** — Shopify's theme editor stores its own copy; verify with a pull after pushing settings changes
- **SVGs in footer** — use `stroke="currentColor"` (not `fill`) for Feather-style icons
- **`sort` filter on collections** — `collection.products | sort: 'price'` silently returns empty. Set sort order via Admin API `collectionUpdate` mutation instead
