/* Aivoraflux hero visual: a slowly rotating 3D network of nodes with data
   packets travelling along its connections. Canvas 2D, no dependencies.
   Pauses off-screen and in background tabs; draws one still frame when the
   visitor prefers reduced motion. Colours are the brand palette only. */
(function () {
  "use strict";
  var canvas = document.querySelector("[data-network]");
  if (!canvas || !canvas.getContext) return;
  var ctx = canvas.getContext("2d");
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var MIST = "204,220,239", CORAL = "255,101,116";
  var W = 0, H = 0, DPR = 1, R = 0;
  var nodes = [], edges = [], packets = [];
  var rotY = 0, tiltX = -0.32, targetTX = -0.32, targetTY = 0, offY = 0;
  var running = false, raf = 0, last = 0, spawnAt = 0;

  // Deterministic pseudo-random so the structure looks the same on every load.
  var seed = 7;
  function rnd() { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; }

  function build() {
    nodes = []; edges = []; packets = []; seed = 7;
    var count = W < 420 ? 48 : 84;
    var hubs = 5;
    for (var i = 0; i < count; i++) {
      // Points on/inside a sphere, biased to the shell for a clear 3D form.
      var u = rnd() * 2 - 1, t = rnd() * Math.PI * 2, s = Math.sqrt(1 - u * u);
      var rad = i < hubs ? 0.55 + rnd() * 0.2 : 0.62 + rnd() * 0.38;
      nodes.push({
        x: s * Math.cos(t) * rad, y: u * rad * 0.92, z: s * Math.sin(t) * rad,
        hub: i < hubs, phase: rnd() * Math.PI * 2, px: 0, py: 0, pz: 0, ps: 1
      });
    }
    // Connect each node to its nearest neighbours; hubs connect to each other.
    for (var a = 0; a < nodes.length; a++) {
      var d = [];
      for (var b = 0; b < nodes.length; b++) if (a !== b) {
        var dx = nodes[a].x - nodes[b].x, dy = nodes[a].y - nodes[b].y, dz = nodes[a].z - nodes[b].z;
        d.push([dx * dx + dy * dy + dz * dz, b]);
      }
      d.sort(function (p, q) { return p[0] - q[0]; });
      var k = nodes[a].hub ? 6 : 3;
      for (var j = 0; j < k; j++) addEdge(a, d[j][1], false);
    }
    for (var h1 = 0; h1 < hubs; h1++) for (var h2 = h1 + 1; h2 < hubs; h2++) addEdge(h1, h2, true);
  }
  function addEdge(a, b, core) {
    for (var i = 0; i < edges.length; i++) if ((edges[i].a === a && edges[i].b === b) || (edges[i].a === b && edges[i].b === a)) return;
    edges.push({ a: a, b: b, core: core });
  }

  function resize() {
    var rect = canvas.getBoundingClientRect();
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    W = rect.width; H = rect.height;
    canvas.width = Math.round(W * DPR); canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    R = Math.min(W, H) * 0.36;
    build();
    draw(performance.now());
  }

  function project(time) {
    var cy = Math.cos(rotY + offY), sy = Math.sin(rotY + offY), cx = Math.cos(tiltX), sx = Math.sin(tiltX);
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      var breathe = 1 + Math.sin(time * 0.0006 + n.phase) * 0.025;
      var x = n.x * breathe, y = n.y * breathe, z = n.z * breathe;
      var x1 = x * cy - z * sy, z1 = x * sy + z * cy;
      var y2 = y * cx - z1 * sx, z2 = y * sx + z1 * cx;
      var persp = 1 / (1.9 - z2 * 0.55);
      n.px = W / 2 + x1 * R * persp * 2.35;
      n.py = H / 2 + y2 * R * persp * 2.35;
      n.pz = z2; n.ps = persp;
    }
  }

  function ring(fn, scale, color) {
    var cy = Math.cos(rotY + offY), sy = Math.sin(rotY + offY), cx = Math.cos(tiltX), sx = Math.sin(tiltX);
    ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.beginPath();
    for (var i = 0; i <= 96; i++) {
      var p = fn(i / 96 * Math.PI * 2), x = p[0] * scale, y = p[1] * scale, z = p[2] * scale;
      var x1 = x * cy - z * sy, z1 = x * sy + z * cy, y2 = y * cx - z1 * sx, z2 = y * sx + z1 * cx;
      var persp = 1 / (1.9 - z2 * 0.55);
      var X = W / 2 + x1 * R * persp * 2.35, Y = H / 2 + y2 * R * persp * 2.35;
      if (i) ctx.lineTo(X, Y); else ctx.moveTo(X, Y);
    }
    ctx.stroke();
  }

  function depthAlpha(z) { return 0.18 + (z + 1) * 0.4; }

  function draw(time) {
    ctx.clearRect(0, 0, W, H);
    project(time);

    // Orbit rings: an equator and a tilted meridian, projected like the nodes.
    ring(function (t) { return [Math.cos(t), 0, Math.sin(t)]; }, 1.08, "rgba(" + MIST + ",0.10)");
    ring(function (t) { return [Math.cos(t) * 0.5, Math.sin(t), Math.cos(t) * 0.86]; }, 1.0, "rgba(" + CORAL + ",0.10)");

    // Connections.
    for (var i = 0; i < edges.length; i++) {
      var e = edges[i], A = nodes[e.a], B = nodes[e.b];
      var al = depthAlpha((A.pz + B.pz) / 2);
      ctx.strokeStyle = e.core ? "rgba(" + CORAL + "," + (al * 0.45).toFixed(3) + ")" : "rgba(" + MIST + "," + (al * 0.28).toFixed(3) + ")";
      ctx.lineWidth = e.core ? 1.2 : 0.8;
      ctx.beginPath(); ctx.moveTo(A.px, A.py); ctx.lineTo(B.px, B.py); ctx.stroke();
    }

    // Data packets.
    for (var p = 0; p < packets.length; p++) {
      var k = packets[p], E = edges[k.e], P = nodes[k.rev ? E.b : E.a], Q = nodes[k.rev ? E.a : E.b];
      var x = P.px + (Q.px - P.px) * k.t, y = P.py + (Q.py - P.py) * k.t;
      var tail = Math.max(0, k.t - 0.18);
      var tx = P.px + (Q.px - P.px) * tail, ty = P.py + (Q.py - P.py) * tail;
      var g = ctx.createLinearGradient(tx, ty, x, y);
      g.addColorStop(0, "rgba(" + CORAL + ",0)"); g.addColorStop(1, "rgba(" + CORAL + ",0.95)");
      ctx.strokeStyle = g; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(x, y); ctx.stroke();
      ctx.fillStyle = "rgba(" + CORAL + ",1)";
      ctx.beginPath(); ctx.arc(x, y, 2.2, 0, Math.PI * 2); ctx.fill();
    }

    // Nodes, back to front.
    var order = nodes.slice().sort(function (a, b) { return a.pz - b.pz; });
    for (var j = 0; j < order.length; j++) {
      var n = order[j], a = depthAlpha(n.pz), r = (n.hub ? 4.4 : 1.9) * n.ps * 2.2;
      if (n.hub) {
        var pulse = 0.5 + 0.5 * Math.sin(time * 0.002 + n.phase);
        ctx.fillStyle = "rgba(" + CORAL + "," + (0.12 + pulse * 0.12).toFixed(3) + ")";
        ctx.beginPath(); ctx.arc(n.px, n.py, r * (3 + pulse * 1.4), 0, Math.PI * 2); ctx.fill();
        ctx.shadowColor = "rgba(" + CORAL + ",0.8)"; ctx.shadowBlur = 16;
        ctx.fillStyle = "rgba(" + CORAL + "," + Math.min(1, a + 0.2).toFixed(3) + ")";
      } else {
        ctx.shadowBlur = 0;
        ctx.fillStyle = "rgba(" + MIST + "," + a.toFixed(3) + ")";
      }
      ctx.beginPath(); ctx.arc(n.px, n.py, r, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
    }
  }

  function step(now) {
    var dt = Math.min(50, now - (last || now)); last = now;
    rotY += dt * 0.00012;
    tiltX += (targetTX - tiltX) * 0.04;
    offY += (targetTY - offY) * 0.04;
    if (now > spawnAt && packets.length < (W < 420 ? 8 : 16)) {
      packets.push({ e: Math.floor(Math.random() * edges.length), t: 0, v: 0.0007 + Math.random() * 0.0009, rev: Math.random() < 0.5 });
      spawnAt = now + 180 + Math.random() * 260;
    }
    for (var i = packets.length - 1; i >= 0; i--) {
      packets[i].t += packets[i].v * dt;
      if (packets[i].t >= 1) packets.splice(i, 1);
    }
    draw(now);
    raf = requestAnimationFrame(step);
  }

  function start() { if (!running && !reduce) { running = true; last = 0; raf = requestAnimationFrame(step); } }
  function stop() { running = false; cancelAnimationFrame(raf); }

  resize();
  var ro = "ResizeObserver" in window ? new ResizeObserver(function () { resize(); }) : null;
  if (ro) ro.observe(canvas); else window.addEventListener("resize", resize);

  if (!reduce) {
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        entries.forEach(function (en) { if (en.isIntersecting && !document.hidden) start(); else stop(); });
      }).observe(canvas);
    } else start();
    document.addEventListener("visibilitychange", function () { if (document.hidden) stop(); else start(); });
    var heroEl = canvas.closest("[data-hero]") || canvas;
    if (window.matchMedia("(pointer: fine)").matches) {
      heroEl.addEventListener("pointermove", function (e) {
        var r = heroEl.getBoundingClientRect();
        targetTY = ((e.clientX - r.left) / r.width - 0.5) * 0.5;
        targetTX = -0.32 + ((e.clientY - r.top) / r.height - 0.5) * 0.35;
      });
      heroEl.addEventListener("pointerleave", function () { targetTY = 0; targetTX = -0.32; });
    }
  }
})();
