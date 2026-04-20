/**
 * Pokemon Deal Browser Scanner
 * Paste into browser console OR save as a bookmarklet.
 * Works on: eBay, Mercari US, Yahoo Auctions Japan, Whatnot,
 *           Facebook Marketplace, Carousell, Kijiji, any page.
 *
 * Posts extracted deals to your dashboard import endpoint.
 */
(function () {
  const DASHBOARD_URL = "https://web-production-0696.up.railway.app";
  const IMPORT_URL    = DASHBOARD_URL + "/api/deals/import";

  // ─── Helpers ────────────────────────────────────────────────────────────────

  function parsePrice(str) {
    if (!str) return 0;
    const m = String(str).replace(/,/g, "").match(/[\d]+\.?\d*/);
    return m ? parseFloat(m[0]) : 0;
  }

  function extractCardCount(text) {
    const m = text.match(/(\d{2,5})\s*(?:card|cards|ct\b|count)/i);
    return m ? parseInt(m[1]) : null;
  }

  function isBulkLot(text) {
    const t = text.toLowerCase();
    return /bulk|lot|collection|binder|\d{3,}\s*card/.test(t) &&
           /pok[eé]mon/i.test(t);
  }

  // ─── Site-specific extractors ───────────────────────────────────────────────

  const EXTRACTORS = {

    // eBay search results
    "ebay.com": function () {
      const deals = [];
      document.querySelectorAll("li.s-card, li.s-item").forEach(el => {
        const title = el.querySelector("[class*='title']")?.innerText?.replace(/\n.*/s, "").trim();
        const priceText = el.querySelector("[class*='price']")?.innerText;
        const link = el.querySelector("a[href*='/itm/']")?.href?.split("?")[0];
        const img = el.querySelector("img")?.src;
        if (!title || !link || title === "Shop on eBay") return;
        const price = parsePrice(priceText);
        if (price <= 1) return;
        if (!isBulkLot(title)) return;
        deals.push({
          title, url: link, price, platform: "ebay",
          description: title, card_count: extractCardCount(title),
          image_url: img || ""
        });
      });
      return deals;
    },

    // eBay individual listing page
    "ebay.com/itm/": function () {
      const title = document.querySelector("h1.x-item-title__mainTitle span")?.innerText
                 || document.querySelector("[class*='x-item-title']")?.innerText;
      const priceText = document.querySelector("[itemprop='price']")?.getAttribute("content")
                     || document.querySelector(".x-price-primary")?.innerText;
      const description = document.querySelector("#desc_ifr")?.contentDocument?.body?.innerText?.slice(0, 500)
                       || document.querySelector("[class*='item-desc']")?.innerText?.slice(0, 500)
                       || "";
      const img = document.querySelector(".ux-image-carousel img")?.src || "";
      if (!title) return [];
      const price = parsePrice(priceText);
      if (price <= 1 || !isBulkLot(title + " " + description)) return [];
      return [{ title, url: location.href.split("?")[0], price, platform: "ebay",
                description, card_count: extractCardCount(title + " " + description), image_url: img }];
    },

    // Mercari US search results
    "mercari.com": function () {
      const deals = [];
      document.querySelectorAll("[data-testid='item-cell'], [class*='ItemCell'], [class*='item-card']").forEach(el => {
        const title = el.querySelector("[class*='name'], [class*='title']")?.innerText?.trim();
        const priceText = el.querySelector("[class*='price']")?.innerText;
        const link = el.querySelector("a")?.href;
        const img = el.querySelector("img")?.src || "";
        if (!title || !link) return;
        const price = parsePrice(priceText);
        if (price <= 1 || !isBulkLot(title)) return;
        deals.push({ title, url: link, price, platform: "other",
                     description: title, card_count: extractCardCount(title), image_url: img });
      });
      return deals;
    },

    // Yahoo Auctions Japan (buyee.jp proxy or direct)
    "buyee.jp": function () {
      const deals = [];
      document.querySelectorAll("[class*='item'], [class*='product']").forEach(el => {
        const title = el.querySelector("[class*='title'], [class*='name']")?.innerText?.trim();
        const priceText = el.querySelector("[class*='price']")?.innerText;
        const link = el.querySelector("a[href*='/item/']")?.href;
        const img = el.querySelector("img")?.src || "";
        if (!title || !link) return;
        const price = parsePrice(priceText);
        if (price <= 1) return;
        if (!/pok[eé]mon|ポケモン/i.test(title)) return;
        deals.push({ title: "[JP] " + title, url: link, price, platform: "yahoo_japan",
                     description: title, card_count: extractCardCount(title), image_url: img });
      });
      return deals;
    },

    // Whatnot
    "whatnot.com": function () {
      const deals = [];
      document.querySelectorAll("[class*='ProductCard'], [class*='ListingCard'], [class*='product-card']").forEach(el => {
        const title = el.querySelector("[class*='title'], [class*='name'], h3, h2")?.innerText?.trim();
        const priceText = el.querySelector("[class*='price']")?.innerText;
        const link = el.querySelector("a")?.href;
        const img = el.querySelector("img")?.src || "";
        if (!title || !link) return;
        const price = parsePrice(priceText);
        if (price <= 1 || !isBulkLot(title)) return;
        deals.push({ title: "[Whatnot] " + title, url: link, price, platform: "other",
                     description: title, card_count: extractCardCount(title), image_url: img });
      });
      return deals;
    },

    // Facebook Marketplace
    "facebook.com/marketplace": function () {
      const deals = [];
      document.querySelectorAll("[aria-label*='Marketplace'] a, [class*='marketplace'] a[href*='/marketplace/item/']").forEach(el => {
        const title = el.querySelector("[class*='title'], span[dir]")?.innerText?.trim()
                   || el.getAttribute("aria-label")?.trim();
        const priceText = el.querySelector("[class*='price'], [class*='Price']")?.innerText;
        const link = "https://www.facebook.com" + (el.pathname || "") || el.href;
        const img = el.querySelector("img")?.src || "";
        if (!title) return;
        const price = parsePrice(priceText);
        if (price <= 1 || !isBulkLot(title)) return;
        deals.push({ title: "[FB] " + title, url: link.split("?")[0], price, platform: "facebook",
                     description: title, card_count: extractCardCount(title), image_url: img });
      });
      return deals;
    },

    // Kijiji Canada
    "kijiji.ca": function () {
      const deals = [];
      document.querySelectorAll("[class*='regular-ad'], [data-testid='listing-card'], [class*='AdCard']").forEach(el => {
        const title = el.querySelector("[class*='title'], [class*='Title']")?.innerText?.trim();
        const priceText = el.querySelector("[class*='price'], [class*='Price']")?.innerText;
        const link = el.querySelector("a[href*='/v-']")?.href;
        const img = el.querySelector("img")?.src || "";
        if (!title || !link) return;
        const price = parsePrice(priceText);
        if (price <= 1 || !isBulkLot(title)) return;
        deals.push({ title: "[CA] " + title, url: link.split("?")[0], price, platform: "kijiji",
                     description: title, card_count: extractCardCount(title), image_url: img });
      });
      return deals;
    },

    // Generic fallback — reads any page for Pokemon card deals
    "generic": function () {
      const deals = [];
      // Try common e-commerce patterns
      const containers = document.querySelectorAll(
        "article, [class*='product'], [class*='listing'], [class*='item-card'], [class*='card-item']"
      );
      containers.forEach(el => {
        const title = el.querySelector("h1,h2,h3,[class*='title'],[class*='name']")?.innerText?.trim();
        const priceText = el.querySelector("[class*='price'],[class*='cost'],[class*='amount']")?.innerText;
        const link = el.querySelector("a")?.href;
        const img = el.querySelector("img")?.src || "";
        if (!title || !link) return;
        const price = parsePrice(priceText);
        if (price <= 1 || !isBulkLot(title)) return;
        deals.push({ title, url: link.split("?")[0], price, platform: "other",
                     description: title, card_count: extractCardCount(title), image_url: img });
      });
      return deals;
    }
  };

  // ─── Main ────────────────────────────────────────────────────────────────────

  function getExtractor() {
    const host = location.hostname;
    if (host.includes("ebay.com") && location.pathname.includes("/itm/")) return EXTRACTORS["ebay.com/itm/"];
    if (host.includes("ebay.com"))    return EXTRACTORS["ebay.com"];
    if (host.includes("mercari.com")) return EXTRACTORS["mercari.com"];
    if (host.includes("buyee.jp"))    return EXTRACTORS["buyee.jp"];
    if (host.includes("whatnot.com")) return EXTRACTORS["whatnot.com"];
    if (host.includes("facebook.com")) return EXTRACTORS["facebook.com/marketplace"];
    if (host.includes("kijiji.ca"))   return EXTRACTORS["kijiji.ca"];
    return EXTRACTORS["generic"];
  }

  const extractor = getExtractor();
  const deals = extractor();

  if (deals.length === 0) {
    alert("🔍 No Pokemon bulk lot deals found on this page.\n\nTry scrolling down to load more listings, or navigate to a search results page.");
    return;
  }

  // Show preview
  const preview = deals.slice(0, 5).map(d =>
    `• ${d.title.slice(0, 60)} — $${d.price}`
  ).join("\n");
  const go = confirm(
    `🎴 Found ${deals.length} deals on ${location.hostname}\n\n${preview}${deals.length > 5 ? "\n...and " + (deals.length - 5) + " more" : ""}\n\nPost to dashboard?`
  );
  if (!go) return;

  // Post to dashboard
  fetch(IMPORT_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ deals })
  })
  .then(r => r.json())
  .then(data => {
    alert(`✅ Done!\n\nSaved: ${data.saved} deals\nSkipped (low score/no price): ${data.skipped}\n\nOpen your dashboard to see results:\n${DASHBOARD_URL}`);
  })
  .catch(err => {
    alert("❌ Error posting to dashboard: " + err.message);
  });

})();
