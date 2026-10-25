"""Start the Just in Case website on your computer.

    python serve.py           with an OpenRouter key in .env: the live agent
                              without a key: the guided planner (no AI), so it can be hosted with no key
    python serve.py --demo    no AI even if a key is set: the guided planner and Ruth's example
    python serve.py --host 0.0.0.0   reachable from other computers (for hosting); the default is this one only

Then open http://localhost:8000 in your browser. Press Control+C to stop.
"""

import argparse
import os
import threading
import webbrowser

import uvicorn

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true", help="no AI: the guided planner and Ruth's example")
    p.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to host it for other computers")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()

    from just_in_case.config import has_ai_key, runtime_env
    if args.demo:
        os.environ["JIC_DEMO"] = "1"
        mode = "guided planner, no AI"
    elif has_ai_key():
        runtime_env()  # stops early with a clear message if the key is set up wrongly
        mode = "live agent"
    else:
        mode = "guided planner, no AI (no OpenRouter key found)"

    url = f"http://localhost:{args.port}"
    print(f"\nJust in Case is running at {url}  ({mode})")
    print("Press Control+C to stop.\n")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("web.server:app", host=args.host, port=args.port, log_level="warning")
