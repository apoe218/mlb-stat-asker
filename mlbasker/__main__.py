"""CLI: serve the web app, build/update the index, or ask from the terminal."""
from __future__ import annotations

import argparse
import logging

from .client import MlbClient
from .cutoff import cutoff_date
from .index import db as dbmod
from .index.sync import FIRST_SEASON, PROFILES, coverage, pbp_depth, sync_recent, sync_seasons
from .timeparse import season_of


def main() -> None:
    ap = argparse.ArgumentParser(prog="mlbasker")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web chat UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--no-feed", action="store_true", help="don't sync the index in the background")
    s.add_argument("--open", action="store_true", help="open the page in your browser once it's up")
    s.add_argument("--history", metavar="DEPTH", help="how far back to index play-by-play: recent (default, "
                   "last 10 seasons), modern (1990+), full (1950+), or a year")

    y = sub.add_parser("sync", help="build / resume the history index (final scores for every year, "
                                    "play-by-play back to --history)")
    y.add_argument("--history", metavar="DEPTH", default="full",
                   help="recent | modern | full (default for this command) | a year")
    y.add_argument("--workers", type=int, default=12)

    sub.add_parser("update", help="index the last week of games (through yesterday)")
    sub.add_parser("status", help="show index coverage and the cutoff date")

    a = sub.add_parser("ask", help="ask one question from the terminal")
    a.add_argument("question", nargs="+")

    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.cmd == "serve":
        import os
        import threading
        import uvicorn
        if args.no_feed:
            os.environ["MLBASKER_NO_FEED"] = "1"
        if args.history:
            os.environ["MLBASKER_HISTORY"] = args.history
        from .app import create_app
        if args.open:
            import webbrowser
            shown = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
            threading.Timer(1.5, webbrowser.open, [f"http://{shown}:{args.port}/"]).start()
        depth = pbp_depth()
        print(f"Stat Asker on http://{args.host}:{args.port}/  ·  play-by-play history from {depth} "
              f"({PROFILES.get(os.environ.get('MLBASKER_HISTORY', 'recent'), 'custom')}); "
              f"change with --history modern|full|YEAR")
        uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
    elif args.cmd == "sync":
        con, client = dbmod.connect(), MlbClient()
        sync_seasons(con, client, FIRST_SEASON, season_of(cutoff_date()), workers=args.workers,
                     pbp_from=pbp_depth(args.history))
        print(coverage(con))
    elif args.cmd == "update":
        con, client = dbmod.connect(), MlbClient()
        print(sync_recent(con, client))
    elif args.cmd == "status":
        print("cutoff:", cutoff_date())
        print("index :", coverage(dbmod.connect()))
        print("history depth (MLBASKER_HISTORY):", pbp_depth(), "on")
    elif args.cmd == "ask":
        from .engine import Asker
        ans = Asker(MlbClient(), dbmod.connect()).ask(" ".join(args.question))
        print(ans.text)
        for r in ans.rows:
            print("  ", " | ".join(str(c) for c in r))
        if ans.footnote:
            print(f"\n({ans.footnote})")


if __name__ == "__main__":
    main()
