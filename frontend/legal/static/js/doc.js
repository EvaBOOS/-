/**
 * Shared behaviour for the /legal/docs/* pages: highlights the current
 * clause in the "На этой странице" mini-TOC as the reader scrolls, smooth-
 * scrolls when a TOC link is clicked, and wires the print button.
 */
(function () {
    var tocLinks = Array.prototype.slice.call(document.querySelectorAll('.toc-clauses a'));
    if (tocLinks.length) {
        var sections = tocLinks
            .map(function (a) { return document.querySelector(a.getAttribute('href')); })
            .filter(Boolean);

        if ('IntersectionObserver' in window && sections.length) {
            var current = null;
            var setCurrent = function (li) {
                if (li === current) return;
                current = li;
                tocLinks.forEach(function (a, i) {
                    a.classList.toggle('is-current', sections[i] === li);
                });
            };
            var io = new IntersectionObserver(
                function (entries) {
                    entries.forEach(function (entry) {
                        if (entry.isIntersecting) setCurrent(entry.target);
                    });
                },
                { rootMargin: '-15% 0px -70% 0px' }
            );
            sections.forEach(function (li) { io.observe(li); });
        }

        tocLinks.forEach(function (a) {
            a.addEventListener('click', function (e) {
                var target = document.querySelector(a.getAttribute('href'));
                if (!target) return;
                e.preventDefault();
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                history.replaceState(null, '', a.getAttribute('href'));
            });
        });
    }

    var printBtn = document.querySelector('.doc-print-btn');
    if (printBtn) {
        printBtn.addEventListener('click', function () { window.print(); });
    }
})();
