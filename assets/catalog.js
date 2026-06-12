(async function () {
  const page = document.body.dataset.page;
  if (!['catalog', 'search', 'new'].includes(page)) return;

  const helpers = window.catalogHelpers;
  const qInput = helpers.qs('#q');
  const typeInput = helpers.qs('#type');
  const genreInput = helpers.qs('#genre');
  const ownerInput = helpers.qs('#owner');
  const isNewInput = helpers.qs('#isNew');
  const results = helpers.qs('#results');
  const resultCount = helpers.qs('#resultCount');
  const shareButton = helpers.qs('#shareSearch');

  const state = {
    q: helpers.params.get('q') || '',
    type: helpers.params.get('type') || '',
    genre: helpers.params.get('genre') || '',
    owner: helpers.params.get('owner') || '',
    isNew: helpers.params.get('isNew') === '1' || page === 'new',
  };

  if (qInput) qInput.value = state.q;
  if (typeInput) typeInput.value = state.type;
  if (isNewInput) isNewInput.checked = state.isNew;

  let items = [];
  try {
    const res = await fetch('data/catalog.json', { cache: 'no-store' });
    if (!res.ok) throw new Error('catalog.json konnte nicht geladen werden');
    const data = await res.json();
    items = data.items || [];
  } catch (error) {
    results.innerHTML = '<p>Fehler beim Laden der Katalogdaten.</p>';
    resultCount.textContent = String(error);
    return;
  }

  const genres = [...new Set(items.map((i) => (i.genre || '').trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'de'));
  const owners = [...new Set(items.map((i) => (i.owner || '').trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'de'));

  for (const g of genres) {
    const option = document.createElement('option');
    option.value = g;
    option.textContent = g;
    genreInput?.append(option);
  }
  for (const o of owners) {
    const option = document.createElement('option');
    option.value = o;
    option.textContent = o;
    ownerInput?.append(option);
  }

  if (genreInput) genreInput.value = state.genre;
  if (ownerInput) ownerInput.value = state.owner;

  function card(item) {
    const safeTitle = helpers.escapeHtml(item.title || 'Ohne Titel');
    const safeAuthor = helpers.escapeHtml(item.author || 'Unbekannt');
    const safeCover = helpers.escapeHtml(item.cover_url || 'assets/placeholder-cover.svg');
    const safeGenre = helpers.escapeHtml(item.genre || 'Ohne Genre');
    const statusPill = item.status === 'Keine ISBN ermittelt' ? '<span class="pill warn">Keine ISBN ermittelt</span>' : '';

    const returnTo = encodeURIComponent(window.location.pathname.split('/').pop() + window.location.search);
    const detailLink = `detail.html?id=${encodeURIComponent(item.id)}&returnTo=${returnTo}`;

    return `
      <article class="media-card">
        <a class="cover-wrap" href="${detailLink}">
          <img src="${safeCover}" alt="Cover: ${safeTitle}" loading="lazy" referrerpolicy="no-referrer" onerror="this.src='assets/placeholder-cover.svg'" />
        </a>
        <div class="card-body">
          <h3 class="card-title"><a href="${detailLink}">${safeTitle}</a></h3>
          <p class="card-meta">${safeAuthor}</p>
          <div class="pill-row">
            <span class="pill">${helpers.escapeHtml(item.type || 'Buch')}</span>
            <span class="pill">${safeGenre}</span>
            ${item.is_new ? '<span class="pill">Neu</span>' : ''}
            ${statusPill}
          </div>
        </div>
      </article>
    `;
  }

  function applyFilters() {
    state.q = qInput?.value?.trim() || '';
    state.type = typeInput?.value || '';
    state.genre = genreInput?.value || '';
    state.owner = ownerInput?.value || '';
    state.isNew = (isNewInput?.checked || false) || page === 'new';

    const q = state.q.toLowerCase();
    const filtered = items.filter((item) => {
      if (state.isNew && !item.is_new) return false;
      if (state.type && (item.type || '') !== state.type) return false;
      if (state.genre && (item.genre || '') !== state.genre) return false;
      if (state.owner && (item.owner || '') !== state.owner) return false;
      if (q && !(item.search_text || '').toLowerCase().includes(q)) return false;
      return true;
    });

    helpers.setQuery({
      q: state.q,
      type: state.type,
      genre: state.genre,
      owner: state.owner,
      isNew: state.isNew && page !== 'new' ? '1' : '',
    });

    resultCount.textContent = `${filtered.length} Treffer`;
    if (!filtered.length) {
      results.innerHTML = '<p>Keine Treffer gefunden.</p>';
      return;
    }

    results.innerHTML = filtered.map(card).join('');
  }

  [qInput, typeInput, genreInput, ownerInput, isNewInput]
    .filter(Boolean)
    .forEach((el) => {
      const eventName = el.tagName === 'INPUT' && el.type === 'search' ? 'input' : 'change';
      el.addEventListener(eventName, applyFilters);
    });

  if (shareButton) {
    shareButton.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(window.location.href);
        shareButton.textContent = 'Link kopiert';
        window.setTimeout(() => (shareButton.textContent = 'Suchlink kopieren'), 1200);
      } catch {
        shareButton.textContent = 'Kopieren nicht möglich';
      }
    });
  }

  applyFilters();
})();
