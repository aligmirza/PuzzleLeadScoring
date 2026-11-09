# Services model: signals

## Roles

Every signal has one role. The role decides what the signal does to a company.

| Role | What it means | Effect |
|---|---|---|
| **Must-have** | Something every customer needs. All 4 must be proven. | All 4 proven: ICP YES (if no hard stop). One proven No: ICP NO. Not proven yet: PENDING. |
| **Hard stop** | Something that makes the company a poor fit for the services offer. | Proven Yes: ICP NO, with the reason. Unknown never stops a company. |
| **Fit signal** | The company looks like a good customer. | Adds points: +3 (high), +2 (medium) or +1 (low). |
| **Buying signal** | Something suggests they need help now (timing). | Adds points: +3, +2 or +1. |
| **Weak signal** | The company is a weaker fit, but not ruled out. | Takes 2 points off. |
| **0 points** | Checked and shown to sales, but does not change the score. | No points. Some change the Route, e.g. "Does its own books" goes to Platform + MCP. |

How it adds up:

- Only signals proven Yes count. Unknown or not found is 0, never a minus.
- Fit, buying and weak signals are scored only for companies that are ICP YES.
- Lead score = points from fit and buying signals, minus 2 for each weak signal. 12 or more is a Strong fit.
- "Too big for services" is one hard stop with three triggers: Series D or later, 100 or more people, or already on NetSuite.
- Weights and the Strong-fit cut-off are provisional until calibrated on Alexey's 152 companies.

## By category

Role: Must-have, Hard stop, +3/+2/+1 (fit or buying signal), -2 (weak signal), 0 (checked, no points).

### Location and legal entity

| ID | Signal | Role |
|---|---|---|
| M1 | Based and operating in the US | Must-have |
| E4 | Mainly operates outside the US | Hard stop |
| F3 | Delaware C-Corp | +3 |
| S1 | Sole proprietorship or partnership | Hard stop |
| W4 | LLC or sole owner, no venture funding | 0 |
| F17 | Runs several US companies | Hard stop |
| B8 | Registered a new subsidiary | Hard stop |
| B6 | Converted from an LLC to a C-Corp | 0 |
| B7 | Incorporated in the last 12 months | 0 |
| W6 | Needs more than one currency | Hard stop |

### Type of business

| ID | Signal | Role |
|---|---|---|
| M2 | Live website with a real product | Must-have |
| F6 | B2B SaaS or tech-enabled service | +3 |
| F7 | Local professional services firm | +3 |
| F10 | AI is core to the product | 0 |
| F5 | Startup selling nationally | 0 |
| W1 | Serves one local area | 0 |
| W2 | Sells hours of labour, not a product | 0 |
| W3 | Physical or single-location business | 0 |
| E3 | Accounting or bookkeeping firm | Hard stop |
| E5 | Non-profit or government body | Hard stop |
| E6 | E-commerce holding physical stock | Hard stop |
| W7 | Manufacturing or hardware | Hard stop |
| S2 | Marketplace | Hard stop |
| W9 | Shopify store without stock | -2 |

### Revenue and payments

| ID | Signal | Role |
|---|---|---|
| M4 | Real transaction activity | Must-have |
| S9 | Takes payments through several processors | +2 |
| S10 | Subscription or membership revenue | +2 |
| F8 | Publishes real paid pricing | 0 |
| B12 | Recently launched paid pricing | 0 |
| F15 | Publishes revenue or growth numbers | +1 |
| W5 | Not making money yet | -2 |
| W8 | Heavy invoicing and bill-paying | -2 |

### Finance tools

| ID | Signal | Role |
|---|---|---|
| M3 | Uses a fintech tool Puzzle connects to | Must-have |
| F2 | Uses Stripe for billing | +1 |
| F4 | Uses QuickBooks Online | +1 |
| F9 | Payroll on Gusto, Rippling or Deel | +3 |
| F19 | Banks with Mercury or Brex | +1 |
| F20 | Uses Ramp or Brex cards | +1 |
| F21 | Cap table in Carta or Pulley | +1 |
| S11 | Uses field-service or construction software | +2 |
| F18 | Early adopter | 0 |
| W10 | Already on NetSuite or similar | Hard stop |
| W11 | Accountant picks the tools | -2 |
| W12 | No fintech tools, spreadsheets and a traditional bank | -2 |
| B11 | Complaining about QuickBooks | +1 |

### Funding, stage and age

| ID | Signal | Role |
|---|---|---|
| F1 | Venture-backed, pre-seed to Series B | 0 |
| F11 | YC or similar accelerator | 0 |
| B1 | Raised money in the last 6 months | +3 |
| B2 | New investor or accelerator | 0 |
| B13 | New board member or investor updates | +3 |
| E1 | Series C or later, or public | 0 |
| S6 | Series D or later, or public | Hard stop |
| E2 | New company, no website, no funding | -2 |
| S12 | In business 10 or more years | +1 |

