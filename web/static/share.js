// The family page: see the list, add your name to an open task.
// Everything from the server is inserted as plain text.

(() => {
  const $ = (id) => document.getElementById(id);
  const shareId = location.pathname.split("/").pop();
  const ownerToken = new URLSearchParams(location.search).get("owner") || "";
  const KEY = "jic-claims-" + shareId;  // this browser's own sign-ups
  let openForm = null;

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

  async function api(path, body) {
    const opts = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
    const r = await fetch(`/api/share/${shareId}${path}`, opts);
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "Something went wrong. Please try again.");
    return data;
  }

  function render(data) {
    const who = data.label;
    $("share-title").textContent = who ? `${who}'s helper list` : "A helper list";
    document.title = $("share-title").textContent;
    $("share-intro").textContent = who
      ? `${who} is making a small backup plan. Here are a few things ${who} would like help with. ` +
        "If you can help with one, add your name. No pressure at all."
      : "Someone close to you is making a small backup plan. Here are a few things that would help. " +
        "If you can help with one, add your name. No pressure at all.";
    const d = new Date(data.expires * 1000);
    $("share-expires").textContent = `This list is deleted on ${d.toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" })}.`;
    $("owner-banner").hidden = !data.owner;

    const mine = myClaims();
    const ul = $("share-rows"); ul.replaceChildren();
    for (const t of data.tasks) {
      const li = el("li", "share-row" + (t.person ? " taken" : ""));
      li.appendChild(el("p", "share-task", t.task));
      const when = [t.date, t.time].filter(Boolean).join(", ");
      if (when) li.appendChild(el("p", "share-when", when));

      if (t.person) {
        const p = el("p", "share-who"); p.append(avatar(t.person), document.createTextNode(`${t.person} is helping`));
        li.appendChild(p);
        const token = mine[t.id] || (data.owner ? ownerToken : "");
        if (token) {
          const b = el("button", "btn btn-quiet share-small"); b.type = "button";
          b.append(icon("dash"), document.createTextNode(mine[t.id] ? "Remove my name" : "Remove this name"));
          b.onclick = async () => {
            try {
              const out = await api("/unclaim", { task_id: t.id, token });
              const c = myClaims(); delete c[t.id]; saveClaims(c);
              status("Done. That task is open again."); render({ ...out, owner: data.owner });
            } catch (e) { status(e.message); }
          };
          li.appendChild(b);
        }
      } else if (openForm === t.id) {
        const f = el("form", "share-form");
        const lab = el("label", null, "Your name");
        const inp = el("input"); inp.maxLength = 120; inp.required = true; inp.autocomplete = "name";
        lab.appendChild(inp);
        const go = el("button", "btn btn-primary"); go.type = "submit"; go.append(icon("heart"), document.createTextNode("Add my name"));
        const cancel = el("button", "btn btn-quiet"); cancel.type = "button"; cancel.textContent = "Cancel";
        cancel.onclick = () => { openForm = null; render(data); };
        f.append(lab, el("div", "share-form-actions"));
        f.lastChild.append(go, cancel);
        f.onsubmit = async (e) => {
          e.preventDefault();
          if (!inp.value.trim()) return;
          try {
            const out = await api("/claim", { task_id: t.id, name: inp.value.trim() });
            const c = myClaims(); c[out.task_id] = out.claim_token; saveClaims(c);
            openForm = null; status(`Thank you, ${inp.value.trim()}. You're on the list.`);
            render({ ...out, owner: data.owner });
          } catch (e2) { status(e2.message); }
        };
        li.appendChild(f);
        setTimeout(() => inp.focus(), 0);
      } else {
        const chip = el("span", "open-chip"); chip.append(icon("plus"), document.createTextNode("Open"));
        li.appendChild(chip);
        const b = el("button", "btn btn-primary share-small"); b.type = "button";
        b.append(icon("heart"), document.createTextNode("I can help"));
        b.onclick = () => { openForm = t.id; render(data); };
        li.appendChild(b);
      }
      ul.appendChild(li);
    }
    if (!data.tasks.length) ul.appendChild(el("li", "share-row", "There's nothing on this list right now."));
  }

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
    $("theme-light").setAttribute("aria-pressed", String(t === "light"));
    $("theme-dark").setAttribute("aria-pressed", String(t === "dark"));
    if (save) { try { localStorage.setItem("jic-theme", t); } catch (_) {} }
  };
  setTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light", false);
  $("theme-light").onclick = () => setTheme("light");
  $("theme-dark").onclick = () => setTheme("dark");
  const SIZES = [18, 20, 22, 24, 27]; let idx = 1;
  try { const s = Number(localStorage.getItem("jic-size")); if (SIZES.includes(s)) idx = SIZES.indexOf(s); } catch (_) {}
  const size = () => { document.documentElement.style.setProperty("--fs", SIZES[idx] + "px"); try { localStorage.setItem("jic-size", SIZES[idx]); } catch (_) {} };
  size();
  $("bigger").onclick = () => { idx = Math.min(SIZES.length - 1, idx + 1); size(); };
  $("smaller").onclick = () => { idx = Math.max(0, idx - 1); size(); };

  load();
  setInterval(() => { if (openForm === null) load(); }, 20000);  // stay current
})();
