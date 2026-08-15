/**
 * Shared "crayon scribble" generator, loaded from one place
 * (/legal/static/js/scribble.js) by landing/client — same cross-app
 * static-sharing pattern already used by cookie-consent.js. Lives under
 * legal/static/js for now rather than a new dedicated mount, since that's
 * the one shared-resource location this project already has.
 *
 * Paths are generated (a looping, jittered curve through a center point),
 * not hand-authored bezier data, so every instance looks like a genuinely
 * different hand-drawn mark instead of one fixed decoration repeated.
 */
(function () {
    var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function scribblePath(cx, cy, rx, ry, loops) {
        var steps = Math.max(48, loops * 28);
        var pts = [];
        for (var i = 0; i <= steps; i++) {
            var t = i / steps;
            var angle = t * loops * Math.PI * 2;
            var wobble = 0.7 + Math.random() * 0.5;
            pts.push((cx + Math.cos(angle) * rx * wobble).toFixed(1) + ',' + (cy + Math.sin(angle) * ry * wobble).toFixed(1));
        }
        return 'M' + pts.join(' L ');
    }

    // Generates a fresh path into svg's <path> child and arms it for a
    // stroke-dashoffset reveal, but keeps it hidden (call reveal() to show).
    function draw(svg, cx, cy, rx, ry) {
        var loops = parseInt(svg.dataset.loops || '4', 10);
        var path = svg.querySelector('path');
        if (!path) return null;
        path.setAttribute('d', scribblePath(cx, cy, rx, ry, loops));
        if (svg.dataset.color) path.style.stroke = svg.dataset.color;
        var len = path.getTotalLength();
        path.style.transition = 'none';
        path.style.strokeDasharray = len;
        path.style.strokeDashoffset = len;
        path.getBoundingClientRect(); // force layout so the hidden state above commits before re-enabling the transition
        path.style.transition = 'stroke-dashoffset 1.1s ease';
        return path;
    }

    function reveal(svg) {
        var path = svg.querySelector('path');
        if (!path) return;
        if (reduceMotion) path.style.transition = 'none';
        path.style.strokeDashoffset = 0;
    }

    window.VGScribble = { path: scribblePath, draw: draw, reveal: reveal, reduceMotion: reduceMotion };
})();
