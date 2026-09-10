/* Kiosk attract loop for the Jon Sarkin catalogue raisonné.
 *
 * Every visual state is a pure function of elapsed cycle time, render(t), so the
 * live loop (requestAnimationFrame) and the video renderer (frame-stepped
 * Playwright screenshots through window.kiosk.render) share one code path.
 *
 * URL params:
 *   record=1  expose window.kiosk {ready, totalMs, render}; no rAF, cookie or cursor hiding
 *   video=1   only slides tagged `video` in slides.txt (the standalone mp4 cut)
 *   n=N       first N slides only
 *   simple=1  no Ken Burns (escape hatch for a stuttering Pi)
 *   idle=S    idle seconds handed to the catalog via the `kiosk` cookie (default 90)
 */
(function () {
  'use strict';

  var q = new URLSearchParams(location.search);
  var RECORD = q.get('record') === '1';
  var VIDEO = q.get('video') === '1';
  var SIMPLE = q.get('simple') === '1';
  var LIMIT = parseInt(q.get('n') || '0', 10) || 0;
  var IDLE = parseInt(q.get('idle') || '90', 10) || 90;

  var T = {
    slide: 26000,        // one artwork
    fadeIn: 1500,
    captionAt: 1500, captionIn: 1200,
    typeAt: 6500, typeRate: 35,   // chars per second
    similarAt: 17000, similarIn: 1000,
    fadeOutAt: 24500,
    intro: 12000, introFade: 1500, introEvery: 6,
    reloadAfter: 60 * 60 * 1000
  };

  var $ = function (s) { return document.querySelector(s); };
  var stage = $('#stage'), art = $('.art'), panel = $('.panel'), intro = $('#intro');
  var layers = [$('#layerA'), $('#layerB')];
  var typed = $('#typed'), similarGrid = $('#similarGrid');
  var cap = {
    cat: $('#capCat'), year: $('#capYear'), medium: $('#capMedium'),
    dims: $('#capDims')
  };

  var slides = [], segments = [], totalMs = 0;
  var current = -1, typedCount = -1;
  var startWall = 0, t0 = 0;
  var images = {};

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function clamp01(x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
  function ramp(t, at, dur) { return clamp01((t - at) / dur); }

  function fit() {
    var s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    stage.style.transform = 'translate(-50%, -50%) scale(' + s + ')';
  }

  /* ---- timeline ---- */

  function buildTimeline() {
    segments = [];
    var t = 0;
    slides.forEach(function (slide, i) {
      if (i % T.introEvery === 0) {
        segments.push({ type: 'intro', start: t, end: t + T.intro });
        t += T.intro;
      }
      segments.push({ type: 'slide', start: t, end: t + T.slide, slide: slide, index: i });
      t += T.slide;
    });
    totalMs = t;
  }

  function segmentAt(t) {
    for (var i = 0; i < segments.length; i++) if (t < segments[i].end) return i;
    return segments.length - 1;
  }

  /* ---- images ---- */

  function preload(src) {
    if (!images[src]) {
      var im = new Image();
      im.src = src;
      images[src] = im;
    }
    return images[src];
  }
  function preloadAround(i) {
    for (var k = 1; k <= 2; k++) {
      var s = slides[(i + k + slides.length) % slides.length];
      preload(s.image);
      s.similar.forEach(function (r) { preload(r.thumb); });
    }
  }
  function whenLoaded(im) {
    return new Promise(function (res) {
      if (im.complete) return res();
      im.addEventListener('load', res, { once: true });
      im.addEventListener('error', res, { once: true });
    });
  }
  function withTimeout(promise, ms) {
    return Promise.race([promise, new Promise(function (res) { setTimeout(res, ms); })]);
  }
  function preloadAll() {
    var all = [];
    slides.forEach(function (s) {
      all.push(preload(s.image));
      s.similar.forEach(function (r) { all.push(preload(r.thumb)); });
    });
    // Wait for the bytes, then decode on a best-effort basis: Chromium defers
    // decode() in hidden tabs, so it must never gate readiness on its own.
    return Promise.all(all.map(whenLoaded)).then(function () {
      return Promise.all(all.map(function (im) {
        return im.decode ? withTimeout(im.decode().catch(function () {}), 5000) : null;
      }));
    });
  }
  layers.forEach(function (layer) {
    layer.addEventListener('error', function () {
      if (layer.dataset.retried) return;
      layer.dataset.retried = '1';
      var src = layer.getAttribute('src');
      setTimeout(function () { layer.src = src + (src.indexOf('?') < 0 ? '?' : '&') + 'r=' + Date.now(); }, 2000);
    });
    layer.addEventListener('load', function () { delete layer.dataset.retried; });
  });

  /* ---- per-segment setup (runs once when a segment starts) ---- */

  function mediumFull(s) {
    // Same rule as the item page: "Ink, Marker on cardboard album sleeve".
    if (s.medium && s.support) return s.medium + ' on ' + s.support.toLowerCase();
    return s.medium || s.support || '';
  }

  function setRow(dd, value) {
    dd.textContent = value || '';
    dd.parentNode.hidden = !value;
  }

  function enterSegment(i) {
    var seg = segments[i];
    current = i;
    typedCount = -1;
    if (seg.type !== 'slide') return;
    var s = seg.slide, layer = layers[seg.index % 2];
    if (layer.getAttribute('src') !== s.image) layer.src = s.image;
    cap.cat.textContent = s.catalog_no;
    cap.year.textContent = s.year;
    setRow(cap.medium, mediumFull(s));
    setRow(cap.dims, s.dims);
    similarGrid.innerHTML = s.similar.map(function (r) {
      return '<figure><img src="' + esc(r.thumb) + '" alt=""></figure>';
    }).join('');
    typed.innerHTML = '';
    if (!RECORD) preloadAround(seg.index);
  }

  /* ---- typing ---- */

  function totalChars(slide) {
    var n = 0;
    slide.transcription.forEach(function (p) { p.forEach(function (l) { n += l.length + 1; }); });
    return n;
  }

  function typedHtml(slide, n) {
    var html = '', used = 0, done = true;
    for (var p = 0; p < slide.transcription.length && done; p++) {
      var lines = slide.transcription[p], parts = [];
      for (var l = 0; l < lines.length; l++) {
        var line = lines[l], room = n - used;
        if (room <= 0) { done = false; break; }
        parts.push(esc(line.slice(0, room)));
        used += line.length + 1;
        if (line.length > room) { done = false; break; }
      }
      if (parts.length) html += '<p>' + parts.join('<br>') + '</p>';
    }
    if (!done) {
      html = html
        ? html.replace(/<\/p>$/, '<span class="caret" id="caret"></span></p>')
        : '<p><span class="caret" id="caret"></span></p>';
    }
    return html;
  }

  /* ---- render(t): the whole picture at cycle time t ---- */

  function render(t) {
    t = ((t % totalMs) + totalMs) % totalMs;
    var i = segmentAt(t);
    if (i !== current) enterSegment(i);
    var seg = segments[i], lt = t - seg.start;

    if (seg.type === 'intro') {
      intro.style.opacity = Math.min(ramp(lt, 0, T.introFade),
        1 - ramp(lt, T.intro - T.introFade, T.introFade));
      art.style.opacity = 0;
      panel.style.opacity = 0;
      layers[0].style.opacity = 0;
      layers[1].style.opacity = 0;
      return;
    }

    intro.style.opacity = 0;
    var s = seg.slide, layer = layers[seg.index % 2], other = layers[(seg.index + 1) % 2];
    var alpha = Math.min(ramp(lt, 0, T.fadeIn), 1 - ramp(lt, T.fadeOutAt, T.slide - T.fadeOutAt));

    art.style.opacity = 1;
    other.style.opacity = 0;
    layer.style.opacity = alpha;
    if (SIMPLE) {
      layer.style.transform = 'none';
    } else {
      var p = lt / T.slide, a = seg.index * 2.399;  // golden-angle drift direction per slide
      layer.style.transform = 'translate3d(' + (Math.cos(a) * 22 * p).toFixed(2) + 'px,' +
        (Math.sin(a) * 22 * p).toFixed(2) + 'px,0) scale(' + (1 + 0.07 * p).toFixed(4) + ')';
    }

    panel.style.opacity = Math.min(ramp(lt, T.captionAt, T.captionIn), alpha);

    var total = totalChars(s);
    var n = lt < T.typeAt ? 0 : Math.min(total, Math.floor((lt - T.typeAt) / 1000 * T.typeRate));
    if (n !== typedCount) {
      typedCount = n;
      typed.innerHTML = typedHtml(s, n);
    }
    var caret = document.getElementById('caret');
    if (caret) caret.style.opacity = lt >= T.typeAt ? 1 : 0;

    similarGrid.style.opacity = ramp(lt, T.similarAt, T.similarIn);
  }

  /* ---- live loop ---- */

  function setCookie() {
    document.cookie = 'kiosk=' + IDLE + '; path=/; max-age=14400; SameSite=Lax';
  }

  function loop(now) {
    var t = now - t0;
    if (t >= totalMs) {
      t0 += Math.floor(t / totalMs) * totalMs;
      t = now - t0;
      setCookie();
      if (Date.now() - startWall > T.reloadAfter) { location.reload(); return; }
    }
    render(t);
    requestAnimationFrame(loop);
  }

  function bindInteraction() {
    var go = function (e) {
      if (e.type === 'keydown' && (e.key === 'F11' || e.key === 'Escape' || e.altKey || e.ctrlKey || e.metaKey)) return;
      var seg = segments[current];
      setCookie();
      location.href = (seg && seg.type === 'slide') ? seg.slide.url : '/faceted-browse/2';
    };
    ['pointerdown', 'touchstart', 'keydown'].forEach(function (ev) {
      window.addEventListener(ev, go, { passive: true });
    });
  }

  /* ---- boot ---- */

  function loadSlides(attempt) {
    return fetch('slides.json', { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .catch(function () {
        var wait = Math.min(30000, 2000 * Math.pow(2, attempt));
        return new Promise(function (res) { setTimeout(res, wait); })
          .then(function () { return loadSlides(attempt + 1); });
      });
  }

  function boot() {
    fit();
    window.addEventListener('resize', fit);
    loadSlides(0).then(function (data) {
      slides = data.slides.filter(function (s) { return !VIDEO || s.video; });
      if (LIMIT) slides = slides.slice(0, LIMIT);
      buildTimeline();
      if (RECORD) {
        window.kiosk = { ready: false, totalMs: totalMs, slides: slides, render: render };
        preloadAll()
          .then(function () { return document.fonts ? document.fonts.ready : null; })
          .then(function () { render(0); window.kiosk.ready = true; });
      } else {
        document.documentElement.setAttribute('data-live', '');
        setCookie();
        bindInteraction();
        preloadAround(-1);
        startWall = Date.now();
        requestAnimationFrame(function (now) { t0 = now; loop(now); });
      }
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
