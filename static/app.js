const dealsNode = document.getElementById('deals');
const connectorStatusNode = document.getElementById('connector-status');
const refreshStatusNode = document.getElementById('refresh-status');
const dealCountNode = document.getElementById('deal-count');
const dealTemplate = document.getElementById('deal-template');

function qs() {
  const params = new URLSearchParams();
  const platform = document.getElementById('platform').value;
  const sortBy = document.getElementById('sort_by').value;
  const minPrice = document.getElementById('min_price').value;
  const maxPrice = document.getElementById('max_price').value;
  if (platform) params.set('platform', platform);
  if (sortBy) params.set('sort_by', sortBy);
  if (minPrice) params.set('min_price', minPrice);
  if (maxPrice) params.set('max_price', maxPrice);
  params.set('limit', '150');
  return params.toString();
}

function formatMoney(value, currency) {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(value);
  } catch {
    return `${currency} ${value.toFixed(2)}`;
  }
}

function renderDeals(items) {
  dealsNode.innerHTML = '';
  dealCountNode.textContent = `${items.length} deals shown`;
  for (const item of items) {
    const fragment = dealTemplate.content.cloneNode(true);
    const img = fragment.querySelector('.deal-image');
    img.src = item.image_url || 'https://placehold.co/800x600?text=No+Image';
    img.loading = 'lazy';
    fragment.querySelector('.platform').textContent = item.platform;
    fragment.querySelector('.method').textContent = item.source_method;
    const title = fragment.querySelector('.deal-title');
    title.href = item.url;
    title.textContent = item.title;
    fragment.querySelector('.price').textContent = formatMoney(item.price, item.currency);
    fragment.querySelector('.ppc').textContent = item.price_per_card
      ? `${formatMoney(item.price_per_card, item.currency)}/card`
      : 'count unclear';
    fragment.querySelector('.metrics').textContent =
      `score ${item.score} • est cards ${item.estimated_card_count} • confidence ${item.confidence}`;
    fragment.querySelector('.timestamps').textContent =
      `last seen ${new Date(item.last_seen_at).toLocaleString()}`;
    dealsNode.appendChild(fragment);
  }
}

function renderStatuses(items) {
  connectorStatusNode.innerHTML = '';
  for (const item of items) {
    const row = document.createElement('div');
    row.className = 'connector-row';
    const stateClass = `state-${item.state}`;
    row.innerHTML = `
      <strong>${item.connector_name}</strong>
      <span class="${stateClass}">${item.state}</span>
      <span>${item.details || ''}${item.last_error ? ` — ${item.last_error}` : ''}</span>
    `;
    connectorStatusNode.appendChild(row);
  }
}

async function refresh() {
  refreshStatusNode.textContent = 'Refreshing…';
  try {
    const [dealsResp, statusResp] = await Promise.all([
      fetch(`/api/deals?${qs()}`),
      fetch('/api/status'),
    ]);
    const deals = await dealsResp.json();
    const statuses = await statusResp.json();
    renderDeals(deals);
    renderStatuses(statuses);
    refreshStatusNode.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    refreshStatusNode.textContent = `Refresh failed: ${error}`;
  }
}

document.getElementById('apply').addEventListener('click', refresh);
refresh();
setInterval(refresh, 20000);
