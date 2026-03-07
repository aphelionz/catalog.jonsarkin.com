// ── Rapid-Fire Metadata Editor ───────────────────────────────────────────────
// Standalone editor for speed-running catalog metadata review.
// Talks to Omeka S API via the serve.py proxy.

'use strict';

// ── Config ──────────────────────────────────────────────────────────────────

const API_BASE = '/api';
const AUTH = { key_identity: 'catalog_api', key_credential: 'sarkin2024' };
const RESOURCE_TEMPLATE_ID = 2;
const CREATOR_ITEM_ID = 3;

// Curate mode
const PERMANENT_COLLECTION_SET_ID = 7490;
const SWIPE_THRESHOLD = 100;    // px of horizontal drag to trigger action
const ROTATION_FACTOR = 0.12;   // degrees per px of drag
const FLY_DISTANCE = 1.5;       // viewport-width multiplier for fly-off

// Property IDs (from enrich_metadata.py)
const PROP = {
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
};

// Controlled vocabularies
const WORK_TYPES = ['Drawing', 'Painting', 'Collage', 'Mixed Media', 'Sculpture', 'Print', 'Other'];
const SUPPORTS = ['Paper', 'Cardboard', 'Cardboard album sleeve', 'Canvas', 'Board', 'Wood', 'Found Object', 'Envelope', 'Album Sleeve', 'Other'];
const MOTIFS = ['Eyes', 'Fish', 'Faces', 'Hands', 'Text Fragments', 'Grids', 'Circles', 'Patterns', 'Animals', 'Names/Words', 'Maps', 'Numbers'];
const CONDITIONS = ['Excellent', 'Good', 'Fair', 'Poor', 'Not Examined'];
const SIGNATURE_ARROWS = ['↖', '↑', '↗', '←', '∅', '→', '↙', '↓', '↘'];
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
let currentItem = null;   // Full item JSON for the item being edited
let snapshot = {};        // Initial form values for dirty-check
let mediaCache = {};      // mediaId → original_url
let saving = false;
let filterMode = 'issues'; // 'issues' | 'dates' | 'all' | 'box' | 'curate'
let boxFilter = '';

// Curate state
let curateMode = false;
let curateQueue = [];
let curateIndex = 0;
let curateLastAction = null;  // { itemId, action: 'keep'|'pass' }
let curateDragState = null;   // { startX, startY, currentX, currentY, cardEl, pointerId }
let curateActing = false;     // prevent double-swipe while fly-off in progress

// ── DOM refs ────────────────────────────────────────────────────────────────

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const dom = {};

function cacheDom() {
  dom.loading = $('#loading');
  dom.loadingText = $('#loading-text');
  dom.main = $('#main');
  dom.queueStatus = $('#queue-status');
  dom.itemLink = $('#item-link');
  dom.countIssues = $('#count-issues');
  dom.countDates = $('#count-dates');
  dom.countAll = $('#count-all');
  dom.boxSelect = $('#box-select');
  dom.progressFill = $('#progress-fill');
  dom.image = $('#item-image');
  dom.imageLoading = $('#image-loading');
  dom.formPanel = $('#form-panel');
  dom.issueBadges = $('#issue-badges');
  dom.toast = $('#toast');
  dom.btnPrev = $('#btn-prev');
  dom.btnSave = $('#btn-save');
  dom.btnSkip = $('#btn-skip');
  dom.btnSaveNext = $('#btn-save-next');
}

// ── API helpers ─────────────────────────────────────────────────────────────

function apiUrl(path, params = {}) {
  const p = new URLSearchParams({ ...AUTH, ...params });
  return `${API_BASE}/${path}?${p}`;
}

async function apiGet(path, params = {}) {
  const resp = await fetch(apiUrl(path, params));
  if (!resp.ok) throw new Error(`API ${resp.status}: ${path}`);
  return { json: await resp.json(), headers: resp.headers };
}

async function apiPatch(path, body) {
  const resp = await fetch(apiUrl(path), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`PATCH ${resp.status}: ${text.slice(0, 200)}`);
  }
  return resp.json();
}

// ── Data loading ────────────────────────────────────────────────────────────

