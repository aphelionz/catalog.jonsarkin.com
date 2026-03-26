# Jon Sarkin Estate — Shopify Theme Redesign Brief

## Context

This is the Shopify theme for jonsarkin.com, the official website of the Jon Sarkin Estate (1953–2024). Jon was an outsider artist based in Gloucester, MA who produced over 20,000 works across three decades following a stroke. The estate is managed by Mark Henderson.

**jonsarkin.com is NOT a store.** It is the estate's official public presence. It happens to run on Shopify because there is a commerce component (selling estate works), but the front door must read as an institutional site, not a retail operation.

There is a companion site — catalog.jonsarkin.com — which is the catalog raisonné (built on Omeka S, not Shopify). The two sites work together:
- jonsarkin.com = front door + available works (commerce)
- catalog.jonsarkin.com = scholarly catalog (4,500+ works documented)

## Design Direction

- **Gallery-level, not retail.** Think Gagosian or David Zwirner viewing rooms — dark walls, artwork luminous against a deep field. Not Etsy, not a boutique.
- **Dark palette.** The Shopify site uses a dark background (near-black or deep charcoal, e.g., `#111`, `#1a1a1a`, or `#0d0d0d`). Light text. The artwork images become the only source of color and light. This suppresses every retail instinct — you don't "shop" in a dark room, you *look*.
- **The catalog is light.** catalog.jonsarkin.com uses a light/white background — clean, readable, institutional, like every serious museum collection database (Met, MoMA, Centre Pompidou). The two sites are deliberately visually distinct.
- **Crossing the threshold.** When a visitor clicks "View in the Catalog Raisonné" from a dark, atmospheric Shopify product page, they arrive in a clean, well-lit research environment. Like stepping from the viewing room into the library. When they click [inquire] from the catalog back to Shopify, they re-enter the gallery. This contrast should feel intentional and dramatic.
- **"Don't assert, let people infer."** The metadata, the advisor names, the institutional holdings tell the story. No superlatives, no sales copy, no editorial assertions about Jon's genius.
- **Gallery language throughout.** Never "Shop," "Buy," or "Store." Use "Available Works," "Acquire This Work," "Inquire." This is an estate, not a retail operation.

## Typography & Color

- **Background:** Dark — near-black or deep charcoal. Not pure `#000` (too harsh). Something like `#111` or `#1a1a1a` that has warmth.
- **Text:** Off-white or warm white (`#f0f0f0`, `#e8e8e8`, or similar). Not pure `#fff` — too much contrast against near-black can feel aggressive. Secondary text in a medium gray (`#888` or `#999`).
- **Body type:** A refined serif or clean sans-serif. Nothing playful or decorative. Same typeface family as catalog.jonsarkin.com (or the same class of typeface) — this is the shared DNA between the two sites.
- **Headings:** Can match body or use a complementary pair. Restraint over personality.
- **Accent color:** If needed, keep it extremely muted. A warm gray, a desaturated gold, or simply white used sparingly as emphasis. The artwork is the only real color on the site.
- **Links:** Subtle — underline or a slight color shift on hover. No bright link colors.
- **Nav bar:** Dark, matching the page background — essentially the nav blends into the page. Clean horizontal layout. Logo/wordmark in white or off-white. Thin bottom border (1px, in a subtle gray like `#333`) to delineate the header from content.
- **Product cards:** Artwork images should feel like they're glowing against the dark background. No card borders, no card backgrounds — just the image floating in space with metadata below in light text.
- **Buttons:** "Acquire This Work" should be a quiet, outlined button (border in gray/white, transparent fill) or a simple text link — not a filled button. The action is understated, not a loud CTA.

## Shared DNA Between Sites

These elements must be consistent across jonsarkin.com and catalog.jonsarkin.com to signal that they are one estate:
- Same logo/wordmark treatment for "Jon Sarkin"
- Same typeface family (or same class)
- Same copyright/ARS footer line
- Same canonical contact sentence
- Same advisory board names and credentials when they appear
- Same accent color if one is used

