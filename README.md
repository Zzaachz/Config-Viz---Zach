#1. Profile Setup
- Which AWS profile to use (logging account 143009743682)
- That you need read access to the S3 bucket

#2. How to Run
- How to install dependencies (pip install -r requirements.txt)
- How to run the script (python cli.py)

#3. How to Read the Diagram
- What the nodes represent (AWS resources)
- What the edges/lines mean (relationships between resources)
-The 5 accounts and what they are (ALS, CCCI, Engineering dev, etc.)


Additional:
Run it with python crawler.py from the Main (Code)/ directory with your AWS credentials active.
cd 'claude\AWS Config Vizualization\'


# The HTML Output

## What it is

A single self-contained file that draws your AWS org's live infrastructure as an
interactive network diagram. Everything (the graph engine, the data, and the
controls) is baked into the one file, so it opens in any browser with no server,
no install, and no AWS access. You can email it or drop it in a shared drive and
anyone can open it.

## What it is for

Seeing the whole org's infrastructure at a glance, reconstructed from AWS Config,
and exploring how resources connect within and across the 5 accounts. It answers
questions like what is in each account, what talks to what, and where the
cross-account links are.

## How to read it

- Nodes are AWS resources. Node color is the account it belongs to (see the
  Accounts key). Node shape is the layer: dot networking, square compute,
  database storage, triangle iam, ellipse other, faded diamond a placeholder.
- Edges are relationships. Grey solid lines are within-account relationships from
  Config. Orange dashed lines are inferred cross-account links, such as shared
  transit gateways.
- Placeholders (faded diamonds) are resources referenced by something but not
  recorded by Config on their own, so their topology still shows.
- Hover a node for its details (type, id, account, layer, key config, tags).
  Hover an edge for the relationship name.
- Controls on the left: toggle layers, toggle accounts, toggle placeholders,
  search, and switch between the org overview and per-account detail.

## How to launch it

- Regenerate: from the Main (Code)/ folder, run `python viz.py` for all accounts
  or `python viz.py --account <id>` for one. Files are written to output/.
- Open: paste the file path into a browser address bar as a file URL, for example
  file:///C:/Users/zachs/IdeaProjects/Claude/AWS%20Config%20Vizualization/Main%20(Code)/output/aws-config-all.html
- After regenerating, the filename does not change, so just refresh the tab.

See Instructions/How To Launch.md for the full step-by-step.