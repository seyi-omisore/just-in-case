"""Make the story video of Ruth's example, in her five moments. Ruth is made up: no real people appear.

Run, after the app changes:   python scripts/make_tour_video.py
Needs (once):                 pip install -r requirements-video.txt
Records with Google Chrome if you have it, or Playwright's Chromium.
Narration uses the Mac's built-in voices (say): Ava (Premium) if it is installed, then Zoe (Premium),
then the best other English voice. Add --no-voice to leave it out.

It starts the app in demo mode, plays Ruth's story at phone size inside a phone frame,
keeps only her five moments (60 to 90 seconds, about 15 each), and writes into web/static/img/:
    tour.mp4          the video, under 10 MB
    tour.vtt          the captions, with their timings
    tour-poster.jpg   the opening screen, shown before the video plays
    tour-captioned.mp4  the same video with the captions drawn in, for slides
    tour-narration.wav  the narration on its own, to listen to
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "static" / "img"
W, H = 480, 1040            # the recording page: the phone, with a caption area underneath
PHONE_W, PHONE_H = 390, 844
HOLD = 2.6                  # seconds for a short look
MAX_MB = 10
PART = 6.0                  # each caption stays on screen at least this long (two captions make a moment, about 15 seconds)
LAST = 4.0                  # the closing line
MIN_S, MAX_S = 60, 90       # the story video's length. If it runs long, cut a moment: never speed up the voice.
# Calm and warm, like the app's Read aloud: the most natural voice first.
VOICES = ("Ava (Premium)", "Zoe (Premium)", "(Premium)", "(Enhanced)", "Moira", "Tessa", "Samantha", "Daniel")
SPEED = 0.9                 # about 90% of the voice's usual pace, set with the voice's own rate (never stretched after)
PAUSE = 0.4                 # seconds of quiet between sentences
AUDIO_RATE = 48000          # Hz, for the narration file and the video
# Ruth's five moments, in her own words: these are both the narration and the captions.
MOMENTS = ("The call", "Telling Haven", "Haven finds help", "Friends reply", "The night before")
CAPTIONS = ["My doctor says I need a procedure next month, and someone has to drive me home.",       # 1 The call
            "I live alone. Who do I ask?",
            "So I told Haven what was coming up.",                                                   # 2 Telling Haven
            "It asked me one thing at a time. I just answered.",
            "It found a free escort service, a key lockbox, and my neighbor Doris for Fifi.",        # 3 Haven finds help
            "I didn't have to ask anyone for a big favor.",
            "Doris signed up for mornings, and Haven told me in What's new.",                        # 4 Friends reply
            "Then I printed my fridge card.",
            "The night before, Haven showed me who is doing what.",                                  # 5 The night before
            "Home safe. Haven wrote a thank-you note to Doris.",
            "That's Ruth's story. Now make your own plan."]
# Burned-in captions for tour-captioned.mp4: dark bold words in the caption area under the phone.
# ffmpeg sizes captions on a 288-pixel-tall canvas, so pixel sizes are scaled to it.
_PX = 288 / H
CAPTION_STYLE = (f"FontName=Verdana,FontSize={22 * _PX:.1f},Bold=1,PrimaryColour=&H0033291F,BorderStyle=1,Outline=0,"
                 f"Shadow=0,Alignment=2,MarginV={round(44 * _PX)}")

FRAME = """<!doctype html><html><head><meta charset="utf-8"><style>
  html, body { margin: 0; height: 100%; overflow: hidden; background: #EDE8E1; font-family: "Atkinson Hyperlegible", Verdana, sans-serif; }
  .stage { position: relative; width: __W__px; height: __H__px; }
  .phone { position: absolute; left: 33px; top: 22px; width: __PW__px; height: __PH__px; border: 12px solid #1F2933;
           border-radius: 46px; overflow: hidden; background: #F7F5F2; box-shadow: 0 18px 40px rgba(31, 41, 51, .18); }
  iframe { width: __PW__px; height: __PH__px; border: 0; display: block; }
  .caption-area { position: absolute; left: 0; right: 0; top: 902px; height: 138px; display: grid; justify-items: center;
                  align-content: start; gap: 8px; padding-top: 12px; color: #52606D; font-size: 15px; }
  .dots { display: flex; gap: 8px; }
  .dots i { width: 9px; height: 9px; border-radius: 50%; background: #CFC8BF; }
  .dots i.on { background: #1F6F78; }
  .tap { position: absolute; width: 56px; height: 56px; margin: -28px 0 0 -28px; border-radius: 50%; pointer-events: none;
         border: 3px solid rgba(31, 111, 120, .85); background: rgba(31, 111, 120, .18); opacity: 0;
         transition: opacity .25s ease, transform .6s ease; transform: scale(.6); }
  .tap.show { opacity: 1; transform: scale(1); }
</style></head><body><div class="stage">
  <div class="phone"><iframe id="app" src="__APP__" title="Just in Case, Ruth's example"></iframe></div>
  <div class="caption-area"><div class="dots" id="dots"></div><div>Ruth is an example. A made-up story.</div></div>
  <div class="tap" id="tap"></div>
  <div id="mark" style="position:fixed; right:0; bottom:0; width:12px; height:12px; background:#EDE8E1"></div>
</div><script>
  // The app's own scrolling can move this outer page too (scrollIntoView reaches every frame). Keep it still.
  addEventListener("scroll", () => { if (scrollX || scrollY) scrollTo(0, 0); });
  // A small colored square marks each caption's start in the recording, so the edit lines up with what was on screen.
  function mark(i) { document.getElementById("mark").style.background = i < 0 ? "#EDE8E1" : (i % 2 ? "#0000FF" : "#FF0000"); }
  function showTap(x, y) { const t = document.getElementById("tap"); t.style.left = x + "px"; t.style.top = y + "px";
    t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 900); }
  function dots(n, on) { document.getElementById("dots").innerHTML = Array.from({ length: n }, (_, i) => `<i class="${i === on ? "on" : ""}"></i>`).join(""); }
</script></body></html>"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        found = shutil.which("ffmpeg")
        if not found:
            sys.exit("ffmpeg is missing. Run: pip install -r requirements-video.txt")
        return found


def pick_voice(wanted: str | None = None) -> str | None:
    if not shutil.which("say"):
        return None
    names = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout.splitlines()
    english = [line.split("  ")[0].strip() for line in names if " en_" in line]
    for want in ((wanted,) if wanted else VOICES):
        for name in english:
            if want in name:
                return name
    return None


def vtt_time(t: float) -> str:
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{t % 60:06.3f}"


class Tour:
    """Plays Ruth's guided story and remembers when each main moment starts and ends."""

    def __init__(self, page, steps: int, speech: dict[str, float]):
        self.page, self.steps, self.t0 = page, steps, time.monotonic()
        self.speech = speech        # how long each caption takes to say, so no moment ends mid-sentence
        self.segments: list[tuple[float, float, str]] = []
        self.seen: list[str] = []   # the story steps already on camera, in order
        self.app = page.frame_locator("#app")

    def now(self) -> float:
        return time.monotonic() - self.t0

    def moment(self, caption: str, dot: int):
        """One caption on camera. It stays until its narration ends, and at least PART seconds (LAST for the end)."""
        tour = self

        class Moment:
            def __enter__(self):
                tour.page.evaluate("([n, on]) => dots(n, on)", [tour.steps, dot])
                tour.page.evaluate("i => mark(i)", len(tour.segments))
                self.start = tour.now()
                tour.mark_seen()

            def __exit__(self, *exc):
                least = LAST if caption == CAPTIONS[-1] else PART
                left = max(tour.speech.get(caption, 0) + 0.9, least) - (tour.now() - self.start)
                if left > 0:
                    tour.hold(left)
                tour.segments.append((self.start, tour.now(), caption))
                tour.page.evaluate("mark(-1)")
                tour.mark_seen()
        return Moment()

    def mark_seen(self) -> str | None:
        """Note which story step is showing. Returns it if it is new, else None."""
        try:
            label = self.story_step()
        except Exception:
            return None
        if " of " not in label or label in self.seen:
            return None
        self.seen.append(label)
        return label

    def hold(self, seconds: float = HOLD):
        self.page.wait_for_timeout(int(seconds * 1000))

    REVEAL = "e => { for (let d = e; d; d = d.parentElement) if (d.tagName === 'DETAILS' && !d.open) d.open = true; }"

    def reveal(self, selector: str, timeout: float = 30):
        """Open any closed drop-down section around the element, so it can be seen and tapped."""
        target = self.app.locator(selector).first
        target.wait_for(state="attached", timeout=timeout * 1000)
        target.evaluate(self.REVEAL)

    def wait(self, selector: str, timeout: float = 30):
        self.reveal(selector, timeout)
        self.app.locator(selector).first.wait_for(state="visible", timeout=timeout * 1000)

    def calm_scroll(self, selector: str):
        self.reveal(selector)
        self.app.locator(selector).first.evaluate("e => e.scrollIntoView({ behavior: 'smooth', block: 'center' })")
        self.page.wait_for_timeout(900)

    def tap(self, selector: str):
        """A soft circle where the finger goes, then the tap."""
        target = self.app.locator(selector).first
        if target.count():
            target.evaluate(self.REVEAL)
        if not target.is_visible():  # Ruth's example may have tapped her first sentence by itself already
            return
        target.scroll_into_view_if_needed()
        box = target.bounding_box()
        self.page.evaluate("([x, y]) => showTap(x, y)", [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
        self.page.wait_for_timeout(650)
        if target.is_visible():      # Ruth's example may have tapped her first sentence by itself already
            target.click()

    def story_step(self) -> str:
        return self.app.locator("#story-count").inner_text().lower()

    def close_tags(self):
        """Fold the plan items back up, so their details (and service numbers) stay off screen."""
        for _ in range(12):   # the plan redraws after each tap, so look again each time
            open_heads = self.app.locator('.tag-head[aria-expanded="true"]')
            if not open_heads.count():
                break
            open_heads.first.click()
            self.page.wait_for_timeout(150)


def record(base: str, work: Path, speech: dict[str, float]) -> tuple[Path, list[tuple[float, float, str]]]:
    from playwright.sync_api import sync_playwright
    frame = work / "frame.html"
    frame.write_text(FRAME.replace("__W__", str(W)).replace("__H__", str(H)).replace("__PW__", str(PHONE_W))
                     .replace("__PH__", str(PHONE_H)).replace("__APP__", f"{base}/?example=1#plan"))
    captions = CAPTIONS
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(work),
                                      record_video_size={"width": W, "height": H})
        # Light theme, no first-time tour: set inside the app before it draws.
        context.add_init_script(f"""if (location.origin === {json.dumps(base)}) {{
            localStorage.setItem("jic-theme-v2", "light"); localStorage.setItem("jic-tour-done", "1"); }}""")
        page = context.new_page()
        tour = Tour(page, len(MOMENTS), speech)
        page.goto(frame.as_uri())
        tour.wait("#story-guide")
        app = tour.app

        def story_next():
            """The story's own Next, off camera between captions."""
            app.locator("#story-next").wait_for(state="visible", timeout=30000)
            end = time.monotonic() + 60
            while not app.locator("#story-next").is_enabled():
                if time.monotonic() > end:
                    raise TimeoutError("the story's Next never opened at: " + app.locator("#story-count").inner_text()
                                       + " / " + app.locator("#story-text").inner_text())
                page.wait_for_timeout(200)
            app.locator("#story-next").click()
            page.wait_for_timeout(500)

        def tap_ring(wait: float = 20):
            """Tap the button the story rings (the soft circle shows the tap)."""
            app.locator(".story-next").first.wait_for(state="visible", timeout=wait * 1000)
            tour.tap(".story-next")
            page.wait_for_timeout(500)

        def taps_until_next(timeout: float = 90):
            """Off camera: tap each ringed button until the story's Next opens. Closes the card if it opens."""
            end = time.monotonic() + timeout
            page.wait_for_timeout(700)                   # let the story catch up first
            while time.monotonic() < end:
                if app.locator("#card-dialog[open]").count():
                    app.locator("#card-close").click()
                if app.locator("#story-next").is_enabled():
                    return
                ring = app.locator(".story-next")
                if ring.count() and ring.first.is_visible():
                    ring.first.click()
                    page.wait_for_timeout(600)
                page.wait_for_timeout(250)
            raise TimeoutError("the story never moved on")

        # 1 The call: her doctor's words, over the hello screen.
        with tour.moment(captions[0], 0):
            tour.hold(1.5)
        with tour.moment(captions[1], 0):
            tour.hold(1.0)
        story_next()
        # 2 Telling Haven: she sends her first words and answers one question.
        with tour.moment(captions[2], 1):
            tour.hold(3.0)
            tap_ring()                                   # Send: "I have a procedure coming up..."
            app.locator(".slip-actions .btn-primary").first.wait_for(timeout=20000)
        with tour.moment(captions[3], 1):
            tap_ring()                                   # yes to the list
            app.get_by_text("comfortable asking").first.wait_for(timeout=20000)
            page.wait_for_timeout(1200)
            tap_ring()                                   # her answer: "Yes, I could ask Doris, my neighbor."
        taps_until_next()                                # the rest of her answers, off camera
        story_next()
        # 3 Haven finds help: she says yes to each part.
        with tour.moment(captions[4], 2):
            tour.hold(0.8)
            tap_ring()
        with tour.moment(captions[5], 2):
            while app.locator(".tag-answer").count():
                tap_ring()
        tour.close_tags()
        story_next()
        page.wait_for_timeout(600)
        tap_ring()                                       # off camera: Share with family...
        app.locator("#consent-dialog[open]").wait_for(timeout=20000)
        app.locator("#consent-yes").click()              # ...and her yes in the consent box
        app.locator("#whats-new .news-helping").first.wait_for(timeout=30000)   # then Doris answers on the shared list
        page.wait_for_timeout(800)
        # 4 Friends reply: What's new, then her fridge card.
        with tour.moment(captions[6], 3):
            tour.hold(1.0)
        story_next()
        with tour.moment(captions[7], 3):
            tap_ring()                                   # See your fridge card
            tour.wait("#card-dialog[open]")
            tour.hold(1.6)
            card = next(f for f in page.frames if "/card/" in f.url)
            card.wait_for_load_state()
            card.evaluate("window.scrollTo({ top: 380, behavior: 'smooth' })")
        app.locator("#card-close").click()
        page.wait_for_timeout(400)
        story_next()
        # 5 The night before: the calm summary, then home safe and a thank-you note.
        with tour.moment(captions[8], 4):
            tour.hold(1.0)
        story_next()
        with tour.moment(captions[9], 4):
            tour.hold(1.0)
        story_next()
        tour.wait("#story-own")
        with tour.moment(captions[10], 4):               # "That's Ruth's story. Now make your own plan."
            tour.hold(1.0)
        moments = sorted({int(re.match(r"(\d+) of", label).group(1)) for label in tour.seen if re.match(r"\d+ of", label)})
        if moments != list(range(1, len(MOMENTS) + 1)):
            sys.exit(f"The video would skip Ruth's moments {sorted(set(range(1, 6)) - set(moments))}. Not saved.")
        total = sum(b - a for a, b, _ in tour.segments)
        if not MIN_S <= total <= MAX_S:
            sys.exit(f"The story would run {total:.0f} seconds (it should be {MIN_S} to {MAX_S}). Cut or shorten a moment. Not saved.")
        print(f"All five of Ruth's moments are shown, in order, in {total:.0f} seconds.")
        tour.hold(6)   # the browser's recording can run behind: give it time to catch up before it closes
        video = Path(page.video.path())
        context.close()
        browser.close()
    return video, tour.segments


def seconds(ffmpeg: str, clip: Path) -> float:
    info = subprocess.run([ffmpeg, "-i", str(clip)], capture_output=True, text=True).stderr
    h, m, sec = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info).groups()
    return int(h) * 3600 + int(m) * 60 + float(sec)


def slower_rate(voice: str, ffmpeg: str, work: Path) -> int:
    """The voice's own rate setting that makes it speak at about SPEED of its usual pace.
    Voices respond differently (Ava Premium barely moves at 158), so this is measured, not guessed."""
    test = "Answer one question at a time. Your plan builds as you go."
    def length(rate: int | None) -> float:
        clip = work / f"rate-{rate}.aiff"
        subprocess.run(["say", "-v", voice, *(["-r", str(rate)] if rate else []), "-o", str(clip), test], check=True)
        return seconds(ffmpeg, clip)
    usual, want = length(None), length(None) / SPEED
    return min(range(90, 181, 5), key=lambda r: abs(length(r) - want))


def speak(work: Path, voice: str, ffmpeg: str) -> dict[str, list[tuple[Path, float, float]]]:
    """Each sentence as its own clip, so there is a short, calm pause between sentences.
    Returns, for each caption: (clip, start within the caption, length)."""
    rate = slower_rate(voice, ffmpeg, work)
    print(f"Voice: {voice}, rate setting {rate} (about {SPEED:.0%} of its usual pace)")
    out = {}
    for i, text in enumerate(CAPTIONS):
        at, clips = 0.4, []
        for j, sentence in enumerate(re.split(r"(?<=[.!?])\s+", text)):
            clip = work / f"say-{i}-{j}.aiff"
            subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(clip), sentence], check=True)
            d = seconds(ffmpeg, clip)
            clips.append((clip, at, d))
            at += d + PAUSE
        out[text] = clips
    return out


def narrate(clips, total: float, ffmpeg: str, out: Path) -> None:
    """The narration as one clean track: each sentence placed in time, one fixed volume, and a gentle fade
    in at the start and out at the end. No other effects, and one resample to AUDIO_RATE."""
    ends = sorted((at, at + d) for _, at, d in clips)
    for (a0, a1), (b0, _) in zip(ends, ends[1:]):
        if b0 < a1 + 0.2:
            sys.exit(f"Two narration lines overlap near {b0:.1f} seconds.")
    inputs = sum((["-i", str(c)] for c, _, _ in clips), [])
    place = ";".join(f"[{k}:a]adelay={int(at * 1000)}[a{k}]" for k, (_, at, _) in enumerate(clips))
    mix = place + ";" + "".join(f"[a{k}]" for k in range(len(clips))) + f"amix=inputs={len(clips)}:normalize=0[m]"
    raw = out.with_suffix(".raw.wav")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", *inputs, "-filter_complex", mix, "-map", "[m]", "-t", f"{total:.3f}",
                    "-c:a", "pcm_s16le", str(raw)], check=True)
    peak = float(re.search(r"max_volume: (-?[\d.]+) dB", subprocess.run(
        [ffmpeg, "-i", str(raw), "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr).group(1))
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(raw), "-af",
                    f"volume={-3 - peak:.2f}dB,afade=t=in:st=0:d=0.6,afade=t=out:st={max(total - 0.9, 0):.3f}:d=0.9,"
                    f"aresample={AUDIO_RATE}", "-c:a", "pcm_s16le", str(out)], check=True)
    raw.unlink()


MARK = 12   # pixels: the marker square in the bottom-right corner of the recording


def align(video: Path, segments, ffmpeg: str):
    """Find where each caption really starts in the recording, from its marker square. The browser's recording can
    run a few seconds behind the script's clock, so times from the clock alone could show the wrong screen."""
    fps = 50
    raw = subprocess.run([ffmpeg, "-loglevel", "error", "-i", str(video), "-vf",
                          f"fps={fps},crop=6:6:iw-9:ih-9,scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    pixels = [tuple(raw[k:k + 3]) for k in range(0, len(raw) - 2, 3)]
    # Each caption turns the square red or blue (taking turns), and it goes back to the page color in between.
    kind = ["red" if r > g + 80 and r > bl + 80 else "blue" if bl > r + 80 and bl > g + 80 else "off" for r, g, bl in pixels]
    starts = [k for k in range(1, len(kind)) if kind[k] != "off" and kind[k] != kind[k - 1]]
    if len(starts) < len(segments):
        sys.exit(f"Found {len(starts)} of {len(segments)} caption starts in the recording. Not saved.")
    return [(starts[i] / fps, starts[i] / fps + (b - a), text) for i, (a, b, text) in enumerate(segments)]


def make(segments, video: Path, narration: Path | None, ffmpeg: str, crf: int, out: Path) -> None:
    """Keep only the main moments, with a quick cross-dissolve between them (no blank flash), and add the narration track as it is."""
    parts, inputs = [], ["-i", str(video)]
    lead = 0.3          # each clip starts this much earlier and melts over the one before, so the total length stays the same
    leads = []
    for i, (a, b, _) in enumerate(segments):
        lead_i = 0.0 if i == 0 else min(lead, a)
        leads.append(lead_i)
        parts.append(f"[0:v]trim=start={a - lead_i:.3f}:end={b:.3f},setpts=PTS-STARTPTS,fps=30,"
                     f"drawbox=x=iw-{MARK}:y=ih-{MARK}:w={MARK}:h={MARK}:color=0xEDE8E1@1:t=fill,format=yuv420p[c{i}]")
    shown, last = 0.0, "c0"
    for i in range(1, len(segments)):
        shown += segments[i - 1][1] - segments[i - 1][0]
        out_label = "v" if i == len(segments) - 1 else f"x{i}"
        parts.append(f"[{last}][c{i}]xfade=transition=fade:duration={leads[i]:.3f}:offset={shown - leads[i]:.3f}[{out_label}]")
        last = out_label
    if len(segments) == 1:
        parts.append("[c0]null[v]")
    graph = ";".join(parts)
    maps = ["-map", "[v]"]
    total = sum(b - a for a, b, _ in segments)
    if narration:
        # On a Mac, Apple's AAC encoder at a steady 160 kbps, copied in untouched. Elsewhere, ffmpeg's own.
        m4a = narration.with_suffix(".m4a")
        if shutil.which("afconvert") and (m4a.exists() or subprocess.run(
                ["afconvert", "-f", "m4af", "-d", "aac", "-b", "160000", "-s", "0", str(narration), str(m4a)]).returncode == 0):
            inputs += ["-i", str(m4a)]
            maps += ["-map", "1:a", "-c:a", "copy"]
        else:
            inputs += ["-i", str(narration)]
            maps += ["-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_RATE)]
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, *maps, "-t", f"{total:.3f}",
                    "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p", "-r", "30",
                    "-movflags", "+faststart", str(out)], check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--voice", help='narrator, for example --voice "Shelley (English (US))" (see: say -v ?)')
    ap.add_argument("--no-voice", action="store_true", help="leave out the spoken narration")
    args = ap.parse_args()
    ffmpeg = ffmpeg_exe()
    voice = None if args.no_voice else pick_voice(args.voice)
    if args.voice and not args.no_voice and not voice:
        sys.exit(f"No English voice matches \"{args.voice}\". See the list with: say -v ?")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen([sys.executable, "serve.py", "--demo", "--port", str(port), "--no-browser"], cwd=ROOT,
                              env={**os.environ, "JIC_DEMO": "1"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/api/config", timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        else:
            sys.exit("The demo app did not start.")
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            spoken = speak(work, voice, ffmpeg) if voice else {}
            speech = {text: (c[-1][1] + c[-1][2]) for text, c in spoken.items()}
            print(f"Recording Ruth's example (narration: {voice or 'none'})...")
            video, segments = record(base, work, speech)
            segments = align(video, segments, ffmpeg)
            starts, t = [], 0.0
            for a, b, _ in segments:
                starts.append(t)
                t += b - a
            clips = [(clip, s + at, d) for s, (_, _, text) in zip(starts, segments) for clip, at, d in spoken.get(text, [])]
            OUT.mkdir(parents=True, exist_ok=True)
            wav = OUT / "tour-narration.wav" if clips else None
            if wav:
                narrate(clips, t, ffmpeg, wav)
                wav.with_suffix(".m4a").unlink(missing_ok=True)   # encoded fresh below, from this narration
            mp4 = OUT / "tour.mp4"
            for crf in (28, 32, 36):                 # smaller until it is under 10 MB
                make(segments, video, wav, ffmpeg, crf, mp4)
                if mp4.stat().st_size <= MAX_MB * 1024 * 1024:
                    break
            cues = [f"{i + 1}\n{vtt_time(s)} --> {vtt_time(s + (b - a) - 0.2)}\n{text}\n"
                    for i, (s, (a, b, text)) in enumerate((s, seg) for s, seg in zip(starts, segments) if seg[2])]
            (OUT / "tour.vtt").write_text("WEBVTT\n\n" + "\n".join(cues))
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", "1.2", "-i", str(mp4), "-frames:v", "1", "-q:v", "3",
                            str(OUT / "tour-poster.jpg")], check=True)
            # For slides: the same video with the captions drawn in. Run from the image folder, so the
            # caption file's name needs no escaping in the filter.
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", "tour.mp4", "-vf",
                            f"subtitles=tour.vtt:original_size={W}x{H}:force_style='{CAPTION_STYLE}'",
                            "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p", "-c:a", "copy",
                            "-movflags", "+faststart", "tour-captioned.mp4"], check=True, cwd=OUT)
        if wav:
            wav.with_suffix(".m4a").unlink(missing_ok=True)
        for f in ("tour.mp4", "tour-captioned.mp4"):  # exactly one sound track: the narration
            info = subprocess.run([ffmpeg, "-i", str(OUT / f)], capture_output=True, text=True).stderr
            assert info.count("Audio:") == (1 if wav else 0), f"{f} has {info.count('Audio:')} audio tracks"
        size = mp4.stat().st_size / 1024 / 1024
        print(f"Done: {t:.0f} seconds, {size:.1f} MB, narration: {voice or 'none'}.")
        print(f"Saved tour.mp4, tour-captioned.mp4, tour.vtt, tour-poster.jpg{', and tour-narration.wav' if wav else ''} "
              f"in {OUT.relative_to(ROOT)}/")
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    main()
