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
  const voiceScore = (v) => {
    if (NOVELTY.test(v.name)) return -1;
    let s = 0;
    if (/^en[-_]US/i.test(v.lang)) s += 100; else if (/^en/i.test(v.lang)) s += 60;
    if (/enhanced|premium|natural|neural/i.test(v.name)) s += 50;
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
  const FEMALE_NAMES = /\b(samantha|karen|moira|tessa|fiona|veena|victoria|allison|ava|susan|serena|kate|nicky|zira|aria|jenny|michelle|sonia|libby|maisie|natasha|annette|clara|neerja|emily|hazel|linda|catherine|heera|heather|leah|molly|mia)\b/i;
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

  // ---- "See an example": plays Ruth's side of the scripted demo ----------
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
      body.appendChild(el("p", null, "This is for " + data.to.join(" and ") + ". You send it yourself."));
      body.appendChild(moreDetails("We never send it. You can change it, or skip it."));
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
          sessionId = m.session; $("demo-banner").hidden = !m.demo;
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
  function renderTasks(plan) {
    tableUrls = { csv: `/table/${sessionId}.csv`, print: `/table/${sessionId}` };
    $("table-download").href = tableUrls.csv;
    const rows = $("helper-rows"); rows.replaceChildren();
    const tasks = plan.tasks || [];
    $("helper-table").hidden = !tasks.length;
    $("table-empty").hidden = !!tasks.length;
    $("helper-count").textContent = tasks.length ? `· ${tasks.length} ${tasks.length === 1 ? "task" : "tasks"}` : "";
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
    $("helper-more").open = true;
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
    $("card-dialog-title").textContent = "Your fridge card"; frame.title = "Your fridge card"; $("card-print-label").textContent = "Print card";
    frame.src = cardUrl + "?t=" + Date.now();
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
    { text: "Her next step card has Call now. When the ride is booked, she taps Done.", target: () => $("next-done"), done: () => story.done },
    { text: "Ruth says yes to the rest of her plan.", target: () => yesOn(), done: () => !planEl.querySelector(".tag-answer") },
    { text: "Every part is covered. A calm picture shows that her plan is ready.", next: true, target: () => $("ready-ill") },
    { text: "Ruth opens her fridge card. It lists her allergies and where her papers are.", target: () => $("card-link"), done: () => story.card },
    { text: "She makes a readiness summary. She decides who sees it.", target: () => $("summary-print"), done: () => story.summary },
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
