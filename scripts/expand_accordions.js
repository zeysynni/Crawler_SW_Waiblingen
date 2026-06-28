// Auto-expand Bootstrap accordions so collapsed content is visible to the
// crawl agent.
//
// Why: accordion answers are present in the DOM but `display:none` while
// collapsed, so they are EXCLUDED from the accessibility snapshot the agent
// reads — it sees only the heading. Relying on the LLM to click every "+" is
// unreliable. This script force-opens every accordion deterministically.
//
// Loaded via `@playwright/mcp --init-script` (see mcp_params.py): it runs in
// every page before the page's own scripts, so we re-run it as the DOM appears.
(() => {
  function expandAll() {
    try {
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
      const obs = new MutationObserver(() => expandAll());
      obs.observe(document.documentElement, { childList: true, subtree: true });
    } catch (e) {
      /* MutationObserver unavailable — the timed retries still cover it */
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