async function fetchAllItems() {
  const perPage = 500;
  let page = 1;
  let total = null;
  allItems = [];

  while (true) {
    const { json, headers } = await apiGet('items', {
      resource_template_id: RESOURCE_TEMPLATE_ID,
      per_page: perPage,
      page,
    });
    if (total === null) {
      total = parseInt(headers.get('Omeka-S-Total-Results') || '0', 10);
    }
    allItems.push(...json);
    dom.loadingText.textContent = `Loading items… ${allItems.length} / ${total}`;
    if (json.length < perPage) break;
    page++;
  }

  // Validate each item
  for (const item of allItems) {
    item._issues = validateItem(item);
    item._identifier = extractValue(item, 'dcterms:identifier') || `item-${item['o:id']}`;
    item._box = extractValue(item, 'schema:box') || '';
  }
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
  } else if (!WORK_TYPES.includes(type)) {
    issues.push({ field: 'Type', level: 'error', msg: `invalid "${type}"` });
  }

  if (!extractValue(item, 'dcterms:medium')) {
    issues.push({ field: 'Medium', level: 'error', msg: 'missing' });
  }

  const support = extractValue(item, 'schema:artworkSurface');
  if (!support) {
    issues.push({ field: 'Support', level: 'error', msg: 'missing' });
  } else if (!SUPPORTS.includes(support)) {
    issues.push({ field: 'Support', level: 'error', msg: `invalid "${support}"` });
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
  if (filterMode === 'issues') {
    queue = allItems
      .filter(it => it._issues.some(i => i.level === 'error'))
      .sort((a, b) => b._issues.length - a._issues.length);
  } else if (filterMode === 'dates') {
    queue = allItems
      .filter(hasBadDate)
      .sort((a, b) => a._identifier.localeCompare(b._identifier));
  } else if (filterMode === 'box') {
    queue = boxFilter
      ? allItems.filter(it => it._box === boxFilter).sort((a, b) => a._identifier.localeCompare(b._identifier))
      : [];
  } else {
    queue = [...allItems].sort((a, b) => a._identifier.localeCompare(b._identifier));
  }
  queueIndex = Math.min(queueIndex, Math.max(0, queue.length - 1));
}

function updateNav() {
  const issueCount = allItems.filter(it => it._issues.some(i => i.level === 'error')).length;
  dom.countIssues.textContent = `(${issueCount})`;
  dom.countDates.textContent = `(${allItems.filter(hasBadDate).length})`;
  dom.countAll.textContent = `(${allItems.length})`;

  if (!queue.length) {
    const emptyMsg = { issues: 'No issues found!', dates: 'All dates fixed!' };
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
    const url = json['o:original_url'] || '';
    // Convert absolute URL to proxy path
    const path = url.replace(/^https?:\/\/[^/]+/, '');
    mediaCache[mediaId] = path;
    return path;
  } catch {
    return null;
  }
}

// Preload next item's image
function preloadNext() {
  if (queueIndex + 1 < queue.length) {
    const next = queue[queueIndex + 1];
    getImageUrl(next).then(url => {
      if (url) { const img = new Image(); img.src = url; }
    });
  }
}

// ── Form population ─────────────────────────────────────────────────────────

function populateForm(item) {
  // Text/select fields
  for (const el of $$('[data-term]')) {
    const term = el.dataset.term;
    if (el.closest('.sig-grid') || el.closest('.chip-group') || el.closest('.date-pills') || el.closest('.transcription-pills')) continue;

    if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
      // For repeatable fields (provenance, inscriptions), join with newline
      if (['dcterms:provenance', 'bibo:annotates'].includes(term)) {
        el.value = extractAllValues(item, term).join('\n');
      } else {
        el.value = extractValue(item, term);
      }
    } else if (el.tagName === 'SELECT') {
      const val = extractValue(item, term);
      // If the value exists but isn't in our options, add it temporarily
      const hasOption = Array.from(el.options).some(o => o.value === val);
      if (val && !hasOption) {
        const opt = document.createElement('option');
        opt.value = val;
        opt.textContent = `${val} (custom)`;
        el.appendChild(opt);
      }
      el.value = val;
    }
  }

  // Date pills
  const dateVal = extractValue(item, 'dcterms:date');
  for (const pill of $$('.date-pill')) {
    pill.classList.toggle('active', pill.dataset.val === dateVal);
  }

  // Transcription pills
  const transVal = extractValue(item, 'bibo:content');
  for (const pill of $$('.transcription-pill')) {
    pill.classList.toggle('active', pill.dataset.val === transVal);
  }

  // Signature grid
  const sigVal = extractValue(item, 'schema:distinguishingSign');
  for (const btn of $$('#sig-grid button')) {
    btn.classList.toggle('active', btn.dataset.val === sigVal);
  }

  // Motif chips
  const motifs = extractAllValues(item, 'dcterms:subject');
  for (const chip of $$('#motif-chips .chip')) {
    chip.classList.toggle('active', motifs.includes(chip.dataset.val));
  }

  // Issue badges
  const issues = validateItem(item);
  dom.issueBadges.innerHTML = issues
    .map(i => `<span class="badge badge-${i.level}">${i.field}: ${i.msg}</span>`)
    .join('');

  // Save snapshot for dirty tracking
  snapshot = captureFormState();
  dom.formPanel.classList.remove('dirty', 'saved', 'error');
}

