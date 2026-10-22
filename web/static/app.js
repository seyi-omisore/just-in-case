// Just in Case: browser side.
// Everything the agent says is inserted as text (never as raw HTML).

(() => {
  const $ = (id) => document.getElementById(id);
  const messages = $("messages"), feed = $("feed"), planEl = $("plan");
  const input = $("message"), sendBtn = $("send"), typing = $("typing");
  let ws, sessionId, busy = false, seenItems = new Set(), cardUrl = null;
  const USPS_HOLD = "https://holdmail.usps.com/holdmail/";

  // ---- small helpers -----------------------------------------------------
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const icon = (name) => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("class", "icon"); s.setAttribute("aria-hidden", "true");
    const u = document.createElementNS("http://www.w3.org/2000/svg", "use");
    u.setAttribute("href", "#i-" + name); s.appendChild(u);
    return s;
  };
  const NEED_ICON = {
    ride_home_after_procedure: "car", transportation: "car", spare_key_access: "key",
    pet_care_during_stay: "paw", mail_while_away: "mail", power_outage: "bolt",
    help_at_home: "home", home_repair: "home", fall_at_home: "shield",
    check_in_after_procedure: "heart",
  };
  const ROUTE = {
    service: ["A service", "check"], preparation: ["Get ready", "check"],
    person: ["A friend helping", "person"], blank: ["Not covered yet", "dash"],
  };
  const FEED_ICON = {
    search: "search", add: "plus", rule: "shield", wait: "wait", human: "hand",
    ask: "check", note: "note", write: "pencil", card: "card", heart: "heart",
  };

  // Turn the agent's simple markdown into safe DOM: paragraphs, "- " lists,
  // **bold**, and tappable phone numbers.
  function inline(parent, text) {
    // Phone numbers: 617-482-1510, 1-800-243-4636, 988, 2-1-1, 911.
    const PHONE = /^(?:1-)?\d{3}-\d{3}-\d{4}$|^(?:988|911|2-1-1)$/;
    const parts = text.split(/(\*\*[^*]+\*\*|\b(?:1-)?\d{3}-\d{3}-\d{4}\b|\b2-1-1\b|\b988\b|\b911\b)/g);
    for (const p of parts) {
      if (!p) continue;
      if (p.startsWith("**") && p.endsWith("**")) inline(parent.appendChild(el("strong")), p.slice(2, -2));
      else if (PHONE.test(p)) {
        const a = el("a", null, p); a.href = "tel:" + p.replace(/-/g, ""); parent.appendChild(a);
      } else parent.appendChild(document.createTextNode(p));
    }
  }
  function renderText(text) {
    const frag = document.createDocumentFragment();
    for (const block of text.trim().split(/\n\s*\n/)) {
      const lines = block.split("\n").filter((l) => l.trim());
      if (lines.length && lines.every((l) => /^\s*[-*]\s+/.test(l))) {
        const ul = el("ul");
        for (const l of lines) { const li = el("li"); inline(li, l.replace(/^\s*[-*]\s+/, "")); ul.appendChild(li); }
        frag.appendChild(ul);
      } else {
        const p = el("p"); inline(p, lines.join(" ")); frag.appendChild(p);
      }
    }
    return frag;
  }

  function scrollChat() {
    const last = messages.lastElementChild;
    if (last) last.scrollIntoView({ block: "end", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }

  // ---- messages ----------------------------------------------------------
  function addMessage(who, text, extraClass) {
    const li = el("li", `msg msg-${who}${extraClass ? " " + extraClass : ""}`);
    if (who === "agent") {
      const av = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      av.setAttribute("viewBox", "0 0 48 48"); av.setAttribute("class", "msg-avatar"); av.setAttribute("aria-hidden", "true");
      av.innerHTML = '<circle cx="24" cy="24" r="24" fill="#D9EEFA"/><path d="M11 24 24 12l13 12v14H11z" fill="#0072B2"/><path d="M24 34s-6-3.6-6-7.6a3.3 3.3 0 0 1 6-1.9 3.3 3.3 0 0 1 6 1.9c0 4-6 7.6-6 7.6z" fill="#E69F00"/>';
      li.appendChild(av);
    }
    const body = el("div", "msg-body");
    const label = el("span", "sr-only", who === "agent" ? "Just in Case said: " : "You said: ");
    body.appendChild(label);
    body.appendChild(renderText(text));
    li.appendChild(body);
    messages.appendChild(li);
    scrollChat();
  }

  // ---- checkpoints -------------------------------------------------------
  const SLIP_TITLE = {
    confirm_needs: "Does this list look right?",
    draft_helper_note: "Here is a note you can send",
    make_fridge_card: "Here is what goes on your fridge card",
    add_helper_task: "Add this to your helper table?",
  };
  function addApproval(msg) {
    const { id, kind, data } = msg;
    const li = el("li", "slip");
    const head = el("div", "slip-head"); head.append(icon("check"), document.createTextNode(SLIP_TITLE[kind] || "Please check this"));
    const body = el("div", "slip-body");

    if (kind === "confirm_needs") {
      body.appendChild(el("p", null, "Here is what we'll plan for:"));
      const ul = el("ul", "slip-needs");
      for (const n of data.needs) { const i = el("li"); i.append(icon(NEED_ICON[n.key] || "check"), document.createTextNode(n.label)); ul.appendChild(i); }
      body.appendChild(ul);
    } else if (kind === "draft_helper_note") {
      body.appendChild(el("p", null, "For " + data.to.join(" and ") + ". We never send it. You send it yourself if you like."));
      const note = el("div", "sticky"); const nh = el("span", "sticky-head"); nh.append(icon("note"), document.createTextNode("Your note"));
      note.append(nh, el("p", null, data.text)); body.appendChild(note);
    } else if (kind === "add_helper_task") {
      const dl = el("dl");
      const row = (t, d) => { if (d) dl.append(el("dt", null, t), el("dd", null, d)); };
      row("Task", data.task); row("Who is helping", data.person); row("Date", data.date); row("Time", data.time);
      body.appendChild(dl);
      body.appendChild(el("p", "helper-small", "You can change or remove it later."));
    } else if (kind === "make_fridge_card") {
      const dl = el("dl");
      const row = (t, d) => { dl.append(el("dt", null, t), el("dd", null, d)); };
      // Only what the user gave. Empty parts are left off, not shown as "None".
      if (data.name) row("Name", data.name);
      if (data.contacts.length) row("People to contact", data.contacts.map((c) => `${c.name}${c.relationship ? " (" + c.relationship + ")" : ""}, ${c.phone}`).join("; "));
      if (data.medications.length) row("Medications, in your words", data.medications.join("; "));
      if (data.pet) row("Pet", data.pet + (data.pet_caregiver ? `, looked after by ${data.pet_caregiver}` : ""));
      if (!dl.children.length) dl.appendChild(el("dd", null, "Just the emergency lines and your plan."));
      body.appendChild(dl);
    }

    const actions = el("div", "slip-actions");
    const yes = el("button", "btn btn-primary"); yes.type = "button"; yes.append(icon("check"), document.createTextNode("Yes, looks good"));
    const no = el("button", "btn btn-quiet"); no.type = "button"; no.append(icon("pencil"), document.createTextNode("Change something"));
    const answer = (ok) => {
      ws.send(JSON.stringify({ type: "answer", id, ok }));
      actions.remove();
      const done = el("p", "slip-done " + (ok ? "yes" : "no"));
      done.append(icon(ok ? "check" : "pencil"), document.createTextNode(ok ? "You said yes" : "You asked for a change"));
      li.appendChild(done);
      if (!ok) input.focus();
    };
    yes.onclick = () => answer(true);
    no.onclick = () => answer(false);
    actions.append(yes, no);
    li.append(head, body, actions);
    messages.appendChild(li);
    scrollChat();
    yes.focus({ preventScroll: true });
  }

  // ---- activity feed -----------------------------------------------------
  function addActivity(kind, text) {
    const empty = feed.querySelector(".feed-empty"); if (empty) empty.remove();
    const li = el("li", "k-" + kind);
    const dot = el("span", "feed-dot"); dot.appendChild(icon(FEED_ICON[kind] || "info"));
    li.append(dot, el("span", null, text));
    feed.appendChild(li);
    feed.scrollTop = feed.scrollHeight;
    if (kind === "heart") {
      const box = $("support"); box.open = true; box.classList.add("glow");
    }
  }

  // ---- the fridge (plan) -------------------------------------------------
  function renderPlan(plan) {
    $("fridge-empty").hidden = plan.items.length > 0 || plan.notes.length > 0;
    planEl.replaceChildren();
    for (const item of plan.items) {
      const key = item.need + ":" + item.route + ":" + item.name;
      const li = el("li", "tag" + (seenItems.has(key) ? "" : " new"));
      seenItems.add(key);
      li.dataset.need = item.need;
      const mag = el("span", "magnet"); mag.appendChild(icon(NEED_ICON[item.need] || "check"));
      li.appendChild(mag);
      li.appendChild(el("p", "tag-need", item.need_label));
      li.appendChild(el("p", "tag-name", item.route === "blank" ? "Not covered yet" : item.name));
      const [rWord, rIcon] = ROUTE[item.route] || ["", "check"];
      const r = el("span", "tag-route route-" + item.route); r.append(icon(rIcon), document.createTextNode(rWord));
      li.appendChild(r);

      const links = el("p", "tag-links");
      if (item.phone && item.phone !== "see website") {
        const a = el("a"); a.href = "tel:" + item.phone.replace(/[^0-9]/g, ""); a.append(icon("phone"), document.createTextNode(item.phone)); links.appendChild(a);
      }
      if (item.url) {
        const a = el("a"); a.href = item.url; a.target = "_blank"; a.rel = "noopener";
        a.append(icon("info"), document.createTextNode("Website")); links.appendChild(a);
      }
      if (links.childNodes.length) li.appendChild(links);
      if (item.need === "mail_while_away") {
        const a = el("a", "btn btn-primary tag-action"); a.href = USPS_HOLD; a.target = "_blank"; a.rel = "noopener";
        a.append(icon("mail"), document.createTextNode("Hold my mail on USPS.com"));
        li.appendChild(a);
      }

      if (item.flags && item.flags.length) {
        const ul = el("ul", "tag-flags");
        for (const f of item.flags) {
          const fi = el("li"); fi.append(icon(/WAITLIST/.test(f) ? "wait" : "info"), document.createTextNode(plainFlag(f)));
          ul.appendChild(fi);
        }
        li.appendChild(ul);
      }
      planEl.appendChild(li);
    }

    const notes = $("notes"); notes.replaceChildren();
    for (const n of plan.notes) {
      const s = el("div", "sticky"); const h = el("span", "sticky-head");
      h.append(icon("note"), document.createTextNode("Note for " + n.helpers.join(" and ")));
      s.append(h, el("p", null, n.text)); notes.appendChild(s);
    }

    renderTasks(plan);
    const card = $("card-link");
    cardUrl = plan.card;
    card.hidden = !plan.card;
  }
  // Flags come from the rules in code; show them in friendlier words.
  function plainFlag(f) {
    return f.replace(/^WAITLIST:\s*/, "")
      .replace(/^NO PHONE LISTED:\s*/, "")
      .replace(/^NOT RECENTLY CHECKED:\s*/, "")
      .replace(/^SAMPLE ENTRY:\s*/, "Sample: ")
      .replace(/^(.)/, (c) => c.toUpperCase());
  }

  // ---- connection --------------------------------------------------------
  function setBusy(on) {
    busy = on; typing.hidden = !on; sendBtn.disabled = on;
    if (on) typing.scrollIntoView({ block: "end" });
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    setBusy(true);
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      switch (m.type) {
        case "hello":
          sessionId = m.session; $("demo-banner").hidden = !m.demo;
          setBusy(false);
          if (!messages.children.length) addMessage("agent", m.greeting);
          if (!feed.children.length) feed.appendChild(el("li", "feed-empty", "Each step shows up here while Just in Case works."));
          break;
        case "agent": addMessage("agent", m.text); break;
        case "activity": addActivity(m.kind, m.text); break;
        case "approval": addApproval(m); break;
        case "plan": renderPlan(m.plan); break;
        case "busy": setBusy(m.on); break;
        case "table_error": $("task-error").textContent = m.text; break;
        case "error": addMessage("agent", m.text, "msg-error"); setBusy(false); break;
      }
    };
    ws.onclose = () => {
      setBusy(false);
      addMessage("agent", "The connection to Just in Case was lost. Please reload the page to start again.", "msg-error");
    };
  }

  function send(text) {
    text = text.trim();
    if (!text || busy || !ws || ws.readyState !== 1) return;
    addMessage("user", text);
    $("starters").hidden = true;
    ws.send(JSON.stringify({ type: "say", text }));
    input.value = "";
  }

  $("composer").addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input.value); }
  });
  for (const b of document.querySelectorAll(".starter")) b.addEventListener("click", () => send(b.dataset.text));

  // ---- text size (remembered on this device only) ------------------------
  const SIZES = [18, 20, 22, 24, 27];
  let sizeIdx = 1;
  try { const s = Number(localStorage.getItem("jic-size")); if (SIZES.includes(s)) sizeIdx = SIZES.indexOf(s); } catch (_) {}
  const applySize = () => {
    document.documentElement.style.setProperty("--fs", SIZES[sizeIdx] + "px");
    try { localStorage.setItem("jic-size", SIZES[sizeIdx]); } catch (_) {}
  };
  applySize();
  $("bigger").onclick = () => { sizeIdx = Math.min(SIZES.length - 1, sizeIdx + 1); applySize(); };
  $("smaller").onclick = () => { sizeIdx = Math.max(0, sizeIdx - 1); applySize(); };

  // ---- helper table ------------------------------------------------------
  let editingId = null, seenTasks = new Set(), tableUrls = {};
  const form = $("task-form");
  function renderTasks(plan) {
    tableUrls = { csv: plan.table_csv, print: plan.table_print };
    $("table-download").href = plan.table_csv || "#";
    const rows = $("helper-rows"); rows.replaceChildren();
    const tasks = plan.tasks || [];
    $("helper-table").hidden = !tasks.length;
    $("table-empty").hidden = !!tasks.length;
    for (const t of tasks) {
      const tr = el("tr", seenTasks.has(t.id) ? "" : "new-row"); seenTasks.add(t.id);
      const td = (v, label, cls) => { const c = el("td", cls || "", v || ""); if (label) c.dataset.label = label; return c; };
      tr.append(td(t.task, "Task"), td(t.date, "Date"), td(t.time, "Time"), td(t.person, "Who", "who"));
      const act = el("td"); const box = el("div", "row-actions");
      const edit = el("button"); edit.type = "button"; edit.setAttribute("aria-label", "Change " + t.task); edit.title = "Change";
      edit.append(icon("pencil"), document.createTextNode("Change"));
      edit.onclick = () => startEdit(t);
      const del = el("button"); del.type = "button"; del.setAttribute("aria-label", "Remove " + t.task); del.title = "Remove";
      del.append(icon("dash"), document.createTextNode("Remove"));
      del.onclick = () => { if (confirm(`Remove "${t.task}" from your table?`)) sendTable("delete", { id: t.id }); };
      box.append(edit, del); act.appendChild(box); tr.appendChild(act);
      rows.appendChild(tr);
    }
  }
  function sendTable(action, row) {
    if (!ws || ws.readyState !== 1) return;
    $("task-error").textContent = "";
    ws.send(JSON.stringify({ type: "table", action, row }));
  }
  function startEdit(t) {
    editingId = t.id;
    for (const k of ["task", "person", "date", "time"]) form.elements[k].value = t[k] || "";
    $("task-form-title").textContent = "Change this row";
    $("task-save").querySelector("span").textContent = "Save changes";
    $("task-cancel").hidden = false;
    form.elements.task.focus();
  }
  function resetForm() {
    editingId = null; form.reset();
    $("task-form-title").textContent = "Add a row";
    $("task-save").querySelector("span").textContent = "Add row";
    $("task-cancel").hidden = true;
  }
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const row = {}; for (const k of ["task", "person", "date", "time"]) row[k] = form.elements[k].value.trim();
    if (!row.task || !row.person) { $("task-error").textContent = "Please add a task and who is helping."; return; }
    if (editingId != null) { row.id = editingId; sendTable("update", row); } else sendTable("add", row);
    resetForm();
  });
  $("task-cancel").onclick = resetForm;
  $("table-print").addEventListener("click", () => {
    if (!tableUrls.print) return;
    $("card-dialog-title").textContent = "Who's helping";
    frame.src = tableUrls.print + "?t=" + Date.now();
    dialog.showModal();
  });

  // ---- fridge card viewer ------------------------------------------------
  const dialog = $("card-dialog"), frame = $("card-frame");
  $("card-link").addEventListener("click", () => {
    if (!cardUrl) return;
    $("card-dialog-title").textContent = "Your fridge card";
    frame.src = cardUrl + "?t=" + Date.now();
    dialog.showModal();
  });
  $("card-print").addEventListener("click", () => { try { frame.contentWindow.print(); } catch (_) { window.open(cardUrl); } });
  $("card-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });

  // ---- people you can talk to ---------------------------------------------
  fetch("/api/support").then((r) => r.json()).then((lines) => {
    const ul = $("support-lines");
    for (const l of lines) {
      const li = el("li");
      li.appendChild(el("p", "s-name", l.name));
      const how = el("p", "s-how"); inline(how, l.how_to_reach); li.appendChild(how);
      li.appendChild(el("p", "s-what", l.what + (l.hours ? " " + l.hours + "." : "")));
      ul.appendChild(li);
    }
  }).catch(() => {});

  // ---- light / dark (remembered on this device only) ---------------------
  const setTheme = (t, save = true) => {
    document.documentElement.dataset.theme = t;
    $("theme-light").setAttribute("aria-pressed", String(t === "light"));
    $("theme-dark").setAttribute("aria-pressed", String(t === "dark"));
    if (save) { try { localStorage.setItem("jic-theme", t); } catch (_) {} }
  };
  setTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light", false);
  $("theme-light").onclick = () => setTheme("light");
  $("theme-dark").onclick = () => setTheme("dark");

  connect();
})();
