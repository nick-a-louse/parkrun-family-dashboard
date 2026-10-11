/* parkrun family dashboard: theme, refresh, import, results feed and the trends chart. */
(function () {
  "use strict";
  var $ = function (s, el) { return (el || document).querySelector(s); };
  var $$ = function (s, el) { return Array.prototype.slice.call((el || document).querySelectorAll(s)); };
  var root = document.documentElement;

  /* ------------------------------------------------------------------ theme */
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function isDark() {
    var t = root.dataset.theme;
    if (t === "dark") return true;
    if (t === "light") return false;
    return !!(media && media.matches);
  }
  $("#theme").addEventListener("click", function () {
    var next = isDark() ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch (e) {}
    drawChart();
  });
  if (media && media.addEventListener) media.addEventListener("change", function () { drawChart(); });

  /* ------------------------------------------------------------------ notices */
  var notice = $("#notice");
  function say(msg, bad) {
    notice.textContent = msg;
    notice.hidden = !msg;
    notice.classList.toggle("bad", !!bad);
  }

  /* ------------------------------------------------------------------ refresh */
  var refreshBtn = $("#refresh");
  function setBusy(busy) {
    refreshBtn.disabled = busy;
    refreshBtn.textContent = busy ? "Checking…" : "Check for results";
  }
  function waitForSync(afterId) {
    fetch("/api/status").then(function (r) { return r.json(); }).then(function (s) {
      var last = s.last_attempt;
      if (s.running || !last || last.id <= afterId || !last.finished_at) {
        setTimeout(function () { waitForSync(afterId); }, 2000);
        return;
      }
      setBusy(false);
      if (last.ok) {
        say(last.new_results ? "Found " + last.message + ". Updating the page…" : "No new results yet. Results usually appear 1 to 2 hours after the start.");
        setTimeout(function () { location.reload(); }, last.new_results ? 900 : 2500);
      } else {
        say("The check didn't work: " + last.message, true);
      }
    }).catch(function () { setTimeout(function () { waitForSync(afterId); }, 4000); });
  }
  refreshBtn.addEventListener("click", function () {
    setBusy(true);
    say("Checking parkrun for new results. This takes about half a minute.");
    fetch("/api/refresh", { method: "POST" }).then(function (r) {
      return r.json().then(function (body) { return { status: r.status, body: body }; });
    }).then(function (res) {
      if (res.status === 202) { waitForSync(res.body.after_log_id); return; }
      setBusy(false);
      say(res.body.message || "Couldn't start a check.", res.status !== 409);
      if (res.status === 409) { setBusy(true); waitForSync(0); }
    }).catch(function () { setBusy(false); say("Couldn't reach the dashboard server.", true); });
  });

  /* ------------------------------------------------------------------ feed */
  var older = $("#show-older");
  if (older) older.addEventListener("click", function () { $(".feed").classList.add("show-all"); });

  /* ------------------------------------------------------------------ import */
  var form = $("#import-form");
  if (form) form.addEventListener("submit", function (e) {
    e.preventDefault();
    var data = new FormData(form);
    say("Importing…");
    fetch("/api/import", { method: "POST", body: data }).then(function (r) {
      return r.json().then(function (b) { return { ok: r.ok, body: b }; });
    }).then(function (res) {
      say(res.body.message || res.body.detail || "Import finished.", !res.ok);
      if (res.ok) setTimeout(function () { location.reload(); }, 1500);
    }).catch(function () { say("Couldn't upload the file.", true); });
  });

  var testEmail = $("#test-email");
  if (testEmail) testEmail.addEventListener("click", function () {
    testEmail.disabled = true;
    fetch("/api/test-email", { method: "POST" }).then(function (r) {
      return r.json().then(function (b) { return { ok: r.ok, body: b }; });
    }).then(function (res) {
      say(res.body.message || res.body.detail, !res.ok);
    }).finally(function () { testEmail.disabled = false; });
  });

  /* ------------------------------------------------------------------ chart */
  var data = JSON.parse($("#chart-data").textContent);
  var chartEl = $("#chart"), tableEl = $("#chart-table"), legendEl = $("#legend");
  var state = { dist: null, view: "chart" };

  function latestDate(series) {
    var d = "";
    series.forEach(function (s) { s.points.forEach(function (p) { if (p.date > d) d = p.date; }); });
    return d;
  }
  // Default to the distance run most recently; grey out distances with no data.
  state.dist = (latestDate(data["5k"] || []) >= latestDate(data["2k"] || [])) ? "5k" : "2k";
  if (!(data[state.dist] || []).length) state.dist = (data["2k"] || []).length ? "2k" : "5k";
  $$("[data-dist]").forEach(function (b) {
    if (!(data[b.dataset.dist] || []).length) b.disabled = true;
    b.addEventListener("click", function () { state.dist = b.dataset.dist; syncControls(); drawChart(); });
  });
  $$("[data-view]").forEach(function (b) {
    b.addEventListener("click", function () { state.view = b.dataset.view; syncControls(); drawChart(); });
  });
  function syncControls() {
    $$("[data-dist]").forEach(function (b) { b.setAttribute("aria-checked", String(b.dataset.dist === state.dist)); });
    $$("[data-view]").forEach(function (b) { b.setAttribute("aria-checked", String(b.dataset.view === state.view)); });
  }
  syncControls();

  function css(name) { return getComputedStyle(root).getPropertyValue(name).trim(); }
  function colourOf(s) { return isDark() ? (s.colour_dark || s.colour) : s.colour; }
  function fmt(sec) {
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.round(sec % 60);
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0");
  }
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function dayLabel(t, withYear) {
    var d = new Date(t);
    return d.getUTCDate() + " " + MONTHS[d.getUTCMonth()] + (withYear ? " " + d.getUTCFullYear() : "");
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  }
  function niceStep(range) {
    var steps = [5, 10, 15, 20, 30, 60, 120, 300, 600, 900, 1800];
    for (var i = 0; i < steps.length; i++) if (range / steps[i] <= 6) return steps[i];
    return 3600;
  }

  // Builds the chart as an SVG string with explicit colours, so the same markup
  // works on screen and as a downloaded file.
  function buildSvg(series, width, opts) {
    opts = opts || {};
    var C = {
      surface: css("--surface"), ink: css("--ink"), muted: css("--muted"),
      line: css("--line"), strong: css("--line-strong"), font: "Barlow, system-ui, sans-serif"
    };
    var titleH = opts.title ? 52 : 0;
    var narrow = width < 520;
    var H = (narrow ? 250 : 320) + titleH;
    var M = { l: 46, r: narrow ? 70 : 92, t: 12 + titleH, b: 28 };
    var iw = width - M.l - M.r, ih = H - M.t - M.b;

    var pts = [];
    series.forEach(function (s, si) {
      s.points.forEach(function (p) { pts.push({ s: si, t: Date.parse(p.date + "T00:00:00Z"), v: p.seconds, p: p }); });
    });
    var tMin = Math.min.apply(null, pts.map(function (d) { return d.t; }));
    var tMax = Math.max.apply(null, pts.map(function (d) { return d.t; }));
    if (tMax - tMin < 14 * 864e5) { tMin -= 7 * 864e5; tMax += 7 * 864e5; }
    var vMin = Math.min.apply(null, pts.map(function (d) { return d.v; }));
    var vMax = Math.max.apply(null, pts.map(function (d) { return d.v; }));
    var step = niceStep(Math.max(vMax - vMin, 30));
    vMin = Math.floor((vMin - step * 0.3) / step) * step;
    vMax = Math.ceil((vMax + step * 0.3) / step) * step;
    var x = function (t) { return M.l + (t - tMin) / (tMax - tMin) * iw; };
    var y = function (v) { return M.t + (v - vMin) / (vMax - vMin) * ih; }; // faster = higher

    var out = [];
    out.push('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + width + " " + H + '" width="' + width + '" height="' + H + '" font-family="' + C.font + '" role="img" aria-label="Finish times over time, ' + esc(opts.distLabel || "") + '">');
    if (opts.background) out.push('<rect width="100%" height="100%" fill="' + C.surface + '"/>');
    if (opts.title) out.push('<text x="' + M.l + '" y="22" font-size="17" font-weight="700" fill="' + C.ink + '">' + esc(opts.title) + "</text>");
    // grid + y axis
    out.push('<g class="grid axis">');
    for (var v = vMin; v <= vMax + 0.1; v += step) {
      out.push('<line x1="' + M.l + '" x2="' + (M.l + iw) + '" y1="' + y(v) + '" y2="' + y(v) + '" stroke="' + C.line + '" stroke-width="1"/>');
      out.push('<text x="' + (M.l - 8) + '" y="' + (y(v) + 4) + '" text-anchor="end" font-size="12" fill="' + C.muted + '">' + fmt(v) + "</text>");
    }
    // x ticks: up to ~6 evenly spaced dates
    var span = tMax - tMin, n = narrow ? 3 : 5, withYear = new Date(tMin).getUTCFullYear() !== new Date(tMax).getUTCFullYear();
    for (var i = 0; i <= n; i++) {
      var t = tMin + span * i / n;
      out.push('<text x="' + x(t) + '" y="' + (H - 8) + '" text-anchor="' + (i === 0 ? "start" : i === n ? "end" : "middle") + '" font-size="12" fill="' + C.muted + '">' + dayLabel(t, withYear) + "</text>");
    }
    out.push("</g>");
    // lines + markers
    var labels = [];
    series.forEach(function (s, si) {
      var col = colourOf(s);
      var mine = pts.filter(function (d) { return d.s === si; }).sort(function (a, b) { return a.t - b.t; });
      var path = mine.map(function (d, i) { return (i ? "L" : "M") + x(d.t).toFixed(1) + " " + y(d.v).toFixed(1); }).join(" ");
      out.push('<path d="' + path + '" fill="none" stroke="' + col + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>');
      mine.forEach(function (d) {
        out.push('<circle cx="' + x(d.t).toFixed(1) + '" cy="' + y(d.v).toFixed(1) + '" r="4" fill="' + col + '" stroke="' + C.surface + '" stroke-width="2"/>');
      });
      var last = mine[mine.length - 1];
      labels.push({ name: s.name, x: x(last.t), y: y(last.v), col: col });
    });
    // direct labels at the line ends, nudged apart so they don't collide
    labels.sort(function (a, b) { return a.y - b.y; });
    for (var k = 1; k < labels.length; k++) if (labels[k].y - labels[k - 1].y < 15) labels[k].y = labels[k - 1].y + 15;
    labels.forEach(function (l) {
      out.push('<text class="lbl" x="' + (l.x + 9) + '" y="' + (l.y + 4) + '" font-size="13" font-weight="600" fill="' + C.ink + '">' + esc(l.name) + "</text>");
    });
    out.push('<line class="xhair" x1="0" x2="0" y1="' + M.t + '" y2="' + (M.t + ih) + '" stroke="' + C.strong + '" stroke-width="1" visibility="hidden"/>');
    out.push("</svg>");
    return { svg: out.join(""), pts: pts, x: x, y: y, M: M, iw: iw, ih: ih, H: H };
  }

  function drawLegend(series) {
    legendEl.textContent = "";
    if (series.length < 2) return;
    series.forEach(function (s) {
      var span = document.createElement("span");
      var i = document.createElement("i");
      i.style.color = colourOf(s);
      span.appendChild(i);
      span.appendChild(document.createTextNode(s.name));
      legendEl.appendChild(span);
    });
  }

  function drawTable(series) {
    var rows = [];
    series.forEach(function (s) { s.points.forEach(function (p) { rows.push({ n: s.name, p: p }); }); });
    rows.sort(function (a, b) { return a.p.date < b.p.date ? 1 : a.p.date > b.p.date ? -1 : a.n.localeCompare(b.n); });
    var t = document.createElement("table");
    t.innerHTML = '<thead><tr><th>Date</th><th>Runner</th><th>Event</th><th class="num">Time</th><th class="num">Age grade</th></tr></thead>';
    var tb = document.createElement("tbody");
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      [r.p.date, r.n, r.p.event, r.p.time, r.p.age_grade ? r.p.age_grade.toFixed(2) + "%" : "–"].forEach(function (v, i) {
        var td = document.createElement("td");
        td.textContent = v;
        if (i >= 3) td.className = "num";
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    tableEl.textContent = "";
    tableEl.appendChild(t);
  }

  var current = null;
  function distLabel() { return state.dist === "2k" ? "2k junior parkrun" : "5k parkrun"; }
  function drawChart() {
    var series = data[state.dist] || [];
    drawLegend(series);
    $("#dl-png").disabled = $("#dl-svg").disabled = !series.length;
    tableEl.hidden = state.view !== "table";
    chartEl.hidden = state.view !== "chart";
    if (state.view === "table") { drawTable(series); return; }
    if (!series.length) {
      chartEl.innerHTML = '<p class="chart-empty">No ' + distLabel() + " results yet.</p>";
      current = null;
      return;
    }
    var width = Math.max(300, Math.round(chartEl.clientWidth || 600));
    current = buildSvg(series, width, { distLabel: distLabel() });
    current.series = series;
    chartEl.innerHTML = current.svg;
    attachHover();
  }

  var tip = null;
  function attachHover() {
    var svg = $("svg", chartEl), xh = $(".xhair", svg);
    if (!tip) { tip = document.createElement("div"); tip.className = "tip"; tip.hidden = true; chartEl.appendChild(tip); }
    else chartEl.appendChild(tip);
    var dates = Array.from(new Set(current.pts.map(function (d) { return d.t; }))).sort();
    function show(clientX) {
      var box = svg.getBoundingClientRect();
      var scale = box.width / svg.viewBox.baseVal.width;
      var px = (clientX - box.left) / scale;
      var best = dates[0], bd = Infinity;
      dates.forEach(function (t) { var d = Math.abs(current.x(t) - px); if (d < bd) { bd = d; best = t; } });
      var cx = current.x(best);
      xh.setAttribute("x1", cx); xh.setAttribute("x2", cx); xh.setAttribute("visibility", "visible");
      var here = current.pts.filter(function (d) { return d.t === best; });
      tip.textContent = "";
      var head = document.createElement("div");
      head.className = "tip-date";
      head.textContent = dayLabel(best, true) + (here[0] ? ", " + here[0].p.event : "");
      tip.appendChild(head);
      here.sort(function (a, b) { return a.v - b.v; }).forEach(function (d) {
        var s = current.series[d.s];
        var row = document.createElement("div"); row.className = "row";
        var key = document.createElement("i"); key.style.background = colourOf(s);
        var val = document.createElement("b"); val.textContent = d.p.time;
        var name = document.createElement("span"); name.textContent = s.name;
        var ag = document.createElement("small"); ag.textContent = d.p.age_grade ? d.p.age_grade.toFixed(1) + "%" : "";
        row.appendChild(key); row.appendChild(val); row.appendChild(name); row.appendChild(ag);
        tip.appendChild(row);
      });
      tip.hidden = false;
      var left = cx * scale + 12;
      if (left + tip.offsetWidth > box.width) left = cx * scale - tip.offsetWidth - 12;
      tip.style.left = Math.max(0, left) + "px";
      tip.style.top = (current.M.t * scale) + "px";
    }
    function hide() { xh.setAttribute("visibility", "hidden"); tip.hidden = true; }
    svg.addEventListener("pointermove", function (e) { show(e.clientX); });
    svg.addEventListener("pointerdown", function (e) { show(e.clientX); });
    svg.addEventListener("pointerleave", hide);
  }

  var resizeTimer;
  window.addEventListener("resize", function () { clearTimeout(resizeTimer); resizeTimer = setTimeout(drawChart, 150); });
  drawChart();

  /* ------------------------------------------------------------------ chart downloads */
  function exportSvg() {
    var series = data[state.dist] || [];
    var built = buildSvg(series, 900, { title: "parkrun times, " + distLabel(), background: true, distLabel: distLabel() });
    // add a legend row under the title for the exported file
    var legend = series.map(function (s, i) {
      return '<g transform="translate(' + (46 + i * 140) + ',42)"><line x1="0" x2="16" y1="0" y2="0" stroke="' + colourOf(s) + '" stroke-width="2"/><text x="22" y="4" font-size="13" fill="' + css("--ink") + '">' + esc(s.name) + "</text></g>";
    }).join("");
    return built.svg.replace(/<line class="xhair"[^>]*\/>/, "").replace("</svg>", legend + "</svg>").replace(/viewBox="0 0 900 (\d+)"/, function (m, h) { return 'viewBox="0 0 900 ' + h + '"'; });
  }
  function download(blob, name) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }
  var stamp = new Date().toISOString().slice(0, 10);
  $("#dl-svg").addEventListener("click", function () {
    download(new Blob([exportSvg()], { type: "image/svg+xml" }), "parkrun-" + state.dist + "-" + stamp + ".svg");
  });
  $("#dl-png").addEventListener("click", function () {
    var svg = exportSvg();
    var m = svg.match(/height="(\d+)"/);
    var w = 900, h = m ? +m[1] : 400, k = 2;
    var img = new Image();
    img.onload = function () {
      var c = document.createElement("canvas");
      c.width = w * k; c.height = h * k;
      var ctx = c.getContext("2d");
      ctx.scale(k, k);
      ctx.drawImage(img, 0, 0, w, h);
      c.toBlob(function (b) { download(b, "parkrun-" + state.dist + "-" + stamp + ".png"); }, "image/png");
    };
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  });
})();
