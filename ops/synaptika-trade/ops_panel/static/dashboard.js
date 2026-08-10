// Synaptika Ops — equity charts (Chart.js) for novice dashboard
(function () {
  const charts = { bn: null, ap: null, s15: null };

  function $(id) {
    return document.getElementById(id);
  }

  function fmtWhen(ts) {
    if (!ts) return "";
    const d = new Date(Number(ts) * (Number(ts) < 1e12 ? 1000 : 1));
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleString("es-MX", {
      timeZone: "America/Mexico_City",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  }

  function upsertChart(key, canvasId, series, color) {
    const canvas = $(canvasId);
    if (!canvas || typeof Chart === "undefined") return;
    const pts = (series && series.points) || [];
    const labels = pts.map((p) => fmtWhen(p.ts));
    const data = pts.map((p) => Number(p.equity));
    if (charts[key]) {
      charts[key].data.labels = labels;
      charts[key].data.datasets[0].data = data;
      charts[key].update("none");
      return;
    }
    charts[key] = new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            data,
            borderColor: color,
            backgroundColor: color + "22",
            tension: 0.25,
            pointRadius: pts.length > 60 ? 0 : 2,
            fill: true,
            borderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            ticks: { maxTicksLimit: 5, font: { size: 10 }, color: "#64748b" },
            grid: { display: false },
          },
          y: {
            ticks: { font: { size: 10 }, color: "#64748b" },
            grid: { color: "#e2e8f0" },
          },
        },
      },
    });
  }

  function paint(data) {
    const eq = (data && data.equity) || {};
    upsertChart("bn", "chart-bn", eq.binance, "#2563eb");
    upsertChart("ap", "chart-ap", eq.alpaca, "#0f172a");
    upsertChart("s15", "chart-s15", eq.alpaca_scalp15, "#059669");
    if (data && data.overview && data.overview.headline) {
      const h = $("headline");
      if (h) h.textContent = data.overview.headline;
    }
  }

  try {
    const boot = $("initial-status");
    if (boot && boot.textContent.trim()) {
      paint(JSON.parse(boot.textContent));
    }
  } catch (e) {
    console.warn("initial chart boot failed", e);
  }

  async function refresh() {
    try {
      const res = await fetch("/ops/api/status", {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!res.ok) return;
      paint(await res.json());
    } catch (_) {
      /* ignore */
    }
  }

  // Charts after Chart.js loads (defer)
  window.addEventListener("load", function () {
    try {
      const boot = $("initial-status");
      if (boot && boot.textContent.trim()) paint(JSON.parse(boot.textContent));
    } catch (_) {}
    setInterval(refresh, 30000);
  });
})();