// ── Form state capture ──────────────────────────────────────────────────────

function captureFormState() {
  const state = {};

  // Text/select fields
  for (const el of $$('[data-term]')) {
    const term = el.dataset.term;
    if (el.closest('.sig-grid') || el.closest('.chip-group') || el.closest('.date-pills') || el.closest('.transcription-pills')) continue;
    if (el.tagName !== 'INPUT' && el.tagName !== 'SELECT' && el.tagName !== 'TEXTAREA') continue;
    state[term] = el.value;
  }

  // Signature
  const activeSig = $('#sig-grid button.active');
  state['schema:distinguishingSign'] = activeSig ? activeSig.dataset.val : '';

  // Motifs
  state['dcterms:subject'] = Array.from($$('#motif-chips .chip.active'))
    .map(c => c.dataset.val)
    .sort()
    .join(',');

  return state;
}

function isDirty() {
  const current = captureFormState();
  for (const key of Object.keys(current)) {
    if (current[key] !== snapshot[key]) return true;
  }
  return false;
}

function updateDirtyState() {
  dom.formPanel.classList.toggle('dirty', isDirty());
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
  return { type: 'literal', property_id: PROP[term], '@value': val };
}

function buildPayload(item, formState) {
  const payload = {};

  // 1. Copy ALL existing vocabulary properties
  for (const [key, val] of Object.entries(item)) {
    if (key.includes(':') && !key.startsWith('o:') && Array.isArray(val)) {
      payload[key] = val.filter(v => typeof v === 'object').map(cleanValue);
    }
  }

  // 2. Copy system keys (NOT o:resource_template — causes 422)
  for (const sysKey of ['o:resource_class', 'o:item_set', 'o:media', 'o:is_public', 'o:site']) {
    if (sysKey in item) payload[sysKey] = item[sysKey];
  }

  // 3. Overwrite edited literal fields
  const literalFields = [
    'dcterms:date', 'dcterms:type', 'dcterms:medium',
    'schema:artworkSurface', 'schema:height', 'schema:width',
    'schema:distinguishingSign', 'schema:itemCondition',
    'dcterms:identifier', 'dcterms:description', 'dcterms:format',
    'bibo:owner', 'dcterms:spatial', 'dcterms:rights',
    'schema:creditText', 'bibo:content', 'bibo:presentedAt',
    'dcterms:bibliographicCitation', 'schema:box', 'curation:note',
  ];

  for (const term of literalFields) {
    const val = (formState[term] || '').trim();
    if (val) {
      payload[term] = [literalValue(term, val)];
    } else {
      // Preserve existing if form is empty (don't delete data we didn't show)
      // But if the focused field is explicitly empty, allow clearing it
      payload[term] = [];
    }
  }

  // 4. Repeatable text fields (provenance, inscriptions) — split on newlines
  for (const term of ['dcterms:provenance', 'bibo:annotates']) {
    const raw = (formState[term] || '').trim();
    if (raw) {
      payload[term] = raw.split('\n').filter(Boolean).map(line => literalValue(term, line.trim()));
    } else {
      payload[term] = [];
    }
  }

  // 5. Motifs (dcterms:subject) — from comma-joined string
  const motifStr = formState['dcterms:subject'] || '';
  if (motifStr) {
    payload['dcterms:subject'] = motifStr.split(',').map(m => literalValue('dcterms:subject', m));
  } else {
    payload['dcterms:subject'] = [];
  }

  // 6. Ensure creator reference exists
  const creatorVals = payload['schema:creator'] || [];
  const hasCreator = creatorVals.some(v => v.value_resource_id === CREATOR_ITEM_ID);
  if (!hasCreator) {
    payload['schema:creator'] = [
      ...creatorVals,
      { type: 'resource:item', property_id: PROP['schema:creator'], value_resource_id: CREATOR_ITEM_ID },
    ];
  }

  return payload;
}

// ── Save ────────────────────────────────────────────────────────────────────

