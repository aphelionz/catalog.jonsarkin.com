/**
 * Sarkin-Jeppesen — Theme JS
 * Citation clipboard, share button, and zoom interaction.
 */
(function () {
    'use strict';

    function esc(s) {
        var d = document.createElement('div');
        d.appendChild(document.createTextNode(s));
        return d.innerHTML;
    }

    // ── Mobile nav toggle ──
    var navToggle = document.querySelector('.nav-toggle');
    var siteNav   = document.getElementById('site-nav');
    if (navToggle && siteNav) {
        navToggle.addEventListener('click', function () {
            var open = siteNav.classList.toggle('open');
            navToggle.setAttribute('aria-expanded', open);
        });
    }

    // ── Mark parent nav active when a subnav link matches the current URL ──
    (function () {
        var loc = window.location.pathname + window.location.search;
        document.querySelectorAll('.site-nav li > ul a').forEach(function (a) {
            var href = a.getAttribute('href');
            if (href && loc === href) {
                var parentLi = a.closest('.site-nav > ul > li');
                if (parentLi) parentLi.classList.add('active');
            }
        });
    })();

    // ── Submenu click-to-toggle ──
    var parentItems = document.querySelectorAll('.site-nav li');
    parentItems.forEach(function (li) {
        var sub = li.querySelector(':scope > ul');
        if (!sub) return;

        var btn = document.createElement('button');
        btn.className = 'submenu-toggle';
        btn.setAttribute('aria-expanded', 'false');
        btn.setAttribute('aria-label', 'Toggle submenu');
        var parentLink = li.querySelector(':scope > a');
        parentLink.after(btn);

        function toggleSubmenu(e) {
            e.preventDefault();
            e.stopPropagation();
            var wasOpen = li.classList.contains('submenu-open');

            // close all other open submenus
            document.querySelectorAll('.site-nav .submenu-open').forEach(function (el) {
                el.classList.remove('submenu-open');
                el.querySelector(':scope > .submenu-toggle').setAttribute('aria-expanded', 'false');
            });

            if (!wasOpen) {
                li.classList.add('submenu-open');
                btn.setAttribute('aria-expanded', 'true');
            }
        }

        btn.addEventListener('click', toggleSubmenu);
        parentLink.addEventListener('click', toggleSubmenu);
    });

    // close submenus on outside click
    document.addEventListener('click', function (e) {
        if (!e.target.closest('.site-nav')) {
            document.querySelectorAll('.site-nav .submenu-open').forEach(function (el) {
                el.classList.remove('submenu-open');
                el.querySelector(':scope > .submenu-toggle').setAttribute('aria-expanded', 'false');
            });
        }
    });

    // close submenus on Escape
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            document.querySelectorAll('.site-nav .submenu-open').forEach(function (el) {
                el.classList.remove('submenu-open');
                var toggle = el.querySelector(':scope > .submenu-toggle');
                toggle.setAttribute('aria-expanded', 'false');
                toggle.focus();
            });
        }
    });

    // ── CITE button: copy Chicago-style citation ──
    var citeBtn = document.getElementById('copy-citation');
    if (citeBtn) {
        citeBtn.addEventListener('click', function () {
            if (!navigator.clipboard || !navigator.clipboard.writeText) return;

            // Build citation from visible record data
            var catEl    = document.getElementById('record-title');
            var year     = document.querySelector('.record-facts .year dd');

            var idText    = catEl ? catEl.textContent.trim() : '';
            var yearText  = year ? year.textContent.trim() : '';
            var accessed  = new Date().toISOString().slice(0, 10);
            var url       = window.location.href;

            var citation = 'Sarkin, Jon.';
            if (idText) citation += ' ' + idText + '.';
            if (yearText && yearText !== '\u2014') citation += ' ' + yearText + '.';
            citation += ' The Jon Sarkin Catalog.';
            citation += ' Accessed ' + accessed + '. ' + url + '.';

            navigator.clipboard.writeText(citation).then(function () {
                citeBtn.textContent = '[copied]';
                setTimeout(function () { citeBtn.textContent = '[cite]'; }, 1200);
            });
        });
    }

    // ── Global share button (header) ──
    var shareBtn = document.getElementById('global-share');
    if (shareBtn) {
        shareBtn.addEventListener('click', function (e) {
            e.preventDefault();
            var url = window.location.href;
            if (navigator.share) {
                navigator.share({ title: document.title, url: url });
            } else if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(url);
                shareBtn.textContent = '[copied!]';
                setTimeout(function () { shareBtn.textContent = '[share]'; }, 1500);
            }
        });
    }

    // ── Similar pieces (async) ──
    var similarSection = document.getElementById('similar-pieces');
    if (similarSection) {
        var itemId = similarSection.getAttribute('data-item-id');
        var site = similarSection.getAttribute('data-site');
        var endpoint = '/similar/' + itemId + '/json';
        if (site) endpoint += '?site=' + encodeURIComponent(site);

        fetch(endpoint)
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
            .then(function (data) {
                var results = (data.results || []).slice(0, 6);
                if (!results.length) return;

                var grid = similarSection.querySelector('.similar-grid');
                results.forEach(function (item) {
                    var a = document.createElement('a');
                    a.href = item.url || '#';
                    a.className = 'similar-card';

                    var img = document.createElement('img');
                    img.src = item.thumbnail;
                    img.alt = item.title;
                    img.loading = 'lazy';
                    a.appendChild(img);

                    var span = document.createElement('span');
                    span.textContent = item.title;
                    a.appendChild(span);

                    grid.appendChild(a);
                });

                similarSection.removeAttribute('hidden');

                // Easter egg: preload hi-res originals, crossfade on hover
                if (!('ontouchstart' in window)) {
                    var cards = grid.querySelectorAll('.similar-card');
                    cards.forEach(function (card, i) {
                        var original = results[i] && results[i].original;
                        if (!original) return;

                        var thumb = card.querySelector('img');
                        thumb.style.position = 'relative';

                        // Create overlay image for crossfade
                        var overlay = document.createElement('img');
                        overlay.className = 'similar-hires';
                        overlay.alt = thumb.alt;
                        overlay.draggable = false;
                        thumb.parentNode.insertBefore(overlay, thumb.nextSibling);

                        // Preload then set src
                        var preload = new Image();
                        preload.onload = function () {
                            overlay.src = original;
                            card.dataset.hiresReady = '1';
                        };
                        preload.src = original;

                        card.addEventListener('mouseenter', function () {
                            if (card.dataset.hiresReady) overlay.style.opacity = '1';
                        });
                        card.addEventListener('mouseleave', function () {
                            overlay.style.opacity = '';
                        });
                    });
                }
            })
            .catch(function () { /* service down — section stays hidden */ });
    }

    // ── Iconographic profile (async, detail page) ──
    var iconSection = document.getElementById('iconographic-profile');
    if (iconSection) {
        var iconItemId = iconSection.getAttribute('data-item-id');
        var iconSite = iconSection.getAttribute('data-site');
        var iconEndpoint = '/iconography/' + iconItemId + '/json';

        fetch(iconEndpoint)
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
            .then(function (data) {
                if (!data.motifs || !data.motifs.length) return;

                var html = '<h4 class="icon-profile-heading">Iconographic Profile</h4>';
                html += '<table class="motif-frequency">';
                html += '<thead><tr>';
                html += '<th>Motif</th>';
                html += '<th>Corpus Frequency</th>';
                html += '<th>% of Works</th>';
                html += '</tr></thead>';
                html += '<tbody>';

                var corpusSize = data.corpus_size || 0;
                var formatted = corpusSize.toLocaleString();

                var browseBase = '/s/' + (iconSite || 'main') + '/item'
                    + '?property%5B0%5D%5Bproperty%5D=dcterms%3Asubject'
                    + '&property%5B0%5D%5Btype%5D=eq&property%5B0%5D%5Btext%5D=';

                data.motifs.forEach(function (m) {
                    var href = browseBase + encodeURIComponent(m.motif);
                    html += '<tr>';
                    html += '<td><a href="' + href + '">' + esc(m.motif) + '</a></td>';
                    html += '<td>' + m.corpus_frequency.toLocaleString() + ' of ' + formatted + '</td>';
                    html += '<td>' + m.corpus_percentage.toFixed(1) + '%</td>';
                    html += '</tr>';
                });

                html += '</tbody></table>';
                html += '<p class="icon-profile-note">';
                html += 'Based on motif distribution across ' + formatted + ' cataloged works.';
                html += '</p>';

                iconSection.innerHTML = html;
                iconSection.removeAttribute('hidden');
            })
            .catch(function () { /* service down — section stays hidden */ });
    }

    // ── Lexical profile (async, detail page) ──
    var lexSection = document.getElementById('lexical-profile');
    if (lexSection) {
        var lexItemId = lexSection.getAttribute('data-item-id');
        var lexSite = lexSection.getAttribute('data-site');
        var lexEndpoint = '/lexical-profile/' + lexItemId + '/json';

        fetch(lexEndpoint)
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
            .then(function (data) {
                if (!data.words || !data.words.length) return;

                var corpusSize = data.corpus_size || 0;
                var formatted = corpusSize.toLocaleString();
                var totalWords = data.total_unique_words || data.words.length;

                var html = '<h4 class="lex-profile-heading">Lexical Profile</h4>';
                html += '<table class="word-frequency">';
                html += '<thead><tr>';
                html += '<th>Word</th>';
                html += '<th>Corpus Frequency</th>';
                html += '<th>% of Works</th>';
                html += '</tr></thead>';
                html += '<tbody>';

                var searchBase = '/s/' + (lexSite || 'main') + '/item'
                    + '?fulltext_search=';

                data.words.forEach(function (w) {
                    var href = searchBase + encodeURIComponent(w.word);
                    html += '<tr>';
                    html += '<td><a href="' + href + '">' + esc(w.word) + '</a></td>';
                    html += '<td>' + w.corpus_frequency.toLocaleString()
                          + ' of ' + formatted + '</td>';
                    html += '<td>' + w.corpus_percentage.toFixed(1) + '%</td>';
                    html += '</tr>';
                });

                html += '</tbody></table>';
                html += '<p class="lex-profile-note">';
                if (totalWords > data.words.length) {
                    html += 'Showing ' + data.words.length + ' rarest words'
                          + ' (of ' + totalWords + ' unique). ';
                }
                html += 'Based on word distribution across '
                      + formatted + ' cataloged works.';
                html += '</p>';

                lexSection.innerHTML = html;
                lexSection.removeAttribute('hidden');
            })
            .catch(function () { /* service down — section stays hidden */ });
    }

    // ── Iconographic badges (async, browse page) ──
    var cards = document.querySelectorAll('.chart-card[data-item-id]');
    if (cards.length) {
        var cardIds = [];
        cards.forEach(function (card) {
            var id = card.getAttribute('data-item-id');
            if (id) cardIds.push(id);
        });

        if (cardIds.length) {
            var batchEndpoint = '/iconography/batch/json?ids=' + cardIds.join(',');
            fetch(batchEndpoint)
                .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
                .then(function (data) {
                    var items = data.items || [];
                    var classMap = {};
                    var labels = { 1: 'I', 2: 'II', 3: 'III', 4: 'IV', 5: 'V' };
                    items.forEach(function (item) {
                        classMap[String(item.omeka_item_id)] = item.class_number;
                    });

                    cards.forEach(function (card) {
                        var id = card.getAttribute('data-item-id');
                        var cls = classMap[id];
                        if (!cls) return;

                        var factsEl = card.querySelector('.card-facts');
                        if (!factsEl) return;

                        factsEl.style.gridTemplateColumns = '1fr 1fr auto';

                        var badge = document.createElement('div');
                        badge.className = 'card-fact card-class-badge';
                        var label = document.createElement('div');
                        label.className = 'card-fact-label';
                        label.textContent = '\u00A0';
                        badge.appendChild(label);
                        var value = document.createElement('div');
                        value.className = 'card-fact-value';
                        value.textContent = labels[cls] || '';
                        badge.appendChild(value);
                        factsEl.appendChild(badge);
                    });
                })
                .catch(function () { /* service down — no badges */ });
        }
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

    // ── Home page: live catalog count ──
    var countEl = document.querySelector('[data-live-count]');
    if (countEl) {
        fetch('/api/items?per_page=1')
            .then(function (r) {
                if (!r.ok) return;
                var total = r.headers.get('Omeka-S-Total-Results');
                if (total) countEl.textContent = parseInt(total, 10).toLocaleString();
            })
            .catch(function () { /* keep fallback */ });
    }

    // ── PRINT QR CODE: render QR for the current item URL ──
    // Deferred to ensure qrcode library is fully initialised.
    $(function () {
        var qrEl = document.getElementById('item-qr');
        if (qrEl && typeof qrcode === 'function') {
            var qrLink = qrEl.parentElement.querySelector('a');
            if (qrLink) {
                var qr = qrcode(0, 'M');
                qr.addData(qrLink.href);
                qr.make();
                var img = document.createElement('img');
                img.src = qr.createDataURL(4, 0);
                img.alt = 'QR code linking to this catalog entry';
                img.width = 120;
                img.height = 120;
                qrEl.parentElement.replaceChild(img, qrEl);
            }
        }
    });
}());
