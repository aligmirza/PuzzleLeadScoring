# List viewer (optional)

A local web page that opens an enriched list as a table. Click a cell to see its answer, reasoning, proof quote
and source link in a side panel.

    .venv/bin/python viewer/server.py      # then open http://127.0.0.1:8790
    # open a specific list: http://127.0.0.1:8790/?list=<list folder name>

- Read-only: it only reads `lists/`. It never runs AI, Prospeo or any other paid step and changes nothing.
- No extra packages: Python standard library plus this project's own code.

## Using it

- **Quick tabs** All / ICP YES / NO / Pending, plus the stats at the top.
- **Columns**: starts on "Data points" (ICP, score, tier and every data point with proof). "Overview" is the short
  version, "Everything" shows all columns. Tick groups (Must-have, Exclusion, Fit, Buying signal, Weak fit) or single
  columns. The choice is remembered per list.
- **Filter**: Clay-style builder with Where/And/Or, operators, groups, eye to disable a row, ⋯ to duplicate or delete.
- **Sort**: click a header (asc, desc, off), shift-click to add a level, or use the Sort button for "Then by" levels.
  Empty values always sort last.
- **Details panel**: answer, reasoning, proof quote, "Open source" link, confidence, who decided it. ↑ ↓ ← → move
  between cells, Esc closes. Cells without proof show their value plus related context (for example Why for ICP).
- **Export**: the current view (filters, sort, visible columns) as CSV, optionally with reasoning, proof, source and
  confidence for each data point.
- Text from websites is shown as plain text, never as HTML. Light theme.

Files: `server.py` (data and static files), `index.html` (markup), `style.css` (styles), `app.js` (behaviour).
