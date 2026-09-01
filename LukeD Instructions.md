# AWS Config Visualization

## Goal

Build a Python tool that reads AWS Config data from the logging account's S3 bucket, reconstructs the current infrastructure inventory across all org accounts, and renders an interactive network diagram (flourish.studio style) with toggleable layer views (networking, storage, IAM, compute). The deliverable is a self-contained HTML file others can open with no server.

## Confirmed facts

- Profile: `logging` (account 143009743682), read access verified.
- Bucket: `s3://log-storage-aws-config-143009743682/AWSLogs/`
- Accounts in scope (5): `124074140119` (ALS), `162521700530` (????), `387075078863` (CCCI), `886031930818` (Engineering dev), `954475336656` (shared-services)
- All accounts deliver to `us-east-2` only.
- Path layout: `AWSLogs/<account>/Config/us-east-2/<YYYY>/<M>/<D>/ConfigHistory/<account>_Config_us-east-2_ConfigHistory_<ResourceType>_<start>_<end>_1.json.gz`
- Only ConfigHistory is delivered (no ConfigSnapshot). Current state must be reconstructed by taking the latest history file per (account, resourceType) and the most recent configurationItems entry per resourceId, dropping items with configurationItemStatus == ResourceDeleted.
- File envelope: gzipped JSON, top-level `configurationItems[]`. Each item has `resourceType`, `resourceId`, `ARN`, `awsAccountId`, `awsRegion`, `tags`, and a `relationships[]` array of typed edges (e.g. "Is contained in Vpc", "Is attached to NetworkAcl").
- Config relationships are within-account only. Cross-account links must be inferred by matching shared IDs/ARNs.

## Library decision

pyvis (vis.js) -> single self-contained interactive HTML: draggable physics nodes, zoom/pan, hover tooltips, search. Layer-toggle buttons and per-account color grouping layered on top. Chosen over dash-cytoscape because it needs no running server, matching the "for others to view" goal.

## Plan

### Phase 1: Project scaffold
- Create `aws-config-viz/` project dir with a Python venv and `requirements.txt` (boto3, pyvis).
- Package layout: `crawler.py`, `parser.py`, `model.py`, `stitch.py`, `viz.py`, `cli.py`, plus `cache/` and `output/`.
- README stub (profile setup, how to run, how to read the diagram).

### Phase 2: Ingestion and parsing
- S3 crawler walks each account's ConfigHistory tree, selects the latest-dated file per (account, resourceType), downloads to a local `cache/` (skip re-download by key + timestamp).
- Parser: gunzip, load JSON, iterate `configurationItems`, keep latest entry per resourceId, drop ResourceDeleted.
- Normalize into two tables:
    - resources: account, region, resourceType, resourceId, arn, name, tags, layer, key config fields.
    - edges: srcId, dstId, relationshipName, account (intra-account edges from `relationships[]`).
- Assign each resourceType to a layer (networking / storage / IAM / compute / other) via a mapping table.
- Persist normalized model to local JSON so viz regen does not re-hit S3.

### Phase 3: Within-account graph + cross-account stitching
- Build the intra-account graph from resources + edges.
- Stitching step infers cross-account edges by matching shared identifiers: TGW attachments to a common transit gateway, VPC peering connections, VPC endpoints to shared services, cross-account IAM role trust (principal ARNs referencing other in-scope accounts), shared RAM resources.
- Emit inferred cross-account edges as a distinct edge class (styled differently in the viz).

### Phase 4: Visualization (first cut -> full)
- First cut: generate pyvis HTML from a SINGLE account so the look/feel can be reviewed before scaling.
- Then scale to all 5 accounts: account-colored node groups, node tooltips (type, id, tags), typed edges.
- Layer toggle: networking / storage / IAM / compute buttons show/hide nodes+edges by layer.
- Add search and a legend. Two zoom levels if feasible: org overview (accounts + cross-account links) expanding into per-account resource detail.

### Phase 5: Packaging and handoff
- `cli.py` entry point: refresh-from-S3 -> rebuild model -> regenerate HTML, with flags (single account, all accounts, layer subset).
- Finalize README. Decide refresh cadence (manual run to start).

## Deferred (needs user input, not blocking)
- Account-ID to human-name mapping (labels the account nodes). Placeholder labels until provided.

## Open scope choices to revisit after first cut
- Resource-type breadth: start broad (all types Config records) vs. focus first on the interconnection-heavy types (networking + IAM). Recommend building the full model but defaulting the first-cut view to networking.
- Detail level per node (how many config fields surfaced in tooltips).  


Simple Breakdown:

1. crawler.py -- hits S3, downloads the latest AWS Config history files for all 5 accounts into a local cache/ folder. This is the only step that needs AWS credentials.
2. parser.py -- reads those gzipped files, deduplicates entries, and drops deleted resources. Gives you a clean flat list of everything that currently exists.
3. model.py -- takes that list and organizes it into two tables: resources (what things are) and edges (how they connect). Labels each resource with a layer and extracts useful config details.
4. stitch.py (not yet built) -- Config only records relationships within a single account. This step infers cross-account links by matching shared IDs and ARNs.
5. viz.py (not yet built) -- takes the final graph and renders it to a self-contained HTML file using pyvis.
6. cli.py (not yet built) -- one command that runs all of the above in order.

The end result: someone on your team opens the HTML file, sees the whole org's infrastructure laid out visually, and can filter and explore it with no server or AWS access needed.