"""CLI: python -m aegis serve | ingest <dataset_root> [--db data/aegis.db] | ask "<question>" [--db ...] [--trace] | eval <dataset_root>"""
import argparse, json, subprocess, sys
from pathlib import Path

def main():
    ap = argparse.ArgumentParser(prog="aegis"); sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ingest"); a.add_argument("root"); a.add_argument("--db", default="data/aegis.db"); a.add_argument("--sidecars", default="data/transcriptions")
    b = sub.add_parser("ask"); b.add_argument("question"); b.add_argument("--db", default="data/aegis.db"); b.add_argument("--trace", action="store_true"); b.add_argument("--json", action="store_true")
    c = sub.add_parser("eval"); c.add_argument("root")
    d = sub.add_parser("serve"); d.add_argument("--host", default="127.0.0.1"); d.add_argument("--port", type=int, default=8000); d.add_argument("--db", default="data/aegis.db")
    args = ap.parse_args()
    if args.cmd == "ingest":
        from .ingest.pipeline import ingest
        kb, rep = ingest(args.root, args.db, args.sidecars)
        print(json.dumps({k: v for k, v in rep.items() if k not in ("raster", "geometry")}, indent=1, default=str)); print(f"saved {args.db}")
    elif args.cmd == "ask":
        from .store.db import KnowledgeBase
        from .query.pipeline import Engine
        env = Engine(KnowledgeBase.load(args.db)).ask(args.question)
        if args.json: print(env.model_dump_json(indent=1)); return
        print(f"[{env.status}] contract_verified={env.contract_verified} quality={env.quality}")
        for s in env.sentences: print(f"  ({s.kind}) {s.text}")
        for e in env.evidence: print(f"    • {e['role']:13} {e['document']}" + (f" p{e['page']}" if e['page'] else "") + f": “{e['quote'][:90]}”")
        if args.trace: print(json.dumps(env.trace, indent=1, default=str))
    elif args.cmd == "serve":
        import os, uvicorn
        os.environ.setdefault("AEGIS_DB", args.db); uvicorn.run("aegis.api.app:create_app", factory=True, host=args.host, port=args.port, proxy_headers=True, server_header=False)
    elif args.cmd == "eval":
        sys.exit(subprocess.call([sys.executable, str(Path(__file__).resolve().parents[2] / "eval/run_eval.py"), args.root]))

if __name__ == "__main__": main()
