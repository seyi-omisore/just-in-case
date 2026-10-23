// Just in Case: browser side.
// Everything the agent says is inserted as text (never as raw HTML).

(() => {
  const $ = (id) => document.getElementById(id);
  const messages = $("messages"), feed = $("feed"), planEl = $("plan");
  const input = $("message"), sendBtn = $("send"), typing = $("typing");
  let ws, sessionId, busy = false, seenItems = new Set(), cardUrl = null;
  const openWhy = new Set();  // "Why this matters" tips stay open when the plan redraws
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
  // A round badge with a helper's initials. Decoration only: the name is always written next to it.
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
  // Each situation keeps one color and icon everywhere, always beside its name.
  const SITUATION = {
    ride_home_after_procedure: "ride", transportation: "ride", spare_key_access: "key",
    pet_care_during_stay: "pet", mail_while_away: "mail",
  };
  const sitBadge = (need) => {
    const b = el("span", "sit-badge"); b.dataset.sit = SITUATION[need] || "other";
    b.setAttribute("aria-hidden", "true"); b.appendChild(icon(NEED_ICON[need] || "check"));
    return b;
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

  // Scroll inside the chat box only, so the page itself does not grow or jump.
  function scrollChat() {
    messages.scrollTop = messages.scrollHeight;
  }

  // ---- messages ----------------------------------------------------------
  function addMessage(who, text, extraClass) {
    const li = el("li", `msg msg-${who}${extraClass ? " " + extraClass : ""}`);
    if (who === "agent") {
      const av = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      av.setAttribute("viewBox", "0 0 48 48"); av.setAttribute("class", "msg-avatar"); av.setAttribute("aria-hidden", "true");
      av.innerHTML = '<circle cx="24" cy="24" r="24" style="fill:var(--primary-tint)"/><path d="M11 24 24 12l13 12v14H11z" style="fill:var(--primary)"/><path d="M24 34s-6-3.6-6-7.6a3.3 3.3 0 0 1 6-1.9 3.3 3.3 0 0 1 6 1.9c0 4-6 7.6-6 7.6z" style="fill:var(--accent)"/>';
      li.appendChild(av);
    }
    const body = el("div", "msg-body");
    const label = el("span", "sr-only", who === "agent" ? "Just in Case said: " : "You said: ");
    body.appendChild(label);
    body.appendChild(renderText(text));
    if (extraClass === "msg-recap") body.querySelector("p").prepend(icon("check"), " ");
    if (extraClass === "msg-ready") body.querySelector("p").prepend(icon("heart"), " ");
    if (extraClass === "msg-error") body.querySelector("p").prepend(icon("alert"), " ", el("strong", null, "Problem: "));
    if (who === "agent" && "speechSynthesis" in window) body.appendChild(readAloudButton(text));
    li.appendChild(body);
    messages.appendChild(li);
    scrollChat();
    if (who === "agent" && !extraClass) lastAgent = text;
    if (extraClass !== "msg-error") chatHistory.push({ who, text, cls: extraClass || "" });
  }

  // ---- save my progress: only in this browser, only after the user says so --
  const SAVE_KEY = "jic-saved-plan";
  const chatHistory = [];  // the words on screen, so a saved plan reopens with them
  let saving = false, saveTimer = null;
  const readSaved = () => { try { return JSON.parse(localStorage.getItem(SAVE_KEY) || "null"); } catch (_) { return null; } };
  const timeText = (ms) => new Date(ms).toLocaleString(undefined, { month: "long", day: "numeric", hour: "numeric", minute: "2-digit" });
  function setSaving(on) {
    saving = on;
    $("save-start").hidden = on; $("save-on").hidden = !on;
    if (on) requestSave();
  }
  function requestSave() {
    if (!saving || EXAMPLE) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "save" })); }, 800);
  }
  function storeSave(state) {
    if (!saving) return;
    const at = Date.now();
    try {
      localStorage.setItem(SAVE_KEY, JSON.stringify({ at, state, history: chatHistory.slice(-100) }));
      $("save-status").textContent = "Saved on this device, " + timeText(at) + ".";
    } catch (_) {
      $("save-status").textContent = "This browser would not save. Your plan is still here while this page is open.";
    }
  }
  $("save-start").addEventListener("click", () => setSaving(true));
  $("save-stop").addEventListener("click", () => {
    if (!confirm("Stop saving? Your saved plan will be deleted from this device. This page stays as it is.")) return;
    try { localStorage.removeItem(SAVE_KEY); } catch (_) {}
    setSaving(false);
  });
  // Shown once, at the start of a visit, if this device has a saved plan.
  function offerSavedPlan() {
    const saved = readSaved();
    if (!saved || !saved.state || EXAMPLE) return;
    const li = el("li", "slip welcome-back");
    const head = el("div", "slip-head"); head.append(icon("bookmark"), document.createTextNode("Welcome back"));
    const body = el("div", "slip-body");
    body.appendChild(el("p", null, "You saved a plan on this device on " + timeText(saved.at) + ". Would you like to pick up where you left off?"));
    const actions = el("div", "slip-actions");
    const go = el("button", "btn btn-primary"); go.type = "button"; go.append(icon("check"), document.createTextNode("Continue my plan"));
    const fresh = el("button", "btn btn-quiet"); fresh.type = "button"; fresh.append(icon("plus"), document.createTextNode("Start fresh"));
    go.onclick = () => {
      messages.replaceChildren();
      for (const m of saved.history || []) addMessage(m.who, m.text, m.cls || undefined);
      $("starters").hidden = true; $("example-open").hidden = true;
      ws.send(JSON.stringify({ type: "restore", state: saved.state }));
      setSaving(true);
    };
    fresh.onclick = () => {
      if (!confirm("Start fresh? Your saved plan will be deleted from this device.")) return;
      try { localStorage.removeItem(SAVE_KEY); } catch (_) {}
      li.remove(); input.focus();
    };
    actions.append(go, fresh);
    li.append(head, body, actions);
    messages.appendChild(li);
    go.focus({ preventScroll: true });
  }

  // ---- read aloud (the browser's own voice; nothing is sent to our server) ---
  let speakingBtn = null;
  const setSpeaking = (btn, on) => {
    btn.replaceChildren(icon(on ? "stop" : "speaker"), document.createTextNode(on ? "Stop" : "Read aloud"));
    btn.setAttribute("aria-pressed", String(on));
  };
  // A warm voice: the most natural one this device has, a little slower and a little higher.
  const NOVELTY = /albert|bad news|bahh|bells|boing|bubbles|cellos|good news|jester|organ|superstar|trinoids|whisper|wobble|zarvox|eddy|flo|grandma|grandpa|reed|rocko|sandy|shelley|fred|junior|ralph|kathy/i;
  const voiceScore = (v) => {
    if (NOVELTY.test(v.name)) return -1;
    let s = 0;
    if (/^en[-_]US/i.test(v.lang)) s += 100; else if (/^en/i.test(v.lang)) s += 60;
    if (/enhanced|premium|natural|neural/i.test(v.name)) s += 50;
    if (/online|google|microsoft/i.test(v.name)) s += 25;
    if (v.default) s += 5;
    return s;
  };
  let voices = [], chosenVoice = null;
  const voicePick = $("voice");
  function loadVoices() {
    const all = speechSynthesis.getVoices();
    const english = all.filter((v) => /^en/i.test(v.lang) && voiceScore(v) >= 0);
    voices = (english.length ? english : all).slice().sort((a, b) => voiceScore(b) - voiceScore(a));
    if (!voices.length) return;
    let saved = null; try { saved = sessionStorage.getItem("jic-voice"); } catch (_) {}
    chosenVoice = voices.find((v) => v.voiceURI === saved) || voices[0];
    voicePick.replaceChildren(...voices.map((v, i) => {
      const o = el("option", null, v.name.replace(/\s*\(.*?\)\s*$/, "") + (i === 0 ? " (recommended)" : ""));
      o.value = v.voiceURI; o.selected = v === chosenVoice; return o;
    }));
    $("voice-row").hidden = false;
  }
  function warmUtterance(text) {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = chosenVoice ? chosenVoice.lang : (document.documentElement.lang || "en");
    if (chosenVoice) u.voice = chosenVoice;
    u.rate = 0.9; u.pitch = 1.1;
    return u;
  }
  if ("speechSynthesis" in window) {
    loadVoices();
    speechSynthesis.addEventListener("voiceschanged", loadVoices);  // most browsers load voices late
    voicePick.addEventListener("change", () => {
      chosenVoice = voices.find((v) => v.voiceURI === voicePick.value) || chosenVoice;
      try { sessionStorage.setItem("jic-voice", chosenVoice.voiceURI); } catch (_) {}
      speechSynthesis.cancel();
      if (speakingBtn) { setSpeaking(speakingBtn, false); speakingBtn = null; }
      speechSynthesis.speak(warmUtterance("Hi. This is how I will sound."));
    });
  }
  function readAloudButton(text) {
    const btn = el("button", "read-aloud"); btn.type = "button";
    setSpeaking(btn, false);
    btn.onclick = () => {
      const wasMe = speakingBtn === btn;
      speechSynthesis.cancel();
      if (speakingBtn) { setSpeaking(speakingBtn, false); speakingBtn = null; }
      if (wasMe) return;  // second tap stops
      const u = warmUtterance(text.replace(/\*\*/g, "").replace(/^\s*[-*]\s+/gm, ""));
      u.onend = u.onerror = () => { if (speakingBtn === btn) { setSpeaking(btn, false); speakingBtn = null; } };
      speakingBtn = btn; setSpeaking(btn, true);
      speechSynthesis.speak(u);
    };
    return btn;
  }

  // ---- "See an example": plays Ruth's side of the scripted demo ----------
  // The page only types Ruth's lines. Every checkpoint still waits for a real tap.
  const EXAMPLE = new URLSearchParams(location.search).get("example") === "1";
  let lastAgent = "", exampleTimer = null;
  const RUTH = [  // [what Just in Case asked, what Ruth says]. Fictional details only.
    [/what's coming up/i, "I'm Ruth. I have a procedure next month and may stay two nights in the hospital. My cat Fifi needs feeding."],
    [/comfortable asking/i, "Yes, I could ask Doris."],
    [/phone numbers/i, "Marion Kelly, 617-555-0142, and Doris Chen, 617-555-0177"],
    [/medications/i, "Blood pressure pill in the morning; eye drops at night"],
    [/allergies/i, "Penicillin and shellfish"],
    [/your pet/i, "Fifi, my cat. Doris looks after her."],
    [/important papers/i, "In the blue folder in my desk drawer"],
    [/top of your card/i, "Ruth"],
  ];
  function exampleNext() {
    if (!EXAMPLE || busy || exampleTimer || messages.querySelector(".slip-actions")) return;
    const question = lastAgent.trim().split(/\n\s*\n/).pop();  // the last paragraph holds the question
    const hit = RUTH.find(([asked]) => asked.test(question));
    if (!hit) return;  // off the script (for example, "Change something"): the viewer takes over
    exampleTimer = setTimeout(() => { exampleTimer = null; send(hit[1]); }, 1500);
  }

  // ---- checkpoints -------------------------------------------------------
  const SLIP_TITLE = {
    confirm_needs: "Does this list look right?",
    draft_helper_note: "Here is a note you can send",
    make_fridge_card: "Here is what goes on your fridge card",
    add_helper_task: "Add this to your helper table?",
    choose_card_sections: "What would you like on your fridge card?",
    confirm_helping_someone: "Does this person agree?",
  };
  const SECTION_ICON = { name: "card", contacts: "phone", medications: "pill", allergies: "alert", pet: "paw",
                         papers: "folder", helpers: "person", plan: "home" };
  function addApproval(msg) {
    const { id, kind, data } = msg;
    const li = el("li", "slip");
    const head = el("div", "slip-head"); head.append(icon("check"), document.createTextNode(SLIP_TITLE[kind] || "Please check this"));
    const body = el("div", "slip-body");

    if (kind === "confirm_needs") {
      body.appendChild(el("p", null, "Here is what we'll plan for:"));
      const ul = el("ul", "slip-needs");
      for (const n of data.needs) { const i = el("li"); i.append(sitBadge(n.key), document.createTextNode(n.label)); ul.appendChild(i); }
      body.appendChild(ul);
    } else if (kind === "draft_helper_note") {
      body.appendChild(el("p", null, "For " + data.to.join(" and ") + ". We never send it. You send it yourself if you like."));
      const note = el("div", "sticky"); const nh = el("span", "sticky-head"); nh.append(icon("note"), document.createTextNode("Your note"));
      note.append(nh, el("p", null, data.text)); body.appendChild(note);
    } else if (kind === "choose_card_sections") {
      body.appendChild(el("p", null, "Tick anything you want. The 911 and 988 lines are always on it."));
      const ul = el("ul", "slip-checks");
      for (const o of data.options) {
        const li2 = el("li"); const lab = el("label");
        const cb = el("input"); cb.type = "checkbox"; cb.value = o.key;
        lab.append(cb, icon(SECTION_ICON[o.key] || "check"), document.createTextNode(o.label));
        li2.appendChild(lab); ul.appendChild(li2);
      }
      body.appendChild(ul);
    } else if (kind === "confirm_helping_someone") {
      const who = data.name || "the person you're helping";
      body.appendChild(el("p", null, `Plans work best when the person helps make them. Does ${who} know about this plan and agree to it?`));
      body.appendChild(el("p", "helper-small", "They decide what goes in it, and who is asked to help."));
    } else if (kind === "add_helper_task") {
      const dl = el("dl");
      const row = (t, d) => { if (d) dl.append(el("dt", null, t), el("dd", null, d)); };
      row("Task", data.task); row("Who is helping", data.person || "Open: family can sign up");
      row("Date", data.date); row("Time", data.time);
      body.appendChild(dl);
      body.appendChild(el("p", "helper-small", "You can change or remove it later."));
    } else if (kind === "make_fridge_card") {
      const dl = el("dl");
      const row = (t, d) => { dl.append(el("dt", null, t), el("dd", null, d)); };
      // Only what the user gave. Empty parts are left off, not shown as "None".
      if (data.name) row("Name", data.name);
      if (data.contacts.length) row("People to contact", data.contacts.map((c) => `${c.name}${c.relationship ? " (" + c.relationship + ")" : ""}, ${c.phone}`).join("; "));
      if (data.medications.length) row("Medications, in your words", data.medications.join("; "));
      if ((data.allergies || []).length) row("Allergies, in your words", data.allergies.join("; "));
      if (data.pet) row("Pet", data.pet + (data.pet_caregiver ? `, looked after by ${data.pet_caregiver}` : ""));
      if (data.papers) row("Where your important papers are", data.papers);
      if (!dl.children.length) dl.appendChild(el("dd", null, "Just the emergency lines and your plan."));
      body.appendChild(dl);
    }

    const actions = el("div", "slip-actions");
    const yes = el("button", "btn btn-primary"); yes.type = "button"; yes.append(icon("check"), document.createTextNode("Yes, looks good"));
    const no = el("button", "btn btn-quiet"); no.type = "button"; no.append(icon("pencil"), document.createTextNode("Change something"));
    const isChoice = kind === "choose_card_sections";
    const answer = (ok) => {
      const msg = { type: "answer", id, ok };
      let doneText = ok ? "You said yes" : "You asked for a change";
      if (kind === "confirm_helping_someone") doneText = ok ? "You said they agree" : "Not yet";
      if (isChoice) {
        const picked = [...body.querySelectorAll("input[type=checkbox]")];
        msg.choice = picked.filter((c) => c.checked).map((c) => c.value);
        picked.forEach((c) => (c.disabled = true));
        const names = picked.filter((c) => c.checked).map((c) => c.parentElement.textContent.trim());
        doneText = names.length ? "You picked: " + names.join(", ") : "You picked just the emergency lines";
      }
      ws.send(JSON.stringify(msg));
      actions.remove();
      if (busy) typing.hidden = false;
      const done = el("p", "slip-done " + (ok ? "yes" : "no"));
      done.append(icon(ok ? "check" : "pencil"), document.createTextNode(doneText));
      li.appendChild(done);
      if (!ok) input.focus();
    };
    yes.onclick = () => answer(true);
    no.onclick = () => answer(false);
    if (isChoice) {
      yes.replaceChildren(icon("check"), document.createTextNode("Continue"));
      actions.append(yes);
    } else actions.append(yes, no);
    if (kind === "confirm_helping_someone") {
      yes.replaceChildren(icon("check"), document.createTextNode("Yes, they agree"));
      no.replaceChildren(icon("wait"), document.createTextNode("Not yet"));
    }
    li.append(head, body, actions);
    messages.appendChild(li);
    scrollChat();
    yes.focus({ preventScroll: true });
    typing.hidden = true;  // waiting for her answer, not thinking
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
    const prog = plan.progress || { covered: 0, total: 0 };
    $("plan-progress").textContent = prog.total ? `: ${prog.covered} of ${prog.total} covered` : "";
    // The ring is decoration; the words above say the same thing.
    const ring = $("progress-ring"), C = 2 * Math.PI * 20;
    ring.toggleAttribute("hidden", !prog.total);  // an SVG has no .hidden property
    ring.querySelector(".ring-fill").style.strokeDashoffset = String(prog.total ? C * (1 - prog.covered / prog.total) : C);
    $("plan-ready").hidden = !plan.ready;
    planEl.replaceChildren();
    for (const item of plan.items) {
      const key = item.need + ":" + item.route + ":" + item.name;
      const li = el("li", "tag" + (seenItems.has(key) ? "" : " new"));
      seenItems.add(key);
      li.dataset.need = item.need; li.dataset.sit = SITUATION[item.need] || "other";
      const mag = el("span", "magnet"); mag.appendChild(icon(NEED_ICON[item.need] || "check"));
      li.appendChild(mag);
      li.appendChild(el("p", "tag-need", item.need_label));
      li.appendChild(el("p", "tag-name", item.route === "blank" ? "Not covered yet" : item.name));
      const [rWord, rIcon] = ROUTE[item.route] || ["", "check"];
      const r = el("span", "tag-route route-" + item.route); r.append(icon(rIcon), document.createTextNode(rWord));
      li.appendChild(r);
      if (item.route !== "blank") li.appendChild(itemAnswer(item));

      const links = el("p", "tag-links");
      if (item.phone && item.phone !== "see website") {
        const a = el("a"); a.href = "tel:" + item.phone.replace(/[^0-9]/g, ""); a.append(icon("phone"), document.createTextNode(item.phone)); links.appendChild(a);
      }
      if (item.url) {
        const a = el("a"); a.href = item.url; a.target = "_blank"; a.rel = "noopener";
        a.append(icon("info"), document.createTextNode("Website")); links.appendChild(a);
      }
      if (links.childNodes.length) li.appendChild(links);
      const checked = checkedLabel(item.checked);
      if (checked) { const c = el("p", "tag-checked"); c.append(icon("check"), document.createTextNode(checked)); li.appendChild(c); }
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
      if (item.why) {
        const tip = el("details", "tag-why"); tip.open = openWhy.has(item.need);
        tip.ontoggle = () => (tip.open ? openWhy.add(item.need) : openWhy.delete(item.need));
        const sum = el("summary"); sum.append(icon("info"), document.createTextNode("Why this matters"));
        tip.append(sum, el("p", null, item.why));
        li.appendChild(tip);
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
    renderShare(plan);
    renderServicesOnly(plan);
    renderTimeline(plan.timeline);
    const card = $("card-link");
    cardUrl = plan.card;
    card.hidden = !plan.card;
    planPrintUrl = plan.plan_print;
    $("plan-print").hidden = !plan.plan_print;
  }
  // No one to ask: a calm services-only box instead of an empty helper table.
  // The table stays one tap away, and any rows the user added keep it showing.
  let showTableAnyway = false;
  function renderServicesOnly(plan) {
    const on = !!plan.services_only && !showTableAnyway && !(plan.tasks || []).length;
    $("services-only").hidden = !on;
    $("helper-body").hidden = on;
    const ul = $("help-lines");
    if (on && !ul.children.length) {
      for (const l of plan.help_lines || []) {
        const li = el("li"); li.appendChild(el("p", "s-name", l.name));
        const how = el("p", "s-how"); inline(how, l.how_to_reach); li.appendChild(how);
        ul.appendChild(li);
      }
    }
  }
  $("show-table").addEventListener("click", () => {
    showTableAnyway = true; $("services-only").hidden = true; $("helper-body").hidden = false;
    form.elements.task.focus();
  });

  // "2026-10-21" -> "Info checked October 2026". Read as text, so time zones cannot shift the month.
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December"];
  function checkedLabel(iso) {
    const m = /^(\d{4})-(\d{2})/.exec(iso || "");
    return m && MONTHS[+m[2] - 1] ? `Info checked ${MONTHS[+m[2] - 1]} ${m[1]}` : "";
  }
  // ---- "Getting ready": the user's date, steps to tick, calendar files ----
  const isoToLocal = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); };
  const dayWords = (iso) => isoToLocal(iso).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
  function renderTimeline(tl) {
    const box = $("getting-ready");
    box.hidden = !tl || !tl.steps.length;
    if (box.hidden) return;
    $("event-label").textContent = tl.label;
    const pick = $("event-date");
    if (document.activeElement !== pick) pick.value = tl.date;
    $("countdown").replaceChildren();
    if (tl.countdown) $("countdown").append(icon("calendar"), document.createTextNode(" " + tl.countdown));
    $("steps-hint").hidden = !!tl.date;
    const ol = $("steps"); ol.replaceChildren();
    for (const s of tl.steps) {
      const li = el("li", "step" + (s.done ? " done" : ""));
      const lab = el("label", "step-check");
      const cb = el("input"); cb.type = "checkbox"; cb.checked = s.done;
      cb.onchange = () => ws.send(JSON.stringify({ type: "step", id: s.id, done: cb.checked }));
      const words = el("span", "step-text", s.text);
      lab.append(cb, words);
      const meta = el("p", "step-when");
      if (s.need) meta.append(sitBadge(s.need));
      meta.append(document.createTextNode(s.when + (s.date ? " · " + dayWords(s.date) : "")));
      if (s.done) meta.append(" ", el("strong", "step-done-word", "Done"));
      li.append(lab, meta);
      if (s.date && !s.past) li.appendChild(calendarButton(s.text, s.date, `Just in Case: ${s.when.toLowerCase()} your ${tl.label}.`));
      ol.appendChild(li);
    }
  }
  // A standard .ics file, made here in the browser. No calendar account is touched,
  // nothing goes to our server, and there is no alarm: the user's calendar decides.
  function makeICS(title, iso, details) {
    const esc = (s) => String(s).replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n");
    const ymd = (d) => d.getFullYear() + String(d.getMonth() + 1).padStart(2, "0") + String(d.getDate()).padStart(2, "0");
    const start = isoToLocal(iso), end = new Date(start); end.setDate(end.getDate() + 1);
    const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
    const uid = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)) + "@justincase";
    // Lines longer than 75 bytes are folded: CRLF, then a space.
    const fold = (line) => {
      const bytes = new TextEncoder().encode(line);
      if (bytes.length <= 75) return line;
      const parts = []; let cur = "", n = 0;
      for (const ch of line) {
        const len = new TextEncoder().encode(ch).length;
        if (n + len > (parts.length ? 74 : 75)) { parts.push(cur); cur = ""; n = 0; }
        cur += ch; n += len;
      }
      parts.push(cur);
      return parts.join("\r\n ");
    };
    return [
      "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Just in Case//Backup plan//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
      "BEGIN:VEVENT", `UID:${uid}`, `DTSTAMP:${stamp}`,
      `DTSTART;VALUE=DATE:${ymd(start)}`, `DTEND;VALUE=DATE:${ymd(end)}`,
      `SUMMARY:${esc(title)}`, `DESCRIPTION:${esc(details)}`, "TRANSP:TRANSPARENT",
      "END:VEVENT", "END:VCALENDAR",
    ].map(fold).join("\r\n") + "\r\n";
  }
  function calendarButton(title, iso, details) {
    const b = el("button", "btn btn-quiet btn-small cal-btn"); b.type = "button";
    b.append(icon("calendar"), document.createTextNode("Add to my calendar"));
    b.setAttribute("aria-label", `Add to my calendar: ${title}, ${dayWords(iso)}`);
    b.onclick = () => {
      const url = URL.createObjectURL(new Blob([makeICS(title, iso, details)], { type: "text/calendar" }));
      const a = el("a"); a.href = url;
      a.download = "just-in-case-" + (title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "step") + ".ics";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    return b;
  }
  // Helper-table dates are typed freely. Only dates we can read for sure get a button.
  const MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august",
                       "september", "october", "november", "december"];
  function readTaskDate(text) {
    const s = String(text || "").trim().toLowerCase(), now = new Date();
    let y = null, m = null, d = null, hit;
    if ((hit = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(s))) [y, m, d] = [+hit[1], +hit[2], +hit[3]];
    else if ((hit = /^(\d{1,2})\/(\d{1,2})(?:\/(\d{2,4}))?$/.exec(s))) [m, d, y] = [+hit[1], +hit[2], hit[3] ? +hit[3] : null];
    else if ((hit = /^([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?$/.exec(s))) {
      const word = hit[1] === "sept" ? "sep" : hit[1];  // "Nov", "November", "Sept"
      m = MONTH_NAMES.findIndex((n) => n.startsWith(word) && (word.length >= 3)) + 1;
      if (!m || !(word === MONTH_NAMES[m - 1] || word.length === 3)) return null;
      d = +hit[2]; y = hit[3] ? +hit[3] : null;
    } else return null;
    if (y !== null && y < 100) y += 2000;
    if (y === null) {  // no year: the next time that date comes around
      y = now.getFullYear();
      if (new Date(y, m - 1, d) < new Date(now.getFullYear(), now.getMonth(), now.getDate())) y += 1;
    }
    const date = new Date(y, m - 1, d);
    if (date.getMonth() !== m - 1 || date.getDate() !== d) return null;  // like "2/31"
    return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  }

  $("event-date").addEventListener("change", (e) => {
    $("timeline-error").textContent = "";
    if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "event_date", date: e.target.value }));
  });

  // Each part counts toward progress only after the user says "Yes, add this".
  const justAdded = new Set();  // for the check mark, shown once
  function itemAnswer(item) {
    if (item.user_ok) {
      const done = el("p", "tag-ok" + (justAdded.has(item.need) ? " pop" : ""));
      justAdded.delete(item.need);
      done.append(icon("check"), document.createTextNode("Added to your plan"));
      return done;
    }
    const box = el("div", "tag-answer");
    box.appendChild(el("p", "tag-waiting", "")).append(icon("wait"), document.createTextNode("Waiting for your OK"));
    const row = el("div", "tag-answer-row");
    const yes = el("button", "btn btn-primary btn-small"); yes.type = "button";
    yes.append(icon("check"), document.createTextNode("Yes, add this"));
    yes.setAttribute("aria-label", "Yes, add this: " + item.need_label);
    const no = el("button", "btn btn-quiet btn-small"); no.type = "button";
    no.append(icon("dash"), document.createTextNode("No thanks"));
    no.setAttribute("aria-label", "No thanks: " + item.need_label);
    const answer = (ok) => {
      if (!ws || ws.readyState !== 1) return;
      if (ok) justAdded.add(item.need);
      ws.send(JSON.stringify({ type: "item", need: item.need, ok }));
    };
    yes.onclick = () => answer(true);
    no.onclick = () => answer(false);
    row.append(yes, no); box.appendChild(row);
    return box;
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
    if (on) scrollChat();
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws${EXAMPLE ? "?example=1" : ""}`);
    setBusy(true);
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      switch (m.type) {
        case "hello":
          sessionId = m.session; $("demo-banner").hidden = !m.demo;
          $("table-download").href = `/table/${sessionId}.csv`;
          setBusy(false);
          if (!messages.children.length) addMessage("agent", m.greeting);
          if (!feed.children.length) feed.appendChild(el("li", "feed-empty", "Each step shows up here while Just in Case works."));
          $("save-zone").hidden = EXAMPLE;
          offerSavedPlan();
          exampleNext();
          break;
        case "saved": storeSave(m.state); break;
        case "restored":
          addMessage("agent", "Welcome back. Here's where we left off. What would you like to do next?");
          if (m.skipped.length) addMessage("agent", "A few parts could not be reopened, so they were left out: " + m.skipped.join(", ") + ". We can add them again.");
          break;
        case "agent": addMessage("agent", m.text); break;
        case "recap": addMessage("agent", m.text, "msg-recap"); break;
        case "ready": addMessage("agent", m.text, "msg-ready"); break;
        case "activity": addActivity(m.kind, m.text); break;
        case "approval": addApproval(m); break;
        case "plan": renderPlan(m.plan); requestSave(); break;
        case "busy": setBusy(m.on); if (!m.on) exampleNext(); break;
        case "table_error": $("task-error").textContent = m.text; break;
        case "notice": addNotice(m.text); break;
        case "timeline_error": $("timeline-error").textContent = m.text; break;
        case "error": addMessage("agent", m.text, "msg-error"); setBusy(false); break;
      }
    };
    ws.onclose = () => {
      setBusy(false);
      addMessage("agent", "The connection to Just in Case was lost. Please reload the page to start again.", "msg-error");
    };
  }

  // ---- speak instead of type (the browser's own speech recognition) ------
  // Words go into the message box only. Nothing is sent until the user taps Send.
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let stopListening = () => {};
  if (Recognition) {
    const mic = $("mic"), micStatus = $("mic-status");
    let rec = null;
    mic.hidden = false; $("mic-note").hidden = false;
    const say = (text, withIcon) => {
      micStatus.replaceChildren();
      if (text) micStatus.append(...(withIcon ? [icon("mic")] : []), document.createTextNode(text));
    };
    const setListening = (on) => {
      mic.replaceChildren(icon(on ? "stop" : "mic"), document.createTextNode(on ? "Stop" : "Speak"));
      mic.setAttribute("aria-pressed", String(on));
      if (on) say("Listening… Tap Stop when you're done.", true);
    };
    const MIC_ERRORS = {
      "not-allowed": "The microphone is turned off for this page. You can type instead.",
      "service-not-allowed": "Speaking isn't available in this browser right now. You can type instead.",
      "no-speech": "I didn't hear anything. Tap Speak to try again, or type instead.",
      "audio-capture": "No microphone was found. You can type instead.",
      "network": "Speaking needs an internet connection in this browser. You can type instead.",
    };
    stopListening = () => { if (rec) rec.stop(); };
    mic.onclick = () => {
      if (rec) { rec.stop(); return; }
      rec = new Recognition();
      rec.lang = "en-US"; rec.interimResults = true; rec.continuous = true;
      const before = input.value.trim() ? input.value.trim() + " " : "";
      let failed = false;
      rec.onresult = (e) => {
        let heard = ""; for (const r of e.results) heard += r[0].transcript;
        input.value = before + heard.trim();
      };
      rec.onerror = (e) => { failed = true; say(MIC_ERRORS[e.error] || "Speaking stopped. You can try again, or type instead."); };
      rec.onend = () => {
        rec = null; setListening(false);
        if (!failed) say(input.value.trim() ? "Check your message, then tap Send." : "");
        input.focus();
      };
      setListening(true);
      try { rec.start(); } catch (_) { rec = null; setListening(false); say(MIC_ERRORS["service-not-allowed"]); }
    };
  }

  function send(text) {
    text = text.trim();
    if (!text || busy || !ws || ws.readyState !== 1) return;
    stopListening();
    $("mic-status").textContent = "";
    addMessage("user", text);
    $("starters").hidden = true;
    $("example-open").hidden = true;
    ws.send(JSON.stringify({ type: "say", text }));
    input.value = "";
  }

  $("composer").addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input.value); }
  });
  for (const b of document.querySelectorAll(".starter")) b.addEventListener("click", () => send(b.dataset.text));
  if (EXAMPLE) { $("starters").hidden = true; $("example-open").hidden = true; $("example-exit").hidden = false; }

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
    tableUrls = { csv: `/table/${sessionId}.csv`, print: `/table/${sessionId}` };
    $("table-download").href = tableUrls.csv;
    const rows = $("helper-rows"); rows.replaceChildren();
    const tasks = plan.tasks || [];
    $("helper-table").hidden = !tasks.length;
    $("table-empty").hidden = !!tasks.length;
    for (const t of tasks) {
      const tr = el("tr", seenTasks.has(t.id) ? "" : "new-row"); seenTasks.add(t.id);
      const td = (v, label, cls) => { const c = el("td", cls || "", v || ""); if (label) c.dataset.label = label; return c; };
      const whoCell = td(t.person, "Who", "who");
      if (!t.person) whoCell.textContent = "No one yet";  // the Status label says "Open"
      else { const who = el("span", "who-name"); who.append(avatar(t.person), document.createTextNode(t.person)); whoCell.replaceChildren(who); }
      tr.append(td(t.task, "Task"), td(t.date, "Date"), td(t.time, "Time"), whoCell);
      const statusCell = td("", "Status"); statusCell.appendChild(statusLabel(taskStatus(t))); tr.appendChild(statusCell);
      const act = el("td"); const box = el("div", "row-actions");
      if (t.person) {
        const c = el("button"); c.type = "button";
        c.append(icon(t.confirmed ? "dash" : "check"), document.createTextNode(t.confirmed ? "Not confirmed" : "Mark confirmed"));
        c.setAttribute("aria-label", (t.confirmed ? "Mark not confirmed: " : "Mark confirmed: ") + t.task);
        c.onclick = () => sendTable("confirm", { id: t.id, confirmed: !t.confirmed });
        box.appendChild(c);
      }
      const edit = el("button"); edit.type = "button"; edit.setAttribute("aria-label", "Change " + t.task); edit.title = "Change";
      edit.append(icon("pencil"), document.createTextNode("Change"));
      edit.onclick = () => startEdit(t);
      const del = el("button"); del.type = "button"; del.setAttribute("aria-label", "Remove " + t.task); del.title = "Remove";
      del.append(icon("dash"), document.createTextNode("Remove"));
      del.onclick = () => { if (confirm(`Remove "${t.task}" from your table?`)) sendTable("delete", { id: t.id }); };
      box.append(edit, del);
      const iso = readTaskDate(t.date);
      if (iso && isoToLocal(iso) >= new Date(new Date().toDateString()))
        box.appendChild(calendarButton(t.task, iso, `Just in Case helper list: ${t.task}` +
          (t.person ? `, with ${t.person}` : "") + (t.time ? `, ${t.time}` : "") + "."));
      act.appendChild(box); tr.appendChild(act);
      rows.appendChild(tr);
    }
  }
  // Status is always a word with an icon, never color alone.
  const STATUS = { open: ["Open", "plus"], helping: ["Someone's helping", "person"], confirmed: ["Confirmed", "check"] };
  const taskStatus = (t) => (!t.person ? "open" : t.confirmed ? "confirmed" : "helping");
  function statusLabel(key) {
    const [word, ic] = STATUS[key];
    const s = el("span", "status status-" + key); s.append(icon(ic), document.createTextNode(word));
    return s;
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
    if (!row.task) { $("task-error").textContent = "Please add a task."; return; }
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

  // ---- helper updates: calm, non-urgent notices -------------------------
  // For example, a helper stepped back from a task. Never styled as an emergency.
  function addNotice(text) {
    const box = el("div", "notice");
    const p = el("p"); p.append(icon("info"), el("strong", null, "Update: "), document.createTextNode(text));
    const ok = el("button", "btn btn-quiet"); ok.type = "button"; ok.append(icon("check"), document.createTextNode("OK"));
    ok.onclick = () => { box.remove(); updateTabCount(); };
    box.append(p, ok);
    $("notices").appendChild(box);
    updateTabCount();
  }
  function updateTabCount() {
    const n = $("notices").children.length, badge = $("tab-updates");
    badge.hidden = !n;
    $("notices-wrap").hidden = !n;  // the 911 line always sits above any update
    badge.textContent = n === 1 ? "1 update" : `${n} updates`;
  }

  // ---- sharing (only after the consent box) -----------------------------
  const consent = $("consent-dialog");
  let shareState = null;
  function renderShare(plan) {
    shareState = plan.share;
    const has = (plan.tasks || []).length > 0;
    $("share-open").hidden = !!plan.share;
    $("share-open").disabled = !has;
    $("share-why-off").hidden = has || !!plan.share;  // say why, not just fade the button
    $("share-box").hidden = !plan.share;
    if (plan.share) {
      const url = location.origin + plan.share.url;
      $("share-link").value = url;
      $("share-owner-link").value = location.origin + plan.share.owner_url;
      $("share-email").href = "mailto:?subject=" + encodeURIComponent("Could you help with my plan?") +
        "&body=" + encodeURIComponent("I made a small backup plan. If you'd like to help with something, you can add your name here. No pressure at all.\n\n" + url);
      const d = new Date(plan.share.expires * 1000);
      $("share-expiry").textContent = "This list will be deleted on " + d.toLocaleDateString(undefined, { month: "long", day: "numeric" }) + ".";
    }
  }
  $("share-open").addEventListener("click", () => { $("consent-name").value = ""; consent.showModal(); });
  $("consent-yes").addEventListener("click", () => {
    ws.send(JSON.stringify({ type: "share_create", consent: true, label: $("consent-name").value.trim() }));
    consent.close();
  });
  const copy = async (inputId, btn) => {
    const v = $(inputId).value;
    try { await navigator.clipboard.writeText(v); } catch (_) { $(inputId).select(); document.execCommand("copy"); }
    const old = btn.textContent; btn.textContent = "Copied"; setTimeout(() => (btn.textContent = old), 1800);
  };
  $("share-copy").onclick = (e) => copy("share-link", e.currentTarget);
  $("share-owner-copy").onclick = (e) => copy("share-owner-link", e.currentTarget);
  $("share-stop").addEventListener("click", () => {
    if (confirm("Stop sharing? Your list will be deleted and the link will stop working.")) ws.send(JSON.stringify({ type: "share_delete" }));
  });

  // ---- fridge card viewer ------------------------------------------------
  const dialog = $("card-dialog"), frame = $("card-frame");
  $("card-link").addEventListener("click", () => {
    if (!cardUrl) return;
    $("card-dialog-title").textContent = "Your fridge card";
    frame.src = cardUrl + "?t=" + Date.now();
    dialog.showModal();
  });
  let planPrintUrl = null;
  $("plan-print").addEventListener("click", () => {
    if (!planPrintUrl) return;
    $("card-dialog-title").textContent = "Your plan";
    frame.src = planPrintUrl + "?t=" + Date.now();
    dialog.showModal();
  });
  $("card-print").addEventListener("click", () => { try { frame.contentWindow.print(); } catch (_) { window.open(frame.src); } });
  $("card-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });

  // ---- "By the numbers": counted by the server from the app itself -------
  fetch("/api/facts").then((r) => r.json()).then((f) => {
    for (const n of document.querySelectorAll("[data-fact]")) {
      const v = f[n.dataset.fact];
      if (v == null) n.closest("li").remove(); else n.textContent = v;
    }
    $("facts").hidden = false;
  }).catch(() => {});  // if it can't load, the section stays hidden rather than showing a guess

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
    for (const k of ["light", "dark", "contrast"]) $("theme-" + k).setAttribute("aria-pressed", String(t === k));
    if (save) { try { localStorage.setItem("jic-theme", t); } catch (_) {} }
  };
  setTheme(["dark", "contrast"].includes(document.documentElement.dataset.theme) ? document.documentElement.dataset.theme : "light", false);
  $("theme-light").onclick = () => setTheme("light");
  $("theme-dark").onclick = () => setTheme("dark");
  $("theme-contrast").onclick = () => setTheme("contrast");

  // Warn if the code was updated (git pull) but the server was not restarted.
  const checkRestart = () => fetch("/api/config").then((r) => r.json())
    .then((c) => { $("restart-banner").hidden = c.needs_restart === false; })  // missing = older server.catch(() => {});
  checkRestart(); setInterval(checkRestart, 60000);

  // ---- tabs: About / Features / Make my plan ------------------------------
  // The 911 and 988 strip sits above the tabs, so it shows on every tab.
  // The chat connects the first time "Make my plan" opens, not before.
  const TABS = ["about", "features", "plan"];
  let connected = false;
  function showTab(name, focus) {
    if (!TABS.includes(name)) name = EXAMPLE ? "plan" : "about";
    for (const t of TABS) $("panel-" + t).hidden = t !== name;
    for (const a of document.querySelectorAll(".tabs a")) {
      if (a.dataset.tab === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    }
    if (name === "plan" && !connected) { connected = true; connect(); }
    if (focus) { window.scrollTo(0, 0); $("panel-" + name).focus({ preventScroll: true }); }
  }
  window.addEventListener("hashchange", () => showTab(location.hash.slice(1), true));
  showTab(location.hash.slice(1), false);
})();
