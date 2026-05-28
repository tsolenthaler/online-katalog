const DATA_CANDIDATES = ["data/catalog.json", "data/catalog_sample.json"];

const elements = {
  bookCount: document.querySelector("#landingBookCount"),
  authorCount: document.querySelector("#landingAuthorCount"),
  genreCount: document.querySelector("#landingGenreCount"),
  topAuthors: document.querySelector("#landingTopAuthors"),
  topGenres: document.querySelector("#landingTopGenres"),
};

async function loadCatalogItems() {
  for (const path of DATA_CANDIDATES) {
    try {
      const response = await fetch(path, { cache: "no-store" });
      if (!response.ok) {
        continue;
      }
      const payload = await response.json();
      if (payload && Array.isArray(payload.items)) {
        return payload.items;
      }
    } catch (_err) {
      // Try next candidate silently.
    }
  }

  throw new Error("Katalogdaten konnten nicht geladen werden.");
}

function countUniqueAuthors(items) {
  return new Set(items.map((item) => String(item.author || "").trim()).filter(Boolean)).size;
}

function countUniqueGenres(items) {
  return new Set(
    items
      .flatMap((item) => (Array.isArray(item.genres) ? item.genres : []))
      .map((genre) => String(genre || "").trim())
      .filter(Boolean)
  ).size;
}

function computeTop(items, selector, limit) {
  const map = new Map();
  for (const item of items) {
    const value = String(selector(item) || "").trim();
    if (!value) {
      continue;
    }
    map.set(value, (map.get(value) || 0) + 1);
  }

  return [...map.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "de", { sensitivity: "base" }))
    .slice(0, limit);
}

function computeTopGenres(items, limit) {
  const map = new Map();

  for (const item of items) {
    const genres = Array.isArray(item.genres) ? item.genres : [];
    for (const genre of genres) {
      const value = String(genre || "").trim();
      if (!value) {
        continue;
      }
      map.set(value, (map.get(value) || 0) + 1);
    }
  }

  return [...map.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "de", { sensitivity: "base" }))
    .slice(0, limit);
}

function renderRanking(target, entries, emptyLabel) {
  target.innerHTML = "";

  if (!entries.length) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${emptyLabel}</span><strong>0</strong>`;
    target.appendChild(li);
    return;
  }

  for (const [name, count] of entries) {
    const li = document.createElement("li");
    const label = document.createElement("span");
    const value = document.createElement("strong");
    label.textContent = name;
    value.textContent = String(count);
    li.append(label, value);
    target.append(li);
  }
}

function renderStats(items) {
  elements.bookCount.textContent = String(items.length);
  elements.authorCount.textContent = String(countUniqueAuthors(items));
  elements.genreCount.textContent = String(countUniqueGenres(items));
  renderRanking(elements.topAuthors, computeTop(items, (item) => item.author, 8), "Keine Autor-Daten");
  renderRanking(elements.topGenres, computeTopGenres(items, 8), "Keine Genre-Daten");
}

function renderFallback() {
  elements.bookCount.textContent = "0";
  elements.authorCount.textContent = "0";
  elements.genreCount.textContent = "0";
  renderRanking(elements.topAuthors, [], "Keine Autor-Daten");
  renderRanking(elements.topGenres, [], "Keine Genre-Daten");
}

async function initLandingStats() {
  if (
    !elements.bookCount ||
    !elements.authorCount ||
    !elements.genreCount ||
    !elements.topAuthors ||
    !elements.topGenres
  ) {
    return;
  }

  try {
    const items = await loadCatalogItems();
    renderStats(items);
  } catch (_err) {
    renderFallback();
  }
}

initLandingStats();
