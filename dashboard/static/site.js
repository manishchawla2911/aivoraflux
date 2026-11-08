/* Aivoraflux public site — small progressive enhancements, no dependencies. */
(function () {
  "use strict";
  document.documentElement.classList.remove("no-js");
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Header border once the page scrolls.
  var header = document.querySelector(".header");
  if (header) {
    var onScroll = function () { header.classList.toggle("is-scrolled", window.scrollY > 8); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  // Mobile menu.
  var menuBtn = document.querySelector("[data-menu-toggle]");
  var mobileNav = document.getElementById("mobile-nav");
  if (menuBtn && mobileNav) {
    var setOpen = function (open) {
      mobileNav.classList.toggle("is-open", open);
      document.body.classList.toggle("nav-open", open);
      menuBtn.setAttribute("aria-expanded", String(open));
      menuBtn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
      menuBtn.querySelector("[data-icon-open]").style.display = open ? "none" : "";
      menuBtn.querySelector("[data-icon-close]").style.display = open ? "" : "none";
    };
    menuBtn.addEventListener("click", function () { setOpen(!mobileNav.classList.contains("is-open")); });
    mobileNav.addEventListener("click", function (e) { if (e.target.closest("a")) setOpen(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") setOpen(false); });
    window.addEventListener("resize", function () { if (window.innerWidth > 1020) setOpen(false); });
  }

  // Scroll reveal with per-group stagger.
  var items = Array.prototype.slice.call(document.querySelectorAll("[data-reveal]"));
  if (reduce || !("IntersectionObserver" in window)) {
    items.forEach(function (el) { el.classList.add("is-visible"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        var group = el.closest("[data-reveal-group]");
        if (group) {
          var sibs = Array.prototype.slice.call(group.querySelectorAll("[data-reveal]"));
          el.style.setProperty("--d", Math.min(sibs.indexOf(el), 6) * 80 + "ms");
        }
        el.classList.add("is-visible");
        io.unobserve(el);
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -6% 0px" });
    items.forEach(function (el) { io.observe(el); });

    // Fast jumps (End key, anchor links, scrollbar drags) can skip past an
    // element without it ever intersecting; reveal anything already passed.
    var pending = false;
    var sweep = function () {
      pending = false;
      var limit = window.innerHeight;
      items = items.filter(function (el) {
        if (el.classList.contains("is-visible")) return false;
        if (el.getBoundingClientRect().top < limit) { el.classList.add("is-visible"); io.unobserve(el); return false; }
        return true;
      });
      if (!items.length) window.removeEventListener("scroll", onSweep);
    };
    var onSweep = function () { if (!pending) { pending = true; setTimeout(sweep, 150); } };
    window.addEventListener("scroll", onSweep, { passive: true });
    window.addEventListener("load", sweep);
  }

  // Hero workflow: step through the example invoice, then loop.
  var flow = document.querySelector("[data-flow]");
  if (flow) {
    var steps = Array.prototype.slice.call(flow.querySelectorAll("li"));
    var status = flow.querySelector("[data-flow-status]");
    var setStatus = function (text, state) {
      status.textContent = text;
      status.classList.toggle("is-running", state === "run");
      status.classList.toggle("is-done", state === "done");
    };
    var paint = function (i) {
      steps.forEach(function (li, n) {
        li.classList.toggle("is-done", n < i);
        li.classList.toggle("is-active", n === i);
      });
      flow.style.setProperty("--p", steps.length > 1 ? Math.min(i, steps.length - 1) / (steps.length - 1) : 0);
    };
    if (reduce) {
      paint(steps.length);
      setStatus("Posted", "done");
    } else {
      flow.classList.add("is-animated");
      var i = -1, timer = null;
      var tick = function () {
        i += 1;
        if (i < steps.length) {
          paint(i);
          setStatus(steps[i].getAttribute("data-label") + "…", "run");
          timer = setTimeout(tick, i === 3 ? 2200 : 1500);
        } else if (i === steps.length) {
          paint(steps.length);
          setStatus("Posted", "done");
          timer = setTimeout(tick, 3200);
        } else {
          i = -1; paint(-1); setStatus("Waiting", null);
          timer = setTimeout(tick, 900);
        }
      };
      // Only run while the card is on screen.
      if ("IntersectionObserver" in window) {
        new IntersectionObserver(function (entries) {
          entries.forEach(function (e) {
            if (e.isIntersecting && !timer) timer = setTimeout(tick, 600);
            else if (!e.isIntersecting && timer) { clearTimeout(timer); timer = null; }
          });
        }).observe(flow);
      } else { tick(); }
    }
  }

  // Enquiry form: client-side validation mirrors the server rules.
  var form = document.querySelector("[data-enquiry-form]");
  if (form) {
    var emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
    var validate = function (field) {
      var input = field.querySelector("input, select, textarea");
      if (!input) return true;
      var v = (input.value || "").trim();
      var msg = "";
      if (input.required && !v) msg = "This field is required.";
      else if (input.type === "email" && v && !emailRe.test(v)) msg = "Enter a valid business email.";
      else if (input.minLength > 0 && v && v.length < input.minLength) msg = "Please add a little more detail (at least " + input.minLength + " characters).";
      field.classList.toggle("has-error", !!msg);
      var err = field.querySelector(".error");
      if (err) err.textContent = msg;
      input.setAttribute("aria-invalid", msg ? "true" : "false");
      return !msg;
    };
    var fields = Array.prototype.slice.call(form.querySelectorAll(".field"));
    fields.forEach(function (f) {
      var input = f.querySelector("input, select, textarea");
      if (input) input.addEventListener("blur", function () { validate(f); });
      if (input) input.addEventListener("input", function () { if (f.classList.contains("has-error")) validate(f); });
    });
    form.addEventListener("submit", function (e) {
      var ok = true, first = null;
      fields.forEach(function (f) { if (!validate(f)) { ok = false; first = first || f; } });
      if (!ok) {
        e.preventDefault();
        var el = first.querySelector("input, select, textarea");
        if (el) el.focus();
        return;
      }
      var btn = form.querySelector("button[type=submit]");
      if (btn) { btn.disabled = true; btn.textContent = "Sending…"; }
    });
  }

  // Hero entrance: staggered word reveal + fade-ups (CSS handles timing).
  var hero = document.querySelector("[data-hero]");
  if (hero) requestAnimationFrame(function () { requestAnimationFrame(function () { hero.classList.add("is-in"); hero.querySelectorAll(".reveal-words").forEach(function (h) { h.classList.add("is-in"); }); }); });

  // Expertise cards: pointer-follow spotlight and a gentle tilt (fine pointers only).
  var finePointer = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  if (finePointer && !reduce) {
    document.querySelectorAll("[data-tilt]").forEach(function (card) {
      var raf = 0;
      card.addEventListener("pointermove", function (e) {
        var r = card.getBoundingClientRect();
        var x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(function () {
          card.style.setProperty("--mx", (x * 100).toFixed(1) + "%");
          card.style.setProperty("--my", (y * 100).toFixed(1) + "%");
          card.style.setProperty("--rx", ((0.5 - y) * 4).toFixed(2) + "deg");
          card.style.setProperty("--ry", ((x - 0.5) * 5).toFixed(2) + "deg");
        });
      });
      card.addEventListener("pointerleave", function () {
        card.style.setProperty("--rx", "0deg"); card.style.setProperty("--ry", "0deg");
      });
    });
  }

  // Scroll-linked effects, batched in one rAF.
  var scrubs = Array.prototype.slice.call(document.querySelectorAll("[data-scrub]"));
  scrubs.forEach(function (el) {
    var words = el.textContent.trim().split(/\s+/);
    el.innerHTML = words.map(function (w) { return '<span class="w">' + w + "</span>"; }).join(" ");
  });
  var approach = document.querySelector("[data-approach]");
  var stages = approach ? Array.prototype.slice.call(approach.querySelectorAll("[data-stage]")) : [];
  var track = approach ? approach.querySelector(".approach__track") : null;
  var ticking = false;
  var onScrollFx = function () {
    ticking = false;
    var vh = window.innerHeight;
    scrubs.forEach(function (el) {
      var r = el.getBoundingClientRect();
      var p = reduce ? 1 : Math.min(1, Math.max(0, (vh * 0.9 - r.top) / (vh * 0.55)));
      var ws = el.children, n = ws.length;
      for (var i = 0; i < n; i++) ws[i].style.opacity = (i / n < p ? 1 : 0.22);
    });
    if (track) {
      var tr = track.getBoundingClientRect();
      var prog = reduce ? 1 : Math.min(1, Math.max(0, (vh * 0.75 - tr.top) / (tr.height + vh * 0.25)));
      track.style.setProperty("--progress", prog.toFixed(3));
      var active = Math.round(prog * (stages.length - 1) + 0.35);
      stages.forEach(function (s, i) { s.classList.toggle("is-active", i <= active - (prog < 0.02 ? 1 : 0)); });
    }
  };
  if (scrubs.length || track) {
    window.addEventListener("scroll", function () { if (!ticking) { ticking = true; requestAnimationFrame(onScrollFx); } }, { passive: true });
    window.addEventListener("resize", onScrollFx);
    onScrollFx();
  }
  stages.forEach(function (s, i) {
    s.addEventListener("pointerenter", function () { stages.forEach(function (o, j) { o.classList.toggle("is-hover", j === i); }); });
  });

  // Industries: accessible tabs (arrow keys, Home/End).
  document.querySelectorAll("[data-industries]").forEach(function (root) {
    var tabs = Array.prototype.slice.call(root.querySelectorAll('[role="tab"]'));
    var select = function (tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        var panel = document.getElementById(t.getAttribute("aria-controls"));
        if (panel) { panel.hidden = !on; panel.classList.toggle("is-shown", on); }
      });
      if (focus) tab.focus();
    };
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(t, false); });
      if (finePointer) t.addEventListener("mouseenter", function () { select(t, false); });
      t.addEventListener("keydown", function (e) {
        var k = e.key, n = null;
        if (k === "ArrowDown" || k === "ArrowRight") n = tabs[(i + 1) % tabs.length];
        else if (k === "ArrowUp" || k === "ArrowLeft") n = tabs[(i - 1 + tabs.length) % tabs.length];
        else if (k === "Home") n = tabs[0];
        else if (k === "End") n = tabs[tabs.length - 1];
        if (n) { e.preventDefault(); select(n, true); }
      });
    });
  });
})();