async function saveCurrentItem() {
  if (saving || !currentItem) return;
  saving = true;
  dom.btnSave.disabled = true;
  dom.btnSaveNext.disabled = true;

  try {
    // Re-fetch to avoid stale overwrites
    const { json: freshItem } = await apiGet(`items/${currentItem['o:id']}`);
    const formState = captureFormState();
    const payload = buildPayload(freshItem, formState);
    const updated = await apiPatch(`items/${currentItem['o:id']}`, payload);

    // Update local state
    currentItem = updated;
    const idx = allItems.findIndex(it => it['o:id'] === updated['o:id']);
    if (idx >= 0) {
      // Preserve _issues and _identifier on the allItems entry
      allItems[idx] = updated;
      allItems[idx]._issues = validateItem(updated);
      allItems[idx]._identifier = extractValue(updated, 'dcterms:identifier') || `item-${updated['o:id']}`;
      allItems[idx]._box = extractValue(updated, 'schema:box') || '';
    }

    snapshot = captureFormState();
    dom.formPanel.classList.remove('dirty');
    flashSave();
    showToast(`Saved ${extractValue(updated, 'dcterms:identifier')}`);

    // Update issue badges for this item
    const issues = validateItem(updated);
    dom.issueBadges.innerHTML = issues
      .map(i => `<span class="badge badge-${i.level}">${i.field}: ${i.msg}</span>`)
      .join('');

    updateNav();
  } catch (err) {
    dom.formPanel.classList.add('error');
    showToast(`Error: ${err.message}`, true);
    console.error('Save failed:', err);
  } finally {
    saving = false;
    dom.btnSave.disabled = false;
    dom.btnSaveNext.disabled = false;
  }
}

function flashSave() {
  dom.formPanel.classList.add('saved');
  setTimeout(() => dom.formPanel.classList.remove('saved'), 600);
}

// ── Navigation ──────────────────────────────────────────────────────────────

async function loadItem(index) {
  if (index < 0 || index >= queue.length) return;
  queueIndex = index;
  savePosition();
  updateNav();

  const summary = queue[queueIndex];

  // Show image loading state
  dom.imageLoading.classList.remove('hidden');
  dom.image.style.opacity = '0.3';

  // Fetch full item
  const { json: fullItem } = await apiGet(`items/${summary['o:id']}`);
  currentItem = fullItem;

  // Load image
  const url = await getImageUrl(fullItem);
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

  populateForm(fullItem);
  preloadNext();

  // Scroll form to top
  dom.formPanel.scrollTop = 0;
}

function confirmIfDirty() {
  if (isDirty()) {
    return confirm('You have unsaved changes. Discard and continue?');
  }
  return true;
}

function goNext() {
  if (queueIndex + 1 < queue.length) loadItem(queueIndex + 1);
}

function goPrev() {
  if (!confirmIfDirty()) return;
  if (queueIndex > 0) loadItem(queueIndex - 1);
}

function skip() {
  if (!confirmIfDirty()) return;
  goNext();
}

async function saveAndNext() {
  await saveCurrentItem();
  if (!saving) goNext();
}

// ── Session persistence ─────────────────────────────────────────────────────

function savePosition() {
  try {
    localStorage.setItem('rapid-editor', JSON.stringify({
      index: queueIndex,
      filter: filterMode,
      box: boxFilter,
    }));
  } catch { /* ignore */ }
}

function restorePosition() {
  try {
    const saved = JSON.parse(localStorage.getItem('rapid-editor') || '{}');
    if (saved.filter) filterMode = saved.filter;
    if (saved.box) boxFilter = saved.box;
    if (typeof saved.index === 'number') queueIndex = saved.index;
  } catch { /* ignore */ }
}

// ── UI setup ────────────────────────────────────────────────────────────────

function setupSelects() {
  const typeSelect = $('#f-type');
  const supportSelect = $('#f-support');
  const conditionSelect = $('#f-condition');

  for (const val of WORK_TYPES) {
    typeSelect.add(new Option(val, val));
  }
  for (const val of SUPPORTS) {
    supportSelect.add(new Option(val, val));
  }
  for (const val of CONDITIONS) {
    conditionSelect.add(new Option(val, val));
  }
}

function setupDatePills() {
  const container = $('#date-pills');
  const dateInput = $('#f-date');

  function makePill(value, label, extraClass) {
    const pill = document.createElement('span');
    pill.className = 'date-pill' + (extraClass ? ' ' + extraClass : '');
    pill.textContent = label;
    pill.dataset.val = value;
    pill.addEventListener('click', () => {
      const wasActive = pill.classList.contains('active');
      for (const p of $$('.date-pill')) p.classList.remove('active');
      if (!wasActive) {
        pill.classList.add('active');
        dateInput.value = value;
      } else {
        dateInput.value = '';
      }
      updateDirtyState();
      // Auto save + next on date pill click
      saveAndNext();
    });
    container.appendChild(pill);
  }

  // Unknown date pill — proper art catalog convention for "during his career"
  makePill('c. 1989–2024', 'c. 1989–2024', 'date-unknown');
  makePill('c. 2000s', 'c. 2000s', 'date-unknown');

  // Year pills with short labels ('87, '88, ... '24)
  for (const year of DATE_YEARS) {
    const label = '\u2019' + year.slice(2); // '87, '88, etc.
    const extra = year.endsWith('0') ? 'decade-start' : '';
    makePill(year, label, extra);
  }
  // Typing in the text input clears the pill selection
  dateInput.addEventListener('input', () => {
    const val = dateInput.value.trim();
    for (const p of $$('.date-pill')) {
      p.classList.toggle('active', p.dataset.val === val);
    }
    updateDirtyState();
  });
}