### Size and team

| ID | Signal | Role |
|---|---|---|
| S7 | 100 or more people | Hard stop |
| S8 | 50 to 99 people | -2 |
| F13 | No finance person visible (up to 25 people) | +2 |
| F14 | Has a finance lead (mid-size and up) | 0 |
| F16 | Remote-first or hybrid | 0 |
| F12 | Several open roles | +1 |

### Hiring

| ID | Signal | Role |
|---|---|---|
| B3 | Hiring for several roles | +1 |
| B4 | Hiring a finance lead | +2 |
| B5 | Hiring ops or chief of staff | +2 |

### Books and bookkeeping

| ID | Signal | Role |
|---|---|---|
| S3 | Books behind or messy | +3 |
| S4 | Looking for bookkeeping help | +3 |
| S5 | Does its own books, DIY (routes to Platform + MCP) | 0 |
| B9 | Lost or replacing a bookkeeper | +3 |
| B10 | Tax deadline coming up | +3 |
| B14 | Preparing for a first audit | +3 |

## By role

### Must-haves (all 4 needed)

| ID | Signal |
|---|---|
| M1 | Based and operating in the US |
| M2 | Live website with a real product |
| M3 | Uses a fintech tool Puzzle connects to |
| M4 | Real transaction activity |

### Hard stops (any one proven = not ICP)

| ID | Signal |
|---|---|
| W10 | Already on NetSuite or similar (Too big for services) |
| S6 | Series D or later, or public (Too big for services) |
| S7 | 100 or more people (Too big for services) |
| E3 | Accounting or bookkeeping firm |
| E4 | Mainly operates outside the US |
| E5 | Non-profit or government body |
| E6 | E-commerce holding physical stock |
| F17 | Runs several US companies |
| B8 | Registered a new subsidiary |
| W6 | Needs more than one currency |
| W7 | Manufacturing or hardware |
| S1 | Sole proprietorship or partnership |
| S2 | Marketplace |

### Fit signals

| ID | Signal | Points |
|---|---|---|
| F3 | Delaware C-Corp | 3 |
| F6 | B2B SaaS or tech-enabled service | 3 |
| F7 | Local professional services firm | 3 |
| F9 | Payroll on Gusto, Rippling or Deel | 3 |
| F13 | No finance person visible (up to 25 people) | 2 |
| S9 | Takes payments through several processors | 2 |
| S10 | Subscription or membership revenue | 2 |
| S11 | Uses field-service or construction software | 2 |
| F2 | Uses Stripe for billing | 1 |
| F4 | Uses QuickBooks Online | 1 |
| F12 | Several open roles | 1 |
| F15 | Publishes revenue or growth numbers | 1 |
| F19 | Banks with Mercury or Brex | 1 |
| F20 | Uses Ramp or Brex cards | 1 |
| F21 | Cap table in Carta or Pulley | 1 |
| S12 | In business 10 or more years | 1 |

### Buying signals

| ID | Signal | Points |
|---|---|---|
| B1 | Raised money in the last 6 months | 3 |
| B9 | Lost or replacing a bookkeeper | 3 |
| B10 | Tax deadline coming up | 3 |
| B13 | New board member or investor updates | 3 |
| B14 | Preparing for a first audit | 3 |
| S3 | Books behind or messy | 3 |
| S4 | Looking for bookkeeping help | 3 |
| B4 | Hiring a finance lead | 2 |
| B5 | Hiring ops or chief of staff | 2 |
| B3 | Hiring for several roles | 1 |
| B11 | Complaining about QuickBooks | 1 |

### Weak signals (-2 points each)

| ID | Signal |
|---|---|
| E2 | New company, no website, no funding |
| W5 | Not making money yet |
| W8 | Heavy invoicing and bill-paying |
| W9 | Shopify store without stock |
| W11 | Accountant picks the tools |
| W12 | No fintech tools, spreadsheets and a traditional bank |
| S8 | 50 to 99 people |

### Checked, 0 points

| ID | Signal |
|---|---|
| E1 | Series C or later, or public |
| S5 | Does its own books, DIY (routes to Platform + MCP) |
| F1 | Venture-backed, pre-seed to Series B |
| F5 | Startup selling nationally |
| F8 | Publishes real paid pricing |
| F10 | AI is core to the product |
| F11 | YC or similar accelerator |
| F14 | Has a finance lead (mid-size and up) |
| F16 | Remote-first or hybrid |
| F18 | Early adopter |
| W1 | Serves one local area |
| W2 | Sells hours of labour, not a product |
| W3 | Physical or single-location business |
| W4 | LLC or sole owner, no venture funding |
| B2 | New investor or accelerator |
| B6 | Converted from an LLC to a C-Corp |
| B7 | Incorporated in the last 12 months |
| B12 | Recently launched paid pricing |
