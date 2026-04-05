// ── Rapid-Fire Metadata Editor ───────────────────────────────────────────────
// Embedded in Omeka admin as the RapidEditor module.
// Uses session-based auth — no proxy needed.

(function() {
'use strict';

// ── Config ──────────────────────────────────────────────────────────────────

const API_BASE = '/api';

// Bucket / swipe mode (used by Exhibition)
const SWIPE_THRESHOLD = 100;
const ROTATION_FACTOR = 0.12;
const FLY_DISTANCE = 1.5;

// Sprint mode — field-specific card workflows (built after WORK_TYPES etc. are defined)
let FIELD_SPRINTS; // initialized in initFieldSprints()

// Property IDs (from enrich_metadata.py)
const PROP = {
  'dcterms:title':                  1,
  'dcterms:identifier':            10,
  'dcterms:date':                   7,
  'dcterms:type':                   8,
  'dcterms:medium':                26,
  'dcterms:format':                 9,
  'dcterms:description':            4,
  'dcterms:subject':                3,
  'dcterms:rights':                15,
  'dcterms:provenance':            51,
  'dcterms:spatial':               40,
  'dcterms:bibliographicCitation': 48,
  'schema:artworkSurface':        931,
  'schema:height':                603,
  'schema:width':                1129,
  'schema:distinguishingSign':    476,
  'schema:itemCondition':        1579,
  'schema:creditText':           1343,
  'schema:creator':               921,
  'schema:box':                  1424,
  'bibo:owner':                    72,
  'bibo:annotates':                57,
  'bibo:content':                  91,
  'bibo:presentedAt':              74,
  'curation:note':               1710,
  'curation:category':           1698,
};

// Controlled vocabularies — populated from Omeka custom_vocabs API at init
let WORK_TYPES = [];
let SUPPORTS = [];
let MOTIFS = [];
let CONDITIONS = [];
let SIGNATURE_ARROWS = ['↖', '↑', '↗', '←', '∅', '→', '↙', '↓', '↘']; // layout order matters for grid
const DATE_YEARS = Array.from({ length: 2024 - 1987 + 1 }, (_, i) => String(1987 + i));

// Write-safe keys for PATCH (from enrich_metadata.py:_clean_value)
const WRITE_KEYS = new Set([
  'type', 'property_id', '@value', '@id', '@language',
  'o:label', 'value_resource_id', 'uri', 'o:is_public',
]);

// Doctor validation patterns
const TEMP_ID_RE = /^JS-\d{4}-T\d+$/;
const EXIF_TS_RE = /^\d{4}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2}$/;
const ISO_TS_RE = /^\d{4}-\d{2}-\d{2}[T ]/;
const YEAR_RE = /\d{4}/;
const EARLIEST_YEAR = 1989;
const DEATH_YEAR = 2024;

// ── State ───────────────────────────────────────────────────────────────────

let allItems = [];       // Full item summaries from API
let queue = [];          // Filtered/sorted working queue
let queueIndex = 0;      // Current position in queue
let mediaCache = {};      // mediaId → original_url
let stickyDims = { height: '', width: '' }; // persist dimensions across cards
let stickyText = {}; // persist text field values across cards (keyed by term)
let ALL_MOTIF_TAGS = []; // all distinct motif values across items (autocomplete corpus)
let filterMode = 'issues'; // 'issues' | 'sprint' | 'exhibit'

// Bucket state (used by Exhibition mode)
let bucketMode = false;
let bucketConfig = null;       // current bucket config object
let bucketQueue = [];
let bucketIndex = 0;
let bucketLastAction = null;   // { itemId, direction, oldFieldValues, addedSets }
let bucketDragState = null;    // { startX, startY, currentX, currentY, cardEl, pointerId }
let bucketActing = false;      // prevent double-swipe while fly-off in progress
let availableItemSets = [];    // fetched from API at init

// Exhibition curation state
let exhibitMode = false;
let exhibitState = null;       // { name, sourceFilter, rounds, currentRound }

// Tournament state (head-to-head bracket within an exhibition)
let tournamentMode = false;
let tournamentState = null;    // { bracket, currentMatch, roundNum, survivors, setId, exhibitName }

// Sprint state
let sprintMode = false;
let sprintField = null;       // key into FIELD_SPRINTS
let sprintQueue = [];
let sprintIndex = 0;
let sprintLastAction = null;  // { itemId, field, oldValues }
let sprintActing = false;

// ── DOM refs ────────────────────────────────────────────────────────────────

const _container = () => document.querySelector('.rapid-editor-container');
const $ = (sel) => _container().querySelector(sel);
const $$ = (sel) => _container().querySelectorAll(sel);

const dom = {};

function cacheDom() {
  dom.loading = $('#loading');
  dom.loadingText = $('#loading-text');
  dom.main = $('#main');
  dom.queueStatus = $('#queue-status');
  dom.itemLink = $('#item-link');
  dom.countIssues = $('#count-issues');
  dom.progressFill = $('#progress-fill');
  dom.image = $('#item-image');
  dom.imageLoading = $('#image-loading');
  dom.formPanel = $('#form-panel');
  dom.toast = $('#toast');
  // Sprint view (inside form panel)
  dom.sprintView = $('#sprint-view');
  dom.sprintViewFieldLabel = $('#sprint-view-field-label');
  dom.sprintViewCount = $('#sprint-view-count');
  dom.sprintViewUndo = $('#sprint-view-undo');
  dom.sprintViewMeta = $('#sprint-view-meta');
  dom.sprintViewCurrent = $('#sprint-view-current');
  dom.sprintViewZone = $('#sprint-view-zone');
  dom.sprintViewSkip = $('#sprint-view-skip');
  dom.sprintViewDone = $('#sprint-view-done');
  dom.sprintViewActions = $('#sprint-view-actions');
}

// ── API helpers ─────────────────────────────────────────────────────────────

function apiUrl(path, params = {}) {
  const p = new URLSearchParams(params);
  const qs = p.toString();
  return `${API_BASE}/${path}${qs ? '?' + qs : ''}`;
}

async function apiGet(path, params = {}) {
  // Route item reads through the module's PHP proxy so private values
  // (is_public=0) are included.  The public REST API strips them, which
  // causes saves to silently drop those properties.
  const itemMatch = path.match(/^items\/(\d+)$/);
  const url = itemMatch
    ? `/admin/rapid-editor/read/${itemMatch[1]}`
    : apiUrl(path, params);
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`API ${resp.status}: ${path}`);
  return { json: await resp.json(), headers: resp.headers };
}

async function apiPatch(path, body) {
  // Route writes through the module's PHP proxy which uses Omeka's internal
  // API — no REST API credentials needed, admin session handles auth.
  const m = path.match(/^items\/(\d+)$/);
  const url = m
    ? `/admin/rapid-editor/patch/${m[1]}`
    : apiUrl(path);   // fallback (shouldn't happen)
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`PATCH ${resp.status}: ${text.slice(0, 200)}`);
  }
  return resp.json();
}

// ── Data loading ────────────────────────────────────────────────────────────

async function fetchAllData() {
  dom.loadingText.textContent = 'Loading items…';

  const resp = await fetch('/admin/rapid-editor/data');
  if (!resp.ok) throw new Error(`Data endpoint ${resp.status}`);
  const data = await resp.json();

  // Apply custom vocabs
  const vocabs = data.vocabs || {};
  if (vocabs['Work Type']?.length) WORK_TYPES = vocabs['Work Type'];
  if (vocabs['Support']?.length) SUPPORTS = vocabs['Support'];
  if (vocabs['Motifs']?.length) MOTIFS = vocabs['Motifs'];
  if (vocabs['Condition']?.length) CONDITIONS = vocabs['Condition'];
  if (vocabs['Signature']?.length) {
    const vocabSet = new Set(vocabs['Signature']);
    SIGNATURE_ARROWS = SIGNATURE_ARROWS.filter(a => vocabSet.has(a));
  }

  // Item sets
  availableItemSets = data.item_sets || [];

  // Items
  allItems = data.items || [];
  for (const item of allItems) {
    item._issues = validateItem(item);
    item._identifier = extractValue(item, 'dcterms:identifier') || `item-${item['o:id']}`;
  }

  // Build motif autocomplete corpus from all existing values
  buildMotifTagCorpus();
}

function buildMotifTagCorpus() {
  const tagSet = new Set();
  for (const item of allItems) {
    for (const v of extractAllValues(item, 'dcterms:subject')) tagSet.add(v);
  }
  ALL_MOTIF_TAGS = [...tagSet].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
}

// ── Value extraction ────────────────────────────────────────────────────────

function extractValue(item, term) {
  const vals = item[term] || [];
  if (!vals.length) return '';
  return (vals[0]['@value'] || vals[0]['o:label'] || '').trim();
}

function extractAllValues(item, term) {
  return (item[term] || [])
    .map(v => (v['@value'] || v['o:label'] || '').trim())
    .filter(Boolean);
}

// ── Catalog ID helpers ──────────────────────────────────────────────────────

function suggestCatalogId(item) {
  const dateVal = extractValue(item, 'dcterms:date');
  const yearMatch = dateVal && dateVal.match(/\d{4}/);
  const year = yearMatch ? yearMatch[0] : '0000';
  return `JS-${year}-T${item['o:id']}`;
}

// ── Client-side validation (mirrors doctor_catalog.py) ──────────────────────

function validateItem(item) {
  const issues = [];

  const id = extractValue(item, 'dcterms:identifier');
  if (!id) {
    issues.push({ field: 'Catalog #', level: 'error', msg: 'missing' });
  }

  const type = extractValue(item, 'dcterms:type');
  if (!type) {
    issues.push({ field: 'Type', level: 'error', msg: 'missing' });
  }

  if (!extractValue(item, 'dcterms:medium')) {
    issues.push({ field: 'Medium', level: 'error', msg: 'missing' });
  }

  const support = extractValue(item, 'schema:artworkSurface');
  if (!support) {
    issues.push({ field: 'Support', level: 'error', msg: 'missing' });
  }

  for (const [term, label] of [['schema:height', 'Height'], ['schema:width', 'Width']]) {
    const v = extractValue(item, term);
    if (!v) {
      issues.push({ field: label, level: 'error', msg: 'missing' });
    } else if (isNaN(parseFloat(v))) {
      issues.push({ field: label, level: 'error', msg: 'non-numeric' });
    }
  }

  const sig = extractValue(item, 'schema:distinguishingSign');
  if (!sig) {
    issues.push({ field: 'Signature', level: 'error', msg: 'missing' });
  } else if (sig.length !== 1 || !SIGNATURE_ARROWS.includes(sig)) {
    issues.push({ field: 'Signature', level: 'error', msg: 'invalid' });
  }

  const date = extractValue(item, 'dcterms:date');
  if (!date) {
    issues.push({ field: 'Date', level: 'error', msg: 'missing' });
  } else {
    if (EXIF_TS_RE.test(date)) {
      issues.push({ field: 'Date', level: 'error', msg: 'EXIF timestamp' });
    } else if (ISO_TS_RE.test(date)) {
      issues.push({ field: 'Date', level: 'error', msg: 'ISO timestamp' });
    } else {
      const ym = date.match(YEAR_RE);
      if (ym) {
        const y = parseInt(ym[0], 10);
        if (y < EARLIEST_YEAR) issues.push({ field: 'Date', level: 'error', msg: `pre-${EARLIEST_YEAR}` });
        else if (y > DEATH_YEAR) issues.push({ field: 'Date', level: 'error', msg: 'posthumous' });
      }
      // Approximate dates (c. 2005) are acceptable — no warning
    }
  }

  if (!extractValue(item, 'dcterms:format')) {
    issues.push({ field: 'Framing', level: 'error', msg: 'missing' });
  }
  if (!extractValue(item, 'bibo:owner')) {
    issues.push({ field: 'Owner', level: 'error', msg: 'missing' });
  }
  if (!extractValue(item, 'dcterms:spatial')) {
    issues.push({ field: 'Location', level: 'error', msg: 'missing' });
  }
  if (!extractAllValues(item, 'dcterms:subject').length) {
    issues.push({ field: 'Motifs', level: 'error', msg: 'missing' });
  }
  if (!(item['o:media'] || []).length) {
    issues.push({ field: 'Media', level: 'error', msg: 'no image' });
  }
  if (!extractValue(item, 'schema:box')) {
    issues.push({ field: 'Box', level: 'error', msg: 'missing' });
  }
  if (!extractValue(item, 'bibo:content')) {
    issues.push({ field: 'Transcription', level: 'error', msg: 'missing' });
  }

  const cat = extractValue(item, 'curation:category');
  if (!cat) {
    issues.push({ field: 'Category', level: 'error', msg: 'missing' });
  } else if (!['A', 'B', 'C', 'D'].includes(cat)) {
    issues.push({ field: 'Category', level: 'error', msg: 'invalid (must be A–D)' });
  }

  // Duplicate values on any property
  for (const term of Object.keys(PROP)) {
    const vals = extractAllValues(item, term);
    if (vals.length < 2) continue;
    const unique = new Set(vals);
    if (unique.size < vals.length) {
      const label = term.split(':')[1];
      issues.push({ field: label, level: 'error', msg: 'duplicate values' });
    }
  }

  return issues;
}

