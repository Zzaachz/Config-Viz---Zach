# AWS Config Visualization - Plan

Status (2026-08-25): Phases 1-5 complete. Tool is built, documented, and handed
off. Only the two deferred items below remain, and neither blocks use.

## Phase 1: Project scaffold
- [x] Project dir, venv, requirements.txt (boto3, pyvis)
- [x] Package layout: crawler.py, parser.py, model.py, stitch.py, viz.py, cli.py, cache/, output/

## Phase 2: Ingestion and parsing
- [x] S3 crawler downloads latest ConfigHistory per (account, resourceType) to cache/
- [x] Parser: gunzip, dedup latest per resourceId, drop ResourceDeleted
- [x] Normalize into resources + edges tables, assign layers, persist cache/model.json

## Phase 3: Within-account graph + cross-account stitching
- [x] Intra-account edges from relationships[]
- [x] Cross-account stitch (TGW, VPC peering, VPC endpoints, IAM trust, RAM)
- [x] Persist cache/cross_account_edges.json

## Phase 4: Visualization
- [x] Fixed model.py edge bug: labels read from "name" (Config field), added dstType
- [x] viz.py renders self-contained HTML via pyvis (vis.js inlined, CDN Bootstrap stripped)
- [x] Account color grouping + human-name legend; node tooltips (type/id/account/layer/config/tags)
- [x] Typed edges: intra (grey, labeled) vs cross-account (orange dashed)
- [x] Faded placeholder nodes for dangling edge targets (referenced but not recorded)
- [x] Layer toggles (other off by default), account toggles, placeholder toggle, search
- [x] Two zoom levels: org overview (account clusters + cross links) <-> per-account detail
- [x] --account single-account first cut; default renders all 5
- [x] Wired viz into cli.py, chained into "all"

Current data: 598 resources across 5 accounts, 433 intra + 7 cross-account edges.
Output: output/aws-config-all.html (700 nodes incl. placeholders, 440 edges).

## Phase 5: Packaging and handoff (complete)
- [x] cli.py commands: crawl / parse / model / stitch / viz / all
- [x] Finalize README (fill in run steps for viz, how to read the diagram)
- [x] Refresh cadence: manual. No scheduler; rerun `python cli.py all` for fresh
      data. Documented in Instructions/How To Launch.md.

## Phase 6: Interaction upgrades (viz.py only) -- complete 2026-08-27
All front-end; no pipeline/data changes. Verified by rerender (700 nodes).
- [x] Embed per-node config (type/id/account/region/configFields/tags) as a JS
      attribute so export works offline
- [x] Right-click context menu on a node:
      - [x] Export config -> downloads <id>.json (node config + connected edges)
      - [x] Filter to connected -> hide all but node + 1-hop neighbors
      - [x] Clear filter (also on empty-canvas right-click)
- [x] Sub-filters in left panel: each layer expands ([+]) to per-resource-type
      toggles (layer button is the master switch; a type toggles independently).
      Node visible only if its type enabled AND its account enabled.

Note: IAM Identity Center (Users/Groups/Permission Sets) was requested but
SKIPPED per user 2026-08-27. Not sourceable from Config S3 (Config does not
record Identity Store); would require a live sso-admin/identitystore API pull.

## Deferred / open (not blocking)
- [ ] Human name for account 162521700530 (currently "Unknown" placeholder,
      awaiting user input)
- [ ] Revisit tooltip detail depth after review
