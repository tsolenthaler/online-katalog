const DATA_CANDIDATES = ["data/catalog.json", "data/catalog_sample.json"];

const elements = {
  bookCount: document.querySelector("#landingBookCount"),
  authorCount: document.querySelector("#landingAuthorCount"),
  genreCount: document.querySelector("#landingGenreCount"),
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

function renderStats(items) {
  elements.bookCount.textContent = String(items.length);
  elements.authorCount.textContent = String(countUniqueAuthors(items));
  elements.genreCount.textContent = String(countUniqueGenres(items));
}

function renderFallback() {
  elements.bookCount.textContent = "0";
  elements.authorCount.textContent = "0";
  elements.genreCount.textContent = "0";
}

async function initLandingStats() {
  if (!elements.bookCount || !elements.authorCount || !elements.genreCount) {
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
