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

## Put it on GitHub Pages behind a password

    .venv/bin/python viewer/build_static.py <list name> --out docs/viewer

It asks for a username and password, then writes `docs/` with the page and one encrypted data file
(AES-256-GCM, key from username + password via PBKDF2, 600,000 rounds). The browser asks for the login and decrypts
the data on the visitor's machine; nothing readable is ever published. In the repo settings, set Pages to deploy from
`main` / `docs`.

- The encrypted file is public, so the password is the only protection. Use a long one (12+ characters is enforced).
- Anyone who knows the login can read the data. To change it or refresh the data, build again and commit `docs/`.
- Never commit anything but the build output; lists/ stays git-ignored.
- The "Lock" button forgets the login. The local server (above) needs no login and listens on 127.0.0.1 only.

Files: `server.py` (data and static files), `index.html` (markup), `style.css` (styles), `app.js` (behaviour),
`build_static.py` (encrypted Pages build).
