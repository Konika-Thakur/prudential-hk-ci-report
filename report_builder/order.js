/* ---------- Review-call feedback & improvements (20-22 Nov 2026) ----------
   1. Charts and metrics at the top: picture galleries move below under "Supporting screenshots and sources".
   2. Inspect Evidence – Back Button:
      - Prominent Back button pinned to the very top of Evidence view with destination label.
      - Full browser history (popstate) support so browser Back navigates back to previous view instead of exiting dashboard.
   3. Platform Filters:
      - WeChat and Xiaohongshu added to all platform filter dropdowns.
      - Selecting them smoothly routes to mainland conversation.
   4. Progressive disclosure everywhere:
      - Headlines and key copywriting first, with refined "See more ▾" / "See less ▴" controls.
      - Applied consistently across all tabs and long lists.
   5. Margins and formatting:
      - Unified layout, spacing, and neat cards across all tabs, especially the Leadership Brief. */
(function () {
  const q = (s, r = document) => r.querySelector(s), qa = (s, r = document) => [...r.querySelectorAll(s)];

  // ---- 1. Galleries and screenshots below the data
  const GAL = qa("#captures, #news-pics, #news-pics-p2, main section.view .vg");
  const bySec = new Map();
  GAL.forEach(el => {
    const sec = el.closest("section.view");
    if (!sec || el.closest(".sup-wrap")) return;
    (bySec.get(sec) || bySec.set(sec, []).get(sec)).push(el);
  });
  bySec.forEach((els, sec) => {
    if (sec.querySelector(".sup-wrap")) return;
    const h = document.createElement("h3");
    h.className = "sup-h";
    h.textContent = "Supporting screenshots and sources";
    const sub = document.createElement("p");
    sub.className = "muted small sup-sub";
    sub.textContent = "Complementary evidence captured from verified Hong Kong sources.";
    const wrap = document.createElement("div");
    wrap.className = "sup-wrap";
    wrap.append(h, sub, ...els);
    sec.appendChild(wrap);
  });

  // ---- 2. Progressive disclosure for text density
  const SKIP = ".pd-hide, .ob-hero, .mc, p.muted.small, table, .insight, #insights, .ds-content, .nw-card, .ov-card, .cn-card, #evFocus, .ckey, .geo-card, nav, .fbar, .op-meta, .sec-head, .fx, .script, details:not([open]) > *, .sup-wrap, .ev-top-bar, .ev-top-nav, .ct-card-head";

  const clampP = p => {
    if (p.dataset.pd2 || p.closest(SKIP) || p.querySelector("button, input, select")) return;
    const txt = p.textContent.trim();
    if (txt.length < 170) return;

    p.dataset.pd2 = "1";
    p.classList.add("pd-clamp2");

    const b = document.createElement("button");
    b.type = "button";
    b.className = "pd-more pd-see";
    b.innerHTML = '<span class="pd-see-txt">See more</span> <span class="pd-see-icon" aria-hidden="true">▾</span>';

    p.insertAdjacentElement("afterend", b);
    b.addEventListener("click", () => {
      const open = p.classList.toggle("pd-clamp2") === false;
      b.querySelector(".pd-see-txt").textContent = open ? "See less" : "See more";
      b.querySelector(".pd-see-icon").textContent = open ? "▴" : "▾";
    });
  };

  const clampList = ul => {
    if (ul.dataset.pd2 || ul.closest(SKIP) || ul.closest("#topPosts") || ul.id === "topPosts") return;
    const items = [...ul.children].filter(x => x.tagName === "LI");
    if (items.length <= 4) return;

    ul.dataset.pd2 = "1";
    const keep = 3;
    items.slice(keep).forEach(li => li.classList.add("pd-li-hide"));

    const b = document.createElement("button");
    b.type = "button";
    b.className = "pd-more pd-see";
    b.innerHTML = `<span class="pd-see-txt">Show ${items.length - keep} more</span> <span class="pd-see-icon" aria-hidden="true">▾</span>`;

    ul.insertAdjacentElement("afterend", b);
    b.addEventListener("click", () => {
      const isHidden = items[keep].classList.contains("pd-li-hide");
      items.slice(keep).forEach(li => li.classList.toggle("pd-li-hide", !isHidden));
      b.querySelector(".pd-see-txt").textContent = isHidden ? "Show fewer" : `Show ${items.length - keep} more`;
      b.querySelector(".pd-see-icon").textContent = isHidden ? "▴" : "▾";
    });
  };

  const runPD = () => {
    qa("main section.view p").forEach(clampP);
    qa("main section.view ul, main section.view ol").forEach(clampList);
  };
  runPD();
  let tPD = null;
  document.addEventListener("click", e => {
    if (e.target.closest("nav a, [data-view]")) {
      clearTimeout(tPD);
      tPD = setTimeout(runPD, 350);
    }
  });

  // ---- 3. Inspect Evidence: Top Back Button & Browser History Handling
  let lastTab = "es", lastTabName = "Executive Summary", lastScroll = 0;
  const TAB_NAMES = {
    es: "Executive Summary",
    one: "Leadership Brief",
    p2: "Competitor Activity",
    p3: "Brand & Campaigns",
    p4: "Products & Propositions",
    p5: "Digital Experience",
    p6: "Strengths, Gaps & Actions",
    deep: "Mainland & In-Depth",
    watch: "Coverage & Watch List",
    gallery: "Campaign Gallery",
    profiles: "Company Profiles"
  };

  document.addEventListener("click", e => {
    const a = e.target.closest("nav.toc a, [data-view]");
    if (!a) return;
    const v = a.dataset.view || (a.getAttribute("href") || "").replace(/^#/, "");
    if (v && v !== "ev") {
      lastTab = v;
      lastTabName = TAB_NAMES[v] || a.textContent.trim().replace(/^\d+\.\s*/, "") || "Overview";
      lastScroll = window.scrollY;
    }
  }, true);

  const evSec = q("#ev");
  function setupEvTopNav() {
    if (!evSec) return;
    let topNav = q("#evTopNav", evSec);
    if (!topNav) {
      topNav = document.createElement("div");
      topNav.id = "evTopNav";
      topNav.className = "ev-top-nav";
      evSec.prepend(topNav);
    }
    const dest = lastTabName || "Overview";
    topNav.innerHTML = `
      <div class="ev-top-bar">
        <button type="button" class="btn primary ev-back-btn" id="evMainBackBtn">
          <span class="ev-back-arrow">←</span> Back to ${dest}
        </button>
        <span class="ev-context-tag">Evidence &amp; Supporting Records</span>
      </div>`;

    const btn = q("#evMainBackBtn", topNav);
    if (btn) {
      btn.onclick = () => {
        goBackFromEvidence();
      };
    }
  }

  function goBackFromEvidence() {
    const target = lastTab || "es";
    const targetLink = q(`nav.toc a[data-view="${target}"], nav.toc a[href="#${target}"]`);
    if (targetLink) {
      targetLink.click();
    } else {
      location.hash = "#" + target;
    }
    setTimeout(() => {
      window.scrollTo({ top: lastScroll || 0, behavior: "smooth" });
    }, 60);
  }

  window.goBackFromEvidence = goBackFromEvidence;

  // Intercept evidence navigation to record state and prevent browser back exit
  document.addEventListener("click", e => {
    const t = e.target.closest(".ov-insp, .lb-ev, [data-act='recs'], [data-act='mcrecs'], .cn-open, .ob-pic, a[href='#ev'], [data-view='ev']");
    if (!t) return;
    const curTab = q("nav.toc a.active, nav.toc a[aria-current]")?.dataset.view || lastTab || "es";
    lastScroll = window.scrollY;
    window.history.pushState({ view: "ev", from: curTab, fromName: lastTabName, scroll: lastScroll }, "", "#ev");
    setTimeout(setupEvTopNav, 50);
  }, true);

  // Popstate listener: browser Back restores previous section without exiting dashboard
  window.addEventListener("popstate", e => {
    const dsheet = q(".dsheet:not([hidden])");
    if (dsheet) {
      if (typeof window.closeSheet === "function") window.closeSheet();
      else dsheet.hidden = true;
      return;
    }
    const lb = q(".vg-lb:not([hidden]), .lc-lb:not([hidden])");
    if (lb) {
      lb.hidden = true;
      return;
    }
    const curSec = q("main section.view.on, main section.view:not([hidden])");
    if (curSec && curSec.id === "ev") {
      const from = (e.state && e.state.from) || lastTab || "es";
      const navA = q(`nav.toc a[data-view="${from}"], nav.toc a[href="#${from}"]`);
      if (navA) navA.click();
      if (e.state && typeof e.state.scroll === "number") {
        setTimeout(() => window.scrollTo({ top: e.state.scroll, behavior: "smooth" }), 60);
      }
    }
  });

  setupEvTopNav();

  // Also pin evidence focus bar if present
  const evF = q("#evFocus");
  if (evSec && evF) {
    const hold = q("#evBarHold", evSec) || document.createElement("div");
    hold.id = "evBarHold";
    if (!hold.parentElement) evSec.insertBefore(hold, evF);
    const sync = () => {
      const bar = evF.querySelector(".ev-bar");
      if (bar) {
        hold.replaceChildren(bar);
      } else if (evF.hidden || !evF.innerHTML.trim()) {
        hold.replaceChildren();
      }
    };
    new MutationObserver(sync).observe(evF, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"] });
    sync();
  }

  // ---- 4. Platform Filters: Add WeChat and Xiaohongshu to all platform filter dropdowns
  function populatePlatformFilters() {
    const selects = qa("select#fPlat, .fbar select, .fg select, select[name*='plat']");
    selects.forEach(sel => {
      const vals = [...sel.options].map(o => o.value);
      if (!vals.includes("Xiaohongshu")) {
        const opt = document.createElement("option");
        opt.value = "Xiaohongshu";
        opt.dataset.cn = "1";
        opt.textContent = "Xiaohongshu (RED)";
        sel.appendChild(opt);
      }
      if (!vals.includes("WeChat")) {
        const opt = document.createElement("option");
        opt.value = "WeChat";
        opt.dataset.cn = "1";
        opt.textContent = "WeChat (微信)";
        sel.appendChild(opt);
      }
    });
  }

  populatePlatformFilters();
  setTimeout(populatePlatformFilters, 500);

  document.addEventListener("change", e => {
    const s = e.target;
    if (s.id !== "fPlat" && !s.classList.contains("f-plat") && !s.closest(".fbar, .fg")) return;
    const o = s.selectedOptions[0];
    if (!o || !o.dataset.cn) { s.dataset.prev = s.value; return; }
    e.stopImmediatePropagation();
    const plat = o.value;
    s.value = s.dataset.prev || "all";
    const nav = q('nav.toc a[data-view="deep"]');
    if (nav) nav.click();
    setTimeout(() => {
      const sec = q("#cnsoc");
      if (!sec) return;
      qa(".cn-p-f button", sec).find(b => b.dataset.p === plat)?.click();
      sec.scrollIntoView({ behavior: "smooth", block: "start" });
      let note = q("#cnNote", sec);
      if (!note) {
        note = document.createElement("p");
        note.id = "cnNote";
        note.className = "small cn-filter-note";
        sec.querySelector("h3")?.insertAdjacentElement("afterend", note);
      }
      note.textContent = `Showing ${plat} conversation. Xiaohongshu and WeChat posts are search-based across the 4 HK insurers (Nov 2025 – Oct 2026).`;
    }, 350);
  }, true);
})();