Everything else can and should differ — the dark/light split is the primary differentiator.

## Architecture — The Two-Path Flow

The homepage presents exactly two paths:

1. **"Explore the Catalog Raisonné"** → external link to catalog.jonsarkin.com
2. **"Available Works"** → internal link to /collections/available-works (or the main collection)

The Shopify store section (/collections, /products) is the commerce layer. The catalog is the scholarly layer. They cross-reference each other:
- Every Shopify product page links to its catalog raisonné entry ("View in the Catalog Raisonné →")
- On the catalog side, [inquire] links route to Shopify product pages (for works that are for sale)

## Page-by-Page Specifications

### Homepage (index template)

**Above the fold:**
- Dark background throughout the entire page (near-black, consistent with site-wide palette)
- "Jon Sarkin" as text wordmark or the existing logo, in white/off-white, prominent
- One strong full-width artwork image (hero) — the image will be luminous against the dark field
- Below the image, in light text:
  ```
  Jon Sarkin (1953–2024)
  American artist. Gloucester, Massachusetts.
  ```
- Two quiet, balanced CTAs (styled as text links, not loud buttons):
  - "Explore the Catalog Raisonné →" (external: catalog.jonsarkin.com)
  - "Available Works →" (internal: /collections/available-works)
- Nothing else above the fold. No cart, no search, no newsletter.

**Below the fold — credibility layer:**
- Separated from hero section by generous negative space (not whitespace — the dark background continues)
- Sections separated by thin horizontal rules in subtle gray (`#333` or `#2a2a2a`)
- Brief bio paragraph (3-4 sentences, past tense) in off-white text
- Advisory Board:
  - Colin Rhodes — Distinguished Professor; Contributing Editor, Raw Vision
  - Tony Millionaire — Eisner Award–winning cartoonist
  - Dr. Alice Flaherty — Harvard Medical School / Massachusetts General Hospital
- Selected Press: The New Yorker, The New York Times, NPR, GQ, Raw Vision, Boston Globe (presented as a horizontal line of names, not a bulleted list)
- Institutional Holdings: Centre Pompidou, deCordova Sculpture Park and Museum, American Visionary Art Museum, Museum of Modern Art Archives (Calvin Tomkins Papers)
- Licensing: "Jon Sarkin's work is represented by Artists Rights Society (ARS), New York."
- Contact: "For inquiries about Jon Sarkin's work, contact Mark Henderson, Estate Manager, at art@jonsarkin.com."

**Remove from homepage:**
- YouTube video embed
- Donation section / Fractured Atlas / Patreon links
- "Book a Private Studio Visit" CTA
- Newsletter signup (move to footer of store pages only, or remove entirely)
- "What's Next for Jon's Art?" section

### Navigation (header — site-wide)

```
About                    Available Works       Catalog Raisonné       Contact
  └── Biography            (→ /collections)      (→ catalog.jonsarkin.com, external)
  └── Press
  └── Advisors
```

- Remove "For Collectors" dropdown
- Remove "Book a Private Visit"
- Remove "Works on Consignment" as a public nav item
- Remove cart icon from homepage (show it only on /collections and /products templates)
- Remove "Log in" link site-wide
- Remove country/region selector entirely
- Remove search from the header (or keep it very subtle, icon-only, on store pages)

**Liquid implementation for conditional cart icon:**
In `sections/header.liquid`, wrap the cart icon in:
```liquid
{% unless template == 'index' %}
  {%- comment -%} cart icon markup here {%- endcomment -%}
{% endunless %}
```

### Collection Page (/collections/available-works)

- Rename the primary collection from "All" to "Available Works"
- Hide or archive "Consignment" and "Featured" collections from public view (move products into Available Works, tag internally)
- Collection intro text at top:
  > Selected works from the estate of Jon Sarkin, available for acquisition. Each work is documented in the catalog raisonné.
  (with "catalog raisonné" linked to catalog.jonsarkin.com)

