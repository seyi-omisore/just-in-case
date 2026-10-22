"""Start the Just in Case website on your computer.

    python serve.py           real agent (uses your OpenRouter key from .env)
    python serve.py --demo    scripted demo: no AI, no internet, no cost

Then open http://localhost:8000 in your browser. Press Control+C to stop.
"""

import argparse
import os
import threading
import webbrowser

import uvicorn

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true", help="scripted demo, no AI")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()

    if args.demo:
        os.environ["JIC_DEMO"] = "1"
    else:
        from just_in_case.config import runtime_env
        runtime_env()  # stops early with a clear message if the key is missing

    url = f"http://localhost:{args.port}"
    print(f"\nJust in Case is running at {url}  ({'demo mode' if args.demo else 'live agent'})")
    print("Press Control+C to stop.\n")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("web.server:app", host="127.0.0.1", port=args.port, log_level="warning")
