import argparse
import sys

from clinic import config


def main():
    ap = argparse.ArgumentParser(prog="python3 -m web", description="Four-pane demo UI (simulation, synthetic data).")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--selftest", action="store_true", help="play runs through the API and check every pane")
    args = ap.parse_args()
    cfg = config.load(args.config)
    if args.selftest:
        from .selftest import main as selftest
        return selftest(cfg)
    from .server import make_server
    srv = make_server(cfg, port=args.port, preview=True)
    host, port = srv.server_address[:2]
    print(f"Clinic wait-time demo (simulation, synthetic data): http://{host}:{port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