// ── Queue management ────────────────────────────────────────────────────────

function hasBadDate(item) {
  const d = extractValue(item, 'dcterms:date');
  if (!d) return true;
  if (EXIF_TS_RE.test(d) || ISO_TS_RE.test(d)) return true;
  // Flag out-of-range years (pre-career or future)
  const m = d.match(YEAR_RE);
  if (m) {
    const y = parseInt(m[0], 10);
    if (y < EARLIEST_YEAR || y > DEATH_YEAR) return true;
  }
  return false;
}

function buildQueue() {
  queue = allItems
    .filter(it => it._issues.some(i => i.level === 'error'))
    .sort((a, b) => b._issues.length - a._issues.length);
  queueIndex = Math.min(queueIndex, Math.max(0, queue.length - 1));
}

function updateNav() {
  const issueCount = allItems.filter(it => it._issues.some(i => i.level === 'error')).length;
  dom.countIssues.textContent = `(${issueCount})`;

  if (!queue.length) {
    const emptyMsg = { issues: 'No issues found!' };
    dom.queueStatus.textContent = emptyMsg[filterMode] || 'No items';
    dom.itemLink.textContent = '';
    dom.itemLink.href = '#';
    dom.progressFill.style.width = '0';
    return;
  }

  dom.queueStatus.textContent = `${queueIndex + 1} / ${queue.length}`;
  const item = queue[queueIndex];
  if (item) {
    const id = item._identifier;
    dom.itemLink.textContent = id;
    dom.itemLink.href = `/admin/item/${item['o:id']}/edit`;
  }
  dom.progressFill.style.width = `${((queueIndex + 1) / queue.length) * 100}%`;
}

// ── Media resolution ────────────────────────────────────────────────────────

async function getImageUrl(item) {
  const media = (item['o:media'] || [])[0];
  if (!media) return null;
  const mediaId = media['o:id'];
  if (mediaCache[mediaId]) return mediaCache[mediaId];

  try {
    const { json } = await apiGet(`media/${mediaId}`);
    const url = json['o:thumbnail_urls']?.large || json['o:original_url'] || '';
    // Convert absolute URL to proxy path
    const path = url.replace(/^https?:\/\/[^/]+/, '');
    mediaCache[mediaId] = path;
    return path;
  } catch {
    return null;
  }
}

// Card modes use large thumbnails instead of originals
async function getCardImageUrl(item) {
  const media = (item['o:media'] || [])[0];
  if (!media) return null;
  const mediaId = media['o:id'];
  const cacheKey = `large_${mediaId}`;
  if (mediaCache[cacheKey]) return mediaCache[cacheKey];

  try {
    const { json } = await apiGet(`media/${mediaId}`);
    const url = json['o:thumbnail_urls']?.large || json['o:original_url'] || '';
    const path = url.replace(/^https?:\/\/[^/]+/, '');
    mediaCache[cacheKey] = path;
    return path;
  } catch {
    return null;
  }
}

// ── Build PATCH payload ─────────────────────────────────────────────────────
// Critical: mirrors backfill_defaults.py:build_payload exactly.
// Must preserve ALL existing properties; only overwrite edited fields.

function cleanValue(v) {
  const clean = {};
  for (const k of WRITE_KEYS) {
    if (k in v) clean[k] = v[k];
  }
  return clean;
}

function literalValue(term, val) {
  return { type: 'literal', property_id: PROP[term], '@value': val, is_public: true };
}

// ── Session persistence ─────────────────────────────────────────────────────

function savePosition() {
  try {
    const state = {
      filter: filterMode,
      sprintField: sprintField,
    };
    if (filterMode === 'exhibit') {
      state.exhibitMode = true;
    }
    localStorage.setItem('rapid-editor', JSON.stringify(state));
  } catch { /* ignore */ }
}

function restorePosition() {
  try {
    const saved = JSON.parse(localStorage.getItem('rapid-editor') || '{}');
    if (saved.filter) filterMode = saved.filter;
    if (saved.sprintField) sprintField = saved.sprintField;
    // Migrate removed filter modes
    if (['dates', 'box', 'all', 'curate'].includes(filterMode)) {
      filterMode = 'issues';
    }
  } catch { /* ignore */ }
}

// ── UI setup ────────────────────────────────────────────────────────────────

function setupFilterButtons() {
  for (const btn of $$('.filter-btn')) {
    btn.addEventListener('click', () => {
      // Exhibition curation mode
      if (btn.dataset.filter === 'exhibit') {
        if (exhibitMode) return;
        enterExhibitMode();
        return;
      }

      // Issues mode — auto-enter sprint on first issue field
      if (btn.dataset.filter === 'issues') {
        if (exhibitMode) exitExhibitMode();
        if (sprintMode) exitSprintMode();
        for (const b of $$('.filter-btn')) b.classList.remove('active');
        btn.classList.add('active');
        filterMode = 'issues';
        autoSprintIssues();
        return;
      }
    });
  }
}

function setupKeyboard() {
  document.addEventListener('keydown', (e) => {
    const mod = e.metaKey || e.ctrlKey;

    // Bucket mode shortcuts (Exhibition uses bucket infrastructure)
    if (bucketMode) {
      if (e.key === 'ArrowRight') {
        e.preventDefault();
        triggerBucketSwipe('right');
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        triggerBucketSwipe('left');
      } else if (mod && e.key === 'z') {
        e.preventDefault();
        bucketUndo();
      }
      return;
    }

    // Sprint mode shortcuts
    if (sprintMode) {
      if (e.key === 'ArrowLeft' && !isTextInput(e.target)) {
        e.preventDefault();
        sprintSkip();
      } else if (mod && e.key === 'z') {
        e.preventDefault();
        sprintUndo();
      }
      return;
    }
  });
}

