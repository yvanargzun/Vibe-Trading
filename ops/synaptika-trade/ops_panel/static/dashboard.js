// Synaptika Ops dashboard — poll /ops/api/status every 20s
(function () {
  const REFRESH_MS = 20000;
  let chartBn = null;
  let chartAp = null;
  let chartS15 = null;

  function $(id) {
    return document.getElementById(id);
  }

  function fmtMoney(n) {
    if (n == null || Number.isNaN(Number(n))) return "—";
    return "$" + Number(n).toFixed(2);
  }

  function fmtPct(n) {
    if (n == null || Number.isNaN(Number(n))) return "—";
    const v = Number(n);
    return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
  }

  function fmtWhen(ts) {
    if (!ts) return "—";
    const d = new Date(Number(ts) * (Number(ts) < 1e12 ? 1000 : 1));
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleString("es-MX", { timeZone: "America/Mexico_City", hour12: false });
  }

  function chipHalt(elId, halted) {
    const el = $(elId);
    if (!el) return;
    el.innerHTML = halted ? '<span class="chip warn">HALT</span>' : "";
  }

  function renderLegs(ulId, legs) {
    const ul = $(ulId);
    if (!ul) return;
    ul.innerHTML = "";
    if (!legs || !legs.length) {
      ul.innerHTML = "<li><span>Sin piernas abiertas</span><span>—</span></li>";
      return;
    }
    for (const L of legs) {
      const li = document.createElement("li");
      const left = document.createElement("span");
      left.textContent = L.asset + (L.sleeve ? " · " + L.sleeve : "");
      const right = document.createElement("strong");
      let extra = "";
      if (L.pnl_pct != null) {
        const p = Number(L.pnl_pct);
        if (!Number.isNaN(p)) extra = " · " + fmtPct(p);
      }
      right.textContent = fmtMoney(L.usd) + extra;
      li.appendChild(left);
      li.appendChild(right);
      ul.appendChild(li);
    }
  }

  function renderVenue(prefix, snap) {
    $(prefix + "-mode").textContent = snap.mode || "—";
    $(prefix + "-equity").textContent = fmtMoney(snap.equity);
    chipHalt(prefix + "-halt", snap.halt);
    renderLegs(prefix + "-legs", snap.legs || []);
    if (prefix === "bn") {
      $("bn-meta").textContent =
        "usable " +
        fmtMoney(snap.usable) +
        " · day " +
        fmtPct(snap.day_pnl_pct) +
        " (" +
        fmtMoney(snap.day_pnl) +
        ") · " +
        (snap.regime || "?") +
        " · buys " +
        (snap.buys_today ?? "—");
      $("bn-reason").textContent = snap.reason || "";
    } else {
      $("ap-meta").textContent =
        "cash " +
        fmtMoney(snap.cash) +
        " · day " +
        fmtPct(snap.day_pnl_pct) +
        " · " +
        (snap.regime || "?") +
        " · " +
        (snap.strategy || "");
    }
  }

  function renderStrategy(briefs, live) {
    const host = $("strategy-cards");
    if (!host) return;
    host.innerHTML = "";
    const order = [
      ["binance", briefs.binance, live && live.binance],
      ["alpaca", briefs.alpaca, live && live.alpaca],
      ["alpaca_scalp15", briefs.alpaca_scalp15, live && live.alpaca_scalp15],
    ];
    for (const [key, brief, livePart] of order) {
      if (!brief) continue;
      const div = document.createElement("div");
      div.className = "strategy-block";
      const knobs = brief.knobs || {};
      const knobsHtml = Object.entries(knobs)
        .map(([k, v]) => "<li><strong>" + k + "</strong>: " + v + "</li>")
        .join("");
      const liveLine = livePart
        ? "<p class=\"meta\">Live: mode <strong>" +
          (livePart.mode || "—") +
          "</strong> · regime " +
          (livePart.regime || "—") +
          (livePart.halt ? " · HALT" : "") +
          "</p>"
        : "";
      div.innerHTML =
        "<h3>" +
        (brief.title || key) +
        "</h3>" +
        "<p>" +
        (brief.summary || "") +
        "</p>" +
        "<p class=\"meta\">" +
        (brief.style || "") +
        "</p>" +
        liveLine +
        "<ul class=\"knob-list\">" +
        knobsHtml +
        "</ul>";
      host.appendChild(div);
    }
  }

  function upsertChart(canvasId, existing, label, series, color) {
    const canvas = $(canvasId);
    if (!canvas || typeof Chart === "undefined") return existing;
    const pts = (series && series.points) || [];
    const labels = pts.map((p) => fmtWhen(p.ts));
    const data = pts.map((p) => Number(p.equity));
    if (existing) {
      existing.data.labels = labels;
      existing.data.datasets[0].data = data;
      existing.update("none");
      return existing;
    }
    return new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label,
            data,
            borderColor: color,
            backgroundColor: color + "33",
            tension: 0.25,
            pointRadius: pts.length > 80 ? 0 : 2,
            fill: true,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { maxTicksLimit: 6, font: { size: 10 } } },
          y: { ticks: { font: { size: 10 } } },
        },
      },
    });
  }

  function feedDetail(row) {
    const k = row.kind;
    if (k === "trade") {
      return (
        (row.side || "?") +
        " " +
        (row.symbol || "?") +
        " · " +
        fmtMoney(row.usd) +
        (row.result ? " · " + row.result : "") +
        (row.pnl_pct != null ? " · " + fmtPct(row.pnl_pct) : "")
      );
    }
    if (k === "skip") {
      return (
        "SKIP " +
        (row.symbol || "") +
        " · " +
        String(row.reason || row.result || "").slice(0, 120)
      );
    }
    if (k === "cycle") {
      const dec = row.decision || {};
      const action = dec.action || row.action || row.kind;
      const reason = dec.reason || row.reason || "";
      return String(action) + (reason ? " · " + String(reason).slice(0, 120) : "");
    }
    return JSON.stringify(row).slice(0, 140);
  }

  function renderFeed(feed) {
    const body = $("feed-body");
    if (!body) return;
    body.innerHTML = "";
    if (!feed || !feed.length) {
      body.innerHTML = "<tr><td colspan=\"4\">Sin actividad reciente</td></tr>";
      return;
    }
    for (const row of feed) {
      const tr = document.createElement("tr");
      const cells = [
        fmtWhen(row.ts || row.ts_unix),
        row.source || "—",
        row.kind || "—",
        feedDetail(row),
      ];
      for (const c of cells) {
        const td = document.createElement("td");
        td.textContent = c;
        tr.appendChild(td);
      }
      body.appendChild(tr);
    }
  }

  function applyStatus(data) {
    const err = $("error-box");
    if (err) err.classList.add("hidden");

    const bn = data.binance || {};
    const ap = data.alpaca || {};
    const s15 = data.alpaca_scalp15 || {};
    renderVenue("bn", bn);
    renderVenue("ap", ap);

    const anyHalt = !!(bn.halt || ap.halt || s15.halt);
    const banner = $("halt-banner");
    if (banner) banner.classList.toggle("hidden", !anyHalt);

    renderStrategy((data.strategy && data.strategy.briefs) || {}, {
      binance: bn,
      alpaca: ap,
      alpaca_scalp15: s15,
    });

    const eq = data.equity || {};
    chartBn = upsertChart("chart-bn", chartBn, "Binance", eq.binance, "#2563eb");
    chartAp = upsertChart("chart-ap", chartAp, "Alpaca", eq.alpaca, "#0f172a");
    chartS15 = upsertChart("chart-s15", chartS15, "scalp15", eq.alpaca_scalp15, "#0d9488");

    renderFeed((data.activity && data.activity.feed) || []);
    const updated = $("last-updated");
    if (updated) updated.textContent = fmtWhen(data.ts || Date.now() / 1000);
  }

  async function refresh() {
    const err = $("error-box");
    try {
      const res = await fetch("/ops/api/status", {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      applyStatus(await res.json());
    } catch (e) {
      if (err) {
        err.textContent = "No se pudo actualizar: " + e.message;
        err.classList.remove("hidden");
      }
    }
  }

  try {
    const boot = document.getElementById("initial-status");
    if (boot && boot.textContent && boot.textContent.trim()) {
      applyStatus(JSON.parse(boot.textContent));
    }
  } catch (e) {
    console.warn("initial status parse failed", e);
  }

  refresh();
  setInterval(refresh, REFRESH_MS);
})();
