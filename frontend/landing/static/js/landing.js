(function () {
    // Segmented feature switcher in the hero -- sliding lime highlight behind
    // whichever capability is active. Purely a visual/interaction touch: all
    // four labels stay readable at all times, clicking just re-emphasizes one.
    var wrap = document.getElementById('featureSwitch');
    if (wrap) {
        var highlight = document.getElementById('featureHighlight');
        var buttons = wrap.querySelectorAll('button');
        var moveHighlight = function (btn) {
            highlight.style.width = btn.offsetWidth + 'px';
            highlight.style.transform = 'translateX(' + btn.offsetLeft + 'px)';
        };
        buttons.forEach(function (btn) {
            btn.addEventListener('click', function () {
                buttons.forEach(function (b) { b.classList.remove('is-active'); });
                btn.classList.add('is-active');
                moveHighlight(btn);
            });
        });
        requestAnimationFrame(function () { moveHighlight(wrap.querySelector('.is-active')); });
        window.addEventListener('resize', function () { moveHighlight(wrap.querySelector('.is-active')); });
    }

    if (!window.VGScribble) return;

    // small marks in the segmented switcher: draw immediately, reveal shortly
    // after load (always visible near the top of the page).
    var marks = document.querySelectorAll('.mark-scribble');
    marks.forEach(function (svg) { VGScribble.draw(svg, 50, 50, 40, 40); });
    setTimeout(function () {
        marks.forEach(function (svg, i) { setTimeout(function () { VGScribble.reveal(svg); }, i * 90); });
    }, 150);

    // bento blobs: draw in when scrolled into view, redraw with fresh jitter on hover.
    var blobs = document.querySelectorAll('.blob-scribble');
    blobs.forEach(function (svg) { VGScribble.draw(svg, 50, 50, 42, 38); });
    if ('IntersectionObserver' in window) {
        var blobIO = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry, i) {
                if (entry.isIntersecting) {
                    setTimeout(function () { VGScribble.reveal(entry.target); }, i * 120);
                    blobIO.unobserve(entry.target);
                }
            });
        }, { threshold: 0.5 });
        blobs.forEach(function (svg) { blobIO.observe(svg); });
    } else {
        blobs.forEach(function (svg) { VGScribble.reveal(svg); });
    }
    document.querySelectorAll('.bento-tile').forEach(function (tile) {
        var svg = tile.querySelector('.blob-scribble');
        if (!svg) return;
        tile.addEventListener('mouseenter', function () {
            VGScribble.draw(svg, 50, 50, 42, 38);
            requestAnimationFrame(function () { VGScribble.reveal(svg); });
        });
    });
})();
