"""
cli.py -- entry point for the AWS Config Visualization pipeline.

Commands:
  crawl   -- download latest ConfigHistory files from S3 to local cache
  parse   -- parse + deduplicate cached files, print resource type summary
  model   -- normalize into resources/edges, save cache/model.json
  stitch  -- infer cross-account edges, save cache/cross_account_edges.json
  viz     -- render the graph to a self-contained HTML file in output/
  all     -- run crawl -> model -> stitch -> viz in sequence
"""

import argparse
import sys


def cmd_crawl(_args):
    from crawler import crawl
    crawl()


def cmd_parse(_args):
    from parser import parse_cache
    items = parse_cache()
    print(f"Loaded {len(items)} live resources")
    by_type: dict[str, int] = {}
    for item in items:
        t = item.get("resourceType", "Unknown")
        by_type[t] = by_type.get(t, 0) + 1
    for t, count in sorted(by_type.items()):
        print(f"  {count:>5}  {t}")


def cmd_model(_args):
    from parser import parse_cache
    from model import normalize, save_model
    items = parse_cache()
    resources, edges = normalize(items)
    layer_counts: dict[str, int] = {}
    for r in resources:
        layer_counts[r["layer"]] = layer_counts.get(r["layer"], 0) + 1
    for layer, count in sorted(layer_counts.items()):
        print(f"  {count:>5}  {layer}")
    save_model(resources, edges)


def cmd_stitch(_args):
    from parser import parse_cache
    from model import load_model
    from stitch import infer_cross_account_edges, save_cross_account_edges

    items = parse_cache()
    resources, intra_edges = load_model()
    cross_edges = infer_cross_account_edges(items)

    by_reason: dict[str, int] = {}
    for e in cross_edges:
        r = e["stitchReason"]
        by_reason[r] = by_reason.get(r, 0) + 1

    print(f"Cross-account edges: {len(cross_edges)}")
    for reason, count in sorted(by_reason.items()):
        print(f"  {count:>4}  {reason}")
    print(f"Intra-account edges: {len(intra_edges)}")
    print(f"Total edges:         {len(intra_edges) + len(cross_edges)}")

    save_cross_account_edges(cross_edges)


def cmd_viz(args):
    from viz import render
    layers = [x.strip() for x in args.layers.split(",")] if args.layers else None
    render(accounts=args.account, layers=layers)


def cmd_all(args):
    cmd_crawl(args)
    cmd_model(args)
    cmd_stitch(args)
    cmd_viz(args)


def main():
    parser = argparse.ArgumentParser(
        description="AWS Config Visualization pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Shared flags for commands that render HTML (viz, all).
    viz_flags = argparse.ArgumentParser(add_help=False)
    viz_flags.add_argument("--account", action="append",
                           help="Render only this account id (repeatable). Default: all.")
    viz_flags.add_argument("--layers",
                           help="Comma-separated layers visible on load "
                                "(networking,storage,iam,compute,other).")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("crawl",  help="Download latest ConfigHistory files from S3")
    sub.add_parser("parse",  help="Parse cache and print resource type summary")
    sub.add_parser("model",  help="Normalize resources/edges into cache/model.json")
    sub.add_parser("stitch", help="Infer cross-account edges")
    sub.add_parser("viz",    parents=[viz_flags], help="Render graph to self-contained HTML")
    sub.add_parser("all",    parents=[viz_flags], help="Run crawl -> model -> stitch -> viz")

    args = parser.parse_args()
    dispatch = {
        "crawl":  cmd_crawl,
        "parse":  cmd_parse,
        "model":  cmd_model,
        "stitch": cmd_stitch,
        "viz":    cmd_viz,
        "all":    cmd_all,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