**Product cards in the grid:**
- Artwork image floating against the dark background — no card borders, no card background color, no drop shadows. Just the image in space.
- Catalog number (e.g., JS-2023-TXXXX) in light text — NOT the editorial title
- Medium and dimensions in secondary gray text
- Price in light text
- No "Add to cart" button on grid cards — click goes to product detail page

**Filters:**
- Rename "Type" → "Format" (or "Support")
- Rename "Subject" → "Motif"
- Keep: Price, Year
- Remove sort options: "Best selling," "Alphabetically A-Z/Z-A"
- Keep sort options: "Price low→high / high→low," "Date new→old / old→new"
- Add: "Recently Added" if possible

### Product Pages (/products/*)

**Layout — large hero image against dark background, structured metadata below in light text:**

```
[LARGE ARTWORK IMAGE — full width or near-full width]
[thumbnail strip for additional images: front, back, detail]

Jon Sarkin
JS-XXXX-TXXXX

Ink and marker on cardboard album sleeve
12.5 × 12.5 in. (31.75 × 31.75 cm)
[Year]

$X,XXX

[Acquire This Work]          [View in the Catalog Raisonné →]
```

- "Acquire This Work" replaces "Add to Cart" globally
  - In locale file `locales/en.default.json`, change `"add_to_cart"` value to `"Acquire This Work"`
- Catalog raisonné link goes to: `https://catalog.jonsarkin.com/s/catalog/item/XXXX`
  (the XXXX is the T-number from the catalog number — store this in a product metafield or in the description)
- For high-value pieces ($10,000+) or sold-out works, show "Inquire" button instead, linking to /pages/contact or mailto:art@jonsarkin.com

**Remove from product pages:**
- "About Jon Sarkin" bio section at the bottom
- Share button
- "Regular price / Sale price / Unit price" block — show a single clean price
- "Sold out" label (replace with "Inquire" for works no longer available)

### About Page (/pages/about)

Full rewrite needed. Current page has factual errors:
- ❌ "Jon is currently represented by the Henry Boxer Outsider Art Gallery" — exclusivity lapsed, remove
- ❌ Birth year listed as 1954 — should be 1953
- ❌ Present tense throughout — Jon died July 19, 2024, use past tense

**Structure:**
1. **Jon Sarkin (1953–2024)** — 2-3 paragraphs, past tense
2. **The Catalog Raisonné** — brief description + link to catalog.jonsarkin.com
3. **Advisory Board** — Colin Rhodes, Tony Millionaire, Dr. Alice Flaherty with roles
4. **The Estate** — Mark Henderson, Estate Manager. Canonical contact sentence.
5. **Selected Press** — publication names with links where available
6. **Books** — keep existing books section (The Art of Jon Sarkin by Colin Rhodes, Shadows Bright As Glass by Amy Ellis Nutt)

### Footer (site-wide)

- Dark background, matching the page — footer should feel like a continuation, not a separate block
- Copyright in secondary gray text: `© The Jon Sarkin Estate / Artists Rights Society (ARS), New York. All rights reserved.`
- Remove payment method icons entirely (these are the most retail-signaling element on the site)
- Remove or minimize policy links — a single "Legal" or "Policies" link in small gray text is sufficient
- Remove newsletter signup from footer
- Social links (Instagram, YouTube) can stay — use muted gray icons that brighten on hover

### Contact Page (/pages/contact)

Must include the canonical sentence:
"For inquiries about Jon Sarkin's work, contact Mark Henderson, Estate Manager, at art@jonsarkin.com."

## Shopify Admin Changes (via MCP API)

These are data-layer changes, not theme changes:

