/* Kiosk mode for the catalog.
 *
 * Active only when layout.phtml has set <html data-kiosk="IDLE_SECONDS"> from the
 * `kiosk` cookie, which asset/kiosk/index.html writes. Returns to the attract loop
 * after idle and neuters everything that would strand a Chromium kiosk with no
 * address bar: external links, originals, downloads, print, new tabs.
 * `?kiosk=0` on any catalog URL clears the cookie (for a phone that opened /kiosk).
 */
(function () {
  'use strict';

  var root = document.documentElement;
  var idle = parseInt(root.getAttribute('data-kiosk') || '0', 10);
  if (!idle) return;

  if (/[?&]kiosk=0\b/.test(location.search)) {
    document.cookie = 'kiosk=; path=/; max-age=0';
    root.removeAttribute('data-kiosk');
    return;
  }

  var KIOSK_URL = '/kiosk';

  /* ---- idle return ---- */
  var timer;
  function reset() {
    clearTimeout(timer);
    timer = setTimeout(function () { location.replace(KIOSK_URL); }, idle * 1000);
  }
  ['pointerdown', 'pointermove', 'touchstart', 'keydown', 'wheel', 'scroll'].forEach(function (ev) {
    window.addEventListener(ev, reset, { passive: true, capture: true });
  });
  reset();

  /* ---- dead-end links ---- */
  function blocked(a) {
    var href = a.getAttribute('href') || '';
    if (!href || href.charAt(0) === '#') return false;
    if (/^(mailto|tel|javascript):/i.test(href)) return true;
    var u;
    try { u = new URL(href, location.href); } catch (e) { return true; }
    if (u.origin !== location.origin) return true;
    return /^\/(s\/catalog\/)?(files\/original\/|api(\/|$)|submit(\/|$))/.test(u.pathname);
  }
  Array.prototype.forEach.call(document.querySelectorAll('a[href]'), function (a) {
    if (blocked(a)) a.setAttribute('data-kiosk-blocked', '');
    a.removeAttribute('target');
  });
  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (a && (a.hasAttribute('data-kiosk-blocked') || blocked(a))) {
      e.preventDefault();
      e.stopPropagation();
    }
  }, true);
  window.print = function () {};
  window.open = function () { return null; };

  /* ---- return pill ---- */
  var back = document.createElement('a');
  back.className = 'kiosk-return';
  back.href = KIOSK_URL;
  back.innerHTML = 'Back to the slideshow <span lang="ja">スライドショーに戻る</span>';
  document.body.appendChild(back);
})();