function isTextInput(el) {
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

// ── Toast ───────────────────────────────────────────────────────────────────

function showToast(msg, isError = false) {
  dom.toast.textContent = msg;
  dom.toast.style.background = isError ? '#c0392b' : '#111';
  dom.toast.classList.remove('hidden');
  // Reset animation
  dom.toast.style.animation = 'none';
  dom.toast.offsetHeight; // trigger reflow
  dom.toast.style.animation = '';
  setTimeout(() => dom.toast.classList.add('hidden'), 2000);
}

// ══════════════════════════════════════════════════════════════════════════════
// ── Bucket Sort Mode — configurable two-bucket swipe sorting ─────────────────
// ══════════════════════════════════════════════════════════════════════════════

// Left swipe is always skip (touch o:modified, no field/set changes)
const BUCKET_LEFT = { label: 'Skip', color: '#555', actions: [] };

// ── Bucket: matching helpers (used by Exhibition) ────────────────────────────

function itemMatchesBucket(item, bucket) {
  if (!bucket.actions.length) return false;
  return bucket.actions.every(action => {
    if (action.type === 'item_set') {
      const sets = item['o:item_set'] || [];
      return sets.some(s => s['o:id'] === action.setId);
    }
    return false;
  });
}

function describeBucket(bucket) {
  if (!bucket.actions.length) return 'Skip';
  return bucket.actions.map(a => {
    if (a.type === 'item_set') return a.setLabel;
    return a.term;
  }).join(', ');
}

// ── Bucket: base payload builder ─────────────────────────────────────────────

function buildBasePayload(item) {
  const payload = {};
  for (const [key, val] of Object.entries(item)) {
    if (key.includes(':') && !key.startsWith('o:') && Array.isArray(val)) {
      payload[key] = val.filter(v => typeof v === 'object').map(cleanValue);
    }
  }
  for (const sysKey of ['o:resource_class', 'o:resource_template', 'o:item_set', 'o:media', 'o:is_public', 'o:site']) {
    if (sysKey in item) payload[sysKey] = item[sysKey];
  }
  // Omeka's REST API omits dcterms:title (surfaced as o:title instead).
  // The internal update API still requires it when the resource template
  // marks title as required — synthesize it so sprint saves don't 500.
  if (!payload['dcterms:title'] && item['o:title']) {
    payload['dcterms:title'] = [{ type: 'literal', property_id: PROP['dcterms:title'], '@value': item['o:title'] }];
  }
  return payload;
}

// ── Bucket: queue ────────────────────────────────────────────────────────────

function buildBucketQueue(config) {
  let items = allItems;
  if (config.queueFilter === 'exclude_matched') {
    items = items.filter(item => !itemMatchesBucket(item, config.right));
  }
  bucketQueue = [...items].sort((a, b) => {
    const ma = (a['o:modified'] && a['o:modified']['@value']) || '';
    const mb = (b['o:modified'] && b['o:modified']['@value']) || '';
    return ma.localeCompare(mb);
  });
  bucketIndex = 0;
}

// ── Bucket: mode switching (used by Exhibition) ─────────────────────────────

function exitBucketMode() {
  bucketMode = false;
  $('#curate-panel').classList.add('hidden');
  dom.main.classList.remove('hidden');
  clearCardStage();
}

// ── Bucket: start swiping ────────────────────────────────────────────────────

function startBucketSwiping(config, isResume = false) {
  bucketConfig = config;

  // Restore card-stage sizing
  const stage = $('#card-stage');
  stage.style.width = '';
  stage.style.height = '';

  // Show swipe UI
  $('#curate-actions').classList.remove('hidden');
  $('#curate-progress').classList.remove('hidden');

  // Update button labels and colors — left is always Skip
  const passBtn = $('#curate-pass');
  const keepBtn = $('#curate-keep');
  passBtn.textContent = `← ${BUCKET_LEFT.label}`;
  keepBtn.textContent = `${config.right.label} →`;
  passBtn.style.background = BUCKET_LEFT.color;
  keepBtn.style.background = config.right.color;

  // Set CSS custom properties for hint colors
  document.documentElement.style.setProperty('--bucket-right-color', config.right.color);
  document.documentElement.style.setProperty('--bucket-left-color', BUCKET_LEFT.color);

  if (!isResume) {
    buildBucketQueue(config);
  }

  updateBucketProgress();

  if (bucketQueue.length) {
    renderBucketCards();
    preloadBucketNext();
  } else {
    showBucketComplete();
  }

  savePosition();
}

// ── Bucket: progress ─────────────────────────────────────────────────────────

function updateBucketProgress() {
  const countEl = $('#curate-count');
  const undoBtn = $('#curate-undo');
  const remaining = bucketQueue.length - bucketIndex;
  countEl.textContent = `${bucketIndex} reviewed · ${remaining} remaining`;
  undoBtn.disabled = !bucketLastAction;

  const pct = bucketQueue.length > 0 ? (bucketIndex / bucketQueue.length) * 100 : 0;
  dom.progressFill.style.width = `${pct}%`;

  // Persist exhibit round progress on each swipe
  if (exhibitMode && exhibitState) {
    persistExhibitRoundProgress();
  }
}

// ── Bucket: card rendering ───────────────────────────────────────────────────

function clearCardStage() {
  $('#card-stage').innerHTML = '';
}

function renderBucketCards() {
  clearCardStage();
  if (bucketIndex + 1 < bucketQueue.length) {
    renderBucketCard(bucketQueue[bucketIndex + 1], 1);
  }
  if (bucketIndex < bucketQueue.length) {
    renderBucketCard(bucketQueue[bucketIndex], 2);
  }
}

async function renderBucketCard(item, zIndex) {
  const stage = $('#card-stage');
  const card = document.createElement('div');
  card.className = 'curate-card';
  card.dataset.itemId = item['o:id'];
  card.style.zIndex = zIndex;

  if (zIndex === 1) {
    card.classList.add('back-card');
    card.style.transform = 'scale(0.95)';
    card.style.opacity = '0.5';
    card.style.pointerEvents = 'none';
  }

  const identifier = item._identifier || `item-${item['o:id']}`;
  const medium = extractValue(item, 'dcterms:medium');
  const date = extractValue(item, 'dcterms:date');
  const h = extractValue(item, 'schema:height');
  const w = extractValue(item, 'schema:width');
  const dims = (h && w) ? `${h}″ × ${w}″` : '';
  const detail = [medium, dims].filter(Boolean).join(', ');

  const editUrl = `/admin/item/${item['o:id']}/edit`;
  card.innerHTML = `
    <div class="card-img-wrap">
      <div class="card-img-loading">Loading…</div>
    </div>
    <div class="card-meta">
      <div class="card-meta-id"><a href="${editUrl}" target="_blank">${identifier}</a></div>
      ${detail ? `<div class="card-meta-detail">${detail}</div>` : ''}
      ${date ? `<div class="card-meta-date">${date}</div>` : ''}
    </div>
  `;

  stage.appendChild(card);

  const url = await getCardImageUrl(item);
  if (url) {
    const imgWrap = card.querySelector('.card-img-wrap');
    const img = document.createElement('img');
    img.src = url;
    img.alt = identifier;
    img.onload = () => {
      const loading = imgWrap.querySelector('.card-img-loading');
      if (loading) loading.remove();
    };
    imgWrap.appendChild(img);
  }

  if (zIndex === 2) {
    attachSwipeHandlers(card);
  }
}

function preloadBucketNext() {
  const ahead = bucketIndex + 2;
  if (ahead < bucketQueue.length) {
    getCardImageUrl(bucketQueue[ahead]).then(url => {
      if (url) { const img = new Image(); img.src = url; }
    });
  }
}

// ── Bucket: swipe gesture system ─────────────────────────────────────────────

function attachSwipeHandlers(cardEl) {
  cardEl.addEventListener('pointerdown', onBucketPointerDown);
}

function onBucketPointerDown(e) {
  if (bucketDragState || bucketActing) return;
  const card = e.currentTarget;
  card.setPointerCapture(e.pointerId);
  card.classList.add('dragging');
  bucketDragState = {
    startX: e.clientX,
    startY: e.clientY,
    currentX: e.clientX,
    currentY: e.clientY,
    cardEl: card,
    pointerId: e.pointerId,
  };
  card.addEventListener('pointermove', onBucketPointerMove);
  card.addEventListener('pointerup', onBucketPointerUp);
  card.addEventListener('pointercancel', onBucketPointerUp);
}

function onBucketPointerMove(e) {
  if (!bucketDragState || e.pointerId !== bucketDragState.pointerId) return;
  bucketDragState.currentX = e.clientX;
  bucketDragState.currentY = e.clientY;

  const dx = bucketDragState.currentX - bucketDragState.startX;
  const dy = (bucketDragState.currentY - bucketDragState.startY) * 0.3;
  const rotation = dx * ROTATION_FACTOR;

  bucketDragState.cardEl.style.transform =
    `translate(${dx}px, ${dy}px) rotate(${rotation}deg)`;

  // Directional hint glow
  bucketDragState.cardEl.classList.toggle('hint-right', dx > SWIPE_THRESHOLD * 0.5);
  bucketDragState.cardEl.classList.toggle('hint-left', dx < -SWIPE_THRESHOLD * 0.5);
}

function onBucketPointerUp(e) {
  if (!bucketDragState || e.pointerId !== bucketDragState.pointerId) return;
  const card = bucketDragState.cardEl;
  card.removeEventListener('pointermove', onBucketPointerMove);
  card.removeEventListener('pointerup', onBucketPointerUp);
  card.removeEventListener('pointercancel', onBucketPointerUp);
  card.classList.remove('dragging', 'hint-right', 'hint-left');

  const dx = bucketDragState.currentX - bucketDragState.startX;
  bucketDragState = null;

  if (dx > SWIPE_THRESHOLD) {
    flyOffCard(card, 'right');
  } else if (dx < -SWIPE_THRESHOLD) {
    flyOffCard(card, 'left');
  } else {
    card.classList.add('snap-back');
    card.style.transform = '';
    card.addEventListener('transitionend', () => {
      card.classList.remove('snap-back');
    }, { once: true });
  }
}

function flyOffCard(cardEl, direction) {
  if (bucketActing) return;
  bucketActing = true;

  const vw = window.innerWidth;
  const targetX = direction === 'right' ? vw * FLY_DISTANCE : -vw * FLY_DISTANCE;
  const rotation = direction === 'right' ? 30 : -30;

  cardEl.classList.add('fly-off');
  cardEl.style.transform = `translate(${targetX}px, 0) rotate(${rotation}deg)`;

  cardEl.addEventListener('transitionend', () => {
    cardEl.remove();
    promoteBucketBackCard();
    bucketActing = false;
  }, { once: true });

  const itemId = Number(cardEl.dataset.itemId);
  const bucket = direction === 'right' ? bucketConfig.right : BUCKET_LEFT;
  applyBucketAction(itemId, bucket, direction);
}

function triggerBucketSwipe(direction) {
  if (bucketActing) return;
  const stage = $('#card-stage');
  let topCard = null;
  for (const c of stage.querySelectorAll('.curate-card:not(.fly-off)')) {
    if (!topCard || Number(c.style.zIndex) > Number(topCard.style.zIndex)) {
      topCard = c;
    }
  }
  if (topCard) {
    flyOffCard(topCard, direction);
  }
}

function promoteBucketBackCard() {
  const stage = $('#card-stage');
  const backCard = stage.querySelector('.curate-card');
  if (backCard && backCard.style.zIndex === '1') {
    backCard.classList.remove('back-card');
    backCard.style.zIndex = 2;
    backCard.style.pointerEvents = '';
    backCard.style.transition = 'transform 0.25s ease-out, opacity 0.25s ease-out';
    backCard.style.transform = '';
    backCard.style.opacity = '1';
    attachSwipeHandlers(backCard);
    backCard.addEventListener('transitionend', () => {
      backCard.style.transition = '';
    }, { once: true });
  }

  if (bucketIndex + 1 < bucketQueue.length) {
    renderBucketCard(bucketQueue[bucketIndex + 1], 1);
  }

  preloadBucketNext();

  if (bucketIndex >= bucketQueue.length) {
    showBucketComplete();
  }
}

// ── Bucket: API actions ──────────────────────────────────────────────────────

async function applyBucketAction(itemId, bucket, direction) {
  // Advance immediately — card is already gone
  bucketIndex++;
  updateBucketProgress();

  // Capture old state for undo
  const item = allItems.find(it => it['o:id'] === itemId);
  const oldFieldValues = {};
  const addedSets = [];

  try {
    const { json: freshItem } = await apiGet(`items/${itemId}`);
    const payload = buildBasePayload(freshItem);

    if (bucket.actions.length === 0) {
      // Empty bucket = just touch o:modified
      await apiPatch(`items/${itemId}`, payload);
      bucketLastAction = { itemId, direction, oldFieldValues: {}, addedSets: [] };
      return;
    }

    for (const action of bucket.actions) {
      if (action.type === 'item_set') {
        const sets = payload['o:item_set'] || [];
        if (!sets.some(s => s['o:id'] === action.setId)) {
          payload['o:item_set'] = [...sets, { 'o:id': action.setId }];
          addedSets.push(action.setId);
        }
      }
    }

    const updated = await apiPatch(`items/${itemId}`, payload);

    // Update local state
    const idx = allItems.findIndex(it => it['o:id'] === itemId);
    if (idx >= 0) {
      allItems[idx] = updated;
      allItems[idx]._issues = validateItem(updated);
      allItems[idx]._identifier = extractValue(updated, 'dcterms:identifier') || `item-${updated['o:id']}`;
    }

    bucketLastAction = { itemId, direction, oldFieldValues, addedSets };
    showToast(`✓ ${describeBucket(bucket)}`);
  } catch (err) {
    showToast(`Error: ${err.message}`, true);
    console.error('Bucket action failed:', err);
    bucketLastAction = null;
  }
}

// ── Bucket: undo ─────────────────────────────────────────────────────────────

async function bucketUndo() {
  if (!bucketLastAction || bucketActing) return;
  const { itemId, oldFieldValues, addedSets } = bucketLastAction;

  try {
    const { json: freshItem } = await apiGet(`items/${itemId}`);
    const payload = buildBasePayload(freshItem);

    // Reverse item set additions
    if (addedSets.length) {
      payload['o:item_set'] = (payload['o:item_set'] || [])
        .filter(s => !addedSets.includes(s['o:id']));
    }

    // Reverse field value changes (if any)
    for (const [term, oldVal] of Object.entries(oldFieldValues)) {
      payload[term] = oldVal ? [literalValue(term, oldVal)] : [];
    }

    await apiPatch(`items/${itemId}`, payload);

    // Update local state
    const idx = allItems.findIndex(it => it['o:id'] === itemId);
    if (idx >= 0) {
      const { json: updatedItem } = await apiGet(`items/${itemId}`);
      allItems[idx] = updatedItem;
      allItems[idx]._issues = validateItem(updatedItem);
      allItems[idx]._identifier = extractValue(updatedItem, 'dcterms:identifier') || `item-${updatedItem['o:id']}`;
    }

    showToast('Undone');
  } catch (err) {
    showToast(`Undo error: ${err.message}`, true);
    return;
  }

  // Re-insert item at previous position
  bucketIndex = Math.max(0, bucketIndex - 1);
  const item = allItems.find(it => it['o:id'] === itemId);
  if (item && bucketIndex <= bucketQueue.length) {
    bucketQueue.splice(bucketIndex, 0, item);
  }

  bucketLastAction = null;
  renderBucketCards();
  updateBucketProgress();
}

// ── Bucket: completion ───────────────────────────────────────────────────────

function showBucketComplete() {
  if (exhibitMode) { showExhibitRoundComplete(); return; }
  // Bucket complete outside exhibition — shouldn't happen, but handle gracefully
  clearCardStage();
}

// ── Bucket: button wiring ────────────────────────────────────────────────────

function setupBucketButtons() {
  $('#curate-pass').addEventListener('click', () => triggerBucketSwipe('left'));
  $('#curate-keep').addEventListener('click', () => triggerBucketSwipe('right'));
  $('#curate-undo').addEventListener('click', bucketUndo);
}

// ══════════════════════════════════════════════════════════════════════════════
// ── Exhibition Curation Mode ─────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════════

const EXHIBIT_STORAGE_KEY = 'rapid-editor-exhibit';
const CURATE_PREFIX = '[Curate] ';
const CURATE_RE = /^\[Curate\] (.+) R(\d+)$/;

function loadExhibitStorage() {
  try { return JSON.parse(localStorage.getItem(EXHIBIT_STORAGE_KEY) || '{}'); }
  catch { return {}; }
}

function saveExhibitStorage(data) {
  try { localStorage.setItem(EXHIBIT_STORAGE_KEY, JSON.stringify(data)); }
  catch { /* ignore */ }
}

// ── Exhibition: discovery ────────────────────────────────────────────────────

function discoverExhibitions() {
  const exhibitions = {};
  for (const set of availableItemSets) {
    const m = set.label.match(CURATE_RE);
    if (!m) continue;
    const name = m[1];
    const round = parseInt(m[2]);
    if (!exhibitions[name]) exhibitions[name] = { name, rounds: [] };
    exhibitions[name].rounds.push({ round, setId: set.id, label: set.label });
  }
  for (const ex of Object.values(exhibitions)) {
    ex.rounds.sort((a, b) => a.round - b.round);
    ex.maxRound = ex.rounds[ex.rounds.length - 1].round;
  }
  return Object.values(exhibitions);
}

function countItemsInSet(setId) {
  return allItems.filter(item =>
    (item['o:item_set'] || []).some(s => s['o:id'] === setId)
  ).length;
}

// ── Exhibition: mode entry/exit ──────────────────────────────────────────────

function enterExhibitMode() {
  exhibitMode = true;
  bucketMode = true;  // reuse bucket infrastructure
  filterMode = 'exhibit';

  dom.main.classList.add('hidden');
  if (sprintMode) exitSprintMode();
  $('#curate-panel').classList.remove('hidden');
  $('#curate-actions').classList.add('hidden');
  $('#curate-progress').classList.add('hidden');

  for (const b of $$('.filter-btn')) {
    b.classList.toggle('active', b.dataset.filter === 'exhibit');
  }

  // Check for in-progress tournament to resume
  const stored = loadExhibitStorage();
  if (stored.tournament && stored.tournament.currentMatch < (stored.tournament.bracket || []).length) {
    if (resumeTournament()) {
      renderTournamentMatch();
      savePosition();
      return;
    }
  }

  // Check for in-progress round to resume
  if (stored.roundProgress && stored.roundProgress.bucketIndex > 0) {
    const ex = stored.exhibitions && stored.exhibitions[stored.roundProgress.exhibitName];
    if (ex) {
      exhibitState = ex;
      // Reconcile set IDs with live data
      reconcileExhibitSets(exhibitState);
      renderExhibitRoundSetup(exhibitState);
      savePosition();
      return;
    }
  }

  renderExhibitList();
  savePosition();
}

function exitExhibitMode() {
  exhibitMode = false;
  exhibitState = null;
  bucketMode = false;

  if (tournamentMode) exitTournamentMode();
  $('#curate-panel').classList.add('hidden');
  dom.main.classList.remove('hidden');
  clearCardStage();
}

// Reconcile stored set IDs with actual item sets (after make pull)
function reconcileExhibitSets(state) {
  for (const round of state.rounds) {
    const expected = `${CURATE_PREFIX}${state.name} R${round.round}`;
    const live = availableItemSets.find(s => s.label === expected);
    if (live) round.setId = live.id;
  }
}

// ── Exhibition: list screen ──────────────────────────────────────────────────

function renderExhibitList() {
  const stage = $('#card-stage');
  stage.style.width = '';
  stage.style.height = '';
  const exhibitions = discoverExhibitions();

  let html = '<div class="exhibit-list">';
  html += '<h2>Exhibition Curation</h2>';

  if (exhibitions.length === 0) {
    html += '<p class="exhibit-empty">No exhibitions yet. Create one to start curating.</p>';
  }

  for (const ex of exhibitions) {
    const lastRound = ex.rounds[ex.rounds.length - 1];
    const survivors = countItemsInSet(lastRound.setId);
    const stored = loadExhibitStorage();
    const storedEx = stored.exhibitions && stored.exhibitions[ex.name];
    const totalR1 = storedEx && storedEx.rounds[0] ? storedEx.rounds[0].total : '?';

    html += `
      <button class="exhibit-card" data-exhibit="${ex.name}">
        <div class="exhibit-card-name">${ex.name}</div>
        <div class="exhibit-card-stats">
          Round ${ex.maxRound} · ${survivors} survivors${totalR1 !== '?' ? ` of ${totalR1}` : ''}
        </div>
      </button>`;
  }

  html += `<button class="exhibit-new-btn" id="exhibit-new">+ New Exhibition</button>`;
  html += '</div>';
  stage.innerHTML = html;

  // Wire click handlers
  for (const card of $$('.exhibit-card')) {
    card.addEventListener('click', () => {
      const name = card.dataset.exhibit;
      const ex = exhibitions.find(e => e.name === name);
      if (!ex) return;
      // Build exhibitState from discovered data + stored metadata
      const stored = loadExhibitStorage();
      const storedEx = stored.exhibitions && stored.exhibitions[name];
      exhibitState = {
        name,
        sourceFilter: storedEx ? storedEx.sourceFilter : { value: 'B' },
        rounds: ex.rounds.map(r => {
          const storedRound = storedEx && storedEx.rounds.find(sr => sr.round === r.round);
          return {
            round: r.round,
            setId: r.setId,
            complete: storedRound ? storedRound.complete : false,
            kept: storedRound ? storedRound.kept : countItemsInSet(r.setId),
            total: storedRound ? storedRound.total : 0,
          };
        }),
        currentRound: ex.maxRound,
      };
      renderExhibitRoundSetup(exhibitState);
    });
  }

  $('#exhibit-new').addEventListener('click', renderNewExhibitForm);
}

// ── Exhibition: new exhibition form ──────────────────────────────────────────

function renderNewExhibitForm() {
  const stage = $('#card-stage');
  stage.innerHTML = `
    <div class="exhibit-form">
      <h2>New Exhibition</h2>
      <label for="exhibit-name">Exhibition Name</label>
      <input type="text" id="exhibit-name" placeholder="e.g. Spring Show 2026" autofocus>
      <label>Source Filter — Category</label>
      <div class="exhibit-source-pills">
        <button type="button" class="exhibit-src-pill" data-val="A">A</button>
        <button type="button" class="exhibit-src-pill active" data-val="B">B</button>
        <button type="button" class="exhibit-src-pill" data-val="C">C</button>
        <button type="button" class="exhibit-src-pill" data-val="D">D</button>
      </div>
      <div id="exhibit-pool-count" class="exhibit-pool-count"></div>
      <div class="exhibit-form-actions">
        <button class="btn btn-nav" id="exhibit-back">Back</button>
        <button class="btn btn-save" id="exhibit-create">Create</button>
      </div>
      <div id="exhibit-error" class="exhibit-error hidden"></div>
    </div>
  `;

  // Source filter pills
  let selectedCategory = 'B';
  const updatePoolCount = () => {
    const count = allItems.filter(it => extractValue(it, 'curation:category') === selectedCategory).length;
    $('#exhibit-pool-count').textContent = `${count.toLocaleString()} pieces with Category ${selectedCategory}`;
  };
  updatePoolCount();

  for (const pill of $$('.exhibit-src-pill')) {
    pill.addEventListener('click', () => {
      for (const p of $$('.exhibit-src-pill')) p.classList.remove('active');
      pill.classList.add('active');
      selectedCategory = pill.dataset.val;
      updatePoolCount();
    });
  }

  $('#exhibit-back').addEventListener('click', renderExhibitList);

  $('#exhibit-create').addEventListener('click', async () => {
    const name = $('#exhibit-name').value.trim();
    if (!name) {
      showExhibitError('Please enter an exhibition name.');
      return;
    }

    // Check for duplicate
    const setTitle = `${CURATE_PREFIX}${name} R1`;
    if (availableItemSets.some(s => s.label === setTitle)) {
      showExhibitError(`Exhibition "${name}" already exists.`);
      return;
    }

    // Create the R1 item set
    const createBtn = $('#exhibit-create');
    createBtn.disabled = true;
    createBtn.textContent = 'Creating…';

    try {
      const resp = await fetch('/admin/rapid-editor/create-set', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: setTitle }),
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.error || 'Create failed');
      }
      const result = await resp.json();

      // Add to available sets
      availableItemSets.push({ id: result['o:id'], label: result['o:title'] });

      // Build exhibit state
      exhibitState = {
        name,
        sourceFilter: { value: selectedCategory },
        rounds: [{ round: 1, setId: result['o:id'], complete: false, kept: 0, total: 0 }],
        currentRound: 1,
      };

      // Persist
      persistExhibitState();

      renderExhibitRoundSetup(exhibitState);
    } catch (err) {
      showExhibitError(err.message);
      createBtn.disabled = false;
      createBtn.textContent = 'Create';
    }
  });
}

