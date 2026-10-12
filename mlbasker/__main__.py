"""CLI: serve the web app, build/update the index, or ask from the terminal."""
from __future__ import annotations

import argparse
import logging

from .client import MlbClient
from .cutoff import cutoff_date
from .index import db as dbmod
from .index.sync import FIRST_PBP_SEASON, coverage, sync_recent, sync_seasons
from .timeparse import season_of


def main() -> None:
    ap = argparse.ArgumentParser(prog="mlbasker")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web chat UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--no-feed", action="store_true", help="don't sync the index in the background")

    y = sub.add_parser("sync", help="build / resume the play-by-play index")
    y.add_argument("--from", dest="first", type=int, default=FIRST_PBP_SEASON)
    y.add_argument("--to", dest="last", type=int, default=None)
    y.add_argument("--workers", type=int, default=8)

    sub.add_parser("update", help="index the last week of games (through yesterday)")
    sub.add_parser("status", help="show index coverage and the cutoff date")

    a = sub.add_parser("ask", help="ask one question from the terminal")
    a.add_argument("question", nargs="+")

    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.cmd == "serve":
        import os
        import uvicorn
        if args.no_feed:
            os.environ["MLBASKER_NO_FEED"] = "1"
        from .app import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port)
    elif args.cmd == "sync":
        con, client = dbmod.connect(), MlbClient()
        sync_seasons(con, client, args.first, args.last or season_of(cutoff_date()), workers=args.workers)
        print(coverage(con))
    elif args.cmd == "update":
        con, client = dbmod.connect(), MlbClient()
        print(sync_recent(con, client))
    elif args.cmd == "status":
        print("cutoff:", cutoff_date())
        print("index :", coverage(dbmod.connect()))
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
