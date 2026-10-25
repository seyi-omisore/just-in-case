// Just in Case: browser side.
// Everything the agent says is inserted as text (never as raw HTML).

(() => {
  const $ = (id) => document.getElementById(id);
  const AGENT = window.JIC_AGENT_NAME || "your helper";   // set by the server from AGENT_NAME in config.py
  const messages = $("messages"), feed = $("feed"), planEl = $("plan");
  const input = $("message"), sendBtn = $("send"), typing = $("typing");

  // ---- simple view: one question per screen ------------------------------
  // Every message and checkpoint is tagged with the page (question) it belongs to.
  // Simple view shows one page at a time. "Show details" shows the whole chat as before.
  let pages = [];   // [{answered: bool}]
  let view = 0;     // the page on screen
  function pageFor(who) {
    let last = pages[pages.length - 1];
    if (who === "agent") {
      if (!last || last.answered) { last = { answered: false }; pages.push(last); view = pages.length - 1; }
    } else if (last) last.answered = true;
    return pages.length - 1;
  }
  let ws, sessionId, busy = false, seenItems = new Set(), cardUrl = null;
  const openItems = new Set();  // plan items you opened stay open when the plan redraws
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
  // Illustrations for the situations. Each has words that describe it for screen readers.
  const ILLUSTRATION = (() => {
    const clinic = ["clinic", "Illustration: a blue car drives along a road toward a small teal house, with a calendar marked with a check, for getting home after a procedure."];
    return {
      ride_home_after_procedure: clinic, check_in_after_procedure: clinic, transportation: clinic,
      power_outage: ["outage", "Illustration: a small teal house with dark windows on a snowy winter night, with a glowing lantern beside it, for a power outage."],
      pet_care_during_stay: ["pet", "Illustration: a gray cat sits next to a full food bowl, with a small heart above, for pet care while you are away."],
      mail_while_away: ["mail", "Illustration: a blue mailbox on a wooden post with its flag up, and envelopes waiting beside it, for holding your mail."],
    };
  })();
  function illustration(need, cls) {
    const art = ILLUSTRATION[need];
    if (!art) return null;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "ill scene " + (cls || "")); svg.setAttribute("viewBox", "0 0 240 120");
    svg.setAttribute("role", "img"); svg.setAttribute("aria-label", art[1]);
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use"); use.setAttribute("href", "#ill-" + art[0]);
    svg.appendChild(use);
    return svg;
  }
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
    li.dataset.page = pageFor(who);
    if (who === "agent") {
      const av = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      av.setAttribute("viewBox", "0 0 48 48"); av.setAttribute("class", "msg-avatar");
      av.setAttribute("role", "img"); av.setAttribute("aria-label", `${AGENT}, your digital planning helper`);
      av.innerHTML = '<circle cx="24" cy="24" r="24" style="fill:var(--primary-tint)"/><path d="M11 24 24 12l13 12v14H11z" style="fill:var(--primary)"/><path d="M24 34s-6-3.6-6-7.6a3.3 3.3 0 0 1 6-1.9 3.3 3.3 0 0 1 6 1.9c0 4-6 7.6-6 7.6z" style="fill:var(--accent)"/>';
      li.appendChild(av);
    }
    const body = el("div", "msg-body");
    const label = el("span", "sr-only", who === "agent" ? "said: " : "You said: ");
    if (who === "agent") { const nm = el("span", "msg-name", AGENT); nm.setAttribute("aria-hidden", "true"); body.appendChild(nm); }  // the avatar already names it for screen readers
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
      // Helpers' phone numbers and emails stay on this device: they ride along in this saved copy only.
      const contactsSaved = (state.tasks || []).map((t) => contacts.get(t.id) || "");
      localStorage.setItem(SAVE_KEY, JSON.stringify({ at, state, history: chatHistory.slice(-100), contacts: contactsSaved }));
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
    li.dataset.page = pageFor("agent");
    const head = el("div", "slip-head"); head.append(icon("bookmark"), document.createTextNode("Welcome back"));
    const body = el("div", "slip-body");
    body.appendChild(el("p", null, "You saved a plan on this device on " + timeText(saved.at) + ". Would you like to pick up where you left off?"));
    const actions = el("div", "slip-actions");
    const go = el("button", "btn btn-primary"); go.type = "button"; go.append(icon("check"), document.createTextNode("Continue my plan"));
    const fresh = el("button", "btn btn-quiet"); fresh.type = "button"; fresh.append(icon("plus"), document.createTextNode("Start fresh"));
    go.onclick = () => {
      messages.replaceChildren(); pages = []; view = 0;
      for (const m of saved.history || []) addMessage(m.who, m.text, m.cls || undefined);
      $("starters").hidden = true; $("example-open").hidden = true;
      restoringContacts = { tasks: saved.state.tasks || [], contacts: saved.contacts || [] };
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
    btn.setAttribute("aria-label", on ? `Stop reading ${AGENT}'s message` : `Read ${AGENT}'s message aloud`);
    btn.setAttribute("aria-pressed", String(on));
  };
  // A warm voice: the most natural one this device has, a little slower and a little higher.
  const NOVELTY = /albert|bad news|bahh|bells|boing|bubbles|cellos|good news|jester|organ|superstar|trinoids|whisper|wobble|zarvox|eddy|flo|grandma|grandpa|reed|rocko|sandy|shelley|fred|junior|ralph|kathy/i;
  // The most natural voices come first, when this device has them: Premium (like Ava or Zoe Premium on Apple
  // devices), then Enhanced (like Samantha Enhanced) and Microsoft's "Online (Natural)" voices in Edge,
  // then the usual voices. A voice the person picks in Settings is remembered and always wins.
  const voiceQuality = (name) => /premium/i.test(name) ? 400
    : /enhanced/i.test(name) || /online \(natural\)/i.test(name) ? 300
    : /natural|neural/i.test(name) ? 200 : 0;
  const voiceScore = (v) => {
    if (NOVELTY.test(v.name)) return -1;
    let s = voiceQuality(v.name);
    if (/^en[-_]US/i.test(v.lang)) s += 100; else if (/^en/i.test(v.lang)) s += 60;
    if (/online|google|microsoft/i.test(v.name)) s += 25;
    if (v.default) s += 5;
    return s;
  };
  // ---- Reading voice: accent, female or male voice, a sample, and speed ---------
  // Accent names come from the voice's language tag. Only accents this device has are shown.
  const ACCENTS = { "en-US": "American", "en-GB": "British", "en-AU": "Australian", "en-IN": "Indian",
                    "en-IE": "Irish", "en-ZA": "South African", "en-CA": "Canadian", "en-NZ": "New Zealand" };
  // Female or male only when the voice says so, or its name is on these lists (Apple, Google, Microsoft).
  // A name that is not here is never guessed. It is just "Voice 1", "Voice 2".
  const FEMALE_NAMES = /\b(zoe|samantha|karen|moira|tessa|fiona|veena|victoria|allison|ava|susan|serena|kate|nicky|zira|aria|jenny|michelle|sonia|libby|maisie|natasha|annette|clara|neerja|emily|hazel|linda|catherine|heera|heather|leah|molly|mia)\b/i;
  const MALE_NAMES = /\b(daniel|rishi|alex|tom|aaron|oliver|lee|arthur|gordon|evan|nathan|david|mark|guy|ryan|thomas|christopher|eric|roger|steffan|brian|davis|jason|tony|liam|william|connor|james|george|richard|prabhat|ravi|mitchell|luke)\b/i;
  const genderOf = (v) => {
    const f = /\bfemale\b/i.test(v.name) || FEMALE_NAMES.test(v.name);
    const m = /\bmale\b/i.test(v.name) || MALE_NAMES.test(v.name);
    return f && !m ? "female" : m && !f ? "male" : null;   // both, or neither: we do not guess
  };
  const accentOf = (v) => {
    const tag = String(v.lang || "").replace("_", "-");
    const k = tag.slice(0, 2).toLowerCase() + "-" + tag.slice(3, 5).toUpperCase();
    return ACCENTS[k] ? k : /^en/i.test(tag) ? "en-other" : "other";
  };
  // The menu shows the accent and its language tag, like "American (en-US)".
  const accentName = (k) => ACCENTS[k] ? `${ACCENTS[k]} (${k})` : (k === "en-other" ? "Other English" : "Other voices");
  const SAMPLE = "Hi, I'm here to help you make your plan.";
  const SPEEDS = [0.7, 0.8, 0.9, 1.0, 1.1];                 // 0.9 is the warm default
  const SPEED_WORDS = ["Slowest", "Slower", "Normal", "Faster", "Fastest"];
  const voiceStore = {
    get: (k) => { try { return localStorage.getItem("jic-voice-" + k); } catch (_) { return null; } },
    set: (k, v) => { try { localStorage.setItem("jic-voice-" + k, String(v)); } catch (_) {} },
  };
  let voices = [], chosenVoice = null, speedIdx = 2;
  { const raw = voiceStore.get("speed"), n = Number(raw); if (raw !== null && Number.isInteger(n) && n >= 0 && n < SPEEDS.length) speedIdx = n; }
  const voicePick = $("voice"), accentPick = $("voice-accent");
  const byScore = (a, b) => voiceScore(b) - voiceScore(a);

  // The voices in one accent, each with its label: "Female voice", "Male voice", or "Voice 1".
  function labelled(accent) {
    const list = voices.filter((v) => accentOf(v) === accent).sort(byScore);
    const counts = { female: 0, male: 0 }; list.forEach((v) => { const g = genderOf(v); if (g) counts[g] += 1; });
    const seen = { female: 0, male: 0, none: 0 };
    return list.map((v) => {
      const g = genderOf(v), n = (seen[g || "none"] += 1);
      const label = g ? `${g === "female" ? "Female" : "Male"} voice` + (counts[g] > 1 ? ` ${n}` : "") : `Voice ${n}`;
      return { v, g, label };
    });
  }
  function renderVoiceControls() {
    if (!chosenVoice) return;
    const accent = accentOf(chosenVoice), list = labelled(accent);
    accentPick.replaceChildren(...[...new Set(voices.map(accentOf))]
      .sort((a, b) => (Object.keys(ACCENTS).indexOf(a) + 1 || 99) - (Object.keys(ACCENTS).indexOf(b) + 1 || 99))
      .map((k) => { const o = el("option", null, accentName(k)); o.value = k; o.selected = k === accent; return o; }));
    // Simple view: Female voice / Male voice (only the ones this accent really has), else the first two unlabeled voices.
    const kinds = [];
    for (const g of ["female", "male"]) { const hit = list.find((x) => x.g === g); if (hit) kinds.push({ ...hit, label: `${g === "female" ? "Female" : "Male"} voice` }); }
    if (!kinds.length) kinds.push(...list.filter((x) => !x.g).slice(0, 2));
    $("voice-kind").replaceChildren(...kinds.map((k) => {
      const b = el("button", null, k.label); b.type = "button";
      b.setAttribute("aria-pressed", String(k.v === chosenVoice || (k.g && genderOf(chosenVoice) === k.g)));
      b.onclick = () => chooseVoice(k.v);
      return b;
    }));
    // Full view: every voice in this accent. Real names are shown only for voices we could identify.
    voicePick.replaceChildren(...list.map((x) => {
      const o = el("option", null, x.g ? `${x.label} (${x.v.name.replace(/^Microsoft\s+|\s*\(.*?\)\s*$|\s*-\s*English.*$/gi, "")})` : x.label);
      o.value = x.v.voiceURI; o.selected = x.v === chosenVoice; return o;
    }));
    $("voice-speed").textContent = SPEED_WORDS[speedIdx];
    // The closed Reading voice heading says the current choice, like "American, female, normal speed".
    const g = genderOf(chosenVoice);
    $("now-voice").textContent = [ACCENTS[accent] || accentName(accent), g, SPEED_WORDS[speedIdx].toLowerCase() + " speed"].filter(Boolean).join(", ");
    $("voice-slower").disabled = speedIdx === 0; $("voice-faster").disabled = speedIdx === SPEEDS.length - 1;
  }
  function chooseVoice(v) {
    chosenVoice = v;
    voiceStore.set("uri", v.voiceURI);
    stopSpeaking();
    renderVoiceControls();   // silent: the person taps Play sample to hear it
  }
  function loadVoices() {
    const all = speechSynthesis.getVoices().filter((v) => !NOVELTY.test(v.name));
    const english = all.filter((v) => /^en/i.test(v.lang));
    voices = english.length ? english : all;
    if (!voices.length) return;
    const saved = voiceStore.get("uri");
    chosenVoice = voices.find((v) => v.voiceURI === saved) || voices.slice().sort(byScore)[0];
    renderVoiceControls();
    $("voice-row").hidden = false; $("voice-none").hidden = true;
  }
  function stopSpeaking() {
    speechSynthesis.cancel();
    if (speakingBtn) { setSpeaking(speakingBtn, false); speakingBtn = null; }
  }
  function warmUtterance(text) {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = chosenVoice ? chosenVoice.lang : (document.documentElement.lang || "en");
    if (chosenVoice) u.voice = chosenVoice;
    u.rate = SPEEDS[speedIdx]; u.pitch = 1.1;   // a little slower and a little higher: warmer
    return u;
  }
  if ("speechSynthesis" in window) {
    loadVoices();
    speechSynthesis.addEventListener("voiceschanged", loadVoices);  // most browsers load voices late
    accentPick.addEventListener("change", () => {
      const pool = labelled(accentPick.value), g = chosenVoice && genderOf(chosenVoice);
      const next = (g && pool.find((x) => x.g === g)) || pool[0];   // keep female or male when the new accent has it
      if (next) chooseVoice(next.v);
    });
    voicePick.addEventListener("change", () => {
      const v = voices.find((x) => x.voiceURI === voicePick.value);
      if (v) chooseVoice(v);
    });
    $("voice-sample").addEventListener("click", () => { stopSpeaking(); speechSynthesis.speak(warmUtterance(SAMPLE)); });
    const setSpeed = (i) => { speedIdx = Math.max(0, Math.min(SPEEDS.length - 1, i)); voiceStore.set("speed", speedIdx); renderVoiceControls(); };
    $("voice-slower").addEventListener("click", () => setSpeed(speedIdx - 1));
    $("voice-faster").addEventListener("click", () => setSpeed(speedIdx + 1));
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

  // ---- "See Ruth's example": plays Ruth's side of the story ----------------
  // The page only types Ruth's lines. Every checkpoint still waits for a real tap.
  const EXAMPLE = new URLSearchParams(location.search).get("example") === "1";
  let lastAgent = "", exampleTimer = null;
  const RUTH = [  // [what Just in Case asked, what Ruth says]. Fictional details only.
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
  // A short line stays on the card; the longer words wait behind a "Show details" link.
  function moreDetails(text) {
    const d = el("details", "more"); d.append(el("summary", null, "Show details"), el("p", null, text));
    return d;
  }
  function addApproval(msg) {
    const { id, kind, data } = msg;
    const li = el("li", "slip");
    li.dataset.page = pageFor("agent");
    const head = el("div", "slip-head"); head.append(icon("check"), document.createTextNode(SLIP_TITLE[kind] || "Please check this"));
    const body = el("div", "slip-body");

    if (kind === "confirm_needs") {
      body.appendChild(el("p", null, "Here is what we'll plan for:"));
      const ul = el("ul", "slip-needs");
      for (const n of data.needs) { const i = el("li"); i.append(sitBadge(n.key), document.createTextNode(n.label)); ul.appendChild(i); }
      body.appendChild(ul);
    } else if (kind === "draft_helper_note") {
      body.appendChild(el("p", null, "This is for " + data.to.join(" and ") + "."));
      body.appendChild(moreDetails(`${AGENT} writes the message. You send it from your own phone or email. You can change it, or skip it.`));
      const note = el("div", "sticky"); const nh = el("span", "sticky-head"); nh.append(icon("note"), document.createTextNode("Your note"));
      note.append(nh, el("p", null, data.text)); body.appendChild(note);
    } else if (kind === "choose_card_sections") {
      body.appendChild(el("p", null, "Tick anything you want. The 911 and 988 lines are always on it."));
      const ul = el("ul", "slip-checks");
      for (const o of data.options) {
        const li2 = el("li"); const lab = el("label");
        const cb = el("input"); cb.type = "checkbox"; cb.value = o.key;
        if (EXAMPLE) cb.checked = ["contacts", "allergies", "papers"].includes(o.key);   // Ruth's picks; you still tap Continue
        lab.append(cb, icon(SECTION_ICON[o.key] || "check"), document.createTextNode(o.label));
        li2.appendChild(lab); ul.appendChild(li2);
      }
      body.appendChild(ul);
    } else if (kind === "confirm_helping_someone") {
      const who = data.name || "the person you're helping";
      body.appendChild(el("p", null, `Plans work best when the person helps make them. Does ${who} know about this plan and agree to it?`));
      body.appendChild(moreDetails("They decide what goes in it, and who is asked to help."));
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
      if (pages[+li.dataset.page]) pages[+li.dataset.page].answered = true;
      actions.remove();
      // An approved note: the send buttons open right here. The person sends it from their own phone or email.
      if (ok && kind === "draft_helper_note") li.appendChild(noteSendBox(data.to, data.text));
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

  // ---- an optional outside service: a link the person may choose, never used for them ----
  function addSuggestion(s) {
    if (familyOpened) return;   // they already looked at "Keep family in the loop"
    const li = el("li", "slip suggest");
    li.dataset.page = Math.max(0, pages.length - 1);
    const head = el("div", "slip-head"); head.append(icon("info"), document.createTextNode("You might also like"));
    const body = el("div", "slip-body");
    const what = el("p"); what.append(el("strong", null, s.name + (s.free && !/\bfree\b/i.test(s.what) ? " (free)" : "")),
                                      document.createTextNode(". " + s.what));
    body.append(what, el("p", "helper-small", s.separate),
                el("p", "helper-small", s.simpler));
    const a = el("a", "btn btn-quiet"); a.href = s.url; a.target = "_blank"; a.rel = "noopener noreferrer";
    a.append(icon("info"), document.createTextNode(`Open ${s.name} (new tab)`));
    body.appendChild(a);
    li.append(head, body);
    messages.appendChild(li);
    scrollChat();
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
  // The one thing to do now. Done or Remind me later shows the next step.
  let nextNeed = null;
  function renderNextStep(step) {
    const box = $("next-step");
    box.hidden = !step;
    if (!step) return;
    nextNeed = step.need || null;
    const finished = !!step.all_done;
    $("next-step-title").lastChild.textContent = finished ? " All done for now" : " Your next step";
    const nextArt = ILLUSTRATION[step.need];   // a picture for the step, when we have one
    const ill = $("next-ill");
    ill.hidden = finished || !nextArt;
    if (nextArt) { ill.setAttribute("aria-label", nextArt[1]); ill.firstElementChild.setAttribute("href", "#ill-" + nextArt[0]); }
    $("next-step-text").textContent = finished ? "You finished every step. You can look over your plan below." : step.text;
    const call = $("next-call");
    // A phone number gets "Call now". With no number, a website gets "Open website". With neither, no button.
    const dest = step.phone ? "tel:" + step.phone.replace(/[^0-9+]/g, "") : step.url || "";
    call.hidden = finished || !dest;
    if (dest) {
      call.href = dest; call.target = step.phone ? "" : "_blank"; call.rel = "noopener";
      $("next-call-label").textContent = step.phone ? "Call now" : "Open website";
    }
    $("next-done").hidden = $("next-later").hidden = finished;
    $("next-left").textContent = finished ? "" : step.left ? `${step.left} more after this one.` : "This is your last step.";
  }
  const sendNext = (action) => {
    if (!nextNeed || !ws || ws.readyState !== 1) return;
    ws.send(JSON.stringify({ type: "next_step", need: nextNeed, action }));
  };
  $("next-done").addEventListener("click", () => sendNext("done"));
  $("next-later").addEventListener("click", () => sendNext("later"));

  // "Mark my plan as reviewed": a dated record, and a gentle nudge a few months later. Never an alarm.
  function renderReview(rev) {
    const box = $("review-box");
    box.hidden = !rev;
    if (!rev) return;
    $("review-text").textContent = rev.text;
    $("review-mark").hidden = rev.reviewed_today;
    $("review-undo").hidden = !rev.reviewed_today;
    $("review-status").textContent = rev.reviewed_today ? "Marked as reviewed today. Thank you for keeping it current." : "";
    const list = $("review-history"); list.replaceChildren();
    for (const d of rev.history) list.appendChild(el("li", null, "Reviewed " + d));
    list.hidden = !rev.history.length;
  }
  const sendReview = (action) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "review", action })); };
  $("review-mark").addEventListener("click", () => sendReview("mark"));
  $("review-undo").addEventListener("click", () => sendReview("undo"));

  function renderPlan(plan) {
    // Until something is in the plan, the side shows one friendly line (and the support lines, always).
    $("side").classList.toggle("plan-empty", !plan.items.length && !(plan.tasks || []).length);
    $("fridge-empty").hidden = plan.items.length > 0 || plan.notes.length > 0;
    const prog = plan.progress || { covered: 0, total: 0 };
    $("plan-progress").textContent = prog.total ? `: ${prog.covered} of ${prog.total} covered` : "";
    // The ring is decoration; the words above say the same thing.
    const ring = $("progress-ring"), C = 2 * Math.PI * 20;
    ring.toggleAttribute("hidden", !prog.total);  // an SVG has no .hidden property
    ring.querySelector(".ring-fill").style.strokeDashoffset = String(prog.total ? C * (1 - prog.covered / prog.total) : C);
    // A calm line at each milestone. The words come from the server, never from the model.
    $("plan-ready").hidden = !plan.encouragement;
    $("ready-ill").toggleAttribute("hidden", !plan.ready);  // the calm "you're prepared" picture, once every part is covered
    $("checkins-cal").hidden = $("checkins-note").hidden = !plan.ready;   // plan check-ins, offered once the plan is ready
    if (plan.ready) playOnce($("ready-ill"), 1600);          // its check mark draws itself, once
    $("plan-ready-text").textContent = plan.encouragement || "";
    renderNextStep(plan.next_step);
    renderReview(plan.review);
    planEl.replaceChildren();
    for (const item of plan.items) {
      const key = item.need + ":" + item.route + ":" + item.name;
      const li = el("li", "tag" + (seenItems.has(key) ? "" : " new"));
      seenItems.add(key);
      li.dataset.need = item.need; li.dataset.sit = SITUATION[item.need] || "other";
      const mag = el("span", "magnet"); mag.appendChild(icon(NEED_ICON[item.need] || "check"));
      li.appendChild(mag);

      // Short list: one line per situation with a status icon and label. Tap to open the details.
      const [sIcon, sText, sKind] = item.user_ok ? ["check", "Added to your plan", "ok"]
        : item.route === "blank" ? ["dash", "Not covered yet", "blank"] : ["wait", "Waiting for your OK", "wait"];
      const pop = item.user_ok && justAdded.has(item.need); justAdded.delete(item.need);
      const panelId = "tag-details-" + item.need;
      const open = openItems.has(item.need);
      const head = el("button", "tag-head"); head.type = "button";
      head.setAttribute("aria-expanded", String(open)); head.setAttribute("aria-controls", panelId);
      const status = el("span", "tag-status status-" + sKind + (pop ? " pop" : "")); status.append(icon(sIcon), document.createTextNode(sText));
      const chev = el("span", "tag-chevron"); chev.setAttribute("aria-hidden", "true");
      head.append(el("span", "tag-title", item.need_label), status, chev);
      li.appendChild(head);

      const panel = el("div", "tag-details"); panel.id = panelId; panel.hidden = !open;
      head.onclick = () => {
        const now = panel.hidden;  // hidden now means it is about to open
        panel.hidden = !now; head.setAttribute("aria-expanded", String(now));
        if (now) openItems.add(item.need); else openItems.delete(item.need);
      };
      li.appendChild(panel);

      const art = illustration(item.need, "tag-ill"); if (art) panel.appendChild(art);
      panel.appendChild(el("p", "tag-name", item.route === "blank" ? "Not covered yet" : item.name));
      const [rWord, rIcon] = ROUTE[item.route] || ["", "check"];
      const r = el("span", "tag-route route-" + item.route); r.append(icon(rIcon), document.createTextNode(rWord));
      panel.appendChild(r);
      if (item.route !== "blank" && !item.user_ok) panel.appendChild(itemAnswer(item));

      const links = el("p", "tag-links");
      if (item.phone && item.phone !== "see website") {
        const a = el("a"); a.href = "tel:" + item.phone.replace(/[^0-9]/g, ""); a.append(icon("phone"), document.createTextNode(item.phone)); links.appendChild(a);
      }
      if (item.url) {
        const a = el("a"); a.href = item.url; a.target = "_blank"; a.rel = "noopener";
        a.append(icon("info"), document.createTextNode("Website")); links.appendChild(a);
      }
      if (links.childNodes.length) panel.appendChild(links);
      if (item.route === "service" && plan.report_email) {   // opens the person's own email app; nothing is sent for them
        const rp = el("a", "report-link");
        rp.href = "mailto:" + plan.report_email + "?subject=" + encodeURIComponent("Problem with a service: " + item.name) +
          "&body=" + encodeURIComponent(`Service: ${item.name}\nWhat's wrong (for example, a wrong number or it has closed):\n\n`);
        rp.append(icon("alert"), document.createTextNode("Report a problem with this service"));
        panel.appendChild(rp);
      }
      const checked = checkedLabel(item.checked);
      if (checked) { const c = el("p", "tag-checked"); c.append(icon("check"), document.createTextNode(checked)); panel.appendChild(c); }
      if (item.need === "mail_while_away") {
        const a = el("a", "btn btn-primary tag-action"); a.href = USPS_HOLD; a.target = "_blank"; a.rel = "noopener";
        a.append(icon("mail"), document.createTextNode("Hold my mail on USPS.com"));
        panel.appendChild(a);
      }

      if (item.flags && item.flags.length) {
        const ul = el("ul", "tag-flags");
        for (const f of item.flags) {
          const fi = el("li"); fi.append(icon(/WAITLIST/.test(f) ? "wait" : "info"), document.createTextNode(plainFlag(f)));
          ul.appendChild(fi);
        }
        li.insertBefore(ul, panel);   // warnings (waitlist, escort rule, no phone) stay visible, never behind a tap
      }
      if (item.why) { const w = el("p", "tag-why"); w.append(icon("info"), document.createTextNode(item.why)); panel.appendChild(w); }
      if (item.plan_b) {   // a friend gives the ride: a checked service stays ready as Plan B
        const pb = el("div", "plan-b");
        pb.appendChild(el("p", "plan-b-title", "Plan B"));
        pb.appendChild(el("p", null, `If your helper can't make it, call ${item.plan_b.name}.`));
        if (item.plan_b.phone) {
          const a = el("a"); a.href = "tel:" + item.plan_b.phone.replace(/[^0-9]/g, "");
          a.append(icon("phone"), document.createTextNode(item.plan_b.phone)); pb.appendChild(a);
        } else if (item.plan_b.url) {
          const a = el("a"); a.href = item.plan_b.url; a.target = "_blank"; a.rel = "noopener";
          a.append(icon("info"), document.createTextNode("Website")); pb.appendChild(a);
        }
        panel.appendChild(pb);
      }
      if (item.why_this) {   // "Why this?": one plain sentence, built from the services list and the person's answers
        const d = el("details", "more why-this");
        d.append(el("summary", null, "Why this?"), el("p", null, item.why_this));
        panel.appendChild(d);
      }
      planEl.appendChild(li);
    }

    const notes = $("notes"); notes.replaceChildren();
    for (const n of plan.notes) {
      const s = el("div", "sticky"); const h = el("span", "sticky-head");
      h.append(icon("note"), document.createTextNode("Note for " + n.helpers.join(" and ")));
      s.append(h, el("p", null, n.text), noteSendBox(n.helpers, n.text)); notes.appendChild(s);
    }

    renderTasks(plan);
    renderShare(plan);
    renderServicesOnly(plan);
    renderTimeline(plan.timeline);
    const card = $("card-link");
    cardUrl = plan.card;
    card.hidden = !plan.card;
    walletUrl = plan.wallet; $("wallet-print").hidden = !plan.wallet;
    planPrintUrl = plan.plan_print;
    $("plan-print").hidden = !plan.plan_print;
    planEmail = plan.plan_email; $("plan-email").hidden = !plan.plan_email;
    renderAttention(plan);
    renderNight(plan);
    $("feedback").hidden = EXAMPLE || !plan.plan_email;   // at the end, once something is in the plan (not in Ruth's example)
    summaryUrl = plan.summary_print;
    $("summary-print").hidden = !plan.summary_available;   // only after something is in the plan, and only if you ask
    if (openSummaryWhenReady && summaryUrl) { openSummaryWhenReady = false; openSummary(); }
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
    $("helper-more").open = true;
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
    $("pc-open").hidden = !tl.date;   // "My plans changed" once there is a date to change
    $("event-label").textContent = tl.label;
    const ride = $("ride-scene"), procedure = /procedure/i.test(tl.label);
    ride.toggleAttribute("hidden", !procedure);
    if (procedure) playOnce(ride, 3500);                     // the car drives to the house, once
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
      if (s.note) li.appendChild(el("p", "helper-small lockbox-note", s.note));
      if (s.date && !s.past) li.appendChild(calendarButton(s.text, s.date, `Just in Case: ${s.when.toLowerCase()} your ${tl.label}.`));
      ol.appendChild(li);
    }
    // "The day before": one short checklist, with an optional calendar event for that day.
    const db = tl.day_before;
    $("day-before").hidden = !db;
    if (db) {
      $("day-before-date").textContent = dayWords(db.date);
      $("day-before-list").replaceChildren(...db.items.map((t) => { const li = el("li"); li.append(icon("check"), document.createTextNode(t)); return li; }));
      $("day-before-cal").replaceChildren();
      if (db.note) $("day-before-cal").appendChild(el("p", "helper-small lockbox-note", db.note));
      if (!db.past) $("day-before-cal").appendChild(calendarButton("The day before: my checklist", db.date, db.items.join("\n")));
    }
  }
  // ---- The night before: who is doing what (built by the server from the plan) ----
  function nightActions(plan, nb) {
    const text = [nb.title, ...nb.lines].join(" ");
    const box = [];
    if ("speechSynthesis" in window) box.push(readAloudButton(text));
    const pr = el("button", "btn btn-quiet btn-small send-btn"); pr.type = "button";
    pr.append(icon("note"), document.createTextNode("Print"));
    pr.onclick = () => {
      $("card-dialog-title").textContent = "The night before"; frame.title = "The night before"; $("card-print-label").textContent = "Print";
      frame.src = plan.night_print + "?t=" + Date.now(); dialog.showModal();
    };
    box.push(pr);
    if (!isoToLocal(nb.date) || isoToLocal(nb.date) >= new Date(new Date().toDateString()))
      box.push(calendarButton("The night before: my summary", nb.date, [nb.title, ...nb.lines].join("\n")));
    return box;
  }
  function renderNight(plan) {
    const nb = plan.night_before;
    $("night-now").hidden = !(nb && nb.show_now);
    $("night-preview").hidden = !nb || nb.show_now;
    if (!nb) return;
    const items = () => nb.lines.map((t) => el("li", null, t));
    if (nb.show_now) {
      $("night-now-text").textContent = nb.title;
      $("night-now-list").replaceChildren(...items());
      $("night-now-actions").replaceChildren(...nightActions(plan, nb));
    } else {
      $("night-preview-list").replaceChildren(...items());
      $("night-preview-actions").replaceChildren(...nightActions(plan, nb));
    }
  }
  // ---- My plans changed ------------------------------------------------------
  $("pc-open").addEventListener("click", () => {
    const open = $("plans-changed").hidden;
    $("plans-changed").hidden = !open; $("pc-open").setAttribute("aria-expanded", String(open));
    if (open) { $("pc-date").value = $("event-date").value; $("pc-date").focus(); }
  });
  $("pc-cancel").addEventListener("click", () => { $("plans-changed").hidden = true; $("pc-open").setAttribute("aria-expanded", "false"); $("pc-open").focus(); });
  $("pc-save").addEventListener("click", () => {
    $("timeline-error").textContent = "";
    if (!$("pc-date").value) { $("timeline-error").textContent = "Please pick the new date."; return; }
    ws.send(JSON.stringify({ type: "plans_changed", date: $("pc-date").value, time: $("pc-time").value.trim() }));
  });
  // The agent's update drafts, one per helper. The person sends each one from their own phone or email.
  function showPlanUpdates(m) {
    $("plans-changed").hidden = true; $("pc-open").setAttribute("aria-expanded", "false");
    const box = $("pc-drafts"); box.replaceChildren(); box.hidden = false;
    box.appendChild(el("p", "pc-done", `Your plan now uses ${m.when}.`));
    box.appendChild(el("p", "helper-small", "Calendar buttons now use the new dates. If you added the old ones to your calendar, you can delete them there."));
    if (!m.drafts.length) box.appendChild(el("p", "helper-small", "No helpers to tell."));
    for (const d of m.drafts) {
      const card = el("div", "invite-panel");
      card.appendChild(el("p", "invite-title", d.to ? `Update for ${d.to.split(" ")[0]}` : "Update for your helper"));
      const ta = el("textarea"); ta.rows = 6; ta.value = d.text; ta.setAttribute("aria-label", "Update message (you can change it)");
      card.appendChild(ta);
      const sent = el("p", "helper-small"); sent.setAttribute("role", "status");
      card.appendChild(sendControls({
        text: () => ta.value, subject: "A quick update",
        contact: () => (d.task_id != null && contacts.get(d.task_id)) || "",
        onSent: () => { sent.textContent = "Thank you. Marked as sent."; },
      }));
      card.append(sent, el("p", "helper-small invite-you-send", `${AGENT} writes the message. You send it from your own phone or email.`));
      box.appendChild(card);
    }
    const close = el("button", "btn btn-quiet btn-small"); close.type = "button"; close.textContent = "Done";
    close.onclick = () => { box.hidden = true; box.replaceChildren(); };
    box.appendChild(close);
    box.scrollIntoView({ block: "start" });
  }
  // A standard .ics file, made here in the browser. No calendar account is touched,
  // nothing goes to our server, and there is no alarm: the user's calendar decides.
  function makeICS(title, iso, details, rrule) {
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
      ...(rrule ? [`RRULE:${rrule}`] : []),
      `SUMMARY:${esc(title)}`, `DESCRIPTION:${esc(details)}`, "TRANSP:TRANSPARENT",
      "END:VEVENT", "END:VCALENDAR",
    ].map(fold).join("\r\n") + "\r\n";
  }
  function downloadICS(title, text) {
    const url = URL.createObjectURL(new Blob([text], { type: "text/calendar" }));
    const a = el("a"); a.href = url;
    a.download = "just-in-case-" + (title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "step") + ".ics";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  // Plan check-ins: one calendar event that repeats every 3 months. Optional, and the person's own calendar.
  const CHECKIN_TITLE = "Review my Just in Case plan";
  const CHECKIN_RULE = "FREQ=MONTHLY;INTERVAL=3";
  $("checkins-cal").addEventListener("click", () => {
    const first = new Date(); first.setMonth(first.getMonth() + 3);
    const iso = `${first.getFullYear()}-${String(first.getMonth() + 1).padStart(2, "0")}-${String(first.getDate()).padStart(2, "0")}`;
    downloadICS(CHECKIN_TITLE, makeICS(CHECKIN_TITLE, iso,
      "A quick look: is everything in your plan still right? Open Just in Case and mark it as reviewed.", CHECKIN_RULE));
    $("checkins-note").textContent = "Downloaded. Open the file to add it to your calendar.";
  });
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
    renderView();
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
          sessionId = m.session;
          // Ruth's example says it is a made-up story. The live agent says clearly that it is AI.
          // The guided planner (the app's own rules, no AI) needs no label.
          $("demo-banner").hidden = m.mode === "guided";
          if (m.mode === "live") {
            $("demo-title").textContent = `${AGENT} is an AI planning helper.`; $("demo-text").textContent = "You make every choice.";
            $("demo-icon").setAttribute("href", "#i-info");
          }
          $("table-download").href = `/table/${sessionId}.csv`;
          setBusy(false);
          if (!messages.children.length) addMessage("agent", m.greeting);
          if (!feed.children.length) feed.appendChild(el("li", "feed-empty", `Each step shows up here while ${AGENT} works.`));
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
        case "suggest": addSuggestion(m); break;
        case "timeline_error": $("timeline-error").textContent = m.text; break;
        case "plans_changed": showPlanUpdates(m); break;
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
  if (EXAMPLE) { $("example-open").hidden = true; $("example-exit").hidden = false; }   // the starters stay: Ruth taps one

  // ---- text size (remembered on this device only) ------------------------
  const SIZES = [18, 20, 22, 24, 27];
  let sizeIdx = 1;
  try { const s = Number(localStorage.getItem("jic-size")); if (SIZES.includes(s)) sizeIdx = SIZES.indexOf(s); } catch (_) {}
  const SIZE_WORDS = ["Small", "Normal", "Large", "Larger", "Largest"];
  const applySize = () => {
    document.documentElement.style.setProperty("--fs", SIZES[sizeIdx] + "px");
    $("now-size").textContent = $("size-word").textContent = SIZE_WORDS[sizeIdx];
    try { localStorage.setItem("jic-size", SIZES[sizeIdx]); } catch (_) {}
  };
  applySize();
  // A− and A+ live in two places, the Display panel and Settings. Both do the same thing.
  for (const b of document.querySelectorAll("[data-size]")) {
    b.onclick = () => { sizeIdx = Math.max(0, Math.min(SIZES.length - 1, sizeIdx + (b.dataset.size === "bigger" ? 1 : -1))); applySize(); };
  }

  // ---- helper table ------------------------------------------------------
  let editingId = null, seenTasks = new Set(), tableUrls = {};
  const form = $("task-form");
  // Helpers' phone numbers and emails: kept in this page (and the saved copy on this device, if
  // saving is on). Never sent to the server, never on the shared list. Keyed by task id.
  const contacts = new Map();
  let pendingContact = null, restoringContacts = null;
  function setContact(id, value) {
    const v = String(value || "").trim().slice(0, 120);
    if (v) contacts.set(id, v); else contacts.delete(id);
    requestSave();
  }
  function renderTasks(plan) {
    lastPlanForInvites = plan;
    if (openInvite && !(plan.tasks || []).some((t) => t.id === openInvite.id)) openInvite = null;
    tableUrls = { csv: `/table/${sessionId}.csv`, print: `/table/${sessionId}` };
    $("table-download").href = tableUrls.csv;
    const rows = $("helper-rows"); rows.replaceChildren();
    const tasks = plan.tasks || [];
    $("helper-table").hidden = !tasks.length;
    $("table-empty").hidden = !!tasks.length;
    $("helper-count").textContent = tasks.length ? `· ${tasks.length} ${tasks.length === 1 ? "task" : "tasks"}` : "";
    // A gentle line about inviting, never when the person said they don't want to be a burden.
    $("invite-hint").hidden = !!plan.quiet_invites || !tasks.some((t) => !t.invited && !t.person);
    if (restoringContacts && tasks.length) {   // a saved plan reopened: put each contact back on its task
      tasks.forEach((t, i) => { const was = restoringContacts.tasks[i];
        if (was && was.task === t.task && restoringContacts.contacts[i]) contacts.set(t.id, restoringContacts.contacts[i]); });
      restoringContacts = null;
    }
    for (const t of tasks) {
      if (pendingContact && !seenTasks.has(t.id) && t.task === pendingContact.task) { setContact(t.id, pendingContact.contact); pendingContact = null; }
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
      const drafts = (plan.invites || {})[String(t.id)] || {};
      if (drafts.remind_now) box.prepend(inviteButton(t, "reminder"));   // a few days before the date
      box.prepend(inviteButton(t, "invite"));
      if (t.reminded) box.appendChild(el("p", "helper-small reminded-note", "Reminder sent " + dayWords(t.reminded) + "."));
      const iso = readTaskDate(t.date);
      if (iso && isoToLocal(iso) >= new Date(new Date().toDateString()))
        box.appendChild(calendarButton(t.task, iso, `Just in Case helper list: ${t.task}` +
          (t.person ? `, with ${t.person}` : "") + (t.time ? `, ${t.time}` : "") + "."));
      act.appendChild(box);
      if (openInvite && openInvite.id === t.id) act.appendChild(invitePanel(t, plan));
      tr.appendChild(act);
      rows.appendChild(tr);
    }
    if (openInvite && openInvite.focus) {   // a panel that just opened: start in its message box
      openInvite.focus = false;
      const box = rows.querySelector(".invite-panel textarea");
      if (box) { box.focus({ preventScroll: true }); box.setSelectionRange(0, 0); box.scrollTop = 0; }   // start at "Hi ..."
    }
  }

  // ---- invite and remind helpers ------------------------------------------
  // The agent writes the message (fixed, checked wording from the server). The person edits it
  // and sends it from their own phone or email. Nothing is ever sent by the app.
  let openInvite = null;   // { id, kind: "invite" | "reminder", text (edited), focus }
  let lastPlanForInvites = null;
  function inviteButton(t, kind) {
    const quiet = !!(lastPlanForInvites && lastPlanForInvites.quiet_invites);
    const b = el("button", quiet ? "invite-btn invite-quiet" : "invite-btn"); b.type = "button";
    const open = !!openInvite && openInvite.id === t.id && openInvite.kind === kind;
    b.append(icon(kind === "invite" ? "send" : "bell"), document.createTextNode(kind === "invite" ? "Invite" : "Send a reminder"));
    b.setAttribute("aria-expanded", String(open));
    b.setAttribute("aria-label", (kind === "invite" ? "Invite someone to help with: " : "Send a reminder about: ") + t.task);
    b.onclick = () => {
      openInvite = open ? null : { id: t.id, kind, text: null, focus: true };
      if (lastPlanForInvites) renderTasks(lastPlanForInvites);
    };
    return b;
  }
  // Text and email links open the person's own Messages or email app with the words filled in.
  // The app itself never sends anything: the person presses send there, or not.
  const CAN_TEXT = /Android|iPhone|iPad|iPod|Mobile|Macintosh/i.test(navigator.userAgent);  // phones, tablets, and Macs open sms: links
  function contactParts(value) {
    const v = String(value || "").trim();
    if (v.includes("@")) return { email: v.replace(/\s+/g, ""), phone: "" };
    const digits = v.replace(/[^\d+]/g, "");
    return { email: "", phone: digits.replace(/\D/g, "").length >= 7 ? digits : "" };
  }
  const smsHref = (phone, text) => `sms:${phone}?&body=${encodeURIComponent(text)}`;
  const mailHref = (email, subject, text) => `mailto:${encodeURIComponent(email).replace(/%40/g, "@")}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(text)}`;
  async function copyText(text, fallbackBox) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch (_) { if (fallbackBox) { fallbackBox.focus(); fallbackBox.select(); try { return document.execCommand("copy"); } catch (e) { return false; } } return false; }
  }
  function sendControls(opts) {
    const wrap = el("div", "send-controls");
    const row = el("div", "send-row");
    const sms = el("a", "btn btn-primary send-btn"); sms.append(icon("chat"), document.createTextNode("Send by text"));
    const mail = el("a", "btn btn-quiet send-btn"); mail.append(icon("mail"), document.createTextNode("Send by email"));
    const copyBtn = el("button", "btn btn-quiet send-btn"); copyBtn.type = "button";
    copyBtn.append(icon("copy"), document.createTextNode("Copy message"));
    const copied = el("p", "helper-small send-copied"); copied.setAttribute("role", "status");
    // Fill in the links at the moment they are tapped, so they carry the latest edits.
    sms.href = "sms:"; mail.href = "mailto:";
    sms.addEventListener("click", () => { sms.href = smsHref(contactParts(opts.contact()).phone, opts.text()); });
    mail.addEventListener("click", () => { mail.href = mailHref(contactParts(opts.contact()).email, opts.subject, opts.text()); });
    copyBtn.addEventListener("click", async () => {
      copied.textContent = (await copyText(opts.text(), wrap.closest(".invite-panel, .sticky")?.querySelector("textarea")))
        ? "Copied. Paste it into a text or an email." : "Select the message above and copy it.";
    });
    if (CAN_TEXT) row.appendChild(sms); else row.appendChild(copyBtn);   // where a text link can't open, copy instead
    row.appendChild(mail);
    // After a send button: "Did you send it?" We only record what the person tells us.
    const ask = el("div", "send-ask"); ask.hidden = true;
    ask.appendChild(el("p", "send-ask-q", "Did you send it?"));
    const askRow = el("div", "send-row");
    const yes = el("button", "btn btn-primary send-btn"); yes.type = "button"; yes.append(icon("check"), document.createTextNode("Yes, I sent it"));
    const notYet = el("button", "btn btn-quiet send-btn"); notYet.type = "button"; notYet.textContent = "Not yet";
    askRow.append(yes, notYet); ask.appendChild(askRow);
    yes.addEventListener("click", () => { ask.hidden = true; opts.onSent(); });
    notYet.addEventListener("click", () => {
      ask.hidden = true;
      if (!copyBtn.isConnected) { row.appendChild(copyBtn); copied.textContent = "If your app didn't open, you can copy the message instead."; }
      copyBtn.focus();
    });
    for (const b of [sms, mail, copyBtn]) b.addEventListener("click", () => setTimeout(() => { ask.hidden = false; }, 400));
    wrap.append(row, copied, ask);
    return wrap;
  }
  // Send buttons for an approved note. If the note is for someone in the helper table, "Yes, I sent it"
  // marks their task Invited, and their saved phone or email is used.
  function noteSendBox(to, text) {
    const box = el("div", "note-send");
    box.appendChild(el("p", "helper-small", `${AGENT} writes the message. You send it from your own phone or email.`));
    const names = (to || []).map((n) => String(n).toLowerCase());
    const theirTasks = () => ((lastPlanForInvites && lastPlanForInvites.tasks) || [])
      .filter((t) => t.person && names.includes(t.person.toLowerCase()));
    box.appendChild(sendControls({
      text: () => text,
      subject: (lastPlanForInvites && lastPlanForInvites.invite_words.invite_subject) || "Could you help with something small?",
      contact: () => { const t = theirTasks().find((x) => contacts.get(x.id)); return t ? contacts.get(t.id) : ""; },
      onSent: () => {
        const mine = theirTasks().filter((t) => !t.invited);
        for (const t of mine) sendTable("invited", { id: t.id });
        if (!mine.length) ws.send(JSON.stringify({ type: "note_sent" }));
      },
    }));
    return box;
  }
  function draftFor(t, plan, kind) {
    const d = (plan.invites || {})[String(t.id)] || {};
    let text = kind === "reminder" ? d.reminder : d.invite;
    if (kind === "invite" && plan.share) text += "\n\n" + plan.invite_words.link_line + " " + location.origin + plan.share.url;
    return text || "";
  }
  function invitePanel(t, plan) {
    const kind = openInvite.kind;
    const panel = el("div", "invite-panel");
    panel.setAttribute("role", "group");
    const who = t.person ? t.person.split(" ")[0] : "someone";
    const title = kind === "invite" ? `Invite ${who}` : `Remind ${who}`;
    panel.setAttribute("aria-label", title);
    panel.appendChild(el("p", "invite-title", title));
    const id = "invite-text-" + t.id;
    const lab = el("label", "invite-label", "Your message (you can change it)"); lab.htmlFor = id;
    const ta = el("textarea"); ta.id = id; ta.rows = 9; ta.maxLength = 1200;
    if (openInvite.text == null) openInvite.text = draftFor(t, plan, kind);
    ta.value = openInvite.text;
    ta.addEventListener("input", () => { openInvite.text = ta.value; });
    // Their phone or email: the same field as in the row form. It stays on this device.
    const cid = "invite-contact-" + t.id;
    const clab = el("label", "invite-label", "Their phone or email (optional)"); clab.htmlFor = cid;
    const cin = el("input"); cin.id = cid; cin.maxLength = 120; cin.autocomplete = "off";
    cin.value = contacts.get(t.id) || "";
    cin.addEventListener("change", () => setContact(t.id, cin.value));
    panel.append(lab, ta, clab, cin, el("p", "helper-small", "Stays on this device only."));
    panel.appendChild(sendControls({
      text: () => ta.value,
      subject: kind === "invite" ? plan.invite_words.invite_subject : plan.invite_words.reminder_subject,
      contact: () => cin.value,
      onSent: () => {   // "Yes, I sent it": an invite marks the task Invited; a reminder notes the day
        openInvite = null;
        sendTable(kind === "invite" ? "invited" : "reminded", { id: t.id });
      },
    }));
    panel.appendChild(el("p", "helper-small invite-you-send", plan.invite_words.you_send));
    const close = el("button", "btn btn-quiet btn-small"); close.type = "button"; close.textContent = "Close";
    close.onclick = () => { openInvite = null; renderTasks(lastPlanForInvites); };
    panel.appendChild(close);
    return panel;
  }
  // Status is always a word with an icon, never color alone.
  const STATUS = { open: ["Open", "plus"], invited: ["Invited", "send"], helping: ["Someone's helping", "person"], confirmed: ["Confirmed", "check"] };
  // The same order as the server: Confirmed, then Invited (until the helper signs up), then Someone's helping, then Open.
  const taskStatus = (t) => (t.person && t.confirmed ? "confirmed" : t.invited ? "invited" : t.person ? "helping" : "open");
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
    $("helper-more").open = true;
    for (const k of ["task", "person", "date", "time"]) form.elements[k].value = t[k] || "";
    form.elements.contact.value = contacts.get(t.id) || "";
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
    const contact = form.elements.contact.value.trim();   // stays here: it is not part of the row we send
    if (editingId != null) { row.id = editingId; setContact(editingId, contact); sendTable("update", row); }
    else { pendingContact = contact ? { task: row.task, contact } : null; sendTable("add", row); }
    resetForm();
  });
  $("task-cancel").onclick = resetForm;
  $("table-print").addEventListener("click", () => {
    if (!tableUrls.print) return;
    $("card-dialog-title").textContent = "Who's helping"; frame.title = "Who's helping"; $("card-print-label").textContent = "Print table";
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
  // Lists shared from this browser, so "Delete everything" can remove them. Kept only on this device.
  const MY_SHARES = "jic-my-shares";
  const readShares = () => { try { return JSON.parse(localStorage.getItem(MY_SHARES) || "[]"); } catch (_) { return []; } };
  function rememberShare(share) {
    const owner = new URLSearchParams(share.owner_url.split("?")[1] || "").get("owner");
    const id = share.url.split("/").pop();
    if (!owner || readShares().some((x) => x.id === id)) return;
    try { localStorage.setItem(MY_SHARES, JSON.stringify([...readShares(), { id, owner }].slice(-20))); } catch (_) {}
  }
  function renderShare(plan) {
    shareState = plan.share;
    if (plan.share && !EXAMPLE) rememberShare(plan.share);
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
    $("card-dialog-title").textContent = "Your fridge card"; frame.title = "Your fridge card"; $("card-print-label").textContent = "Print card";
    frame.src = cardUrl + "?t=" + Date.now();
    dialog.showModal();
  });
  // "Email my plan": opens the person's own email app with the summary filled in. They choose who gets it.
  let planEmail = null;
  $("plan-email").addEventListener("click", () => {
    $("plan-email").href = "mailto:?subject=" + encodeURIComponent("My Just in Case plan") + "&body=" + encodeURIComponent(planEmail || "");
  });
  // "Was this helpful?": only the answer and the comment are sent. No session, no plan, no name.
  let fbAnswer = null;
  for (const [id, val] of [["fb-up", true], ["fb-down", false]]) {
    $(id).addEventListener("click", () => {
      fbAnswer = val;
      $("fb-up").setAttribute("aria-pressed", String(val === true));
      $("fb-down").setAttribute("aria-pressed", String(val === false));
      $("fb-send").disabled = false;
    });
  }
  $("fb-send").addEventListener("click", async () => {
    if (fbAnswer === null) return;
    $("fb-send").disabled = true;
    try {
      const r = await fetch("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ helpful: fbAnswer, comment: $("fb-comment").value.trim() }) });
      if (!r.ok) throw new Error();
      $("fb-status").textContent = "Thank you. Your answer was saved.";
      for (const id of ["fb-up", "fb-down", "fb-comment"]) $(id).disabled = true;
    } catch (_) {
      $("fb-status").textContent = "That didn't go through. You can try again.";
      $("fb-send").disabled = false;
    }
  });
  // "Delete everything": saved progress, helpers' contacts, and every list shared from this browser.
  $("delete-everything").addEventListener("click", async () => {
    if (!confirm("Delete everything? This removes your saved plan and any helper lists you shared. It can't be undone.")) return;
    $("delete-status").textContent = "Deleting...";
    for (const s of readShares()) {
      try { await fetch(`/api/share/${encodeURIComponent(s.id)}/delete`, { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ owner: s.owner }) }); } catch (_) {}
    }
    try {
      for (const k of Object.keys(localStorage)) {
        if (k === SAVE_KEY || k === MY_SHARES || k.startsWith("jic-claims-")) localStorage.removeItem(k);
      }
    } catch (_) {}
    contacts.clear(); saving = false;
    $("delete-status").textContent = "Everything is deleted. Starting fresh.";
    setTimeout(() => { location.href = location.pathname + "#home"; location.reload(); }, 1200);   // a new, empty visit
  });
  let walletUrl = null;
  $("wallet-print").addEventListener("click", () => {
    if (!walletUrl) return;
    $("card-dialog-title").textContent = "Your wallet card"; frame.title = "Your wallet card"; $("card-print-label").textContent = "Print wallet card";
    frame.src = walletUrl + "?t=" + Date.now();
    dialog.showModal();
  });
  let planPrintUrl = null;
  // Opt in only: the button asks first. Nothing is made until the person says yes.
  let summaryUrl = null, openSummaryWhenReady = false;
  function openSummary() {
    story.summary = true;
    $("card-dialog-title").textContent = "My readiness summary"; frame.title = "My readiness summary"; $("card-print-label").textContent = "Print summary";
    frame.src = summaryUrl + "?t=" + Date.now();
    dialog.showModal();
  }
  $("summary-print").addEventListener("click", () => $("summary-dialog").showModal());
  $("summary-yes").addEventListener("click", () => {
    if (!ws || ws.readyState !== 1) return;
    openSummaryWhenReady = true;
    ws.send(JSON.stringify({ type: "summary_consent", agree: true }));
    $("summary-dialog").close();
  });
  $("plan-print").addEventListener("click", () => {
    if (!planPrintUrl) return;
    $("card-dialog-title").textContent = "Your plan"; frame.title = "Your plan"; $("card-print-label").textContent = "Print plan";
    frame.src = planPrintUrl + "?t=" + Date.now();
    dialog.showModal();
  });
  $("card-print").addEventListener("click", () => { try { frame.contentWindow.print(); } catch (_) { window.open(frame.src); } });
  $("card-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });

  // ---- Gentle motion: the home scene plays about nine seconds, then settles into a still picture ----
  // Nothing moves when the device asks for less motion. "Pause" is there the whole time it plays.
  const stillOnly = matchMedia("(prefers-reduced-motion: reduce)");
  const hero = $("hero-scene"), sceneBtn = $("scene-btn");
  const SCENE_MS = 9500;
  let sceneState = "still", sceneLeft = 0, sceneStart = 0, sceneTimer = null;
  function sceneButton(word, ic) { sceneBtn.replaceChildren(icon(ic), document.createTextNode(" "), el("span", null, word)); }
  function sceneRun() { sceneStart = Date.now(); sceneTimer = setTimeout(sceneEnd, sceneLeft); }
  function sceneEnd() {
    clearTimeout(sceneTimer); hero.classList.remove("playing", "paused");
    sceneState = "still"; sceneButton("Play scene", "play");
  }
  function scenePlay() {
    if (stillOnly.matches) return;
    sceneEnd(); void hero.getBoundingClientRect();   // start again from the beginning
    hero.classList.add("playing"); sceneLeft = SCENE_MS; sceneRun();
    sceneState = "playing"; sceneButton("Pause", "pause");
  }
  sceneBtn.addEventListener("click", () => {
    if (sceneState === "still") scenePlay();
    else if (sceneState === "playing") {
      clearTimeout(sceneTimer); sceneLeft -= Date.now() - sceneStart;
      hero.classList.add("paused"); sceneState = "paused"; sceneButton("Play", "play");
    } else { hero.classList.remove("paused"); sceneRun(); sceneState = "playing"; sceneButton("Pause", "pause"); }
  });
  const motionSetting = () => { sceneBtn.hidden = stillOnly.matches; if (stillOnly.matches) sceneEnd(); };
  stillOnly.addEventListener("change", motionSetting); motionSetting();
  // Short pictures play once when they first appear: the car on the ride home, the check when the plan is ready.
  function playOnce(svg, ms) {
    if (stillOnly.matches || svg.dataset.played) return;
    svg.dataset.played = "1"; svg.classList.add("playing");
    setTimeout(() => svg.classList.remove("playing"), ms);
  }

  // ---- The 1-minute tour on About: never plays by itself. If the video is missing, show the picture and the steps. ----
  const player = $("tour-player"), playBtn = $("tour-play");
  const tourMissing = () => { player.hidden = playBtn.hidden = true; $("tour-fallback").hidden = false; };
  player.querySelector("source").addEventListener("error", tourMissing);
  fetch("/static/img/tour.mp4", { method: "HEAD" }).then((r) => { if (!r.ok) tourMissing(); }).catch(() => {});
  const tourLabel = () => playBtn.replaceChildren(icon(player.paused ? "play" : "pause"), document.createTextNode(" "),
                                                 el("span", null, player.paused ? "Play tour" : "Pause"));
  playBtn.addEventListener("click", () => (player.paused ? player.play() : player.pause()));
  for (const ev of ["play", "pause", "ended"]) player.addEventListener(ev, tourLabel);
  for (const tr of player.textTracks) tr.mode = "showing";      // captions on, from the start

  // ---- Features: tap a bubble to read one sentence about it. One bubble is open at a time. ----
  const bubbles = [...document.querySelectorAll(".bubble")];
  for (const b of bubbles) b.addEventListener("click", () => {
    const opening = b.getAttribute("aria-expanded") !== "true";
    for (const o of bubbles) { o.setAttribute("aria-expanded", "false"); $(o.getAttribute("aria-controls")).hidden = true; }
    if (opening) { b.setAttribute("aria-expanded", "true"); $(b.getAttribute("aria-controls")).hidden = false; }
  });

  // ---- "By the numbers": counted by the server from the app itself -------
  fetch("/api/facts").then((r) => r.json()).then((f) => {
    for (const n of document.querySelectorAll("[data-fact]")) {
      const v = f[n.dataset.fact];
      if (v == null) n.closest("li").remove(); else n.textContent = v;
    }
    $("facts").hidden = false;
  }).catch(() => {});  // if it can't load, the section stays hidden rather than showing a guess

  // ---- Keep family in the loop: outside services from the services list, a link they may choose ----
  let familyOpened = false;
  $("family-loop").addEventListener("toggle", () => { if ($("family-loop").open) familyOpened = true; });
  fetch("/api/family-updates").then((r) => r.json()).then((list) => {
    const ul = $("family-services");
    for (const s of list) {
      const li = el("li");
      const name = el("p", "s-name", s.name);
      if (s.cost === "free") name.append(" ", el("span", "free-badge", "Free"));
      li.append(name, el("p", "s-what", s.how_to_use));
      const checked = checkedLabel(s.last_verified);
      if (checked) { const c = el("p", "tag-checked"); c.append(icon("check"), document.createTextNode(checked)); li.appendChild(c); }
      li.appendChild(el("p", "helper-small", s.separate));
      const a = el("a", "btn btn-quiet btn-block"); a.href = s.url; a.target = "_blank"; a.rel = "noopener noreferrer";
      a.append(icon("info"), document.createTextNode(`Open ${s.name} (new tab)`));
      li.appendChild(a);
      ul.appendChild(li);
    }
  }).catch(() => {});

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

  // ---- Settings: reading voice, text size, and colors, behind the gear -----
  const settings = $("settings-dialog");
  $("settings-open").addEventListener("click", () => settings.showModal());
  $("settings-close").addEventListener("click", () => settings.close());
  settings.addEventListener("click", (e) => { if (e.target === settings) settings.close(); });
  settings.addEventListener("close", () => $("settings-open").focus());

  // ---- light / dark (remembered on this device only) ---------------------
  const setTheme = (t, save = true) => {
    document.documentElement.dataset.theme = t;
    for (const b of document.querySelectorAll("[data-theme-pick]")) b.setAttribute("aria-pressed", String(b.dataset.themePick === t));
    $("now-colors").textContent = { light: "Light", dark: "Dark", contrast: "High contrast" }[t];
    if (save) { try { localStorage.setItem("jic-theme-v2", t); } catch (_) {} }
  };
  setTheme(["dark", "contrast"].includes(document.documentElement.dataset.theme) ? document.documentElement.dataset.theme : "light", false);
  for (const b of document.querySelectorAll("[data-theme-pick]")) b.onclick = () => setTheme(b.dataset.themePick);

  // The Display and Menu drop-downs close when you tap outside them or press Escape.
  const dropdowns = [$("display-menu"), $("more-menu")];
  document.addEventListener("click", (e) => { for (const d of dropdowns) if (d.open && !d.contains(e.target)) d.open = false; });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    for (const d of dropdowns) if (d.open) { d.open = false; d.querySelector("summary").focus(); }
  });

  // Warn if the code was updated (git pull) but the server was not restarted.
  const checkRestart = () => fetch("/api/config").then((r) => r.json())
    .then((c) => {
      $("restart-banner").hidden = c.needs_restart === false;   // missing = older server
      // The demo runs without an AI key, so it restarts with --demo; the live app does not.
      $("restart-cmd").textContent = c.demo ? "python serve.py --demo" : "python serve.py";
      // Without AI running, the FAQ does not call the helper AI.
      if (c.ai === false) $("faq-who").textContent = `${AGENT} is the app's planning helper. It suggests options, ` +
        "but you make every decision, and it never sends, shares, or books anything without your OK.";
    }).catch(() => {});
  checkRestart(); setInterval(checkRestart, 60000);

  // ---- simple view controls: Back, Next, progress dots, Show details ---------
  const chat = document.querySelector(".chat");
  function renderView() {
    const last = pages.length - 1;
    view = Math.max(0, Math.min(view, last));
    const reviewing = view < last;
    const pending = !!messages.querySelector(".slip-actions");
    for (const li of messages.children) li.toggleAttribute("data-active", +li.dataset.page === view);
    chat.classList.toggle("reviewing", reviewing);
    chat.classList.toggle("has-slip", pending && !reviewing);
    const dots = $("step-dots");
    dots.replaceChildren();
    const shown = Math.min(pages.length, 10);  // the last ten, so a long visit never overflows
    for (let i = pages.length - shown; i < pages.length; i++) dots.appendChild(el("span", "step-dot" + (i === view ? " on" : "")));
    $("step-count").textContent = pages.length ? "Step " + (view + 1) : "";
    $("step-back").disabled = view <= 0;
    $("step-next").disabled = !reviewing && (pending || busy || !pages.length);
    $("step-hint").textContent = "";
  }
  new MutationObserver(renderView).observe(messages, { childList: true, subtree: true });
  // When Back or Next changes the screen, say what is on it (screen readers do not hear a hidden list swap).
  function announceStep() {
    const words = [...messages.querySelectorAll("[data-active]")].map((li) => li.innerText.trim()).join(" ");
    $("step-announce").textContent = `Step ${view + 1}. ${words}`.slice(0, 400);
  }
  $("step-back").onclick = () => { view -= 1; renderView(); announceStep(); };
  $("step-next").onclick = () => {
    if (view < pages.length - 1) { view += 1; renderView(); announceStep(); return; }
    if (!input.value.trim()) { $("step-hint").textContent = "Type your answer above, then tap Next."; input.focus(); return; }
    send(input.value);
  };
  // Simple view or Full view. The choice is remembered on this device only,
  // like the color theme. It is a display setting, not part of anyone's plan.
  function setView(mode, save = true) {
    const simple = mode !== "full";
    chat.classList.toggle("simple", simple);
    document.body.classList.toggle("simple-view", simple);  // the Settings panel shows "All voices" in Full view only
    $("view-simple").setAttribute("aria-pressed", String(simple));
    $("view-full").setAttribute("aria-pressed", String(!simple));
    if (save) { try { localStorage.setItem("jic-view", simple ? "simple" : "full"); } catch (_) {} }
    if (!simple) scrollChat();
  }
  $("view-simple").onclick = () => setView("simple");
  $("view-full").onclick = () => setView("full");
  let savedView = null; try { savedView = localStorage.getItem("jic-view"); } catch (_) {}
  setView(savedView === "full" && !EXAMPLE ? "full" : "simple", false);  // Simple is the default, and Ruth's example is always Simple


  // ---- one seasonal tip on Home, for a part of the plan the app already knows ----
  // "Check it" opens that part of the plan, or puts a starting sentence in the box. Nothing is sent for you.
  const SEASON = (m) => (m >= 8 || m <= 1)   // September to February
    ? { need: "power_outage", icon: "snow", lead: m >= 11 || m <= 1 ? "Winter is here." : "Winter is coming.",
        ask: "Is your power outage plan ready?", say: "I'd like to get ready for a winter power outage." }
    : { need: "mail_while_away", icon: "mail", lead: m <= 4 ? "Going away this spring?" : "Going away this summer?",
        ask: "Is your mail plan ready?", say: "I'm going away for two weeks." };
  const season = SEASON(new Date().getMonth());
  $("season-lead").textContent = season.lead; $("season-ask").textContent = season.ask;
  $("season-link").href = "#plan-" + season.need;
  $("season-tip").querySelector("use").setAttribute("href", "#i-" + season.icon);
  // ---- What needs attention: the server's list of real gaps, each with one button ----
  function renderAttention(plan) {
    const list = plan.attention;
    $("attention").hidden = !list;
    if (!list) return;
    const ul = $("attention-list"); ul.replaceChildren();
    $("attention-ok").hidden = list.length > 0;
    $("attention-ok-text").textContent = plan.all_set;
    $("attention-title").querySelector("span").textContent = list.length ? "What needs attention" : "Your plan";
    for (const a of list) {
      const li = el("li", "attention-item attention-" + a.kind);
      li.append(icon(a.kind === "unconfirmed" ? "person" : a.kind === "waitlist" ? "wait" : "info"), el("span", null, a.text));
      let b;
      if ((a.kind === "waitlist" || a.kind === "old_info") && a.phone && a.phone !== "see website") {
        b = el("a", "btn btn-quiet btn-small attention-btn"); b.href = "tel:" + a.phone.replace(/[^0-9]/g, "");
      } else {
        b = el("button", "btn btn-quiet btn-small attention-btn"); b.type = "button";
        b.onclick = () => {
          if (a.kind === "unconfirmed") {   // the Round 10 send panel, with the reminder draft
            $("helper-more").open = true;
            openInvite = { id: a.task_id, kind: "reminder", text: null, focus: true };
            renderTasks(lastPlanForInvites);
            const row = [...document.querySelectorAll("#helper-rows .invite-panel")][0];
            if (row) row.scrollIntoView({ block: "center" });
          } else openPlanPart(a.need);
        };
      }
      b.append(icon(a.kind === "unconfirmed" ? "bell" : a.kind === "uncovered" ? "search" : "phone"), document.createTextNode(a.action));
      li.appendChild(b);
      ul.appendChild(li);
    }
  }
  function openPlanPart(need) {
    const tag = planEl.querySelector(`.tag[data-need="${need}"]`);
    if (tag) {
      const head = tag.querySelector(".tag-head");
      if (head.getAttribute("aria-expanded") === "false") head.click();
      tag.scrollIntoView({ block: "center" }); head.focus({ preventScroll: true });
    } else if (need === season.need) {
      if (!input.value.trim()) input.value = season.say;
      input.scrollIntoView({ block: "center" }); input.focus({ preventScroll: true });
    }
  }

  // ---- Ruth's example: a guided story -------------------------------------------
  // Ruth's words are typed for her. Every yes is still a real tap: the guide rings the button to tap.
  const story = { at: 0, shown: -1, mark: null, card: false, summary: false, done: false };
  const isoIn = (days) => { const d = new Date(); d.setDate(d.getDate() + days);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
  function yesOn(need) {   // the "Yes, add this" button on a plan part, opened so it can be seen
    const tag = [...planEl.querySelectorAll(".tag")].find((t) => t.querySelector(".tag-answer") && (!need || t.dataset.need === need));
    if (!tag) return null;
    const head = tag.querySelector(".tag-head");
    if (head.getAttribute("aria-expanded") === "false") head.click();
    return tag.querySelector(".tag-answer .btn-primary");
  }
  const lastPage = () => { view = pages.length - 1; renderView(); };
  const STORY = [
    { text: `${AGENT} says hello. Ruth taps a sentence to start.`, target: () => document.querySelector(".starter"),
      auto: () => setTimeout(() => { if (!chatHistory.some((m) => m.who === "user")) document.querySelector(".starter").click(); }, 4000),   // time to read the greeting first
      done: () => chatHistory.some((m) => m.who === "user") },
    { text: `${AGENT} asks one short question at a time. Ruth answers, and you tap the ringed button for her.`,
      target: () => messages.querySelector(".slip-actions .btn-primary"),
      done: () => !!cardUrl && !busy && !messages.querySelector(".slip-actions") },
    { text: `${AGENT} sums up what is planned so far. Back and Next move between the steps.`, next: true,
      show: () => { const r = [...messages.querySelectorAll(".msg-recap")].pop(); if (r) { view = +r.dataset.page; renderView(); } },
      target: () => [...messages.querySelectorAll(".msg-recap")].pop() },
    // The timeline lists only parts Ruth said yes to, so her first yes comes before it.
    { text: "Ruth says yes to her ride home.", target: () => yesOn("ride_home_after_procedure"),
      done: () => !!planEl.querySelector('.tag[data-need="ride_home_after_procedure"] .status-ok') },
    { text: "Ruth picks the date of her procedure. Each step to get ready can go in her calendar.", next: true,
      show: () => { lastPage(); const d = $("event-date"); if (!d.value) { d.value = isoIn(30); d.dispatchEvent(new Event("change")); } },
      target: () => document.querySelector("#steps .cal-btn") || $("getting-ready") },
    { text: "Doris will check in on Ruth. Her row in the helper table says \u201cSomeone's helping.\u201d", next: true,
      show: () => { $("helper-more").open = true; }, target: () => document.querySelector("#helper-rows .status-helping") },
    { text: `Ruth taps Invite. ${AGENT} writes a warm message, and she sends it from her own phone or email.`, next: true,
      show: () => { $("helper-more").open = true; },
      target: () => document.querySelector("#helper-rows .send-row .send-btn") || document.querySelector("#helper-rows .invite-btn") },
    { text: "Her next step card has Call now. When the ride is booked, she taps Done.", target: () => $("next-done"), done: () => story.done },
    { text: "Ruth says yes to the rest of her plan.", target: () => yesOn(), done: () => !planEl.querySelector(".tag-answer") },
    { text: "Every part is covered. A calm picture shows that her plan is ready.", next: true, target: () => $("ready-ill") },
    { text: "Each part has a \u201cWhy this?\u201d link. It explains the choice using only facts.", next: true,
      target: () => planEl.querySelector(".why-this summary"), done: () => story.card },
    { text: `${AGENT} checks her plan each time. \u201cWhat needs attention\u201d lists only real gaps.`, next: true,
      target: () => $("attention"), done: () => story.card },
    { text: "Ruth opens her fridge card. It lists her allergies and where her papers are.", target: () => $("card-link"), done: () => story.card },
    { text: "She can print a wallet card too, with the same details.", next: true, target: () => $("wallet-print"), done: () => story.summary },
    { text: "The night before, a calm summary shows who is doing what. She can preview it now.", next: true,
      show: () => { $("night-preview").open = true; }, target: () => $("night-preview"), done: () => story.summary },
    { text: "She makes a readiness summary. She decides who sees it.", target: () => $("summary-print"), done: () => story.summary },
    { text: "If her date moves, \u201cMy plans changed\u201d moves everything and drafts an update for each helper.", next: true,
      target: () => $("pc-open"), done: () => $("review-mark").hidden },
    { text: "Last, she marks her plan as reviewed.", target: () => $("review-mark"), done: () => $("review-mark").hidden },
    { text: "That's Ruth's story. Your own plan works the same way, one question at a time.", last: true },
  ];
  function storyTick() {
    if (!EXAMPLE || $("panel-plan").hidden) return;
    while (STORY[story.at].done && STORY[story.at].done()) story.at += 1;
    const step = STORY[story.at];
    if (story.shown !== story.at) {
      story.shown = story.at;
      $("story-guide").hidden = false;
      $("story-count").textContent = `Ruth's example \u00b7 step ${story.at + 1} of ${STORY.length}`;
      $("story-text").textContent = step.text;
      $("story-next").hidden = !step.next; $("story-own").hidden = !step.last;
      if (step.show) step.show();
      if (step.auto) step.auto();
    }
    const t = step.target ? step.target() : null;
    if (t !== story.mark) {
      if (story.mark) story.mark.classList.remove("story-next");
      story.mark = t;
      if (t) { t.classList.add("story-next"); t.scrollIntoView({ block: "center" }); }
    }
  }
  $("story-next").addEventListener("click", () => { story.at = Math.min(story.at + 1, STORY.length - 1); storyTick(); });
  $("next-done").addEventListener("click", () => { story.done = true; });
  $("card-link").addEventListener("click", () => { story.card = true; });
  if (EXAMPLE) {
    let queued = false;
    new MutationObserver(() => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; storyTick(); }); } })
      .observe($("main"), { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"] });
    // The fridge card and summary open in dialogs outside the page, so also look again after each tap and each close.
    document.addEventListener("click", () => setTimeout(storyTick, 0));
    for (const d of document.querySelectorAll("dialog")) d.addEventListener("close", () => setTimeout(storyTick, 0));
  }

  // ---- first-time tour: four short tips, shown once. "Show me how" plays it again. ----
  // Each tip sits in the page next to what it points to, so it never covers the 911 and 988 bar.
  const TOUR_KEY = "jic-tour-done";
  const TOUR = [
    { tab: "home", target: () => document.querySelector(".home-buttons .btn-primary"),
      text: () => `Start here. ${AGENT} asks a few short questions, one at a time.` },
    { tab: "plan", target: () => ($("mic").hidden ? input : $("mic")), after: () => $("composer"),
      text: () => ($("mic").hidden ? "Type your answer in this box. Or tap one of the sentences above it."
                                    : "Tap Speak to talk instead of typing. You check the words before they are sent.") },
    { tab: "plan", target: () => $("plan-section"),
      text: () => "Your plan shows up here as you talk. Nothing is added until you say yes." },
    { tab: "plan", target: () => $("plan-section"),
      text: () => "When your plan is ready, print your fridge card here. It has 911 at the top and who to call." },
  ];
  let tourTip = null, tourTarget = null;
  function endTour() {
    if (tourTip) tourTip.remove();
    if (tourTarget) tourTarget.classList.remove("tour-target");
    tourTip = tourTarget = null;
  }
  function showTip(i, focus) {
    endTour();
    const step = TOUR[i], last = i === TOUR.length - 1;
    if (location.hash.slice(1) !== step.tab) { history.pushState(null, "", "#" + step.tab); showTab(step.tab, false); }
    tourTarget = step.target(); tourTarget.classList.add("tour-target");
    tourTip = el("section", "tour-tip"); tourTip.tabIndex = -1;
    tourTip.setAttribute("aria-labelledby", "tour-count");
    const count = el("p", "tour-count", `Tip ${i + 1} of ${TOUR.length}`); count.id = "tour-count";
    const row = el("div", "tour-actions");
    const skip = el("button", "tour-skip", "Skip tour"); skip.type = "button"; skip.onclick = endTour; skip.hidden = last;
    const next = el("button", "btn tour-next", last ? "Done" : "Next"); next.type = "button";
    next.onclick = () => (last ? endTour() : showTip(i + 1, true));
    row.append(skip, next);
    tourTip.append(count, el("p", "tour-text", step.text()), row);
    tourTip.addEventListener("keydown", (e) => { if (e.key === "Escape") endTour(); });
    (step.after || step.target)().after(tourTip);
    tourTip.scrollIntoView({ block: "center" });
    if (focus) tourTip.focus({ preventScroll: true });
  }
  function startTour(focus) {
    try { localStorage.setItem(TOUR_KEY, "1"); } catch (_) {}   // shown once, whether finished or skipped
    showTip(0, focus);
  }

  // ---- screens: Home (the default), Make my plan, Get help, and the Menu ----
  // Home shows three big buttons. About and Features live in the small Menu.
  // The 911 and 988 strip sits above everything, so it shows on every screen.
  // The chat connects the first time "Make my plan" opens, not before.
  const TABS = ["home", "about", "features", "privacy", "who", "plan", "help"];
  let connected = false;
  function showTab(raw, focus) {
    if (raw.startsWith("plan-")) { history.replaceState(null, "", "#plan"); showTab("plan", false); openPlanPart(raw.slice(5)); return; }
    if (raw === "tour") { history.replaceState(null, "", "#home"); showTab("home", false); startTour(true); return; }
    const seePlan = raw === "my-plan";           // "See my plan": the plan screen, scrolled to your plan
    let name = seePlan ? "plan" : raw;
    if (!TABS.includes(name)) name = EXAMPLE ? "plan" : "home";
    for (const t of TABS) $("panel-" + t).hidden = t !== name;
    $("tabs").hidden = !["about", "features", "who", "privacy"].includes(name);   // the tab row, on the pages about the app
    for (const a of document.querySelectorAll(".more-menu a, .tabs a")) {
      if (a.dataset.tab === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    }
    $("menu-home").hidden = name === "home";
    $("more-menu").open = false;
    if (name === "plan" && !connected) { connected = true; connect(); }
    if (seePlan) { $("plan-section").scrollIntoView(); $("plan-section").focus({ preventScroll: true }); }
    else if (focus) { window.scrollTo(0, 0); $("panel-" + name).focus({ preventScroll: true }); }
  }
  $("more-menu").addEventListener("click", (e) => { if (e.target.closest("a")) $("more-menu").open = false; });
  window.addEventListener("hashchange", () => { endTour(); showTab(location.hash.slice(1), true); });
  showTab(location.hash.slice(1), false);
  if (!$("panel-home").hidden) scenePlay();                 // the home scene plays once when Home first opens
  let toured = "1"; try { toured = localStorage.getItem(TOUR_KEY); } catch (_) {}   // no storage: never nag
  if (!toured && !EXAMPLE && !$("panel-home").hidden) startTour(false);
})();
