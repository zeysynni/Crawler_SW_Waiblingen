// Auto-expand Bootstrap accordions so collapsed content is visible to the
// crawl agent.
//
// Why: accordion answers are in the DOM but `display:none` while collapsed, so
// they are EXCLUDED from the accessibility snapshot the agent reads — it sees
// only the heading. Relying on the LLM to click every "+" is unreliable.
//
// How: the robust mechanism is a CSS override with `!important` that forces all
// accordion panels visible. This beats Bootstrap's class toggling AND its
// single-open (`data-bs-parent`) logic, which otherwise re-collapses panels
// just after we open them. We also flip classes/aria as a backup.
//
// Loaded via `@playwright/mcp --init-script` (see mcp_params.py): runs in every
// page before the page's own scripts, so we re-apply as the DOM appears.
(() => {
  function injectStyle() {
    if (document.getElementById('__crawler_expand_css')) return;
    const style = document.createElement('style');
    style.id = '__crawler_expand_css';
    style.textContent =
      '.accordion-collapse, .accordion-collapse.collapse {' +
      ' display: block !important; height: auto !important;' +
      ' visibility: visible !important; overflow: visible !important; }';
    (document.head || document.documentElement).appendChild(style);
  }

  function expandAll() {
    try {
      injectStyle();
      document.querySelectorAll('.accordion-collapse').forEach((p) => p.classList.add('show'));
      document.querySelectorAll('.accordion-button').forEach((b) => {
        b.classList.remove('collapsed');
        b.setAttribute('aria-expanded', 'true');
      });
    } catch (e) {
      /* ignore — nothing to expand yet */
    }
  }

  function start() {
    expandAll();
    // Catch late-rendered / Bootstrap-initialized accordions.
    [200, 800, 1600, 3000].forEach((t) => setTimeout(expandAll, t));
    try {
      new MutationObserver(() => expandAll()).observe(document.documentElement, {
        childList: true,
        subtree: true,
      });
    } catch (e) {
      /* MutationObserver unavailable — the timed retries still cover it */
    }
  }

  injectStyle(); // earliest possible, before the page's own scripts run
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