1. **Rename products:** Change product titles from editorial names to catalog numbers (or add catalog numbers as the primary display, keeping editorial titles in a metafield)
2. **Update product descriptions:** Add catalog raisonné links, structured medium/dimensions/year data
3. **Restructure collections:** Rename "All" → "Available Works," archive "Consignment" and "Featured"
4. **Update page content:** Rewrite About page, Contact page
5. **Update navigation menus:** Restructure main nav per the spec above
6. **Remove/hide customer login:** Disable customer accounts if not needed

## Key Metafields to Create/Use

If not already present, create product metafields for:
- `custom.catalog_number` (single line text) — e.g., "JS-2023-T1234"
- `custom.catalog_url` (URL) — full URL to catalog.jonsarkin.com entry
- `custom.medium` (single line text) — e.g., "Ink and marker on cardboard album sleeve"
- `custom.dimensions_in` (single line text) — e.g., "12.5 × 12.5"
- `custom.dimensions_cm` (single line text) — e.g., "31.75 × 31.75"
- `custom.year` (single line text or integer) — e.g., "2023"

These metafields should be displayed on the product page template and collection cards.

## Files You Should NOT Touch

- Do not modify checkout templates (Shopify controls these)
- Do not delete any existing product images
- Do not change the domain configuration
- Do not modify the Shopify Payments or shipping settings

## Dark Palette — CSS Implementation

The fastest path in Dawn is to override the theme's CSS custom properties. In `assets/base.css` or a new `assets/custom.css` (linked in `layout/theme.liquid`), set:

```css
:root {
  --color-background: #111111;
  --color-foreground: #e8e8e8;
  --color-foreground-secondary: #888888;
  --color-border: #2a2a2a;
  --color-border-subtle: #1e1e1e;
  --color-button-border: #555555;
  --color-link-hover: #ffffff;
}

body {
  background-color: var(--color-background);
  color: var(--color-foreground);
}
```

Dawn's theme settings also expose color scheme options — you may be able to set a dark color scheme in `config/settings_data.json` without touching CSS at all. Check the theme's color scheme settings first. If Dawn supports a "Dark" scheme natively, use it as the base and then fine-tune with CSS overrides.

**Key styling concerns with dark backgrounds:**
- Artwork images need no additional treatment — they'll naturally pop against dark.
- Text contrast: ensure body text passes WCAG AA (4.5:1 ratio minimum). `#e8e8e8` on `#111` passes comfortably.
- Form inputs (if any remain — email signup, search): style with dark backgrounds and light borders, not white input fields.
- The Shopify checkout is NOT customizable in most themes — it will remain light/white. This is fine; the checkout is a transactional step, not part of the gallery experience.

## Testing Checklist

After all changes, verify:
- [ ] **Dark palette:** entire site renders on dark background with light text — no white page flashes, no unstyled sections
- [ ] Homepage loads with hero image, name/dates, two-path CTAs, credibility layer — all on dark background
- [ ] No cart icon visible on homepage
- [ ] Cart icon appears on /collections and /products pages
- [ ] "Acquire This Work" appears instead of "Add to Cart" everywhere
- [ ] Every product page has a working "View in the Catalog Raisonné" link
- [ ] Collection page shows catalog numbers (not editorial titles) as primary identifiers
- [ ] Product cards: images float against dark background, no card borders or backgrounds
- [ ] About page uses past tense, no Henry Boxer reference, correct birth year (1953)
- [ ] Footer: dark background, correct copyright, no payment icons, no newsletter signup
- [ ] Navigation matches the spec (About, Available Works, Catalog Raisonné, Contact)
- [ ] Mobile responsive — especially the homepage credibility layer and dark palette
- [ ] Full visitor flow works: homepage → catalog link → catalog item page (dark → light transition)
- [ ] Reverse flow works: homepage → Available Works → product → "View in catalog" → catalog entry
- [ ] Canonical contact sentence appears on: homepage, about page, contact page
- [ ] SEO meta title: "Jon Sarkin (1953–2024) — Estate of the American Artist"
- [ ] No "Sold out" text anywhere — replaced with "Inquire" where applicable
- [ ] Text contrast passes WCAG AA on all pages