function setupTranscriptionPills() {
  const container = $('#transcription-pills');
  const textarea = $('#f-transcription');
  const pills = [
    { value: '∅', label: 'No text' },
    { value: '[Needs enrichment]', label: 'Needs enrichment' },
  ];
  for (const { value, label } of pills) {
    const pill = document.createElement('span');
    pill.className = 'transcription-pill';
    pill.textContent = label;
    pill.dataset.val = value;
    pill.addEventListener('click', () => {
      const wasActive = pill.classList.contains('active');
      for (const p of $$('.transcription-pill')) p.classList.remove('active');
      if (!wasActive) {
        pill.classList.add('active');
        textarea.value = value;
      } else {
        textarea.value = '';
      }
      updateDirtyState();
      saveAndNext();
    });
    container.appendChild(pill);
  }
}

function setupMotifChips() {
  const container = $('#motif-chips');
  for (const motif of MOTIFS) {
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = motif;
    chip.dataset.val = motif;
    chip.addEventListener('click', () => {
      chip.classList.toggle('active');
      updateDirtyState();
    });
    container.appendChild(chip);
  }
}

function setupSignatureGrid() {
  for (const btn of $$('#sig-grid button')) {
    btn.addEventListener('click', () => {
      // Toggle: if already active, deactivate; otherwise switch
      const wasActive = btn.classList.contains('active');
      for (const b of $$('#sig-grid button')) b.classList.remove('active');
      if (!wasActive) btn.classList.add('active');
      updateDirtyState();
    });
  }
}

function setupBoxSelect() {
  const boxes = [...new Set(allItems.map(it => it._box).filter(Boolean))].sort();
  for (const box of boxes) {
    dom.boxSelect.add(new Option(box, box));
  }
  dom.boxSelect.addEventListener('change', () => {
    boxFilter = dom.boxSelect.value;
    queueIndex = 0;
    buildQueue();
    updateNav();
    if (queue.length) loadItem(0);
  });
}

function setupFilterButtons() {
  for (const btn of $$('.filter-btn')) {
    btn.addEventListener('click', () => {
      // Curate mode: special handling
      if (btn.dataset.filter === 'curate') {
        if (curateMode) return; // already in curate mode
        if (!confirmIfDirty()) return;
        enterCurateMode();
        return;
      }

      // Leaving curate mode — skip dirty check (no editor form to lose)
      const wasCurate = curateMode;
      if (curateMode) exitCurateMode();

      if (!wasCurate && !confirmIfDirty()) return;
      for (const b of $$('.filter-btn')) b.classList.remove('active');
      btn.classList.add('active');
      filterMode = btn.dataset.filter;
      dom.boxSelect.classList.toggle('hidden', filterMode !== 'box');
      queueIndex = 0;
      buildQueue();
      updateNav();
      if (queue.length) loadItem(0);
    });
  }
}

function setupDirtyTracking() {
  // Track changes on all form inputs
  for (const el of $$('input, select, textarea')) {
    el.addEventListener('input', updateDirtyState);
    el.addEventListener('change', updateDirtyState);
  }
}

function setupButtons() {
  dom.btnPrev.addEventListener('click', goPrev);
  dom.btnSave.addEventListener('click', saveCurrentItem);
  dom.btnSkip.addEventListener('click', skip);
  dom.btnSaveNext.addEventListener('click', saveAndNext);
}