function showExhibitError(msg) {
  const el = $('#exhibit-error');
  el.textContent = msg;
  el.classList.remove('hidden');
}

// ── Exhibition: round setup screen ───────────────────────────────────────────

function renderExhibitRoundSetup(state) {
  exhibitState = state;
  const stage = $('#card-stage');
  stage.style.width = '';
  stage.style.height = '';

  const round = state.currentRound;
  const pool = buildExhibitQueue(state);
  const roundData = state.rounds.find(r => r.round === round);
  const isComplete = roundData && roundData.complete;

  // Check for resume
  const stored = loadExhibitStorage();
  const progress = stored.roundProgress;
  const canResume = progress
    && progress.exhibitName === state.name
    && progress.round === round
    && progress.bucketIndex > 0
    && !isComplete;

  let sourceLabel;
  if (round === 1) {
    sourceLabel = `Category ${state.sourceFilter.value} pieces`;
  } else {
    sourceLabel = `survivors from Round ${round - 1}`;
  }

  let html = `
    <div class="exhibit-round-setup">
      <h2>${state.name}</h2>
      <div class="exhibit-round-label">Round ${round}</div>
      <div class="exhibit-round-pool">${pool.length.toLocaleString()} ${sourceLabel}</div>`;

  if (isComplete) {
    const kept = roundData.kept || countItemsInSet(roundData.setId);
    html += `
      <div class="exhibit-round-complete-badge">
        Round ${round} complete — kept ${kept} of ${roundData.total || pool.length}
      </div>`;

    if (kept > 0) {
      html += `<button class="btn btn-save" id="exhibit-next-round">Start Round ${round + 1}</button>`;
    }
    html += `<button class="btn btn-nav" id="exhibit-back-list">Back to Exhibitions</button>`;
  } else if (pool.length === 0) {
    html += `<p>No items in pool.</p>
      <button class="btn btn-nav" id="exhibit-back-list">Back to Exhibitions</button>`;
  } else {
    if (pool.length > 2 && pool.length <= 100) {
      html += `<button class="btn btn-save" id="exhibit-tournament">⚔️ Tournament Mode</button>`;
    }
    if (canResume) {
      html += `<button class="btn ${pool.length <= 100 ? 'btn-nav' : 'btn-save'}" id="exhibit-resume">Resume (${progress.bucketIndex} / ${pool.length} reviewed)</button>`;
    }
    html += `<button class="btn btn-nav" id="exhibit-start">Start${canResume ? ' Over' : ''} Swipe Round</button>`;
    html += `<button class="btn btn-nav" id="exhibit-back-list">Back to Exhibitions</button>`;
  }

  html += '</div>';
  stage.innerHTML = html;

  // Wire buttons
  if ($('#exhibit-tournament')) {
    $('#exhibit-tournament').addEventListener('click', () => enterTournamentMode(state));
  }
  if ($('#exhibit-start')) {
    $('#exhibit-start').addEventListener('click', () => startExhibitRound(state, round, false));
  }
  if ($('#exhibit-resume')) {
    $('#exhibit-resume').addEventListener('click', () => startExhibitRound(state, round, true));
  }
  if ($('#exhibit-next-round')) {
    $('#exhibit-next-round').addEventListener('click', () => startNextExhibitRound(state));
  }
  if ($('#exhibit-back-list')) {
    $('#exhibit-back-list').addEventListener('click', renderExhibitList);
  }
}

// ── Exhibition: queue building ────────────────────────────────────────────────

function buildExhibitQueue(state) {
  if (state.currentRound === 1) {
    return allItems.filter(item =>
      extractValue(item, 'curation:category') === state.sourceFilter.value
    );
  }
  // Round N > 1: items in previous round's survivor set
  const prevRound = state.rounds.find(r => r.round === state.currentRound - 1);
  if (!prevRound) return [];
  return allItems.filter(item =>
    (item['o:item_set'] || []).some(s => s['o:id'] === prevRound.setId)
  );
}

// ── Exhibition: start / resume a round ───────────────────────────────────────

function startExhibitRound(state, roundNum, resume) {
  const roundData = state.rounds.find(r => r.round === roundNum);
  if (!roundData) return;

  // Build the queue
  const pool = buildExhibitQueue(state);
  if (!pool.length) {
    showToast('No items in pool', true);
    return;
  }

  // Store total for stats
  roundData.total = pool.length;

  // Configure bucket sort for this round
  bucketConfig = {
    right: {
      label: 'Keep',
      color: '#2a7d2e',
      actions: [{ type: 'item_set', setId: roundData.setId }],
    },
    queueFilter: undefined,
  };

  bucketQueue = [...pool];


  if (resume) {
    const stored = loadExhibitStorage();
    const progress = stored.roundProgress;
    if (progress && progress.queueItemIds) {
      // Reconstruct queue order from stored IDs
      const idSet = new Set(progress.queueItemIds);
      const idOrder = new Map(progress.queueItemIds.map((id, i) => [id, i]));
      bucketQueue = pool
        .filter(it => idSet.has(it['o:id']))
        .sort((a, b) => (idOrder.get(a['o:id']) || 0) - (idOrder.get(b['o:id']) || 0));
      bucketIndex = Math.min(progress.bucketIndex, bucketQueue.length);
    }
  } else {
    bucketIndex = 0;
  }

  // Show swipe UI
  $('#curate-actions').classList.remove('hidden');
  $('#curate-progress').classList.remove('hidden');

  const passBtn = $('#curate-pass');
  const keepBtn = $('#curate-keep');
  passBtn.textContent = '← Cut';
  keepBtn.textContent = 'Keep →';
  passBtn.style.background = '#8b0000';
  keepBtn.style.background = '#2a7d2e';
  document.documentElement.style.setProperty('--bucket-right-color', '#2a7d2e');
  document.documentElement.style.setProperty('--bucket-left-color', '#8b0000');

  // Persist queue order for resume
  persistExhibitRoundProgress();
  persistExhibitState();

  updateBucketProgress();

  if (bucketIndex < bucketQueue.length) {
    renderBucketCards();
    preloadBucketNext();
  } else {
    showBucketComplete();
  }
}

// ── Exhibition: next round ───────────────────────────────────────────────────

async function startNextExhibitRound(state) {
  const nextRound = state.currentRound + 1;
  const setTitle = `${CURATE_PREFIX}${state.name} R${nextRound}`;

  // Check if set already exists
  let existing = availableItemSets.find(s => s.label === setTitle);
  let setId;

  if (existing) {
    setId = existing.id;
  } else {
    try {
      const resp = await fetch('/admin/rapid-editor/create-set', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: setTitle }),
      });
      if (!resp.ok) throw new Error('Failed to create round set');
      const result = await resp.json();
      setId = result['o:id'];
      availableItemSets.push({ id: setId, label: setTitle });
    } catch (err) {
      showToast(`Error: ${err.message}`, true);
      return;
    }
  }

  state.rounds.push({ round: nextRound, setId, complete: false, kept: 0, total: 0 });
  state.currentRound = nextRound;
  persistExhibitState();
  renderExhibitRoundSetup(state);
}

// ── Exhibition: round complete ────────────────────────────────────────────────

