# Site Architecture

Information architecture for the public catalog site at catalog.jonsarkin.com. Covers page hierarchy, navigation structure, collection taxonomy, and the rationale behind each decision.

Last updated: March 2026.

---

## Navigation Tree

```
CATALOG ▾
  ├── Artwork       → /s/catalog/faceted-browse/2?resource_class_id[]=225
  └── Writing       → /s/catalog/faceted-browse/2?resource_class_id[]=118

COLLECTIONS ▾
  ├── Institutional Holdings  → /s/catalog/item-set?property[0][property]=8&...text=Institutional+Holding
  └── Estate Collections      → /s/catalog/item-set?property[0][property]=8&...text=Estate+Collection

ABOUT ▾
  ├── About the Catalog  → /s/catalog/page/about-the-catalog  (page 20)
  └── About the Artist   → /s/catalog/page/about-jon-sarkin   (page 18)

CONTACT                  → /s/catalog/page/contact            (page 17)
```

Source: `site.navigation` JSON for site ID 5. The three parent items (Catalog, Collections, About) are URL-type nav links pointing to `#` — they serve as labels only, with no destination of their own. Contact is a direct page link at the top level.

### Submenu implementation

Click-to-toggle, not hover. JavaScript in `sarkin.js` (lines 36–90) detects `<li>` items with nested `<ul>`, injects a `.submenu-toggle` button with a chevron, and toggles `.submenu-open` on click. Escape key and click-outside close open submenus. Mobile (<=767px): submenus render inline with indented padding instead of absolute dropdowns.

The menu renders via `$site->publicNav()->menu()->renderMenu(null, ['maxDepth' => 1])` in `layout.phtml:84`.

---

## Page Inventory

### Pages in the navigation

| Page | Slug | Type | Nav location |
|------|------|------|--------------|
| Artwork | — | Faceted browse filtered by `resource_class_id=225` (VisualArtwork) | Catalog > Artwork |
| Writing | — | Faceted browse filtered by `resource_class_id=118` (CreativeWork) | Catalog > Writing |
| Institutional Holdings | — | Item-set browse filtered by `dcterms:type=Institutional Holding` | Collections > Institutional Holdings |
| Estate Collections | — | Item-set browse filtered by `dcterms:type=Estate Collection` | Collections > Estate Collections |
| About the Catalog | `about-the-catalog` | Site page (page 20) | About > About the Catalog |
| About the Artist | `about-jon-sarkin` | Site page (page 18) | About > About the Artist |
| Contact | `contact` | Site page (page 17) | Top-level |

### Pages not in the navigation

| Page | Slug | How accessed |
|------|------|--------------|
| Home | `home` | Site root `/s/catalog` |
| Rights & Reproduction | `rights` | Footer link only |
| Understanding the Jon Sarkin Catalog | `understanding-the-catalog` | Linked from homepage (Colin Rhodes essay) |
| Scholarly Archives | `scholarly-archives` | Direct URL only (legacy page) |
| Welcome | `welcome` | Direct URL only (legacy page) |
| Technical Spec | `technical-spec` | Not public (`is_public=0`) |

---

## Homepage Structure

The homepage (page 13, slug `home`) is a single HTML block doing significant editorial work. Sections in order:

1. **Masthead** — "Jon Sarkin / 1953–2024 / Catalog Raisonné"

2. **Portrait + biographical intro** — Photo (Janet Knott), three-paragraph biography covering the 1988 neurosurgical event, thirty-five years of practice, and *Shadows Bright as Glass*. Links to Wikipedia biography.

3. **Institutional credentials** — Three-column row linking to Centre Pompidou, American Visionary Art Museum, and MoMA Archives (Calvin Tomkins Papers). These are prestige signals — the institutions hold work but may have zero digitized items in the catalog.

4. **Colin Rhodes quote** — Pull quote: "Sarkin's work approaches what André Breton dreamed of but which most Surrealists never achieved: pure psychic automatism." Attribution to Rhodes with publication credit (Cambridge University Press, 2023).

5. **Studio photograph** — Jon at Fish City Studios, Gloucester (Tom Robinson-Cox).

6. **Catalog statistics** — Two-column: "Works Cataloged: 3,679" and "Years Active: 1989–2024". (Note: these are hardcoded in the HTML block, not dynamic.)

7. **Featured collections** — Two-column layout highlighting:
   - **Boltflashed Pieces** — The discovery project (compulsive mailings to strangers). Includes a CTA: "Did you receive unsolicited artwork from Jon Sarkin? Contact the estate."
   - **Permanent Collection** — A-group works held by the estate, designated for institutional placement.

### Why the homepage carries critical context

The Rhodes quote and institutional credentials are the site's intellectual thesis statement. They establish Sarkin's position in the canon (alongside Johns, Rauschenberg, Basquiat, Twombly) before the visitor reaches any catalog data. This is deliberate: the homepage argues for the work's significance, the About pages document the project.

