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

Leave `descriptionHtml` empty — the template ignores it in favor of metafields. The framing line ("Ships framed with 99% UV conservation glass") is rendered by the theme template for any product whose `artwork.support` contains "album sleeve"; set `artwork.unframed` = `true` (single line text) on a product to suppress it (currently set on JS-2021-1834).

## SEO titles
The theme auto-generates SEO titles from metafields:
`{medium} on {support}, {year} — Jon Sarkin ({catalog_number})`

When adding new products, also set the Shopify admin SEO title field to the same value as a fallback (the `global.title_tag` metafield).

- **Page titles.** Works stay untitled and named by catalog number (the catalog's convention). Their `<title>` leads with medium and support, which puts the searchable words first.
- **Titled products.** A product whose title is not a `JS-` number (sets and series, e.g. the Alphabet Series) gets `{title} — Jon Sarkin` as its page title, the title as H1 with the catalog number beneath (when it has one), and alt text that leads with the title.
- **Years in titles.** Only a four-digit `artwork.year` goes into the `<title>`/OG title; ranges like `c. '89–'24` stay on the page only.
- **Signature line.** `artwork.signed` follows the catalog convention (arrow = position, optional initials/date, `∅` = unsigned) and renders as words, e.g. `↘ JMS 17` → "Signed lower right (JMS 17)".
- **Structured data.** `sections/product-detail.liquid` emits Product + VisualArtwork JSON-LD with an Offer (price, availability, free US shipping).
- **Alt text** comes from `snippets/artwork-alt.liquid` (artist, title, medium, support, dimensions).

## Release list
- `snippets/release-signup.liquid` is a Shopify customer (newsletter) form. Signups become customers subscribed to email marketing, tagged `release-list` plus `signup-home`, `signup-product`, `signup-acquired` or `signup-404`. Segment on those tags in Shopify Messaging.
- It appears on the homepage, under every product (different line for acquired works) and on the 404 page.
- The homepage "Currently Available" module now shows only available works: optional pinned handles first (`pinned_handles`), then the rest of Available Works under the price cap, up to `featured_count`.
- `sections/not-found.liquid` turns dead `/products/` links into a landing page: available works, a catalogue search for the old handle, and the Release list.

## Price routing
- In stock → "Acquire This Work" button (adds to cart) — all price tiers, including flagship
- Out of stock → "Acquired" (disabled)
- No price gate: the former `>= $10,000 → "Inquire About This Work"` contact-page route was removed 2026-05-30 (all works go to the cart). Purchases over $5,000 are steered to ACH/Zcash by the cart notice in `sections/cart.liquid`, not by hiding the cart.

## Files not to touch
- Checkout templates (Shopify controls these)
- `config/settings_data.json` without `--only` flag (theme editor may own it)
- Shopify Payments / shipping settings
- Domain configuration

## Footguns
- **Always `cd shopify/` before `npx shopify theme push/pull`** — pushing from project root uploads CLAUDE.md, docker-compose.yml, etc. as theme files
- **CLI hangs with no output** — it's blocked on an interactive browser login. Export the theme access token first: `export SHOPIFY_CLI_THEME_TOKEN=$(grep -m1 password config.yml | sed 's/.*password: *//;s/"//g')` (plus `SHOPIFY_FLAG_STORE=jonsarkin.myshopify.com`)
- **`image_tag` and JS image switching** — Shopify's `image_tag` generates `srcset`, so `.src` swaps via JS don't work. Use plain `<img src>` when JS needs to swap images
- **`settings_data.json` caching** — Shopify's theme editor stores its own copy; verify with a pull after pushing settings changes
- **SVGs in footer** — use `stroke="currentColor"` (not `fill`) for Feather-style icons
- **`sort` filter on collections** — `collection.products | sort: 'price'` silently returns empty. Set sort order via Admin API `collectionUpdate` mutation instead