function showExhibitRoundComplete() {
  $('#curate-actions').classList.add('hidden');
  $('#curate-progress').classList.add('hidden');

  const state = exhibitState;
  const roundData = state.rounds.find(r => r.round === state.currentRound);
  const kept = countItemsInSet(roundData.setId);

  // Mark round complete
  roundData.complete = true;
  roundData.kept = kept;
  if (!roundData.total) roundData.total = bucketIndex;
  persistExhibitState();

  // Clear round progress
  const stored = loadExhibitStorage();
  delete stored.roundProgress;
  saveExhibitStorage(stored);

  const stage = $('#card-stage');
  const pct = roundData.total > 0 ? Math.round((kept / roundData.total) * 100) : 0;

  let html = `
    <div class="exhibit-round-done">
      <h2>Round ${state.currentRound} Complete</h2>
      <div class="exhibit-round-stats-grid">
        <div class="exhibit-stat">
          <div class="exhibit-stat-num">${kept}</div>
          <div class="exhibit-stat-label">kept</div>
        </div>
        <div class="exhibit-stat">
          <div class="exhibit-stat-num">${roundData.total - kept}</div>
          <div class="exhibit-stat-label">cut</div>
        </div>
        <div class="exhibit-stat">
          <div class="exhibit-stat-num">${pct}%</div>
          <div class="exhibit-stat-label">kept</div>
        </div>
      </div>`;

  if (kept === 0) {
    html += '<p>No pieces advanced. Exhibition is empty.</p>';
  } else if (kept <= 100 && kept > 2) {
    html += `<p>${kept} pieces remaining — ready for tournament mode!</p>`;
  } else if (kept <= 2) {
    html += `<p>${kept} piece${kept === 1 ? '' : 's'} remaining — your final selection.</p>`;
  }

  html += '<div class="exhibit-round-done-actions">';
  if (kept > 2 && kept <= 100) {
    html += `<button class="btn btn-save" id="exhibit-tournament">⚔️ Tournament Mode</button>`;
  }
  if (kept > 0) {
    html += `<button class="btn ${kept > 100 ? 'btn-save' : 'btn-nav'}" id="exhibit-next-round">Start Round ${state.currentRound + 1}</button>`;
  }
  html += '<button class="btn btn-nav" id="exhibit-back-list">Back to Exhibitions</button>';
  html += '</div></div>';

  stage.innerHTML = html;

  if ($('#exhibit-tournament')) {
    $('#exhibit-tournament').addEventListener('click', () => enterTournamentMode(state));
  }
  if ($('#exhibit-next-round')) {
    $('#exhibit-next-round').addEventListener('click', () => startNextExhibitRound(state));
  }
  $('#exhibit-back-list').addEventListener('click', renderExhibitList);
}

// ── Exhibition: persistence ──────────────────────────────────────────────────

function persistExhibitState() {
  const stored = loadExhibitStorage();
  if (!stored.exhibitions) stored.exhibitions = {};
  stored.exhibitions[exhibitState.name] = exhibitState;
  stored.activeExhibition = exhibitState.name;
  saveExhibitStorage(stored);
}

function persistExhibitRoundProgress() {
  const stored = loadExhibitStorage();
  stored.roundProgress = {
    exhibitName: exhibitState.name,
    round: exhibitState.currentRound,
    bucketIndex,
    queueItemIds: bucketQueue.map(it => it['o:id']),
  };
  saveExhibitStorage(stored);
}

// ══════════════════════════════════════════════════════════════════════════════
// ── Tournament Mode — head-to-head bracket within an exhibition ──────────────
// ══════════════════════════════════════════════════════════════════════════════

async function enterTournamentMode(exhibitSt) {
  const roundData = exhibitSt.rounds.find(r => r.round === exhibitSt.currentRound);

  // Build pool: if current round is complete, use its survivor set;
  // otherwise use buildExhibitQueue (previous round's survivors / source filter)
  let pool;
  if (roundData && roundData.complete) {
    pool = allItems.filter(item =>
      (item['o:item_set'] || []).some(s => s['o:id'] === roundData.setId)
    );
  } else {
    pool = buildExhibitQueue(exhibitSt);
  }
  if (pool.length < 2) {
    showToast('Need at least 2 items for tournament', true);
    return;
  }

  const stage = $('#card-stage');
  stage.innerHTML = '<div class="exhibit-round-done"><h2>⚔️ Seeding Tournament...</h2><p>Analyzing visual similarity with CLIP...</p></div>';

  // Call tournament-seed endpoint
  try {
    const resp = await fetch('/admin/rapid-editor/tournament-seed', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_ids: pool.map(it => it['o:id']) }),
    });
    if (!resp.ok) throw new Error(`Seed failed: ${resp.status}`);
    const seed = await resp.json();

    tournamentMode = true;
    tournamentState = {
      exhibitName: exhibitSt.name,
      setId: roundData.setId,
      bracket: seed.matchups,     // [{a, b, similarity}, ...]
      byes: seed.byes || [],      // auto-advance
      currentMatch: 0,
      roundNum: 1,
      survivors: [...(seed.byes || [])],  // byes auto-advance
      eliminated: [],
      totalMatches: seed.matchups.length,
    };

    persistTournamentState();
    renderTournamentMatch();
  } catch (err) {
    showToast(`Tournament error: ${err.message}`, true);
  }
}

function renderTournamentMatch() {
  const ts = tournamentState;
  if (!ts) return;

  const stage = $('#card-stage');

  // Check if round is complete
  if (ts.currentMatch >= ts.bracket.length) {
    showTournamentRoundComplete();
    return;
  }

  const matchup = ts.bracket[ts.currentMatch];
  const itemA = allItems.find(it => it['o:id'] === matchup.a);
  const itemB = allItems.find(it => it['o:id'] === matchup.b);
  if (!itemA || !itemB) {
    // Skip missing items
    ts.currentMatch++;
    persistTournamentState();
    renderTournamentMatch();
    return;
  }

  const progress = `Match ${ts.currentMatch + 1} / ${ts.bracket.length}`;
  const roundLabel = `Tournament Round ${ts.roundNum}`;
  const simPct = Math.round(matchup.similarity * 100);

  const idA = itemA._identifier || `#${itemA['o:id']}`;
  const idB = itemB._identifier || `#${itemB['o:id']}`;
  const medA = extractValue(itemA, 'dcterms:medium') || '';
  const medB = extractValue(itemB, 'dcterms:medium') || '';
  const dateA = extractValue(itemA, 'dcterms:date') || '';
  const dateB = extractValue(itemB, 'dcterms:date') || '';

  stage.innerHTML = `
    <div class="tournament-arena">
      <div class="tournament-header">
        <div class="tournament-round-label">${roundLabel}</div>
        <div class="tournament-progress">${progress}</div>
        <div class="tournament-similarity">${simPct}% similar</div>
      </div>
      <div class="tournament-matchup">
        <div class="tournament-card" data-pick="a">
          <div class="card-img-wrap"><div class="card-img-loading">Loading…</div></div>
          <div class="tournament-card-meta">
            <div class="tournament-card-id">${idA}</div>
            <div class="tournament-card-detail">${[medA, dateA].filter(Boolean).join(', ')}</div>
          </div>
        </div>
        <div class="tournament-vs">VS</div>
        <div class="tournament-card" data-pick="b">
          <div class="card-img-wrap"><div class="card-img-loading">Loading…</div></div>
          <div class="tournament-card-meta">
            <div class="tournament-card-id">${idB}</div>
            <div class="tournament-card-detail">${[medB, dateB].filter(Boolean).join(', ')}</div>
          </div>
        </div>
      </div>
      <div class="tournament-actions">
        <button class="btn btn-nav" id="tournament-keep-both">Keep Both</button>
        <button class="btn btn-nav" id="tournament-cut-both">Cut Both</button>
      </div>
    </div>
  `;

  // Load images
  const cards = stage.querySelectorAll('.tournament-card');
  loadTournamentCardImage(cards[0], itemA);
  loadTournamentCardImage(cards[1], itemB);

  // Preload next matchup images
  preloadTournamentNext();

  // Click handlers: pick a winner
  cards[0].addEventListener('click', () => tournamentPick('a'));
  cards[1].addEventListener('click', () => tournamentPick('b'));

  // Keep Both / Cut Both
  $('#tournament-keep-both').addEventListener('click', () => tournamentPick('both'));
  $('#tournament-cut-both').addEventListener('click', () => tournamentPick('neither'));

  // Keyboard: 1/left = A, 2/right = B, 3 = both, 0 = neither
  document.addEventListener('keydown', tournamentKeyHandler);
}

function tournamentKeyHandler(e) {
  if (!tournamentMode || !tournamentState) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return; // don't swallow browser shortcuts
  if (e.key === '1' || e.key === 'ArrowLeft') { e.preventDefault(); tournamentPick('a'); }
  else if (e.key === '2' || e.key === 'ArrowRight') { e.preventDefault(); tournamentPick('b'); }
  else if (e.key === '3') { e.preventDefault(); tournamentPick('both'); }
  else if (e.key === '0' || e.key === 'Backspace') { e.preventDefault(); tournamentPick('neither'); }
}

function tournamentPick(choice) {
  const ts = tournamentState;
  if (!ts || ts.currentMatch >= ts.bracket.length) return;

  document.removeEventListener('keydown', tournamentKeyHandler);

  const matchup = ts.bracket[ts.currentMatch];
  const stage = $('#card-stage');

  // Animate the chosen card(s)
  const cards = stage.querySelectorAll('.tournament-card');
  if (choice === 'a') {
    cards[1].classList.add('tournament-eliminated');
    cards[0].classList.add('tournament-winner');
    ts.survivors.push(matchup.a);
    ts.eliminated.push(matchup.b);
  } else if (choice === 'b') {
    cards[0].classList.add('tournament-eliminated');
    cards[1].classList.add('tournament-winner');
    ts.survivors.push(matchup.b);
    ts.eliminated.push(matchup.a);
  } else if (choice === 'both') {
    cards[0].classList.add('tournament-winner');
    cards[1].classList.add('tournament-winner');
    ts.survivors.push(matchup.a, matchup.b);
  } else {
    cards[0].classList.add('tournament-eliminated');
    cards[1].classList.add('tournament-eliminated');
    ts.eliminated.push(matchup.a, matchup.b);
  }

  ts.currentMatch++;
  persistTournamentState();

  // Brief pause for animation, then next match
  setTimeout(() => renderTournamentMatch(), 350);
}

async function loadTournamentCardImage(cardEl, item) {
  const url = await getCardImageUrl(item);
  if (url) {
    const imgWrap = cardEl.querySelector('.card-img-wrap');
    const img = document.createElement('img');
    img.src = url;
    img.alt = item._identifier || '';
    img.onload = () => {
      const loading = imgWrap.querySelector('.card-img-loading');
      if (loading) loading.remove();
    };
    imgWrap.appendChild(img);
  }
}

function preloadTournamentNext() {
  const ts = tournamentState;
  if (!ts) return;
  const next = ts.currentMatch + 1;
  if (next < ts.bracket.length) {
    const m = ts.bracket[next];
    const itemA = allItems.find(it => it['o:id'] === m.a);
    const itemB = allItems.find(it => it['o:id'] === m.b);
    if (itemA) getCardImageUrl(itemA).then(u => { if (u) { new Image().src = u; } });
    if (itemB) getCardImageUrl(itemB).then(u => { if (u) { new Image().src = u; } });
  }
}

async function showTournamentRoundComplete() {
  document.removeEventListener('keydown', tournamentKeyHandler);
  const ts = tournamentState;
  const stage = $('#card-stage');
  const survivors = ts.survivors.length;
  const eliminated = ts.eliminated.length;

  let html = `
    <div class="exhibit-round-done">
      <h2>Tournament Round ${ts.roundNum} Complete</h2>
      <div class="exhibit-round-stats-grid">
        <div class="exhibit-stat">
          <div class="exhibit-stat-num">${survivors}</div>
          <div class="exhibit-stat-label">advancing</div>
        </div>
        <div class="exhibit-stat">
          <div class="exhibit-stat-num">${eliminated}</div>
          <div class="exhibit-stat-label">eliminated</div>
        </div>
      </div>`;

  if (survivors <= 1) {
    html += '<p>Tournament complete!</p>';
  } else if (survivors <= 30) {
    html += `<p>🏆 ${survivors} finalists — could be your exhibition set!</p>`;
  } else {
    html += `<p>Still ${survivors} pieces — another round will narrow it down.</p>`;
  }

  html += '<div class="exhibit-round-done-actions">';
  if (survivors > 1) {
    html += '<button class="btn btn-save" id="tournament-next-round">Next Tournament Round</button>';
  }
  html += '<button class="btn btn-nav" id="tournament-finish">Finish & Save Survivors</button>';
  html += '<button class="btn btn-nav" id="tournament-back">Back to Exhibitions</button>';
  html += '</div></div>';

  stage.innerHTML = html;

  if ($('#tournament-next-round')) {
    $('#tournament-next-round').addEventListener('click', startNextTournamentRound);
  }
  $('#tournament-finish').addEventListener('click', finishTournament);
  $('#tournament-back').addEventListener('click', () => {
    exitTournamentMode();
    renderExhibitList();
  });
}

