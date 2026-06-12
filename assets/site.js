(function () {
  const params = new URLSearchParams(window.location.search);
  const navLinks = document.querySelectorAll('.nav a');

  navLinks.forEach((link) => {
    if (link.getAttribute('href') === `${window.location.pathname.split('/').pop()}`) {
      link.classList.add('active');
    }
  });

  // Keep this helper globally available for page-specific scripts.
  window.catalogHelpers = {
    qs: (selector) => document.querySelector(selector),
    qsa: (selector) => [...document.querySelectorAll(selector)],
    params,
    setQuery(next) {
      const updated = new URL(window.location.href);
      Object.entries(next).forEach(([key, value]) => {
        if (value === '' || value === null || value === undefined || value === false) {
          updated.searchParams.delete(key);
        } else {
          updated.searchParams.set(key, String(value));
        }
      });
      window.history.replaceState({}, '', updated.toString());
    },
    escapeHtml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    },
  };
})();
