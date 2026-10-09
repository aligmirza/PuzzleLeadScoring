# ICP Pipeline Map

How a lead list moves through the two phases, from your CSV to a scored list where every original row is kept.

- **Solid boxes** are built and tested.
- **Dashed boxes** are designed and waiting on the AI step (needs an Anthropic API key).
- Colours: **green** = ICP YES, **red** = ICP NO, **amber** = PENDING.

```bash
.venv/bin/python icp_check.py leads.csv        # phase 1
.venv/bin/python signals_check.py leads        # phase 2
.venv/bin/python -m enrich explain acme.com    # every answer and its proof for one company
```

The diagrams use Mermaid. They render on GitHub and in VS Code with a Mermaid preview extension (for example "Markdown Preview Mermaid Support").

For more detail, see [HOW_IT_WORKS.md](HOW_IT_WORKS.md) (step by step), [SIGNALS.md](SIGNALS.md) (each check) and [PLAN.md](PLAN.md) (status and roadmap).

---

## 1. The whole flow

Phase 1 decides whether each company is our ICP. Only companies that pass go to phase 2, so no time or AI is spent on signals for companies we would never contact. Both phases feed the same final file.

```mermaid
flowchart LR
    L["Your list<br/>CSV, 1 to 50k rows"]
    P1["Phase 1: Is it ICP?<br/>icp_check.py<br/>12 page types + job feed<br/>4 must-haves, 6 exclusions"]
    P2["Phase 2: Signals<br/>signals_check.py<br/>7 more page types<br/>21 fit, 14 buying, 12 weak"]
    N["Not ICP<br/>row kept, reason given"]
    F["Final list<br/>leads_enriched.csv<br/>every row + ICP + score + findings"]

    L -->|rows| P1
    P1 -->|YES / PENDING| P2
    P1 -->|NO| N
    P2 -->|scored| F
    N -->|as NO| F

    classDef key stroke:#2d55c8,stroke-width:2px
    classDef no fill:#f8e1dc,stroke:#b93a27,color:#18202e
    class P1,P2,F key
    class N no
```

Every row of your list ends up in the final file, in its original order. Duplicates are marked "Duplicate of row N". Rows without a usable domain are kept as NO with the reason.

---

## 2. Phase 1: downloading a website

About 50 companies run at the same time. Each one gets only the pages the must-haves and exclusions need, plus its open jobs. Pages are saved in a slim form, so rules can be re-run without downloading again.

```mermaid
flowchart LR
    H["Open homepage<br/>https, then www, then http"]
    FP["Find pages<br/>links, then sitemap,<br/>then /pricing, /about"]
    D["Download<br/>12 page types<br/>robots.txt, 90 s cap"]
    J["Read open jobs<br/>Greenhouse, Lever,<br/>Ashby, Workable"]
    S["Slim and save<br/>raw/(domain)/<br/>85% smaller"]
    R["Rules run<br/>(section 3)"]

    DNS["Public DNS check<br/>Google, Cloudflare"]
    DEAD["Dead<br/>ICP NO"]
    UN["Unreachable<br/>PENDING, retry"]
    BL["Blocked<br/>PENDING, retry with a browser"]

    H --> FP --> D --> J --> S -->|live| R
    H -->|"didn't load"| DNS
    DNS -->|"domain doesn't exist,<br/>or 404 / 410"| DEAD
    DNS -->|"timeout, or only<br/>your DNS fails"| UN
    DNS -->|bot protection| BL

    classDef key stroke:#2d55c8,stroke-width:2px
    classDef no fill:#f8e1dc,stroke:#b93a27,color:#18202e
    classDef pending fill:#f6ead2,stroke:#9a6510,color:#18202e
    class R key
    class DEAD no
    class UN,BL pending
```

A site is only marked dead when public DNS also says the domain doesn't exist. In testing, the local router failed on 4 of 14 real domains. Those now stay PENDING instead of being failed.

---

## 3. Phase 1: the ICP verdict

Free pattern-matching rules answer each must-have and exclusion with **Yes**, **No**, **Needs check** or **Unknown**, each with a quote and page link as proof. The verdict is then decided top to bottom. The first "yes" wins.

```mermaid
flowchart TD
    Q1{"Website not reached?"}
    Q2{"An exclusion proven?"}
    Q3{"A must-have proven false?"}
    Q4{"A clue still needs checking?"}
    Q5{"All 4 must-haves confirmed?"}

    O1["PENDING<br/>retry the download later"]
    O2["NO<br/>e.g. UK entity, holds physical stock"]
    O3["NO<br/>e.g. domain doesn't exist"]
    O4["PENDING<br/>AI check, e.g. accounting firm?"]
    O5["YES<br/>all must-haves proven"]
    O6["YES<br/>some must-haves unconfirmed"]

    Q1 -->|yes| O1
    Q1 -->|no| Q2
    Q2 -->|yes| O2
    Q2 -->|no| Q3
    Q3 -->|yes| O3
    Q3 -->|no| Q4
    Q4 -->|yes| O4
    Q4 -->|no| Q5
    Q5 -->|yes| O5
    Q5 -->|no| O6

    classDef yes fill:#dcf1e6,stroke:#1d7f53,color:#18202e
    classDef no fill:#f8e1dc,stroke:#b93a27,color:#18202e
    classDef pending fill:#f6ead2,stroke:#9a6510,color:#18202e
    class O5,O6 yes
    class O2,O3 no
    class O1,O4 pending
```

"Unknown" never makes a company a NO; only proof does. A company that is still unconfirmed on some must-haves is a YES, as profile.yaml requires.