async function startNextTournamentRound() {
  const ts = tournamentState;
  if (ts.survivors.length < 2) return;

  const stage = $('#card-stage');
  stage.innerHTML = '<div class="exhibit-round-done"><h2>⚔️ Re-seeding...</h2><p>Analyzing similarity for next round...</p></div>';

  try {
    const resp = await fetch('/admin/rapid-editor/tournament-seed', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_ids: ts.survivors }),
    });
    if (!resp.ok) throw new Error(`Seed failed: ${resp.status}`);
    const seed = await resp.json();

    ts.bracket = seed.matchups;
    ts.byes = seed.byes || [];
    ts.currentMatch = 0;
    ts.roundNum++;
    ts.survivors = [...(seed.byes || [])];
    ts.eliminated = [];
    ts.totalMatches = seed.matchups.length;

    persistTournamentState();
    renderTournamentMatch();
  } catch (err) {
    showToast(`Tournament error: ${err.message}`, true);
  }
}

async function finishTournament() {
  const ts = tournamentState;
  if (!ts) return;

  // Create a new item set for tournament survivors
  const setTitle = `${CURATE_PREFIX}${ts.exhibitName} Tournament`;
  let setId;

  const existing = availableItemSets.find(s => s.label === setTitle);
  if (existing) {
    setId = existing.id;
  } else {
    try {
      const resp = await fetch('/admin/rapid-editor/create-set', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: setTitle }),
      });
      if (!resp.ok) throw new Error('Failed to create set');
      const result = await resp.json();
      setId = result['o:id'];
      availableItemSets.push({ id: setId, label: setTitle });
    } catch (err) {
      showToast(`Error: ${err.message}`, true);
      return;
    }
  }

  // Bulk-add survivors to the set via direct SQL (avoids Omeka's full-update stripping)
  let added = 0;
  try {
    const resp = await fetch('/admin/rapid-editor/add-to-set', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_ids: ts.survivors, set_id: setId }),
    });
    if (!resp.ok) throw new Error('Failed to add items to set');
    const result = await resp.json();
    added = result.added || 0;
    // Update local item data
    for (const itemId of ts.survivors) {
      const item = allItems.find(it => it['o:id'] === itemId);
      if (item) {
        const sets = (item['o:item_set'] || []).map(s => ({ 'o:id': s['o:id'] }));
        if (!sets.some(s => s['o:id'] === setId)) sets.push({ 'o:id': setId });
        item['o:item_set'] = sets;
      }
    }
  } catch (err) {
    showToast(`Error saving: ${err.message}`, true);
    return;
  }

  showToast(`${added} pieces saved to "${setTitle}"`);
  exitTournamentMode();
  renderExhibitList();
}

function exitTournamentMode() {
  document.removeEventListener('keydown', tournamentKeyHandler);
  tournamentMode = false;
  tournamentState = null;
  // Clear persisted tournament
  const stored = loadExhibitStorage();
  delete stored.tournament;
  saveExhibitStorage(stored);
}

function persistTournamentState() {
  const stored = loadExhibitStorage();
  stored.tournament = tournamentState;
  saveExhibitStorage(stored);
}

function resumeTournament() {
  const stored = loadExhibitStorage();
  if (!stored.tournament) return false;
  tournamentMode = true;
  tournamentState = stored.tournament;
  return true;
}

// ══════════════════════════════════════════════════════════════════════════════
// ── Sprint Mode — field-specific card editing ────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════════

// ── Sprint: field config ──────────────────────────────────────────────────────

function initFieldSprints() {
  const dateOptions = [
    { value: 'c. 1989–2024', label: 'c. 1989–2024', extraClass: 'date-unknown' },
    { value: 'c. 2000s', label: 'c. 2000s', extraClass: 'date-unknown' },
    ...DATE_YEARS.map(y => ({
      value: y,
      label: '\u2019' + y.slice(2),
      extraClass: y.endsWith('0') ? 'decade-start' : '',
    })),
  ];

  FIELD_SPRINTS = {
    identifier: {
      label: 'Catalog #',
      term: 'dcterms:identifier',
      filterFn: item => !extractValue(item, 'dcterms:identifier'),
      inputType: 'text',
      placeholder: 'e.g. JS-2020-T1234',
      suggestValue: item => suggestCatalogId(item),
    },
    date: {
      label: 'Date',
      term: 'dcterms:date',
      filterFn: hasBadDate,
      inputType: 'pills',
      options: dateOptions,
      autoAdvance: true,
      allowCustom: true,
      customPlaceholder: 'Custom date (e.g. c. 2005)',
    },
    type: {
      label: 'Type',
      term: 'dcterms:type',
      filterFn: item => !extractValue(item, 'dcterms:type'),
      inputType: 'pills',
      options: WORK_TYPES.map(t => ({ value: t, label: t })),
      autoAdvance: true,
    },
    support: {
      label: 'Support',
      term: 'schema:artworkSurface',
      filterFn: item => !extractValue(item, 'schema:artworkSurface'),
      inputType: 'pills',
      options: SUPPORTS.map(s => ({ value: s, label: s })),
      autoAdvance: true,
    },
    condition: {
      label: 'Condition',
      term: 'schema:itemCondition',
      filterFn: item => !extractValue(item, 'schema:itemCondition'),
      inputType: 'pills',
      options: CONDITIONS.map(c => ({ value: c, label: c })),
      autoAdvance: true,
    },
    signature: {
      label: 'Signature',
      term: 'schema:distinguishingSign',
      filterFn: item => {
        const v = extractValue(item, 'schema:distinguishingSign');
        return !v || !SIGNATURE_ARROWS.includes(v);
      },
      inputType: 'grid',
      gridCols: 3,
      options: SIGNATURE_ARROWS.map(a => ({ value: a, label: a })),
      autoAdvance: true,
    },
    motifs: {
      label: 'Motifs',
      term: 'dcterms:subject',
      filterFn: item => extractAllValues(item, 'dcterms:subject').length < 2,
      inputType: 'tagger',
      autoAdvance: false,
      multiSelect: true,
    },
    transcription: {
      label: 'Transcription',
      term: 'bibo:content',
      filterFn: item => !extractValue(item, 'bibo:content'),
      inputType: 'pills',
      options: [
        { value: '∅', label: 'No text' },
        { value: '[Needs enrichment]', label: 'Needs enrichment' },
      ],
      autoAdvance: true,
      allowCustom: true,
      customPlaceholder: 'Type transcription…',
      customInputType: 'textarea',
    },
    medium: {
      label: 'Medium',
      term: 'dcterms:medium',
      filterFn: item => !extractValue(item, 'dcterms:medium'),
      inputType: 'text',
      placeholder: 'e.g. Marker on paper',
    },
    dimensions: {
      label: 'Height/Width',
      terms: ['schema:height', 'schema:width'],
      filterFn: item => {
        const h = extractValue(item, 'schema:height');
        const w = extractValue(item, 'schema:width');
        return !h || !w || isNaN(parseFloat(h)) || isNaN(parseFloat(w));
      },
      inputType: 'dimensions',
    },
    framing: {
      label: 'Framing',
      term: 'dcterms:format',
      filterFn: item => !extractValue(item, 'dcterms:format'),
      inputType: 'text',
      placeholder: 'e.g. Unframed',
    },
    owner: {
      label: 'Owner',
      term: 'bibo:owner',
      filterFn: item => !extractValue(item, 'bibo:owner'),
      inputType: 'text',
      placeholder: 'e.g. Estate of Jon Sarkin',
      sticky: true,
    },
    location: {
      label: 'Location',
      term: 'dcterms:spatial',
      filterFn: item => !extractValue(item, 'dcterms:spatial'),
      inputType: 'text',
      placeholder: 'e.g. Studio, Gloucester MA',
      sticky: true,
    },
    box: {
      label: 'Box',
      term: 'schema:box',
      filterFn: item => !extractValue(item, 'schema:box'),
      inputType: 'text',
      placeholder: 'e.g. BOX-A1',
    },
    category: {
      label: 'Category',
      term: 'curation:category',
      filterFn: item => {
        const v = extractValue(item, 'curation:category');
        return !v || !['A', 'B', 'C', 'D'].includes(v);
      },
      inputType: 'pills',
      options: ['A', 'B', 'C', 'D'].map(c => ({ value: c, label: c })),
      autoAdvance: true,
    },
  };
}

// ── Sprint: menu ──────────────────────────────────────────────────────────────

function populateSprintMenu() {
  const menu = $('#sprint-menu');
  menu.innerHTML = '';
  for (const [key, config] of Object.entries(FIELD_SPRINTS)) {
    const count = allItems.filter(config.filterFn).length;
    if (count === 0) continue;
    const btn = document.createElement('button');
    btn.className = 'sprint-menu-item';
    if (sprintMode && sprintField === key) btn.classList.add('active');
    btn.innerHTML = `${config.label} <span class="sprint-menu-count">${count}</span>`;
    btn.addEventListener('click', () => {
      closeSprintMenu();
      enterSprintMode(key);
    });
    menu.appendChild(btn);
  }
  if (!menu.children.length) {
    menu.innerHTML = '<div class="sprint-menu-item" style="color:#666;cursor:default">All fields complete!</div>';
  }
}

function toggleSprintMenu() {
  const menu = $('#sprint-menu');
  const caret = $('#sprint-caret');
  if (!menu || !caret) return;
  const isOpen = !menu.classList.contains('hidden');
  if (isOpen) {
    closeSprintMenu();
  } else {
    populateSprintMenu();
    menu.classList.remove('hidden');
    caret.classList.add('open');
    // Close on outside click
    setTimeout(() => {
      document.addEventListener('click', closeSprintMenuOutside, { once: true });
    }, 0);
  }
}

function closeSprintMenu() {
  $('#sprint-menu')?.classList.add('hidden');
  $('#sprint-caret')?.classList.remove('open');
}

function closeSprintMenuOutside(e) {
  const menu = $('#sprint-menu');
  const caret = $('#sprint-caret');
  if (!menu || !caret) return;
  if (!menu.contains(e.target) && e.target !== caret) {
    closeSprintMenu();
  }
}

function setupSprintMenu() {
  $('#sprint-caret')?.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleSprintMenu();
  });
}

// ── Sprint: mode switching ────────────────────────────────────────────────────

function enterSprintMode(fieldKey) {
  // Exit other modes first
  if (bucketMode) exitBucketMode();
  sprintMode = true;
  sprintField = fieldKey;
  filterMode = 'sprint';

  // Show main layout (image + sprint view)
  dom.main.classList.remove('hidden');
  $('#curate-panel').classList.add('hidden');
  dom.sprintView.classList.remove('hidden');
  dom.sprintViewDone.classList.add('hidden');

  // Update nav buttons
  for (const b of $$('.filter-btn')) b.classList.remove('active');

  const config = FIELD_SPRINTS[fieldKey];
  dom.sprintViewFieldLabel.textContent = config.label;

  buildSprintQueue(fieldKey);
  updateSprintProgress();

  if (sprintQueue.length) {
    renderSprintItem();
  } else {
    showSprintComplete();
  }

  savePosition();
}

function exitSprintMode() {
  sprintMode = false;
  sprintField = null;
  dom.sprintView.classList.add('hidden');
}

// ── Sprint: queue ─────────────────────────────────────────────────────────────

function buildSprintQueue(fieldKey) {
  const config = FIELD_SPRINTS[fieldKey];
  sprintQueue = allItems.filter(config.filterFn);
  // Sort by identifier for predictable order
  sprintQueue.sort((a, b) => (a._identifier || '').localeCompare(b._identifier || ''));
  sprintIndex = 0;
}

// ── Sprint: progress ──────────────────────────────────────────────────────────

function updateSprintProgress() {
  const remaining = sprintQueue.length - sprintIndex;
  dom.sprintViewCount.textContent = `${sprintIndex} done · ${remaining} remaining`;
  dom.sprintViewUndo.disabled = !sprintLastAction;

  const pct = sprintQueue.length > 0 ? (sprintIndex / sprintQueue.length) * 100 : 0;
  dom.progressFill.style.width = `${pct}%`;

  // Update nav status
  dom.queueStatus.textContent = `${sprintIndex + 1} / ${sprintQueue.length}`;
}

// ── Sprint: render current item into side-by-side layout ─────────────────────

