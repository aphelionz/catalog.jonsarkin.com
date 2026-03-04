/**
 * Sarkin-Jeppesen — Theme JS
 * Citation clipboard, share button, and zoom interaction.
 */
(function () {
    'use strict';

    // ── CITE button: copy Chicago-style citation ──
    var citeBtn = document.getElementById('copy-citation');
    if (citeBtn) {
        citeBtn.addEventListener('click', function () {
            if (!navigator.clipboard || !navigator.clipboard.writeText) return;

            // Build citation from visible record data
            var title   = document.getElementById('record-title');
            var catId   = document.querySelector('.catalog-id');
            var year    = document.querySelector('.record-facts .year dd');

            var titleText = title ? title.textContent.trim() : 'Untitled';
            // Title case the uppercase title
            titleText = titleText.charAt(0).toUpperCase() + titleText.slice(1).toLowerCase();

            var yearText  = year ? year.textContent.trim() : '';
            var idText    = catId ? catId.textContent.trim().replace('#', '') : '';
            var accessed  = new Date().toISOString().slice(0, 10);
            var url       = window.location.href;

            var citation = 'Sarkin, Jon. ' + titleText + '.';
            if (yearText && yearText !== '\u2014') citation += ' ' + yearText + '.';
            citation += ' The Jon Sarkin Catalog';
            if (idText && idText !== '\u2014') citation += ', cat. no. ' + idText;
            citation += '. Accessed ' + accessed + '. ' + url + '.';

            navigator.clipboard.writeText(citation).then(function () {
                citeBtn.textContent = 'COPIED';
                setTimeout(function () { citeBtn.textContent = 'CITE'; }, 1200);
            });
        });
    }

    // ── Share button ──
    var shareBtn = document.querySelector('[data-action="share"]');
    if (shareBtn) {
        shareBtn.addEventListener('click', function () {
            if (navigator.share) {
                var title = document.getElementById('record-title');
                navigator.share({
                    title: title ? title.textContent.trim() : document.title,
                    url: window.location.href
                });
            } else {
                // Fallback: copy URL
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(window.location.href);
                }
            }
        });
    }

    // ── Zoom follow cursor (2× magnification) ──
    var zoom = document.querySelector('.zoom');
    var media = document.querySelector('.record-media');
    if (zoom && media) {
        var zoomImg = zoom.querySelector('img');
        var mainImg = media.querySelector('img');
        var ZOOM = 2;
        var CIRCLE = 200;

        if (mainImg && zoomImg) {
            media.addEventListener('mousemove', function (e) {
                var rect = media.getBoundingClientRect();
                var x = (e.clientX - rect.left) / rect.width;   // 0–1
                var y = (e.clientY - rect.top) / rect.height;   // 0–1
                // Render image at 2× main image size for true magnification
                var imgW = mainImg.offsetWidth * ZOOM;
                var imgH = mainImg.offsetHeight * ZOOM;
                zoomImg.style.width = imgW + 'px';
                zoomImg.style.height = imgH + 'px';
                // Centre the cursor point in the circle
                zoomImg.style.left = -(x * imgW - CIRCLE / 2) + 'px';
                zoomImg.style.top = -(y * imgH - CIRCLE / 2) + 'px';
            });
        }
    }
}());
