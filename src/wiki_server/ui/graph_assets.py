"""The graph view: a force-directed map of the pages and their links (d3)."""

# Pinned CDN build of d3; everything else is inline.
D3_URL = "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"

GRAPH_BODY = """
<div class="graph-controls">
  <input type="search" id="g-search" placeholder="Trouver une page…" autocomplete="off">
  <label><input type="checkbox" id="g-orphans"> Orphelines</label>
  <label><input type="checkbox" id="g-dups"> Doublons probables</label>
  <label><input type="checkbox" id="g-temporal"> Tâches et événements</label>
  <span class="graph-stats" id="g-stats"></span>
</div>
<div class="graph-wrap" id="g-wrap"><svg id="g-svg" role="img" aria-label="Graphe des pages"></svg>
  <div class="graph-tip" id="g-tip"></div></div>
<div class="graph-legend" id="g-legend"></div>
<p class="muted" id="g-broken"></p>
"""

GRAPH_SCRIPT = """
(function () {
  // One hue per category, readable on the light and the dark panel alike.
  const COLORS = {
    self: "#d9534f", people: "#3a8fd9", places: "#2fa37a", organizations: "#c9871f",
    projects: "#8a63d2", concepts: "#d0569b", sources: "#6b8e23", temporal: "#7f8c8d"
  };
  const LABELS = {
    self: "Moi", people: "Personnes", places: "Lieux", organizations: "Organisations",
    projects: "Projets", concepts: "Concepts", sources: "Sources", temporal: "Temps"
  };
  const color = c => COLORS[c] || "#999";
  const svg = d3.select("#g-svg"), wrap = document.getElementById("g-wrap");
  const tip = document.getElementById("g-tip");
  const ink = getComputedStyle(document.body).color;
  const panel = getComputedStyle(wrap).backgroundColor;
  let sim = null;

  function pageUrl(id) { return "/ui/page/" + id.split("/").map(encodeURIComponent).join("/"); }

  function load() {
    const t = document.getElementById("g-temporal").checked ? "1" : "0";
    fetch("/ui/graph.json?temporal=" + t, { credentials: "same-origin" })
      .then(r => r.json()).then(draw);
  }

  function draw(data) {
    if (sim) sim.stop();
    svg.selectAll("*").remove();
    const w = wrap.clientWidth, h = wrap.clientHeight;
    const nodes = data.nodes.map(n => Object.assign({}, n));
    const links = data.edges.map(e => Object.assign({}, e));
    const orphans = new Set(data.orphans);
    const dupOf = new Map();
    data.duplicates.forEach((g, i) => g.forEach(id => dupOf.set(id, i)));
    const radius = n => 4 + Math.sqrt(n.degree) * 2.2;
    const labelled = d => d.degree >= 8;

    document.getElementById("g-stats").textContent =
      nodes.length + " pages, " + links.length + " liens, " + orphans.size
      + (orphans.size > 1 ? " orphelines" : " orpheline");
    document.getElementById("g-broken").textContent = data.broken.length
      ? data.broken.length + " liens cassés (vers une page qui n'existe pas)." : "";
    const cats = [...new Set(nodes.map(n => n.category))].sort();
    document.getElementById("g-legend").replaceChildren(...cats.map(c => {
      const s = document.createElement("span");
      s.style.setProperty("--c", color(c)); s.textContent = LABELS[c] || c; return s;
    }));

    const root = svg.append("g");
    let scale = 1;
    const zoom = d3.zoom().scaleExtent([0.2, 6]).on("zoom", ev => {
      root.attr("transform", ev.transform);
      scale = ev.transform.k;
      label.style("display", d => (scale > 1.5 || labelled(d)) ? null : "none");
    });
    svg.call(zoom);

    const link = root.append("g").attr("stroke", ink)
      .selectAll("line").data(links).join("line").attr("stroke-width", 1);
    const node = root.append("g").selectAll("circle").data(nodes).join("circle")
      .attr("r", radius).attr("fill", d => color(d.category))
      .attr("stroke-width", 1.5).style("cursor", "pointer")
      .on("click", (ev, d) => { location.href = pageUrl(d.id); })
      .on("mouseenter", (ev, d) => {
        tip.replaceChildren();
        const b = document.createElement("b"); b.textContent = d.label; tip.append(b);
        if (d.photo) {
          const img = document.createElement("img");
          img.src = "/ui/page/" + d.photo.split("/").map(encodeURIComponent).join("/");
          img.alt = ""; img.style.cssText = "display:block;max-width:100%;max-height:140px;border-radius:6px;margin:.3rem 0";
          tip.append(img);
        }
        tip.append(document.createTextNode(
          (d.description ? d.description + " " : "") + "(" + d.backlinks + " liens entrants)"));
        tip.style.display = "block";
        focus(d);
      })
      .on("mousemove", ev => {
        const r = wrap.getBoundingClientRect();
        tip.style.left = Math.max(4, Math.min(ev.clientX - r.left + 12, r.width - 290)) + "px";
        tip.style.top = (ev.clientY - r.top + 12) + "px";
      })
      .on("mouseleave", () => { tip.style.display = "none"; refresh(); })
      .call(d3.drag()
        .on("start", (ev, d) => { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag", (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
        .on("end", (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
    const label = root.append("g").selectAll("text").data(nodes).join("text")
      .text(d => d.label).attr("font-size", 10).attr("fill", ink).attr("dx", d => radius(d) + 3)
      .attr("dy", "0.35em").style("pointer-events", "none")
      .style("display", d => labelled(d) ? null : "none");

    const neighbors = new Map(nodes.map(n => [n.id, new Set([n.id])]));
    links.forEach(l => { neighbors.get(l.source).add(l.target); neighbors.get(l.target).add(l.source); });

    function refresh() {
      const showOrph = document.getElementById("g-orphans").checked;
      const showDup = document.getElementById("g-dups").checked;
      const q = document.getElementById("g-search").value.trim().toLowerCase();
      const hit = d => (!q || d.label.toLowerCase().includes(q) || d.id.toLowerCase().includes(q))
        && (!showOrph || orphans.has(d.id)) && (!showDup || dupOf.has(d.id));
      const any = q || showOrph || showDup;
      node.attr("opacity", d => !any || hit(d) ? 1 : 0.15)
        .attr("stroke", d => any && hit(d) ? ink : panel);
      link.attr("stroke-opacity", any ? 0.06 : 0.18);
      label.attr("opacity", d => !any || hit(d) ? 1 : 0.15)
        .style("display", d => (scale > 1.5 || labelled(d) || (any && hit(d))) ? null : "none");
    }
    function focus(d) {
      const near = neighbors.get(d.id);
      node.attr("opacity", n => near.has(n.id) ? 1 : 0.15);
      label.attr("opacity", n => near.has(n.id) ? 1 : 0.15)
        .style("display", n => near.has(n.id) ? null : "none");
      link.attr("stroke-opacity", l => (l.source.id === d.id || l.target.id === d.id) ? 0.7 : 0.04);
    }
    ["g-orphans", "g-dups"].forEach(id => document.getElementById(id).onchange = refresh);
    const search = document.getElementById("g-search");
    search.oninput = refresh;
    search.onkeydown = ev => {
      if (ev.key !== "Enter") return;
      const q = search.value.trim().toLowerCase();
      const d = nodes.find(n => n.label.toLowerCase().includes(q) || n.id.toLowerCase().includes(q));
      if (!d) return;
      svg.transition().duration(600).call(zoom.transform,
        d3.zoomIdentity.translate(w / 2, h / 2).scale(2.2).translate(-d.x, -d.y));
    };

    // Frame the whole graph (a small wiki would otherwise sit as a tight
    // cluster in the middle of the canvas).
    function frame() {
      if (!nodes.length) return;
      const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
      const x0 = Math.min(...xs) - 30, x1 = Math.max(...xs) + 90;
      const y0 = Math.min(...ys) - 30, y1 = Math.max(...ys) + 30;
      const k = Math.max(0.2, Math.min(2.2, 0.92 * Math.min(w / (x1 - x0), h / (y1 - y0))));
      svg.call(zoom.transform,
        d3.zoomIdentity.translate(w / 2, h / 2).scale(k).translate(-(x0 + x1) / 2, -(y0 + y1) / 2));
    }
    function draw() {
      link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      node.attr("cx", d => d.x).attr("cy", d => d.y);
      label.attr("x", d => d.x).attr("y", d => d.y);
    }

    sim = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(links).id(d => d.id).distance(55).strength(0.4))
      .force("charge", d3.forceManyBody().strength(-120))
      .force("center", d3.forceCenter(w / 2, h / 2))
      .force("x", d3.forceX(w / 2).strength(0.04)).force("y", d3.forceY(h / 2).strength(0.06))
      .force("collide", d3.forceCollide(d => radius(d) + 2))
      .on("tick", draw)
      .stop();
    // Lay out up front, then show the settled graph at once (it also works in
    // a background tab, where animation frames never fire); dragging restarts it.
    sim.tick(300);
    draw();
    frame();
    refresh();
  }

  document.getElementById("g-temporal").onchange = load;
  load();
})();
"""