---

## 4. AI: two steps per data point

Each data point the rules can't settle has its own prompt file in `prompts/`. Step 1 asks AI to read our saved pages. Only if that finds nothing does step 2 search the internet, with the company's details filled into the prompt's search queries.

```mermaid
flowchart LR
    A["Needs AI<br/>rule said Needs check,<br/>or an AI-only signal"]
    B["Load prompt file<br/>prompts/(group)/(data point).json"]
    S1["Step 1: our data<br/>AI reads saved pages<br/>Haiku, cached"]
    C{"Medium or high<br/>confidence?"}
    W["Write back<br/>answer + proof<br/>verdict and score recalculated"]
    V["Variables<br/>company: Resend<br/>domain: resend.com<br/>legal name, year"]
    S2["Step 2: the web<br/>Claude web search<br/>Sonnet, max 3 searches"]
    G{"Confidence gate<br/>is it this company?<br/>how good is the source?"}
    U["Stays Unknown<br/>hint kept as proof"]

    A --> B
    B -->|"fallback mode (47)"| S1
    B -->|"web-first (10)"| S2
    S1 --> C
    C -->|yes| W
    C -->|no| S2
    V -->|fill queries| S2
    S2 --> G
    G -->|high / medium| W
    G -->|low| U

    classDef plan stroke-dasharray: 6 4
    classDef key stroke:#2d55c8,stroke-width:2px
    class S1,S2 plan
    class C,G key
```

| Variable | Filled with | Example |
|---|---|---|
| `{company}` | Company name from your list | Resend |
| `{domain}` | Cleaned domain from your list | resend.com |
| `{legal_name}` | Legal name found in phase 1, else the company name | Resend |
| `{year}`, `{last_year}` | Current and previous year | 2026, 2025 |

Example query for "Venture-backed": `"Resend" raises seed OR "Series A" OR "Series B"`.

| Web search mode | When step 2 runs | Data points |
|---|---|---|
| Fallback | Only if step 1 found nothing or only low confidence | 47 |
| Web-first | Always; step 1 is skipped | 10 (e.g. raised in the last 6 months, new board member) |
| Off | Never; the web can't prove it | 5 (e.g. live website, no finance person visible) |

| Confidence | Means | Counts? |
|---|---|---|
| High | Official record, the company's own page, or 2+ good sources, and a source mentions the domain | Yes, including making a company NO |
| Medium | One reputable source, with a domain or name-and-location match | Yes, but can't make a company NO |
| Low | Name-only match, SEO page, old information, or sources disagree | No; stays Unknown |

---

## 5. Phase 2: the lead score

Each signal answered Yes adds points by its weight in profile.yaml. Needs check and Unknown add nothing.

| Weight | Points |
|---|---|
| High | +3 |
| Medium | +2 |
| Low | +1 |
| Weak fit signal | -2 |

```mermaid
flowchart LR
    Y["Signals answered Yes"] --> P["Add points<br/>high 3, medium 2, low 1"]
    W["Weak fit signals"] --> M["Subtract 2 each"]
    P --> T["Lead score"]
    M --> T
    T --> Q{"Score at least 12?"}
    Q -->|yes| SF["Strong fit"]
    Q -->|no| WF["Weak fit"]

    classDef yes fill:#dcf1e6,stroke:#1d7f53,color:#18202e
    class SF yes
```

The two real scores from the 14-company test:

| Company | Score | Tier | Breakdown |
|---|---|---|---|
| linear.app | **21** | Strong fit | Stripe +3, Delaware C-Corp +3, Paid pricing +3, Several open roles +2, Growth numbers +2, Remote-first +2, Early adopter +2, Hiring +2, Tax deadline +2 |
| resend.com | **15** | Strong fit | Venture-backed +3, Stripe +3, Paid pricing +3, YC +2, Remote-first +2, Tax deadline +2 |

```
linear.app  ███████████████████████████████████████████▏ 21
resend.com  ██████████████████████████████▏              15
            0          6          12         18         24
                                  ↑ Strong fit cut-off
```

Notes:
- Scores are lower than they will be: "Startup selling nationally" and "B2B SaaS" (3 points each) wait on the AI step.
- "Tax deadline" adds 2 to almost everyone near a filing deadline. You can set it to 0 in [scoring.json](scoring.json).
- The cut-off of 12 is provisional. It should be set from about 100 hand-labelled companies.

---

## 6. Where everything is saved

Every list gets its own folder, so lists never mix.

```
lists/leads/              # one folder per list
  input.csv               # copy of your list
  raw/<domain>/           # slim pages + jobs
    home.html.gz
    pricing.html.gz
    jobs.json.gz
  cache.sqlite            # answers + proof
  output/
    leads_enriched.csv    # the main file
    icp_check.csv         # phase 1 detail
    signals.csv           # phase 2 detail
    *_evidence.jsonl      # quote + link per answer
    *_ai_queue.jsonl      # prompts waiting for AI
```

Nothing is deleted without asking:

```mermaid
flowchart LR
    E["Phase ends"] --> O["Lists raw pages no longer needed<br/>(companies that are NO) and their size"]
    O --> A{"Delete? [y/n] (n)"}
    A -->|"n, Enter, Ctrl+C,<br/>or no terminal"| K["Nothing deleted"]
    A -->|y| D["Raw pages deleted<br/>answers and proof stay in cache.sqlite"]

    classDef pending fill:#f6ead2,stroke:#9a6510,color:#18202e
    class A pending
```