---

## Collection Taxonomy

### Two-tier structure

Collections are split into two types via the `dcterms:type` property (ID 8) on each item set. The Collections submenu filters the item-set browse page by this property.

#### Estate Collections

Browsable series with cataloged works assigned to them.

| Item Set | ID | Count | Description |
|----------|----|-------|-------------|
| Permanent Collection | 7490 | 66 | A-group works held by the estate. Reserved for museum loans, publications, scholarship. Not for sale. |
| Super Artist | 387 | 190 | Works from Sarkin's first posthumous exhibition. |
| Boltflashed Pieces | 7491 | 1 | Works recovered from Sarkin's practice of mailing unsolicited art to strangers ("boltflashing"). Small count because most remain in unknown hands — this is an active discovery project. |
| Scholarly Archives | 4 | 0 | Studies, ephemera, process materials retained for research. |

#### Institutional Holdings

Prestige/provenance documentation. These institutions hold Sarkin works but the works may not be digitized or cataloged yet.

| Item Set | ID | Count | Description |
|----------|----|-------|-------------|
| Centre Pompidou | 7492 | 0 | Permanent collection, Paris. |
| American Visionary Art Museum | 7493 | 0 | Permanent collection, Baltimore. |
| MoMA Archives | 7494 | 0 | Calvin Tomkins Papers, New York. |
| Henry Boxer Collection | 7495 | 0 | Henry Boxer Gallery, London (long-standing representation). |
| Pingry Exhibition | 7496 | 0 | Exhibition at Pingry School. |

#### Why the separation

Mixing estate collections (with browsable works) and institutional holdings (with 0 works) in a flat list creates a confusing experience — visitors click "Centre Pompidou" and see an empty page. The two-tier split communicates that institutional holdings exist for provenance documentation and prestige context, while estate collections contain the actual browsable catalog.

#### Item sets not in the Collections nav

| Item Set | ID | Count | How accessed |
|----------|----|-------|--------------|
| JIM Stories | 8020 | 159 | Via Catalog > Writing (resource class filter, not item set filter). Literary corpus — accessed through the Artwork/Writing split, not through Collections. |
| jsarkin.com Writings, 1997–2019 | 7502 | 150 | Same — part of the Writing corpus. |
| Press & Documentation | 8021 | 154 | Not in any nav. Archival/reference material. |

---

## The Catalog Browse Experience

### Artwork vs. Writing split

The split uses `resource_class_id`, not item sets:
- **Artwork** → `resource_class_id=225` (VisualArtwork) — 4,531 items
- **Writing** → `resource_class_id=118` (CreativeWork) — 160 items

Both link to the same faceted browse page (page ID 2) with a query string filter. The faceted browse module renders the sidebar facets and the chart-card grid.

### Faceted browse configuration

Facet page ID 2, category "Facets". Default sort: `created DESC`.

| Position | Facet | Type | Control | Notes |
|----------|-------|------|---------|-------|
| 1 | Type | resource_class | Single select | VisualArtwork or CreativeWork |
| 2 | Work Type | value (property 8) | Multiple list | Drawing, Painting, Collage, Mixed Media, Sculpture, Print, Other |
| 3 | Literary Form | value (property 1610) | Multiple list | Poetry, Prose, Prose Poem, Essay, Photo Essay — only relevant for Writing |
| 4 | Narrative Voice | value (property 1698) | Multiple list | Third Person, First Person, Second Person, Mixed/Unstable — only relevant for Writing |
| 5 | Location | value (property 40) | Multiple list (truncate 20) | ~250 place names referenced in works |
| 6 | Cultural Reference | value (property 13) | Multiple list (truncate 20) | ~600 cultural references (artists, songs, books, films, places) |
| 7 | Motifs | value (property 3) | Multiple list (truncate 12) | 34 visual motifs including the core 12 + extended set |
| 8 | Collection | item_set | Multiple list | 19 item sets |
| 9 | Support | value (property 931) | Multiple list | Paper, Cardboard, Canvas, Board, etc. |
| 10 | Year | value (property 7) | Single select | 1989–2024 including "c." dates |
| 11 | Condition | value (property 1579) | Single list | Excellent, Good, Fair, Poor, Not Examined |

Browse results display as a two-column chart-card grid (Jeppesen approach-chart style) with thumbnail, year, and dimensions. 24 items per page.

### Sort and export

Sort options are handled by the faceted browse controller. Export links (CSV, JSON) appear in the browse heading bar.

---

## About Section Structure

### About the Catalog (page 20, `/s/catalog/page/about-the-catalog`)

Blocks: pageTitle → html (Colin Rhodes essay) → itemWithMetadata (Colin Rhodes person item 9476) → html (Critical Context + catalog documentation + acknowledgments).

