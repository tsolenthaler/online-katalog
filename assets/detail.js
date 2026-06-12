(async function () {
  if (document.body.dataset.page !== 'detail') return;

  const helpers = window.catalogHelpers;
  const detailHost = helpers.qs('#detailView');
  const backLink = helpers.qs('#backToResults');
  const id = helpers.params.get('id');
  const returnTo = helpers.params.get('returnTo');

  if (returnTo) {
    backLink.href = returnTo;
  }

  if (!id) {
    detailHost.innerHTML = '<p>Kein Medium ausgewählt.</p>';
    return;
  }

  try {
    const res = await fetch('data/catalog.json', { cache: 'no-store' });
    if (!res.ok) throw new Error('Katalogdaten konnten nicht geladen werden.');
    const data = await res.json();
    const item = (data.items || []).find((entry) => String(entry.id) === id);

    if (!item) {
      detailHost.innerHTML = '<p>Medium nicht gefunden.</p>';
      return;
    }

    const title = item.title || 'Unbekanntes Medium';
    const author = item.author || 'Unbekannt';
    const isbn = (item.isbn || '').trim();
    const cover = item.cover_url || 'assets/placeholder-cover.svg';
    const description = item.description || 'Keine Beschreibung vorhanden.';
    const summary = `${title} von ${author} - ${description.slice(0, 120)}`;
    const googleBooksLink = item.google_books_link || (isbn ? `https://books.google.ch/books?vid=ISBN${encodeURIComponent(isbn)}&hl=de` : '');

    document.title = `${title} - Bibliothek Stein AR`;

    const ogTitle = document.querySelector('meta[property="og:title"]');
    const ogDescription = document.querySelector('meta[property="og:description"]');
    const ogImage = document.querySelector('meta[property="og:image"]');

    if (ogTitle) ogTitle.setAttribute('content', title);
    if (ogDescription) ogDescription.setAttribute('content', summary);
    if (ogImage) ogImage.setAttribute('content', cover);

    const encodedText = encodeURIComponent(`${title} - ${author}\n${window.location.href}`);
    const waLink = `https://wa.me/?text=${encodedText}`;

    detailHost.innerHTML = `
      <article class="detail-grid">
        <div>
          <img src="${helpers.escapeHtml(cover)}" alt="Cover von ${helpers.escapeHtml(title)}" onerror="this.src='assets/placeholder-cover.svg'" />
        </div>
        <div>
          <h1>${helpers.escapeHtml(title)}</h1>
          <p class="lead">${helpers.escapeHtml(author)}</p>
          <p>${helpers.escapeHtml(description)}</p>

          <ul class="meta-list">
            <li><strong>ISBN:</strong> ${helpers.escapeHtml(isbn || '-')}</li>
            <li><strong>Typ:</strong> ${helpers.escapeHtml(item.type || 'Buch')}</li>
            <li><strong>Genre:</strong> ${helpers.escapeHtml(item.genre || '-')}</li>
            <li><strong>Besitzer:</strong> ${helpers.escapeHtml(item.owner || '-')}</li>
            <li><strong>Status:</strong> ${helpers.escapeHtml(item.status || 'OK')}</li>
          </ul>

          <div class="link-row">
            ${item.openlibrary_link ? `<a class="button-secondary" href="${helpers.escapeHtml(item.openlibrary_link)}" target="_blank" rel="noopener">Open Library</a>` : ''}
            ${googleBooksLink ? `<a class="button-secondary" href="${helpers.escapeHtml(googleBooksLink)}" target="_blank" rel="noopener">Google Books</a>` : ''}
            ${item.dnb_link ? `<a class="button-secondary" href="${helpers.escapeHtml(item.dnb_link)}" target="_blank" rel="noopener">DNB</a>` : ''}
          </div>

          <div class="share-row">
            <a class="button-secondary" href="${waLink}" target="_blank" rel="noopener">Auf WhatsApp teilen</a>
            <button id="copyDetailLink" type="button">Link kopieren</button>
          </div>
        </div>
      </article>
    `;

    const copyButton = helpers.qs('#copyDetailLink');
    copyButton?.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(window.location.href);
        copyButton.textContent = 'Link kopiert';
        window.setTimeout(() => (copyButton.textContent = 'Link kopieren'), 1200);
      } catch {
        copyButton.textContent = 'Kopieren nicht möglich';
      }
    });
  } catch (error) {
    detailHost.innerHTML = `<p>${helpers.escapeHtml(String(error))}</p>`;
  }
})();