function setupKeyboard() {
  document.addEventListener('keydown', (e) => {
    const mod = e.metaKey || e.ctrlKey;

    // Curate mode shortcuts
    if (curateMode) {
      if (e.key === 'ArrowRight') {
        e.preventDefault();
        triggerCurateSwipe('right');
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        triggerCurateSwipe('left');
      } else if (mod && e.key === 'z') {
        e.preventDefault();
        curateUndo();
      }
      return; // Don't process editor shortcuts in curate mode
    }

    // Normal editor shortcuts
    if (mod && e.key === 's') {
      e.preventDefault();
      saveCurrentItem();
    } else if (mod && e.key === 'ArrowRight') {
      e.preventDefault();
      saveAndNext();
    } else if (mod && e.key === 'ArrowLeft') {
      e.preventDefault();
      goPrev();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      skip();
    }
  });
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
// ── Curate Mode — Tinder-style Permanent Collection curation ────────────────
// ══════════════════════════════════════════════════════════════════════════════

// ── Curate: queue ────────────────────────────────────────────────────────────

function buildCurateQueue() {
  curateQueue = allItems
    .filter(item => {
      const sets = item['o:item_set'] || [];
      return !sets.some(s => s['o:id'] === PERMANENT_COLLECTION_SET_ID);
    })
    .sort((a, b) => {
      // Oldest modified first — touched items sink to the bottom
      const ma = (a['o:modified'] && a['o:modified']['@value']) || '';
      const mb = (b['o:modified'] && b['o:modified']['@value']) || '';
      return ma.localeCompare(mb);
    });
  curateIndex = 0;
}

// ── Curate: mode switching ───────────────────────────────────────────────────

function enterCurateMode() {
  curateMode = true;
  filterMode = 'curate';

  dom.main.classList.add('hidden');
  $('#curate-panel').classList.remove('hidden');

  for (const b of $$('.filter-btn')) {
    b.classList.toggle('active', b.dataset.filter === 'curate');
  }
  dom.boxSelect.classList.add('hidden');

  buildCurateQueue();
  updateCurateProgress();

  if (curateQueue.length) {
    renderCurateCards();
    preloadCurateNext();
  } else {
    showCurateComplete();
  }

  savePosition();
}

function exitCurateMode() {
  curateMode = false;
  $('#curate-panel').classList.add('hidden');
  dom.main.classList.remove('hidden');
  clearCardStage();
}

// ── Curate: progress ─────────────────────────────────────────────────────────

function updateCurateProgress() {
  const countEl = $('#curate-count');
  const undoBtn = $('#curate-undo');
  const remaining = curateQueue.length - curateIndex;
  const reviewed = curateIndex;
  countEl.textContent = `${reviewed} reviewed · ${remaining} remaining`;
  undoBtn.disabled = !curateLastAction;

  // Update nav progress bar too
  const pct = curateQueue.length > 0 ? (curateIndex / curateQueue.length) * 100 : 0;
  dom.progressFill.style.width = `${pct}%`;
}

// ── Curate: card rendering ───────────────────────────────────────────────────

function clearCardStage() {
  const stage = $('#card-stage');
  stage.innerHTML = '';
}

function renderCurateCards() {
  clearCardStage();
  // Next card behind (if exists)
  if (curateIndex + 1 < curateQueue.length) {
    renderCurateCard(curateQueue[curateIndex + 1], 1);
  }
  // Current card on top
  if (curateIndex < curateQueue.length) {
    renderCurateCard(curateQueue[curateIndex], 2);
  }
}

async function renderCurateCard(item, zIndex) {
  const stage = $('#card-stage');
  const card = document.createElement('div');
  card.className = 'curate-card';
  card.dataset.itemId = item['o:id'];
  card.style.zIndex = zIndex;

  // Back card: scaled down for depth effect, hide meta to prevent text bleed
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

  card.innerHTML = `
    <div class="card-img-wrap">
      <div class="card-img-loading">Loading…</div>
    </div>
    <div class="card-meta">
      <div class="card-meta-id">${identifier}</div>
      ${detail ? `<div class="card-meta-detail">${detail}</div>` : ''}
      ${date ? `<div class="card-meta-date">${date}</div>` : ''}
    </div>
  `;

  stage.appendChild(card);

  // Load image
  const url = await getImageUrl(item);
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

  // Only attach swipe handlers to the top card
  if (zIndex === 2) {
    attachSwipeHandlers(card);
  }
}

function preloadCurateNext() {
  // Preload 2 items ahead (next is already rendered)
  const ahead = curateIndex + 2;
  if (ahead < curateQueue.length) {
    getImageUrl(curateQueue[ahead]).then(url => {
      if (url) { const img = new Image(); img.src = url; }
    });
  }
}

// ── Curate: swipe gesture system ─────────────────────────────────────────────

function attachSwipeHandlers(cardEl) {
  cardEl.addEventListener('pointerdown', onCuratePointerDown);
}

function onCuratePointerDown(e) {
  if (curateDragState || curateActing) return;
  const card = e.currentTarget;
  card.setPointerCapture(e.pointerId);
  card.classList.add('dragging');
  curateDragState = {
    startX: e.clientX,
    startY: e.clientY,
    currentX: e.clientX,
    currentY: e.clientY,
    cardEl: card,
    pointerId: e.pointerId,
  };
  card.addEventListener('pointermove', onCuratePointerMove);
  card.addEventListener('pointerup', onCuratePointerUp);
  card.addEventListener('pointercancel', onCuratePointerUp);
}

function onCuratePointerMove(e) {
  if (!curateDragState || e.pointerId !== curateDragState.pointerId) return;
  curateDragState.currentX = e.clientX;
  curateDragState.currentY = e.clientY;

  const dx = curateDragState.currentX - curateDragState.startX;
  const dy = (curateDragState.currentY - curateDragState.startY) * 0.3;
  const rotation = dx * ROTATION_FACTOR;

  curateDragState.cardEl.style.transform =
    `translate(${dx}px, ${dy}px) rotate(${rotation}deg)`;

  // Green glow hint when dragging right past half-threshold
  curateDragState.cardEl.classList.toggle('hint-right', dx > SWIPE_THRESHOLD * 0.5);
}

function onCuratePointerUp(e) {
  if (!curateDragState || e.pointerId !== curateDragState.pointerId) return;
  const card = curateDragState.cardEl;
  card.removeEventListener('pointermove', onCuratePointerMove);
  card.removeEventListener('pointerup', onCuratePointerUp);
  card.removeEventListener('pointercancel', onCuratePointerUp);
  card.classList.remove('dragging', 'hint-right');

  const dx = curateDragState.currentX - curateDragState.startX;
  curateDragState = null;

  if (dx > SWIPE_THRESHOLD) {
    flyOffCard(card, 'right');
  } else if (dx < -SWIPE_THRESHOLD) {
    flyOffCard(card, 'left');
  } else {
    // Snap back
    card.classList.add('snap-back');
    card.style.transform = '';
    card.addEventListener('transitionend', () => {
      card.classList.remove('snap-back');
    }, { once: true });
  }
}

function flyOffCard(cardEl, direction) {
  if (curateActing) return;
  curateActing = true;

  const vw = window.innerWidth;
  const targetX = direction === 'right' ? vw * FLY_DISTANCE : -vw * FLY_DISTANCE;
  const rotation = direction === 'right' ? 30 : -30;

  cardEl.classList.add('fly-off');
  cardEl.style.transform = `translate(${targetX}px, 0) rotate(${rotation}deg)`;

  cardEl.addEventListener('transitionend', () => {
    cardEl.remove();
    promoteCurateBackCard();
    curateActing = false;
  }, { once: true });

  // Trigger the action
  const itemId = Number(cardEl.dataset.itemId);
  if (direction === 'right') {
    curateKeep(itemId);
  } else {
    curatePass(itemId);
  }
}

function triggerCurateSwipe(direction) {
  if (curateActing) return;
  const stage = $('#card-stage');
  // Find the top card by z-index (can't use :last-child — DOM order
  // changes after promoteCurateBackCard appends a new back card)
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

function promoteCurateBackCard() {
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

  // Render new back card if available
  if (curateIndex + 1 < curateQueue.length) {
    renderCurateCard(curateQueue[curateIndex + 1], 1);
  }

  preloadCurateNext();

  if (curateIndex >= curateQueue.length) {
    showCurateComplete();
  }
}

// ── Curate: API actions ──────────────────────────────────────────────────────

function buildCuratePayload(item) {
  // Same pattern as buildPayload() but with no form edits — preserve everything
  const payload = {};

  for (const [key, val] of Object.entries(item)) {
    if (key.includes(':') && !key.startsWith('o:') && Array.isArray(val)) {
      payload[key] = val.filter(v => typeof v === 'object').map(cleanValue);
    }
  }

  for (const sysKey of ['o:resource_class', 'o:item_set', 'o:media', 'o:is_public', 'o:site']) {
    if (sysKey in item) payload[sysKey] = item[sysKey];
  }

  return payload;
}

async function curateKeep(itemId) {
  // Advance immediately — card is already gone, API works in background
  curateIndex++;
  curateLastAction = { itemId, action: 'keep' };
  updateCurateProgress();

  try {
    const { json: freshItem } = await apiGet(`items/${itemId}`);
    const payload = buildCuratePayload(freshItem);

    // Append Permanent Collection set if not already present
    const sets = payload['o:item_set'] || [];
    if (!sets.some(s => s['o:id'] === PERMANENT_COLLECTION_SET_ID)) {
      payload['o:item_set'] = [...sets, { 'o:id': PERMANENT_COLLECTION_SET_ID }];
    }

    await apiPatch(`items/${itemId}`, payload);
    showToast('✓ Added to Permanent Collection');

    // Update local state
    const idx = allItems.findIndex(it => it['o:id'] === itemId);
    if (idx >= 0) {
      allItems[idx]['o:item_set'] = payload['o:item_set'];
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, true);
    console.error('Curate keep failed:', err);
    curateLastAction = null;
  }
}

async function curatePass(itemId) {
  // Advance immediately — card is already gone, API works in background
  curateIndex++;
  curateLastAction = { itemId, action: 'pass' };
  updateCurateProgress();

  try {
    // "Touch" the item — re-save without changes to bump o:modified
    const { json: freshItem } = await apiGet(`items/${itemId}`);
    const payload = buildCuratePayload(freshItem);
    await apiPatch(`items/${itemId}`, payload);
  } catch (err) {
    showToast(`Error: ${err.message}`, true);
    console.error('Curate pass failed:', err);
    curateLastAction = null;
  }
}

// ── Curate: undo ─────────────────────────────────────────────────────────────

async function curateUndo() {
  if (!curateLastAction || curateActing) return;
  const { itemId, action } = curateLastAction;

  if (action === 'keep') {
    // Remove from Permanent Collection set
    try {
      const { json: freshItem } = await apiGet(`items/${itemId}`);
      const payload = buildCuratePayload(freshItem);
      payload['o:item_set'] = (payload['o:item_set'] || [])
        .filter(s => s['o:id'] !== PERMANENT_COLLECTION_SET_ID);
      await apiPatch(`items/${itemId}`, payload);
      showToast('Undone — removed from collection');

      // Update local state
      const idx = allItems.findIndex(it => it['o:id'] === itemId);
      if (idx >= 0) {
        allItems[idx]['o:item_set'] = payload['o:item_set'];
      }
    } catch (err) {
      showToast(`Undo error: ${err.message}`, true);
      return;
    }
  } else {
    showToast('Undone');
  }

  // Re-insert item at previous position
  curateIndex = Math.max(0, curateIndex - 1);
  const item = allItems.find(it => it['o:id'] === itemId);
  if (item && curateIndex <= curateQueue.length) {
    curateQueue.splice(curateIndex, 0, item);
  }

  curateLastAction = null;
  renderCurateCards();
  updateCurateProgress();
}

// ── Curate: completion ───────────────────────────────────────────────────────

function showCurateComplete() {
  const stage = $('#card-stage');
  const kept = allItems.filter(item => {
    const sets = item['o:item_set'] || [];
    return sets.some(s => s['o:id'] === PERMANENT_COLLECTION_SET_ID);
  }).length;

  stage.innerHTML = `
    <div class="curate-done">
      <h2>Done</h2>
      <p>${curateIndex} items reviewed · ${kept} in Permanent Collection</p>
      <button class="btn btn-nav" id="curate-restart">Start over</button>
    </div>
  `;
  $('#curate-restart').addEventListener('click', () => {
    buildCurateQueue();
    renderCurateCards();
    updateCurateProgress();
  });
}

// ── Curate: button & keyboard wiring ─────────────────────────────────────────

function setupCurateButtons() {
  $('#curate-pass').addEventListener('click', () => triggerCurateSwipe('left'));
  $('#curate-keep').addEventListener('click', () => triggerCurateSwipe('right'));
  $('#curate-undo').addEventListener('click', curateUndo);
}

// ── Init ────────────────────────────────────────────────────────────────────

async function init() {
  cacheDom();
  setupSelects();
  setupDatePills();
  setupTranscriptionPills();
  setupMotifChips();
  setupSignatureGrid();
  setupButtons();
  setupKeyboard();
  setupFilterButtons();
  setupDirtyTracking();
  setupCurateButtons();

  // Restore saved position
  restorePosition();

  // Set active filter button
  for (const btn of $$('.filter-btn')) {
    btn.classList.toggle('active', btn.dataset.filter === filterMode);
  }
  dom.boxSelect.classList.toggle('hidden', filterMode !== 'box');

  try {
    await fetchAllItems();
  } catch (err) {
    dom.loadingText.textContent = `Failed to load: ${err.message}`;
    return;
  }

  setupBoxSelect();

  dom.loading.classList.add('hidden');

  // Enter curate mode if that was the saved filter
  if (filterMode === 'curate') {
    enterCurateMode();
  } else {
    buildQueue();
    updateNav();
    dom.main.classList.remove('hidden');
    if (queue.length) {
      await loadItem(queueIndex);
    }
  }
}

init();