Content sections:
- **Colin Rhodes introductory essay** — Full biographical/critical essay placing Sarkin in the context of Johns, Rauschenberg, Basquiat, Twombly, and R. Crumb. Covers the 1989 medical event, working methods, materials, motifs, and the "stream-of-consciousness" practice.
- **Colin Rhodes** — Person item rendered via the `item-with-metadata` block template (photo carousel + bio).
- **Critical Context** — Summary of Rhodes's five defining characteristics of Sarkin's work.
- **About the Catalog Raisonné** — Scope, methodology, cataloging standards, catalog numbering (JS-YYYY-NNNN), iconographic profiling, full-text transcription, visual similarity, citation format.
- **Acknowledgments** — Estate direction (Mark Henderson / Fish City Studios), ARS rights administration, Henry Boxer Gallery.

This is the page for scholars, catalogers, and institutions.

### About the Artist (page 18, nav label "About the Artist", `/s/catalog/page/about-jon-sarkin`)

Blocks: pageTitle → itemWithMetadata (Jon Sarkin person item 3) → html (Critical Context + catalog documentation) → itemWithMetadata (Colin Rhodes person item 9476).

Content sections:
- **Jon Sarkin** — Person item rendered via `item-with-metadata` block: photo carousel + biography from the person record.
- **Critical Context + catalog documentation** — Same HTML block as the About the Catalog page (shared content).
- **Colin Rhodes** — Person item with photo and bio.

This is the page for journalists, casual visitors, and anyone who wants the human story.

### Deliberate separation

The critical/interpretive content (Rhodes's arguments about the work's significance) lives on the homepage and the essay page, not only on the About pages. The About the Catalog page is reference documentation — scope, methodology, citation format. The About the Artist page leads with the person, then provides context.

---

## Footer

Two links in the footer nav (`layout.phtml:97–99`):
- **Rights & Reproduction** → `/s/catalog/page/rights` — Copyright notice (ARS), image request process, fair use statement, citation format.
- **Contact** → `/s/catalog/page/contact`

Below: copyright text from Omeka site settings. Conditionally: estate public key in `<code>` if configured.

---

## Access Control (SiteLockdown)

The SiteLockdown module (`omeka/volume/modules/SiteLockdown/Module.php`) gates all public site routes behind a password form.

### What's gated

Every public route except:
- Admin routes
- API routes (consumed by frontend JS)
- SimilarPieces JSON endpoints (similar, iconography, lexical-profile)
- `robots.txt`
- **Preview items** — 5 hardcoded item IDs (2082, 7467, 5440, 8824, 8818) are accessible without authentication. These allow sharing individual works while the site is in pre-launch.

### Authentication mechanism

- POST `lockdown_password` to any gated URL
- Server checks bcrypt hash stored in Omeka settings
- Sets `site_lockdown_auth` cookie (HMAC of hash + secret)
- Cookie persists until browser session ends

### SEO blocking (while gated)

- `X-Robots-Tag: noindex, nofollow` header on every response
- `<meta name="robots" content="noindex, nofollow">` injected into `<head>`
- Custom `robots.txt` route disallowing all crawlers

### Removal plan

At public launch: uninstall the module or clear the password hash in settings. The preview item IDs and SEO blocking become irrelevant.

---

## Design Rationale

**Why submenus instead of more top-level nav items** — Four top-level items (Catalog, Collections, About, Contact) keeps the nav bar clean and scannable. The full site has 7+ destinations; cramming them all at the top level creates visual noise. The click-to-toggle pattern works on both desktop and mobile.

**Why the essay page has no nav slot** — The Colin Rhodes essay (`understanding-the-catalog`, page 10) is a destination, not a waypoint. It's linked from the homepage and the About pages. Putting a 3,000-word scholarly essay in the nav would imply it's a standard browse page. It's not — it's a standalone critical text.

**Why Catalog splits into Artwork / Writing** — Sarkin's literary practice (159 JIM Stories, 150 jsarkin.com writings) is genuinely distinct from his visual corpus (4,531 works). They have different metadata schemas (literary form, narrative voice vs. medium, support, dimensions), different resource classes, and serve different research questions. Mixing them in a single browse creates confusion — a "Drawing" facet is meaningless for a prose poem.

**Why institutional holdings are separated from estate collections** — See the Collection Taxonomy section above. The "0 works" problem.

**Why critical context lives on the homepage rather than only on About subpages** — The homepage is the thesis statement. Most visitors see only the homepage. If the canonical argument for Sarkin's significance (Rhodes's positioning alongside Johns, Basquiat, Twombly) is buried on a subpage, most visitors never see it. The homepage quote and institutional row do the persuasive work; the About pages provide the evidence and documentation.