async function renderSprintItem() {
  if (sprintIndex >= sprintQueue.length) {
    showSprintComplete();
    return;
  }

  const item = sprintQueue[sprintIndex];
  const config = FIELD_SPRINTS[sprintField];
  const identifier = item._identifier || `item-${item['o:id']}`;

  // Update nav link
  dom.itemLink.textContent = identifier;
  dom.itemLink.href = `/admin/item/${item['o:id']}/edit`;

  // Load image into existing image panel
  dom.imageLoading.classList.remove('hidden');
  dom.image.style.opacity = '0.3';
  const url = await getImageUrl(item);
  if (url) {
    dom.image.src = url;
    dom.image.onload = () => {
      dom.image.style.opacity = '1';
      dom.imageLoading.classList.add('hidden');
    };
  } else {
    dom.image.src = '';
    dom.image.style.opacity = '1';
    dom.imageLoading.classList.add('hidden');
  }

  // Item metadata
  const medium = extractValue(item, 'dcterms:medium');
  const date = extractValue(item, 'dcterms:date');
  const h = extractValue(item, 'schema:height');
  const w = extractValue(item, 'schema:width');
  const dims = (h && w) ? `${h}″ × ${w}″` : '';
  const detail = [medium, dims].filter(Boolean).join(', ');
  dom.sprintViewMeta.innerHTML = `
    <div class="sv-meta-id">${identifier}</div>
    ${detail || date ? `<div class="sv-meta-detail">${[detail, date].filter(Boolean).join(' · ')}</div>` : ''}
  `;

  // Current value for this field
  let currentValue = '';
  if (config.terms) {
    const vals = config.terms.map(t => extractValue(item, t)).filter(Boolean);
    currentValue = vals.join(' × ');
  } else if (config.multiSelect) {
    currentValue = extractAllValues(item, config.term).join(', ');
  } else {
    currentValue = extractValue(item, config.term);
  }
  dom.sprintViewCurrent.textContent = currentValue ? `Current: ${currentValue}` : '';

  // Render input controls
  dom.sprintViewZone.innerHTML = '';
  dom.sprintViewDone.classList.add('hidden');
  dom.sprintViewActions.classList.remove('hidden');
  renderSprintInput(dom.sprintViewZone, item, config);

  // Scroll to top
  dom.formPanel.scrollTop = 0;

  preloadSprintNext();
}

function renderSprintInput(zone, item, config) {
  const itemId = item['o:id'];

  switch (config.inputType) {
    case 'pills': {
      const wrap = document.createElement('div');
      wrap.className = 'sprint-pills';
      for (const opt of config.options) {
        const pill = document.createElement('button');
        pill.className = 'sprint-pill' + (opt.extraClass ? ' ' + opt.extraClass : '');
        pill.textContent = opt.label;
        pill.addEventListener('click', () => {
          if (sprintActing) return;
          sprintSaveAndAdvance(itemId, config, opt.value);
        });
        wrap.appendChild(pill);
      }
      zone.appendChild(wrap);
      // Custom text input for free-form entry
      if (config.allowCustom) {
        const inputType = config.customInputType === 'textarea' ? 'textarea' : 'input';
        const input = document.createElement(inputType);
        input.className = 'sprint-text';
        input.placeholder = config.customPlaceholder || 'Custom value…';
        input.style.marginTop = '6px';
        if (inputType === 'textarea') input.rows = 2;
        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            const val = input.value.trim();
            if (val && !sprintActing) sprintSaveAndAdvance(itemId, config, val);
          }
        });
        zone.appendChild(input);
      }
      break;
    }

    case 'grid': {
      const grid = document.createElement('div');
      grid.className = 'sprint-grid';
      grid.style.gridTemplateColumns = `repeat(${config.gridCols || 3}, auto)`;
      for (const opt of config.options) {
        const btn = document.createElement('button');
        btn.textContent = opt.label;
        btn.addEventListener('click', () => {
          if (sprintActing) return;
          sprintSaveAndAdvance(itemId, config, opt.value);
        });
        grid.appendChild(btn);
      }
      zone.appendChild(grid);
      break;
    }

    case 'chips': {
      const chipWrap = document.createElement('div');
      chipWrap.className = 'sprint-chips';
      const selected = new Set(extractAllValues(item, config.term));
      for (const opt of config.options) {
        const chip = document.createElement('button');
        chip.className = 'sprint-chip' + (selected.has(opt.value) ? ' active' : '');
        chip.textContent = opt.label;
        chip.dataset.val = opt.value;
        chip.addEventListener('click', () => chip.classList.toggle('active'));
        chipWrap.appendChild(chip);
      }
      zone.appendChild(chipWrap);
      // Done button
      const saveBtn = document.createElement('button');
      saveBtn.className = 'sprint-save';
      saveBtn.textContent = 'Save + Next →';
      saveBtn.addEventListener('click', () => {
        if (sprintActing) return;
        const vals = [...chipWrap.querySelectorAll('.sprint-chip.active')].map(c => c.dataset.val);
        sprintSaveAndAdvance(itemId, config, vals);
      });
      zone.appendChild(saveBtn);
      break;
    }

    case 'tagger': {
      const tagger = document.createElement('div');
      tagger.className = 'sprint-tagger';
      const selectedTags = new Set(extractAllValues(item, config.term));

      // Tag pills container
      const tagsWrap = document.createElement('div');
      tagsWrap.className = 'sprint-tagger-tags';
      tagger.appendChild(tagsWrap);

      // Input row: input + info button
      const inputRow = document.createElement('div');
      inputRow.style.cssText = 'display:flex;gap:6px;align-items:center;';
      const inputWrap = document.createElement('div');
      inputWrap.className = 'sprint-tagger-input-wrap';
      inputWrap.style.flex = '1';
      const tagInput = document.createElement('input');
      tagInput.type = 'text';
      tagInput.className = 'sprint-tagger-input';
      tagInput.placeholder = 'Type to add motif…';
      tagInput.autocomplete = 'off';
      const dropdown = document.createElement('div');
      dropdown.className = 'sprint-tagger-dropdown';
      dropdown.style.display = 'none';
      inputWrap.appendChild(tagInput);
      inputWrap.appendChild(dropdown);
      inputRow.appendChild(inputWrap);

      // Info button + popup
      const infoBtn = document.createElement('button');
      infoBtn.className = 'sprint-tagger-info-btn';
      infoBtn.textContent = '?';
      infoBtn.title = 'Tagging principles';
      const infoPopup = document.createElement('div');
      infoPopup.className = 'sprint-tagger-info';
      infoPopup.style.display = 'none';
      infoPopup.innerHTML = `<h4>Tagging Principles</h4><ul>
<li><strong>Be specific but reusable.</strong> "Cactus" not "plant" (too vague) or "barrel cactus in terracotta pot" (too specific). A tag should fit 3\u20135+ works.</li>
<li><strong>Consistent granularity.</strong> Keep subjects and techniques at the same level of specificity. "Crosshatching" and "fish" are both leaf-level \u2014 good. Avoid mixing "drawing techniques" (parent) with "crosshatching" (child).</li>
<li><strong>Singular nouns.</strong> "Fish" not "fishes", "face" not "faces". Pick one convention and stick to it.</li>
<li><strong>Tag what you see.</strong> "Spiral" is observable; "anxiety" is interpretation. Keep it concrete.</li>
<li><strong>Plan for splitting.</strong> Subjects and techniques may separate later. Each tag should clearly be one or the other.</li>
<li><strong>Don\u2019t over-tag.</strong> 3\u20138 motifs per work is the sweet spot. If everything gets tagged "lines", the tag carries no information.</li>
<li><strong>Prefer established terms.</strong> Align with AAT conventions when possible ("crosshatching" not "cross-hatch").</li>
</ul>`;
      infoBtn.addEventListener('click', (e) => {
        e.preventDefault();
        infoPopup.style.display = infoPopup.style.display === 'none' ? 'block' : 'none';
      });
      inputWrap.appendChild(infoPopup);
      inputRow.appendChild(infoBtn);

      // Suggest button (Claude-powered)
      const suggestBtn = document.createElement('button');
      suggestBtn.className = 'sprint-tagger-suggest-btn';
      suggestBtn.textContent = '✨ Suggest';
      suggestBtn.title = 'Ask Claude to suggest motifs';
      suggestBtn.addEventListener('click', (e) => {
        e.preventDefault();
        if (suggestBtn.disabled) return;
        suggestBtn.disabled = true;
        suggestBtn.textContent = 'Suggesting…';
        fetchMotifSuggestions(itemId, selectedTags, tagger, renderPills, suggestBtn);
      });
      inputRow.appendChild(suggestBtn);

      tagger.appendChild(inputRow);

      let hlIndex = -1;

      function renderPills() {
        tagsWrap.innerHTML = '';
        for (const tag of selectedTags) {
          const pill = document.createElement('span');
          pill.className = 'sprint-tagger-pill';
          pill.textContent = tag;
          const rm = document.createElement('button');
          rm.className = 'sprint-tagger-remove';
          rm.textContent = '×';
          rm.addEventListener('mousedown', (e) => {
            e.preventDefault();
            selectedTags.delete(tag);
            renderPills();
          });
          pill.appendChild(rm);
          tagsWrap.appendChild(pill);
        }
      }

      function filterDropdown(query) {
        const q = query.toLowerCase().trim();
        dropdown.innerHTML = '';
        hlIndex = -1;
        if (!q) { dropdown.style.display = 'none'; return; }
        const matches = ALL_MOTIF_TAGS.filter(t =>
          t.toLowerCase().includes(q) && !selectedTags.has(t)
        ).slice(0, 12);
        if (!matches.length) { dropdown.style.display = 'none'; return; }
        for (const m of matches) {
          const opt = document.createElement('div');
          opt.className = 'sprint-tagger-option';
          opt.textContent = m;
          opt.addEventListener('mousedown', (e) => {
            e.preventDefault();
            addTag(m);
          });
          dropdown.appendChild(opt);
        }
        dropdown.style.display = 'block';
      }

      function highlightOption(idx) {
        const opts = dropdown.querySelectorAll('.sprint-tagger-option');
        for (const o of opts) o.classList.remove('hl');
        if (idx >= 0 && idx < opts.length) {
          opts[idx].classList.add('hl');
          opts[idx].scrollIntoView({ block: 'nearest' });
        }
      }

      function addTag(tag) {
        const trimmed = tag.trim();
        if (!trimmed || selectedTags.has(trimmed)) return;
        selectedTags.add(trimmed);
        // Push new tag to corpus if novel
        if (!ALL_MOTIF_TAGS.includes(trimmed)) {
          ALL_MOTIF_TAGS.push(trimmed);
          ALL_MOTIF_TAGS.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
        }
        tagInput.value = '';
        dropdown.style.display = 'none';
        hlIndex = -1;
        renderPills();
        tagInput.focus();
      }

      tagInput.addEventListener('input', () => filterDropdown(tagInput.value));

      // Save + Next button (declared early so keydown handler can reference it)
      const saveBtn = document.createElement('button');
      saveBtn.className = 'sprint-save';
      saveBtn.textContent = 'Save + Next →';
      saveBtn.addEventListener('click', () => {
        if (sprintActing) return;
        sprintSaveAndAdvance(itemId, config, [...selectedTags]);
      });

      tagInput.addEventListener('keydown', (e) => {
        const opts = dropdown.querySelectorAll('.sprint-tagger-option');
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          if (dropdown.style.display !== 'none' && opts.length) {
            hlIndex = Math.min(hlIndex + 1, opts.length - 1);
            highlightOption(hlIndex);
          }
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          if (dropdown.style.display !== 'none' && opts.length) {
            hlIndex = Math.max(hlIndex - 1, 0);
            highlightOption(hlIndex);
          }
        } else if (e.key === 'Enter') {
          e.preventDefault();
          if (hlIndex >= 0 && hlIndex < opts.length) {
            addTag(opts[hlIndex].textContent);
          } else if (tagInput.value.trim()) {
            addTag(tagInput.value);
          } else {
            saveBtn.click();
          }
        } else if (e.key === 'Tab' && dropdown.style.display !== 'none' && hlIndex >= 0 && hlIndex < opts.length) {
          e.preventDefault();
          addTag(opts[hlIndex].textContent);
        } else if (e.key === 'Backspace' && !tagInput.value) {
          // Remove last tag
          const arr = [...selectedTags];
          if (arr.length) {
            selectedTags.delete(arr[arr.length - 1]);
            renderPills();
          }
        }
      });

      tagInput.addEventListener('blur', () => {
        setTimeout(() => { dropdown.style.display = 'none'; }, 150);
      });
      tagInput.addEventListener('focus', () => {
        if (tagInput.value.trim()) filterDropdown(tagInput.value);
      });

      renderPills();
      zone.appendChild(tagger);
      zone.appendChild(saveBtn);
      setTimeout(() => tagInput.focus(), 100);
      break;
    }

    case 'text': {
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'sprint-text';
      input.placeholder = config.placeholder || '';
      // Pre-fill with existing value, sticky value, or suggestion
      const existing = extractValue(item, config.term);
      if (existing) input.value = existing;
      else if (config.sticky && stickyText[config.term]) input.value = stickyText[config.term];
      else if (config.suggestValue) input.value = config.suggestValue(item);
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          const val = input.value.trim();
          if (val && !sprintActing) sprintSaveAndAdvance(itemId, config, val);
        }
      });
      zone.appendChild(input);
      const saveBtn = document.createElement('button');
      saveBtn.className = 'sprint-save';
      saveBtn.textContent = 'Save + Next →';
      saveBtn.addEventListener('click', () => {
        const val = input.value.trim();
        if (val && !sprintActing) sprintSaveAndAdvance(itemId, config, val);
      });
      zone.appendChild(saveBtn);
      // Auto-focus
      setTimeout(() => input.focus(), 100);
      break;
    }

    case 'dimensions': {
      const wrap = document.createElement('div');
      wrap.className = 'sprint-dims';
      const hInput = document.createElement('input');
      hInput.type = 'text';
      hInput.placeholder = 'Height';
      hInput.value = extractValue(item, 'schema:height') || stickyDims.height;
      const xLabel = document.createElement('span');
      xLabel.className = 'dims-x';
      xLabel.textContent = '×';
      const wInput = document.createElement('input');
      wInput.type = 'text';
      wInput.placeholder = 'Width';
      wInput.value = extractValue(item, 'schema:width') || stickyDims.width;
      wrap.appendChild(hInput);
      wrap.appendChild(xLabel);
      wrap.appendChild(wInput);
      zone.appendChild(wrap);

      const doSave = () => {
        const h = hInput.value.trim();
        const w = wInput.value.trim();
        if (h && w && !sprintActing) {
          sprintSaveAndAdvance(itemId, config, { 'schema:height': h, 'schema:width': w });
        }
      };
      wInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); doSave(); } });
      hInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); wInput.focus(); } });

      const saveBtn = document.createElement('button');
      saveBtn.className = 'sprint-save';
      saveBtn.textContent = 'Save + Next →';
      saveBtn.addEventListener('click', doSave);
      zone.appendChild(saveBtn);
      setTimeout(() => hInput.focus(), 100);
      break;
    }
  }
}

