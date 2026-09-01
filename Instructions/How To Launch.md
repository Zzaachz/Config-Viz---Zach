# How To Launch the AWS Config Visualization

A simple rundown for regenerating the diagram and opening it in a browser.

## One-time setup

Install the dependencies (only needed once, or after they change):

```
cd "C:\Users\zachs\IdeaProjects\Claude\AWS Config Vizualization\Main (Code)"
pip install -r ..\requirements.txt
```

## Regenerate the HTML

Open a terminal in the code folder. In IntelliJ this is the Terminal tab at the
bottom of the window.

```
cd "C:\Users\zachs\IdeaProjects\Claude\AWS Config Vizualization\Main (Code)"
```

Then run one of these:

```
python viz.py                          all 5 accounts
python viz.py --account 124074140119   a single account
```

The quotes around the path matter because the folder names contain spaces.

Each run prints where it saved the file, for example:

```
Rendered 700 nodes, 440 edges -> ...\output\aws-config-all.html
```

The files are written to the `output/` folder next to `viz.py`.

## Open it in a browser

Paste one of these into the address bar of Chrome, Edge, or Firefox. The `%20`
is just an encoded space.

```
file:///C:/Users/zachs/IdeaProjects/Claude/AWS%20Config%20Vizualization/Main%20(Code)/output/aws-config-all.html
file:///C:/Users/zachs/IdeaProjects/Claude/AWS%20Config%20Vizualization/Main%20(Code)/output/aws-config-124074140119.html
```

To print the exact URL for every file currently in `output/`, run this from the
code folder:

```
python -c "from pathlib import Path; [print(p.as_uri()) for p in Path('output').glob('*.html')]"
```

Note: opening the file from inside IntelliJ usually opens the IDE editor, not a
browser. Use the address bar above, or right-click the HTML file and choose
Open In, then Browser.

After you regenerate, the filename does not change, so just refresh the browser
tab with Ctrl+R. No new URL needed.

## Using the diagram

- The graph animates while it lays itself out, then freezes. You can still drag
  nodes; they will not drift on their own.
- Layers panel: networking, storage, iam, and compute are on by default. The
  other layer is off. Click a button to toggle it.
- Accounts panel: click an account to hide or show it. The color key doubles as
  the legend. Cross-account links are the orange dashed lines.
- Placeholders: faded diamond nodes are resources that are referenced but were
  not recorded by Config. Toggle them off with the Placeholders button.
- Search: type a name, id, or resource type to highlight and zoom to matches.
- Zoom: Collapse to org overview groups each account into one bubble plus the
  cross-account links. Expand, or double-click a bubble, to drill back in.

## Full refresh from AWS (optional)

Refresh cadence is manual for now: there is no scheduler or automation. Rerun
the full pipeline whenever you want current data (for example before a review).

The steps above reuse cached data, so they need no AWS access. To pull fresh
data from S3 first, set the profile and run the whole pipeline:

```
set AWS_PROFILE=logging
python cli.py all
```

`all` runs crawl, model, stitch, and viz in order.

## Command reference

```
python cli.py crawl    download latest Config files from S3 (needs AWS creds)
python cli.py model    rebuild the resource and edge tables
python cli.py stitch   infer cross-account links
python cli.py viz      render the HTML into output/
python cli.py all      run everything in order
```

`viz` and `all` also accept `--account <id>` (repeatable) and
`--layers networking,iam` to preselect which layers show on load.
