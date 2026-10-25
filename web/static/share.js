// The family page: see the list, add your name to an open task.
// Everything from the server is inserted as plain text.

(() => {
  const $ = (id) => document.getElementById(id);
  const shareId = location.pathname.split("/").pop();
  const ownerToken = new URLSearchParams(location.search).get("owner") || "";
  const KEY = "jic-claims-" + shareId;  // this browser's own sign-ups

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
  // A round badge with a helper's initials, same as the main page. The name is always written next to it.
  const TINTS = ["sky", "green", "orange", "purple", "yellow"];
  const initials = (name) => {
    const w = name.trim().split(/\s+/).filter((x) => /\p{L}/u.test(x));
    return w.length ? (Array.from(w[0])[0] + (w.length > 1 ? Array.from(w[w.length - 1])[0] : "")).toUpperCase() : "";
  };
  const avatar = (name) => {
    let h = 0; for (const c of name.toLowerCase()) h = (h * 31 + c.codePointAt(0)) >>> 0;
    const a = el("span", "initials tint-" + TINTS[h % TINTS.length], initials(name));
    a.setAttribute("aria-hidden", "true");
    return a;
  };
  const myClaims =() => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (_) { return {}; } };
  const saveClaims = (c) => { try { localStorage.setItem(KEY, JSON.stringify(c)); } catch (_) {} };
  const status = (t) => { $("share-status").textContent = t || ""; };

  // ---- "Add to my calendar" for a helper's own task --------------------------
  // The file is made here, in the helper's browser. No account, no app, nothing sent to our server.
  // Only dates we can read for sure get a button (the same rules as the main page).
  const MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
                  "september", "october", "november", "december"];
  function readTaskDate(text) {
    const s = String(text || "").trim().toLowerCase(), now = new Date();
    let y = null, m = null, d = null, hit;
    if ((hit = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(s))) [y, m, d] = [+hit[1], +hit[2], +hit[3]];
    else if ((hit = /^(\d{1,2})\/(\d{1,2})(?:\/(\d{2,4}))?$/.exec(s))) [m, d, y] = [+hit[1], +hit[2], hit[3] ? +hit[3] : null];
    else if ((hit = /^([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?$/.exec(s))) {
      const word = hit[1] === "sept" ? "sep" : hit[1];
      m = MONTHS.findIndex((n) => n.startsWith(word)) + 1;
      if (!m || !(word === MONTHS[m - 1] || word.length === 3)) return null;
      d = +hit[2]; y = hit[3] ? +hit[3] : null;
    } else return null;
    if (y !== null && y < 100) y += 2000;
    if (y === null) {
      y = now.getFullYear();
      if (new Date(y, m - 1, d) < new Date(now.getFullYear(), now.getMonth(), now.getDate())) y += 1;
    }
    const date = new Date(y, m - 1, d);
    if (date.getMonth() !== m - 1 || date.getDate() !== d) return null;
    return date;
  }
  function helperICS(title, day, details) {
    const esc = (s) => String(s).replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n");
    const ymd = (x) => x.getFullYear() + String(x.getMonth() + 1).padStart(2, "0") + String(x.getDate()).padStart(2, "0");
    const end = new Date(day); end.setDate(end.getDate() + 1);
    const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
    const uid = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)) + "@justincase";
    return ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Just in Case//Helper list//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
      "BEGIN:VEVENT", `UID:${uid}`, `DTSTAMP:${stamp}`, `DTSTART;VALUE=DATE:${ymd(day)}`, `DTEND;VALUE=DATE:${ymd(end)}`,
      `SUMMARY:${esc(title)}`, `DESCRIPTION:${esc(details)}`, "TRANSP:TRANSPARENT", "END:VEVENT", "END:VCALENDAR"].join("\r\n") + "\r\n";
  }
  function helperCalendarButton(t, who) {
    const day = readTaskDate(t.date);
    if (!day || day < new Date(new Date().toDateString())) return null;
    const b = el("button", "btn btn-quiet share-small"); b.type = "button";
    b.append(icon("calendar"), document.createTextNode("Add to my calendar"));
    b.onclick = () => {
      const title = (who ? `Helping ${who}: ` : "Helping: ") + t.task + (t.time ? `, ${t.time}` : "");
      const url = URL.createObjectURL(new Blob([helperICS(title, day, "From a Just in Case helper list. Thank you for helping.")], { type: "text/calendar" }));
      const a = el("a"); a.href = url; a.download = "helping.ics";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    return b;
  }

  async function api(path, body) {
    const opts = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
    const r = await fetch(`/api/share/${shareId}${path}`, opts);
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "Something went wrong. Please try again.");
    return data;
  }

  // ---- Signing up: pick one or more open tasks, and say when you're free ------------
  const DAYS = [["any", "Any day"], ["weekdays", "Weekdays"], ["weekends", "Weekends"]];
  const TIMES = [["any", "Any time"], ["mornings", "Mornings"], ["afternoons", "Afternoons"]];
  const FREE = (d, t) => d === "any" ? (t === "any" ? "any time" : t) : t === "any" ? d : (d === "weekdays" ? "weekday " : "weekend ") + t;
  const OFFERS_KEY = "jic-offers-" + shareId;   // what this browser offered, to show it again (this device only)
  const myOffers = () => { try { return JSON.parse(localStorage.getItem(OFFERS_KEY) || "{}"); } catch (_) { return {}; } };
  const saveOffers = (o) => { try { localStorage.setItem(OFFERS_KEY, JSON.stringify(o)); } catch (_) {} };
  // The person may add their phone number to the link, after "#", so helpers can text back.
  // That part of a link never reaches any server. Digits only.
  const textBack = (() => { const m = /(?:^#|&)from=(\+?\d{7,15})(?:&|$)/.exec(location.hash); return m ? m[1] : ""; })();
  const chosen = new Map();   // task id -> { days, times, note }
  let lastData = null, thanks = null;

  function radios(name, options, value, onPick, legendText) {
    const fs = el("fieldset", "free-pick"); fs.appendChild(el("legend", null, legendText));
    const ul = el("ul", "slip-checks free-options");
    for (const [v, label] of options) {
      const li2 = el("li"); const lab = el("label");
      const r = el("input"); r.type = "radio"; r.name = name; r.value = v; r.checked = v === value;
      r.onchange = () => onPick(v);
      lab.append(r, document.createTextNode(label)); li2.appendChild(lab); ul.appendChild(li2);
    }
    fs.appendChild(ul);
    return fs;
  }
  function pickBox(t) {
    const c = chosen.get(t.id);
    const box = el("div", "free-box");
    box.appendChild(radios(`days-${t.id}`, DAYS, c.days, (v) => { c.days = v; }, "Which days work for you?"));
    box.appendChild(radios(`times-${t.id}`, TIMES, c.times, (v) => { c.times = v; }, "Which times work for you?"));
    const lab = el("label", "free-note", "A short note (optional)");
    const inp = el("input"); inp.maxLength = 120; inp.value = c.note; inp.placeholder = "Like: I can bring soup too";
    inp.oninput = () => { c.note = inp.value; };
    lab.appendChild(inp); box.appendChild(lab);
    return box;
  }
  function letThemKnow(who, lines) {
    if (!textBack) return null;
    const body = `Hi${who ? " " + who : ""}, it's me. I signed up on your helper list: ${lines.join("; ")}. Thank you for asking.`;
    const a = el("a", "btn btn-quiet share-small"); a.href = `sms:${textBack}?&body=${encodeURIComponent(body)}`;
    a.append(icon("chat"), document.createTextNode(who ? `Let ${who} know` : "Let them know"));
    return a;
  }
  function renderThanks(data) {
    const box = $("share-thanks"); box.replaceChildren(); box.hidden = !thanks;
    if (!thanks) return;
    box.appendChild(el("p", "share-thanks-title", `Thank you, ${thanks.name}.`));
    const who = data.label;
    const ul = el("ul", "share-thanks-list");
    for (const x of thanks.items) {
      const li = el("li"); const t = data.tasks.find((r) => r.id === x.id) || { task: x.task, date: "", time: "" };
      li.appendChild(el("p", null, x.placed ? `You're helping with: ${t.task}.` :
        `You offered to help with: ${t.task}. ${who || "They"} will see it and let you know.`));
      if (x.placed) { const cal = helperCalendarButton(t, who); if (cal) li.appendChild(cal); }
      ul.appendChild(li);
    }
    box.appendChild(ul);
    const tell = letThemKnow(who, thanks.items.map((x) => `${x.task} (${FREE(x.days, x.times)} work for me)`));
    if (tell) box.appendChild(tell);
    const ok = el("button", "btn btn-quiet share-small"); ok.type = "button"; ok.textContent = "Done";
    ok.onclick = () => { thanks = null; renderThanks(data); };
    box.appendChild(ok);
  }

  function render(data) {
    lastData = data;
    const who = data.label;
    $("share-title").textContent = who ? `${who}'s helper list` : "A helper list";
    document.title = $("share-title").textContent;
    $("share-intro").textContent = who
      ? `${who} is making a small backup plan. Pick anything that fits your schedule. No pressure at all.`
      : "Someone close to you is making a small backup plan. Pick anything that fits your schedule. No pressure at all.";
    const d = new Date(data.expires * 1000);
    $("share-expires").textContent = `This list is deleted on ${d.toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" })}.`;
    $("owner-banner").hidden = !data.owner;
    renderThanks(data);

    const mine = myClaims(), offered = myOffers();
    const ul = $("share-rows"); ul.replaceChildren();
    for (const t of data.tasks) {
      const li = el("li", "share-row" + (t.person ? " taken" : ""));
      li.appendChild(el("p", "share-task", t.task));
      const when = [t.date, t.time].filter(Boolean).join(", ");
      if (when) li.appendChild(el("p", "share-when", when));

      const STATUS = { open: ["Open", "plus"], helping: ["Someone's helping", "person"], confirmed: ["Confirmed", "check"] };
      const [sWord, sIcon] = STATUS[t.status || (t.person ? "helping" : "open")];
      const badge = el("span", "status status-" + (t.status || "open")); badge.append(icon(sIcon), document.createTextNode(sWord));
      li.appendChild(badge);
      if (t.person) {
        const p = el("p", "share-who"); p.append(avatar(t.person), document.createTextNode(`${t.person} is helping`));
        li.appendChild(p);
        if (mine[t.id]) { const cal = helperCalendarButton(t, who); if (cal) li.appendChild(cal); }   // only the helper's own task
        const token = mine[t.id] || (data.owner ? ownerToken : "");
        if (token) li.appendChild(removeButton(t, token, mine[t.id] ? "Remove my name" : "Remove this name", data));
      } else if (mine[t.id] && offered[t.id]) {   // they offered, and their times did not fit: the person decides
        const o = offered[t.id];
        li.appendChild(el("p", "share-who", `You offered to help (${FREE(o.days, o.times)}). ${who || "They"} will see it.`));
        li.appendChild(removeButton(t, mine[t.id], "Remove my offer", data));
      } else if (!data.owner) {
        const lab = el("label", "share-pick");
        const cb = el("input"); cb.type = "checkbox"; cb.checked = chosen.has(t.id);
        cb.onchange = () => {
          if (cb.checked) chosen.set(t.id, { days: "any", times: "any", note: "" }); else chosen.delete(t.id);
          render(lastData);
        };
        lab.append(cb, document.createTextNode("I can help with this"));
        li.appendChild(lab);
        if (chosen.has(t.id)) li.appendChild(pickBox(t));
      }
      ul.appendChild(li);
    }
    if (!data.tasks.length) ul.appendChild(el("li", "share-row", "There's nothing on this list right now."));
    for (const id of [...chosen.keys()]) if (!data.tasks.some((t) => t.id === id && !t.person)) chosen.delete(id);
    $("signup").hidden = !chosen.size;
    $("signup-count").textContent = chosen.size === 1 ? "You picked 1 task." : `You picked ${chosen.size} tasks.`;
  }
  function removeButton(t, token, label, data) {
    const b = el("button", "btn btn-quiet share-small"); b.type = "button";
    b.append(icon("dash"), document.createTextNode(label));
    b.onclick = async () => {
      try {
        const out = await api("/unclaim", { task_id: t.id, token });
        const c = myClaims(); delete c[t.id]; saveClaims(c);
        const o = myOffers(); delete o[t.id]; saveOffers(o);
        status("Done. That task is open again."); render({ ...out, owner: data.owner });
      } catch (e) { status(e.message); }
    };
    return b;
  }
  $("signup").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("signup-name").value.trim();
    if (!name) { status("Please type your first name."); $("signup-name").focus(); return; }
    const items = [];
    let out = null;
    for (const [id, c] of chosen) {
      const t = lastData.tasks.find((r) => r.id === id);
      try {
        out = await api("/claim", { task_id: id, name, days: c.days, times: c.times, note: c.note.trim() });
        const mine = myClaims(); mine[id] = out.claim_token; saveClaims(mine);
        if (!out.placed) { const o = myOffers(); o[id] = { days: c.days, times: c.times }; saveOffers(o); }
        items.push({ id, task: t ? t.task : "", placed: out.placed, days: c.days, times: c.times });
      } catch (err) { status(err.message); }
    }
    chosen.clear();
    if (items.length) { thanks = { name, items }; status(""); }
    if (out) render({ ...out, owner: lastData.owner }); else render(lastData);
    if (items.length) $("share-thanks").scrollIntoView({ block: "start" });
  });

  async function load() {
    try { render(await api(ownerToken ? `?owner=${encodeURIComponent(ownerToken)}` : "")); }
    catch (e) {
      $("share-title").textContent = "This list isn't available";
      $("share-intro").textContent = "It may have been deleted, or the link may be incomplete. Please ask the person who sent it.";
      $("share-rows").replaceChildren();
    }
  }

  $("delete-list").onclick = async () => {
    if (!confirm("Stop sharing? This list will be deleted for everyone, and the link will stop working. This cannot be undone.")) return;
    try { await api("/delete", { owner: ownerToken }); location.replace(location.pathname); }
    catch (e) { status(e.message); }
  };

  // theme and text size, same as the main page
  const setTheme = (t, save = true) => {
    document.documentElement.dataset.theme = t;
    for (const k of ["light", "dark", "contrast"]) $("theme-" + k).setAttribute("aria-pressed", String(t === k));
    if (save) { try { localStorage.setItem("jic-theme-v2", t); } catch (_) {} }
  };
  setTheme(["dark", "contrast"].includes(document.documentElement.dataset.theme) ? document.documentElement.dataset.theme : "light", false);
  $("theme-light").onclick = () => setTheme("light");
  $("theme-dark").onclick = () => setTheme("dark");
  $("theme-contrast").onclick = () => setTheme("contrast");
  const SIZES = [18, 20, 22, 24, 27]; let idx = 1;
  try { const s = Number(localStorage.getItem("jic-size")); if (SIZES.includes(s)) idx = SIZES.indexOf(s); } catch (_) {}
  const size = () => { document.documentElement.style.setProperty("--fs", SIZES[idx] + "px"); try { localStorage.setItem("jic-size", SIZES[idx]); } catch (_) {} };
  size();
  $("bigger").onclick = () => { idx = Math.min(SIZES.length - 1, idx + 1); size(); };
  $("smaller").onclick = () => { idx = Math.max(0, idx - 1); size(); };

  load();
  setInterval(() => { if (!chosen.size && document.activeElement?.id !== "signup-name") load(); }, 20000);  // stay current
})();