// ── Sprint: motif suggestions (Claude-powered) ──────────────────────────────

async function fetchMotifSuggestions(itemId, selectedTags, tagger, renderPillsFn, suggestBtn) {
  try {
    const resp = await fetch(`/admin/rapid-editor/suggest-motifs/${itemId}`, {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    suggestBtn.textContent = '✨ Suggest';
    suggestBtn.disabled = false;

    if (data.error) {
      showToast(`Suggestion error: ${data.error}`, true);
      return;
    }

    if (data.suggestions && data.suggestions.length) {
      // Filter out tags already selected
      const novel = data.suggestions.filter(t => !selectedTags.has(t));
      if (novel.length) {
        renderSuggestionPills(novel, selectedTags, tagger, renderPillsFn);
      } else {
        showToast('All suggestions already tagged');
      }
      if (data.usage) {
        console.log(`Suggest cost: $${data.usage.cost_usd?.toFixed(4)} (${data.from_cache ? 'cached' : data.usage.model})`);
      }
    } else {
      showToast('No suggestions returned');
    }
  } catch (err) {
    suggestBtn.textContent = '✨ Suggest';
    suggestBtn.disabled = false;
    showToast('Suggestion failed: ' + err.message, true);
    console.warn('Motif suggestion failed:', err);
  }
}

function renderSuggestionPills(suggestions, selectedTags, tagger, renderPillsFn) {
  // Remove any existing suggestion container
  tagger.querySelector('.sprint-tagger-suggestions')?.remove();

  const wrap = document.createElement('div');
  wrap.className = 'sprint-tagger-suggestions';
  const label = document.createElement('span');
  label.className = 'sprint-tagger-suggestions-label';
  label.textContent = 'Suggestions';
  wrap.appendChild(label);

  for (const tag of suggestions) {
    const pill = document.createElement('span');
    pill.className = 'sprint-tagger-suggestion-pill';
    pill.textContent = tag;

    // Click pill text to accept
    pill.addEventListener('mousedown', (e) => {
      e.preventDefault();
      selectedTags.add(tag);
      // Push to autocomplete corpus if novel
      if (!ALL_MOTIF_TAGS.includes(tag)) {
        ALL_MOTIF_TAGS.push(tag);
        ALL_MOTIF_TAGS.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
      }
      pill.remove();
      renderPillsFn();
      // Remove container if empty
      if (!wrap.querySelector('.sprint-tagger-suggestion-pill')) wrap.remove();
    });

    // Dismiss button
    const dismiss = document.createElement('button');
    dismiss.className = 'sprint-tagger-suggestion-dismiss';
    dismiss.textContent = '×';
    dismiss.addEventListener('mousedown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      pill.remove();
      if (!wrap.querySelector('.sprint-tagger-suggestion-pill')) wrap.remove();
    });
    pill.appendChild(dismiss);
    wrap.appendChild(pill);
  }

  // Insert after the tags container
  const tagsWrap = tagger.querySelector('.sprint-tagger-tags');
  if (tagsWrap) {
    tagsWrap.after(wrap);
  } else {
    tagger.prepend(wrap);
  }
}

function preloadSprintNext() {
  const next = sprintIndex + 1;
  if (next < sprintQueue.length) {
    getImageUrl(sprintQueue[next]).then(url => {
      if (url) { const img = new Image(); img.src = url; }
    });
  }
}

// ── Sprint: save + advance ────────────────────────────────────────────────────

async function sprintSaveAndAdvance(itemId, config, value) {
  sprintActing = true;

  // Capture old values for undo
  const item = sprintQueue[sprintIndex];
  let oldValues;
  if (config.terms) {
    oldValues = {};
    for (const t of config.terms) oldValues[t] = extractValue(item, t);
  } else if (config.multiSelect) {
    oldValues = extractAllValues(item, config.term);
  } else {
    oldValues = extractValue(item, config.term);
  }

  // Advance index immediately (before async)
  sprintIndex++;
  sprintLastAction = { itemId, field: sprintField, oldValues };
  updateSprintProgress();

  // Render next item immediately
  sprintActing = false;
  renderSprintItem();

  // API save in background
  try {
    const { json: freshItem } = await apiGet(`items/${itemId}`);
    const payload = buildBasePayload(freshItem);

    if (config.terms) {
      // Multi-term (dimensions)
      for (const [term, val] of Object.entries(value)) {
        payload[term] = val ? [literalValue(term, val)] : [];
      }
      // Persist dimensions for next card
      if (value['schema:height']) stickyDims.height = value['schema:height'];
      if (value['schema:width']) stickyDims.width = value['schema:width'];
    } else if (config.multiSelect) {
      // Multi-value (motifs)
      payload[config.term] = value.map(v => literalValue(config.term, v));
    } else {
      // Single value
      payload[config.term] = value ? [literalValue(config.term, value)] : [];
      // Persist sticky text fields for next card
      if (config.sticky && value) stickyText[config.term] = value;
    }

    const updated = await apiPatch(`items/${itemId}`, payload);

    // Update local state
    const idx = allItems.findIndex(it => it['o:id'] === itemId);
    if (idx >= 0) {
      allItems[idx] = updated;
      allItems[idx]._issues = validateItem(updated);
      allItems[idx]._identifier = extractValue(updated, 'dcterms:identifier') || `item-${updated['o:id']}`;
    }

    showToast(`✓ ${config.label} saved`);
  } catch (err) {
    showToast(`Error: ${err.message}`, true);
    console.error('Sprint save failed:', err);
    sprintLastAction = null;
  }
}

// ── Sprint: skip ──────────────────────────────────────────────────────────────

function sprintSkip() {
  if (sprintActing || sprintIndex >= sprintQueue.length) return;

  sprintIndex++;
  sprintLastAction = null; // can't undo a skip
  updateSprintProgress();
  renderSprintItem();
}

// ── Sprint: undo ──────────────────────────────────────────────────────────────

async function sprintUndo() {
  if (!sprintLastAction || sprintActing) return;
  const { itemId, field, oldValues } = sprintLastAction;
  const config = FIELD_SPRINTS[field];

  try {
    const { json: freshItem } = await apiGet(`items/${itemId}`);
    const payload = buildBasePayload(freshItem);

    if (config.terms) {
      for (const [term, val] of Object.entries(oldValues)) {
        payload[term] = val ? [literalValue(term, val)] : [];
      }
    } else if (config.multiSelect) {
      payload[config.term] = oldValues.map(v => literalValue(config.term, v));
    } else {
      payload[config.term] = oldValues ? [literalValue(config.term, oldValues)] : [];
    }

    await apiPatch(`items/${itemId}`, payload);
    showToast('Undone');

    // Update local state
    const idx = allItems.findIndex(it => it['o:id'] === itemId);
    if (idx >= 0) {
      const { json: updatedItem } = await apiGet(`items/${itemId}`);
      allItems[idx] = updatedItem;
      allItems[idx]._issues = validateItem(updatedItem);
      allItems[idx]._identifier = extractValue(updatedItem, 'dcterms:identifier') || `item-${updatedItem['o:id']}`;
    }
  } catch (err) {
    showToast(`Undo error: ${err.message}`, true);
    return;
  }

  // Re-insert item at previous position
  sprintIndex = Math.max(0, sprintIndex - 1);
  const item = allItems.find(it => it['o:id'] === itemId);
  if (item && sprintIndex <= sprintQueue.length) {
    sprintQueue.splice(sprintIndex, 0, item);
  }

  sprintLastAction = null;
  renderSprintItem();
  updateSprintProgress();
}

// ── Sprint: completion ────────────────────────────────────────────────────────

function showSprintComplete() {
  const config = FIELD_SPRINTS[sprintField];
  dom.sprintViewZone.innerHTML = '';
  dom.sprintViewCurrent.textContent = '';
  dom.sprintViewMeta.innerHTML = '';
  dom.sprintViewActions.classList.add('hidden');
  dom.sprintViewDone.classList.remove('hidden');
  dom.sprintViewDone.innerHTML = `
    <h2>Done</h2>
    <p>${sprintIndex} items fixed · ${config.label} sprint complete</p>
    <button class="btn btn-nav" id="sprint-restart">Start over</button>
  `;
  $('#sprint-restart').addEventListener('click', () => {
    buildSprintQueue(sprintField);
    dom.sprintViewDone.classList.add('hidden');
    if (sprintQueue.length) {
      renderSprintItem();
    } else {
      showSprintComplete();
    }
    updateSprintProgress();
  });
}

// ── Sprint: button wiring ─────────────────────────────────────────────────────

function setupSprintButtons() {
  dom.sprintViewSkip.addEventListener('click', sprintSkip);
  dom.sprintViewUndo.addEventListener('click', sprintUndo);
}

// ── Auto-sprint: pick the first issue field and enter sprint mode ────────────

function autoSprintIssues() {
  buildQueue();
  updateNav();

  if (!queue.length) {
    // No issues — show "all done" in sprint view
    dom.main.classList.remove('hidden');
    dom.sprintView.classList.remove('hidden');
    dom.sprintViewDone.classList.remove('hidden');
    dom.sprintViewActions.classList.add('hidden');
    dom.sprintViewZone.innerHTML = '';
    dom.sprintViewMeta.innerHTML = '';
    dom.sprintViewCurrent.textContent = '';
    dom.sprintViewFieldLabel.textContent = '';
    dom.sprintViewCount.textContent = '';
    dom.sprintViewDone.innerHTML = `
      <h2>All clear</h2>
      <p>No issues found across ${allItems.length} items.</p>
    `;
    dom.image.src = '';
    return;
  }

  // Find the first issue field that has a sprint config
  const firstItem = queue[0];
  const issueFieldMap = {
    'Catalog #': 'identifier', 'Date': 'date', 'Type': 'type',
    'Medium': 'medium', 'Support': 'support', 'Height': 'dimensions',
    'Width': 'dimensions', 'Signature': 'signature', 'Condition': 'condition',
    'Framing': 'framing', 'Owner': 'owner', 'Location': 'location',
    'Motifs': 'motifs', 'Box': 'box', 'Transcription': 'transcription',
    'Category': 'category',
  };

  let fieldKey = null;
  for (const issue of firstItem._issues) {
    const mapped = issueFieldMap[issue.field];
    if (mapped && FIELD_SPRINTS[mapped]) {
      fieldKey = mapped;
      break;
    }
  }

  // Fallback: pick the first FIELD_SPRINTS key
  if (!fieldKey) fieldKey = Object.keys(FIELD_SPRINTS)[0];

  enterSprintMode(fieldKey);
}

// ── Init ────────────────────────────────────────────────────────────────────

async function init() {
  cacheDom();
  try {
    await fetchAllData();
  } catch (err) {
    dom.loadingText.textContent = `Failed to load: ${err.message}`;
    return;
  }

  initFieldSprints();
  setupKeyboard();
  setupFilterButtons();
  setupBucketButtons();
  setupSprintButtons();
  setupSprintMenu();

  // Restore saved position
  restorePosition();

  // Set active filter button
  for (const btn of $$('.filter-btn')) {
    btn.classList.toggle('active', btn.dataset.filter === filterMode);
  }

  dom.loading.classList.add('hidden');

  // Enter the saved mode
  if (filterMode === 'exhibit') {
    enterExhibitMode();
  } else if (filterMode === 'sprint' && sprintField && FIELD_SPRINTS[sprintField]) {
    enterSprintMode(sprintField);
  } else {
    // Default: auto-sprint on issues
    autoSprintIssues();
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
})();
