"""CLI: python3 -m radar {watch|check|status} [--config config.json]"""
from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .app import (cmd_check, cmd_export_state, cmd_import_state, cmd_report,
                  cmd_status, cmd_watch)
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="radar",
        description="Listing Radar — wczesne wykrywanie nowych listowań "
                    "kryptowalut na CoinGecko i SafeTrade.",
    )
    parser.add_argument("--version", action="version", version=f"radar {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", metavar="PLIK", default=None,
                        help="ścieżka do config.json (domyślnie ./config.json)")
    common.add_argument("-v", "--verbose", action="store_true",
                        help="logowanie debug ( szczegóły HTTP )")

    sub.add_parser("watch", parents=[common],
                   help="ciągły monitoring (domyślny tryb pracy)")
    sub.add_parser("check", parents=[common],
                   help="jedno sprawdzenie i wyjście")
    sub.add_parser("status", parents=[common],
                   help="statystyki bazy i ostatnie zdarzenia")
    p_report = sub.add_parser("report", parents=[common],
                              help="wyślij raport z ostatnich 24h teraz")
    p_report.add_argument("--hours", type=int, default=24,
                          help="zakres raportu w godzinach (domyślnie 24)")

    p_exp = sub.add_parser("export-state", parents=[common],
                           help="[GitHub] zapisz stan do pliku JSON")
    p_exp.add_argument("path", nargs="?", default="state.json",
                       help="plik wyjściowy (domyślnie state.json)")
    p_imp = sub.add_parser("import-state", parents=[common],
                           help="[GitHub] wczytaj stan z pliku JSON")
    p_imp.add_argument("path", nargs="?", default="state.json",
                       help="plik wejściowy (domyślnie state.json)")

    args = parser.parse_args()
    cmd = args.cmd or "watch"
    if args.cmd is None and len(sys.argv) > 1 and sys.argv[1] not in ("watch", "check", "status", "report"):
        # np. `python3 -m radar --config x.json` bez podkomendy
        args.config = None
        args.hours = 24
        for i, a in enumerate(sys.argv):
            if a == "--config" and i + 1 < len(sys.argv):
                args.config = sys.argv[i + 1]
            if a == "--hours" and i + 1 < len(sys.argv):
                try:
                    args.hours = int(sys.argv[i + 1])
                except ValueError:
                    pass

    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(getattr(args, "config", None))
    if cmd == "watch":
        cmd_watch(cfg)
    elif cmd == "check":
        cmd_check(cfg)
    elif cmd == "report":
        cmd_report(cfg, hours=getattr(args, "hours", 24))
    elif cmd == "status":
        cmd_status(cfg)
    elif cmd == "export-state":
        cmd_export_state(cfg, getattr(args, "path", "state.json"))
    elif cmd == "import-state":
        cmd_import_state(cfg, getattr(args, "path", "state.json"))


if __name__ == "__main__":
    main()
