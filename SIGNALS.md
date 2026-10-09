# How We Check Each Signal

This file goes through every item in `profile.yaml` one by one and explains how we'll find the answer. It covers four groups:

1. [Must-haves](#1-must-haves): a company has to meet all of these
2. [Exclusions](#2-exclusions): any one of these knocks a company out
3. [Strong fit signals](#3-strong-fit-signals): these add points (this group includes the fit signals and the buying signals)
4. [Weak fit signals](#4-weak-fit-signals): these take points away but never disqualify

Groups 1 and 2 are checked in **phase 1** (is it our ICP?). Groups 3 and 4 are checked in **phase 2**, only for companies that passed phase 1.

For the bigger picture, see [PLAN.md](PLAN.md). For the step-by-step workflow, see [HOW_IT_WORKS.md](HOW_IT_WORKS.md).

**Where each part lives:**
- the code rules: [enrich/rules.py](enrich/rules.py)
- the AI prompt for each data point: [prompts/](prompts/), one JSON file each
- the points per signal: [scoring.json](scoring.json)

The short codes in this file (M1, E3, F12...) are only used here and in the code, to match each signal to its rule. Output files and the terminal use plain names.

---

## How to read this file

Every check gives one of four answers:

- **Yes:** we found proof that it's true
- **No:** we found proof that it's false
- **Needs check:** code found a clue but can't be sure, so that data point's AI prompt runs
- **Unknown:** we couldn't find proof either way

**Unknown never hurts a company.** It doesn't fail a must-have, doesn't trigger an exclusion and doesn't cost points. It just means the signal doesn't count.

For each signal, we try methods in order from cheapest to most expensive, and we stop as soon as we have a clear answer. For the AI part this means two steps: first AI reads our saved pages, and only if that finds nothing does it search the internet (see [HOW_IT_WORKS.md](HOW_IT_WORKS.md#two-steps-per-data-point-our-data-first-then-the-internet)). A web answer only counts at medium or high confidence, and only high confidence with a domain match can exclude a company.

| Method | What it means | Cost |
|---|---|---|
| **Code** | Pattern matching on the website's pages and source code | Free |
| **Directory** | Looking the company up in a free public list we've downloaded | Free |
| **AI read** | A low-cost AI model reads the website text and answers a question | Low |
| **Search** | Claude searches the web with the company name and domain, and reports sources and confidence. Runs only if the steps above found nothing (or straight away for data that only exists on the web) | Medium |
| **Paid data** | A paid service such as Clay, Crunchbase or Apollo | Varies |

Each signal below lists:

- **Weight:** how many points it's worth (high = 3, medium = 2, low = 1, all adjustable)
- **Yes when:** what proof we need
- **No when:** what proof rules it out (if anything can)
- **How we check:** the methods, in the order we try them
- **Watch out for:** common mistakes to avoid

---

## Quick reference

| # | Signal | Group | Weight | Main method | Fills a column? |
|---|---|---|---|---|---|
| M1 | Based and operating in the US | Must-have | | Code | |
| M2 | Live website with a real product | Must-have | | Code | |
| M3 | Uses a fintech tool Puzzle connects to | Must-have | | Code | partners |
| M4 | Real transaction activity | Must-have | | Code | |
| E1 | Series C or later, or public | Exclusion | | Directory, Search | funding_stage |
| E2 | New company, no website, no funding | Exclusion | | Code, Directory | |
| E3 | Accounting or bookkeeping firm | Exclusion | | AI read | |
| E4 | Mainly operates outside the US | Exclusion | | Code | |
| E5 | Non-profit or government body | Exclusion | | Directory, Code | entity_type |
| E6 | E-commerce holding physical stock | Exclusion | | Code, AI read | |
| F1 | Venture-backed, pre-seed to Series B | Fit | High | Directory, Search | funding_stage, partners |
| F2 | Uses Stripe for billing | Fit | High | Code | partners |
| F3 | Delaware C-Corp | Fit | High | Code, Directory | entity_type |
| F4 | Uses QuickBooks Online | Fit | High | Code (job posts) | competitors |
| F5 | Startup selling nationally | Fit | High | AI read | |
| F6 | B2B SaaS or tech-enabled service | Fit | High | AI read | vertical |
| F7 | Local professional services firm | Fit | Medium | AI read | vertical |
| F8 | Publishes real paid pricing | Fit | High | Code | |
| F9 | Payroll on Gusto, Rippling or Deel | Fit | Medium | Code (job posts) | partners |
| F10 | AI is core to the product | Fit | Medium | AI read | vertical |
| F11 | YC or similar accelerator | Fit | Medium | Directory | partners |
| F12 | Several open roles | Fit | Medium | Code (job boards) | |
| F13 | No finance person visible | Fit | Medium / Low / none | AI read, Paid data | |
| F14 | Has a finance lead (mid-size and up) | Fit | Medium | Paid data, AI read | |
| F15 | Publishes revenue or growth numbers | Fit | Medium | Code, Search | |
| F16 | Remote-first or hybrid | Fit | Medium | Code | |
| F17 | Runs several US companies | Fit | Medium | Search, Paid data | |
| F18 | Early adopter | Fit | Medium | Code, Directory | |
| F19 | Banks with Mercury or Brex | Fit | Low | Code, Search | partners |
| F20 | Uses Ramp or Brex cards | Fit | Low | Code, Search | partners |
| F21 | Cap table in Carta or Pulley | Fit | Low | Code, Search | partners |
| B1 | Raised money in the last 6 months | Buying | High | Directory, Search | funding_stage |
| B2 | New investor or accelerator | Buying | High | Directory, Search | partners |
| B3 | Hiring for several roles | Buying | Medium | Code (job boards) | |
| B4 | Hiring a finance lead | Buying | Medium | Code (job boards) | |
| B5 | Hiring ops or chief of staff | Buying | Medium | Code (job boards) | |
| B6 | Recently became a Delaware C-Corp | Buying | High | Code (old snapshots), Paid data | entity_type |
| B7 | Incorporated in the last 12 months | Buying | Medium | Directory, Paid data | |
| B8 | Added a new company or subsidiary | Buying | Medium | Paid data, Search | |
| B9 | Lost or replacing a bookkeeper | Buying | Medium | Search, Code | |
| B10 | Tax deadline coming up | Buying | Medium | Calendar | |
| B11 | Complaining about QuickBooks | Buying | Medium | Search | competitors |
| B12 | Recently launched paid pricing | Buying | Medium | Code (old snapshots) | |
| B13 | New board member or investor updates | Buying | Low | Search | |
| B14 | Preparing for a first audit | Buying | Low | Code (job posts), Search | |
| W1 | Serves one local area | Weak | | Code, AI read | |
| W2 | Sells hours of labour, not a product | Weak | | AI read | |
| W3 | Physical or single-location business | Weak | | Code, AI read | |
| W4 | LLC or sole owner, no venture funding | Weak | | Code | entity_type |
| W5 | Not making money yet | Weak | | Code, AI read | |
| W6 | Needs more than one currency | Weak | | Code | |
| W7 | Manufacturing or hardware | Weak | | AI read | |
| W8 | Heavy invoicing and bill-paying | Weak | | AI read | |
| W9 | Shopify store without stock | Weak | | Code | |
| W10 | Already on NetSuite or similar with a full finance team | Weak | | Code, Paid data | competitors |
| W11 | An outside accountant picks their tools | Weak | | Search | |
| W12 | No fintech tools at all | Weak | | Code | |

---

## 1. Must-haves

**The rule:** a company passes if **none of the four is a confirmed No**. A company with all four as Yes is strongest. A company with some Unknowns still passes. We record how many were confirmed so you can sort by confidence.

### M1. Based and operating in the United States

- **Yes when:** a US address appears on the site (footer, contact page, privacy policy), or the terms of service name a US state's law, or an official filing lists a US address.
- **No when:** the only address is outside the US and the company clearly runs from there. This shares its evidence with exclusion E4.
- **How we check:**
  1. **Code:** look for US addresses (state name or abbreviation plus a 5-digit ZIP code), +1 phone numbers, prices in dollars and "laws of the State of ..." in the terms.
  2. **Directory:** the SEC Form D filing lists the business address. The YC directory lists location.
  3. **AI read:** the about and contact pages, if the code found nothing clear.
  4. **Paid data:** headquarters country from Clay or Apollo.
- **Watch out for:** a US company with an offshore team still passes. A remote company with no address shown is Unknown, not No.

### M2. Live website with a real product or service

- **Yes when:** the site loads, has several real pages and describes something people can buy or use.
- **No when:** the domain doesn't load, is parked, is listed for sale or shows only a "coming soon" page.
- **How we check:**
  1. **Code:** does the domain resolve and load? We check for known parked-page and for-sale templates (GoDaddy, Sedo, HugeDomains and similar), count the pages and measure how much real text there is.
  2. **AI read:** "Does this site describe a real product or service a customer can buy or use?"
- **Watch out for:** if a domain redirects to another domain, follow it and note the new address. A site that blocks bots isn't a dead site. Retry it with a full browser before marking it No.

### M3. Uses at least one fintech tool Puzzle connects to

The tools: Mercury, Brex, Ramp, Stripe, Gusto, Rippling, Deel, Pulley and Chase business banking.

- **Yes when:** we find proof the company itself uses one of these.
- **No when:** almost never provable. We leave it Unknown rather than guess.
- **How we check:**
  1. **Code:** Stripe scripts in the page code (`js.stripe.com`), Stripe checkout links, the list of vendors in the privacy policy, and job posts mentioning Gusto, Rippling or Deel benefits. A careers page hosted on Rippling's job board also counts.
  2. **Search:** "company name" plus each tool name, for case studies and blog posts.
  3. **Paid data:** a tech-stack lookup (BuiltWith or Wappalyzer).
- **Watch out for:** "Connect your Stripe account" on a product page means their product **works with** Stripe. It doesn't prove the company **pays with** Stripe. The AI checks the wording when the only match is on an integrations page.
- **Also fills:** the `partners` column for every tool confirmed.

### M4. Real transaction activity

- **Yes when:** any one of these is found:
  - published paid pricing
  - a working checkout
  - an app store listing with paid plans
  - open job postings (which means payroll)
- **No when:** nothing can be bought and there are no jobs, **and** the site shows clear signs of being pre-launch (a waitlist only, "coming soon").
- **How we check:**
  1. **Code:** prices on the pricing page, checkout links (Stripe, Paddle, Chargebee, Shopify checkout), app store links. We pull open jobs from the company's job board.
  2. **Directory:** Apple App Store search shows whether the app has paid plans.
  3. **AI read:** the pricing page, if prices are shown in an unusual format.
- **Watch out for:** a donation page, membership fee or event ticket doesn't count as transaction activity (the profile says so directly).

---

## 2. Exclusions

**The rule:** an exclusion only applies with **real proof**. A guess, an estimate or missing information never excludes a company. Each exclusion we apply records the exact proof in `exclusion_reason`.

### E1. Raised Series C or later, or publicly traded

- **Exclude when:** there's a confirmed Series C or later round, an IPO or a public statement saying so.
- **How we check:**
  1. **Directory:** check the SEC list of public companies (company names and stock tickers).
  2. **Code:** look on the site for words like "Series C", "Series D" or "NASDAQ" / "NYSE" plus a ticker.
  3. **Search:** "company name Series C" and "company name IPO", read by AI to confirm.
  4. **Paid data:** funding rounds from Crunchbase, if connected.
- **Watch out for:**
  - Looking big or polished isn't proof.
  - The total amount raised (for example from Form D filings) isn't proof of a round's name. It can only give an estimated stage, and estimates never exclude.
  - A subsidiary of a public company gets flagged for review instead of being excluded automatically.

### E2. New company with no live website and no disclosed funding

- **Exclude when:** all three are confirmed: the domain is dead or parked, no funding shows up anywhere, and the company is newly formed.
- **How we check:**
  1. **Code:** the result from M2. Is the site dead or parked?
  2. **Directory:** no Form D filing, no accelerator listing.
  3. **Paid data:** incorporation date (OpenCorporates), if available.
- **Watch out for:** in practice, a dead domain already fails M2. This exclusion mostly makes the reason clearer in the output.

### E3. Accounting firm, bookkeeping practice or fractional CFO firm

- **Exclude when:** the company **sells** accounting, bookkeeping, tax or outsourced finance **services**.
- **How we check:**
  1. **Code:** look for words like "CPA", "bookkeeping services", "fractional CFO", "outsourced accounting" or "tax preparation" in the page title, homepage headline and services pages. This only flags the company for the next step.
  2. **AI read:** "Is this company itself an accounting, bookkeeping or fractional CFO firm?"
- **Watch out for:** a company that makes **software** for accountants isn't an accounting firm. A business that mentions its own accountant isn't one either.

### E4. Mainly operates outside the US, or reports in a currency other than dollars

- **Exclude when:** the headquarters and main operations are clearly abroad, or the company only prices in non-US currency.
- **How we check:**
  1. **Code:**
     - a foreign address with no US address
     - a foreign legal ending (Ltd, GmbH, SAS, Pty, BV) together with a foreign address
     - prices only in EUR, GBP, INR and similar currencies
     - a country domain (such as .de or .in) together with a site in that country's language
  2. **AI read:** "Where is this company based and where does it mainly operate?"
  3. **Paid data:** headquarters country.
- **Watch out for:** many founders from abroad set up a Delaware "Inc." with a US headquarters. Those pass. A US company that also prices in euros for its EU customers also passes.

### E5. Non-profit, charity, foundation, religious group, membership association or government body

The profile is strict here: this exclusion depends on **what the company is**, never on who its customers are or which words appear on its site.

- **Exclude only when one of these is true:**
  - our entity type check returns "Non-profit"
  - there's direct proof of the company's own tax-exempt status: a 501(c)(3) or charity registration number, a tax ID shown for tax-deductible donations, or a published annual report or Form 990
  - the site's main call to action is to donate or volunteer, and nothing commercial is for sale
- **How we check:**
  1. **Directory:** match the name and state against the IRS list of tax-exempt organizations. ProPublica's free Nonprofit Explorer adds Form 990 filings.
  2. **Code:** "501(c)(3)", "tax-deductible", "EIN" near donation wording, and a donate button in the main menu. Government domains (.gov, .mil) are excluded straight away.
  3. **AI read:** "Is this organisation itself a non-profit, or a for-profit company that serves non-profits?"
- **Watch out for:**
  - Donor CRMs, fundraising platforms and grant software are **not** excluded, even if "non-profit" appears on every page.
  - A Public Benefit Corporation is a for-profit company and is **not** excluded.

### E6. E-commerce or retail business that holds physical stock

- **Exclude when we see proof such as:**
  - a store selling physical goods
  - stock or "sold out" labels
  - shipping and returns policies for physical items
  - a warehouse or fulfilment address
  - jobs in supply chain, inventory or fulfilment
- **How we check:**
  1. **Code:**
     - the store platform (Shopify, WooCommerce, BigCommerce)
     - product data built into the page code, which often states "in stock" or "out of stock" directly
     - "add to cart" buttons
     - shipping and returns pages
  2. **Code (job posts):** warehouse, fulfilment or inventory roles.
  3. **AI read:** "Does this company hold physical stock that it sells?"
- **Watch out for:** these are **not** excluded:
  - digital goods
  - subscriptions and memberships
  - software
  - services booked online
  - print-on-demand (Printful and Printify are easy to spot in the code)
  - dropshipping

  If we can't tell whether they hold stock, we don't exclude. Shopify stores without stock score as weak signal W9 instead.

---

## 3. Strong fit signals

These add points. The profile splits them into **fit signals** (what kind of company it is) and **buying signals** (something happening right now that creates a reason to buy).

General rule from the profile: check the company's own site first (homepage, about, pricing, careers, blog, footer, privacy policy, terms), then job posts, press, accelerator and investor pages, Product Hunt and app stores.

### Fit signals

#### F1. Venture-backed at pre-seed, seed, Series A or Series B (high)

- **Yes when:** we confirm a round at one of these stages, **or** the company clearly looks like a venture-backed startup based on several clues. Clues include team size, open roles, product maturity, published pricing and press coverage. In that case the stage is marked "estimated".
- **How we check:**
  1. **Directory:** SEC Form D filings show that a company raised money, when and how much. Accelerator lists (YC, Techstars and others) show backing.
  2. **Code:** investor logos or "backed by" text, and phrases like "our seed round" on the about and team pages.
  3. **Search:** funding announcements and press.
  4. **AI read:** if there's still no direct proof, estimate the stage from the clues and say which clues were used.
  5. **Paid data:** Crunchbase funding rounds.
- **Output:** every company gets a final line in its reasoning, in exactly this format: `Funding stage: <value> (<basis>)`.
  - `<value>` is one of: Pre-Seed, Seed, Series A, Series B, Series C or later, Bootstrapped or Unknown.
  - `<basis>` is one of: confirmed, estimated or not found.
- **Watch out for:**
  - "Unknown (not found)" and "Bootstrapped (confirmed)" are different answers. Only use Bootstrapped when we have proof they took no venture money.
  - The reasoning must never say a company falls short because its funding couldn't be found.

#### F2. Uses Stripe for billing (high)

- **Yes when:** Stripe appears in the page code, at checkout, or in the privacy policy's list of vendors.
- **How we check:**
  1. **Code:** `js.stripe.com` in the source, `checkout.stripe.com` or `buy.stripe.com` links, "Stripe" in the privacy policy or terms.
  2. **AI read:** the privacy policy, if the wording is unclear.
- **Watch out for:** the same mistake as M3. A product that "integrates with Stripe" doesn't prove the company bills with Stripe.

#### F3. Delaware C-Corp (high)

- **Yes when:** the legal name ends in "Inc." **and** the terms name Delaware law, **or** an official filing says Delaware.
- **How we check:**
  1. **Code:** the legal name in the footer (for example "© 2025 Acme, Inc."), "laws of the State of Delaware" in the terms, and the company name in the privacy policy.
  2. **Directory:** SEC Form D filings list the state of incorporation and entity type, which is strong proof. The developer's legal name on the Apple App Store helps too.
  3. **AI read:** the legal and company information pages.
  4. **Paid data:** OpenCorporates.
- **Output:** also fills `entity_type` with exactly one of: Delaware C-Corp, Other US C-Corp, LLC, Public Benefit Corporation, Non-US entity, Non-profit or Unknown.

#### F4. Uses QuickBooks Online (high)

- **Yes when:** a job post asks for QuickBooks experience, or the company says publicly that it uses QuickBooks.
- **How we check:**
  1. **Code (job posts):** search open jobs, especially finance and ops roles, for "QuickBooks" or "QBO".
  2. **Search:** "company name QuickBooks".
- **Watch out for:**
  - Rarely visible, but decisive when it is.
  - A product that **integrates with** QuickBooks doesn't mean the company **uses** it.
- **Also fills:** the `competitors` column. The same check also catches Xero, NetSuite, Wave, Zoho Books, Bench, Pilot and the other competitors listed in the profile.

#### F5. Startup selling nationally, not a small local business (high)

The profile calls this the most important single signal. It can't fire together with F7.

- **Yes when:** the company sells a software or tech product to customers beyond its own city, and revenue can grow without hiring more people.
- **No when:** it sells its own staff's hours in one area, as an agency, consultancy, contractor, clinic or single-location business does.
- **How we check:**
  1. **Code (clues only):**
     - Points toward a startup: self-serve plans on a pricing page, "sign up" or "start free trial" buttons, app store links.
     - Points toward a local business: a "locations" or "areas we serve" page.
  2. **AI read:** the homepage, pricing page and any locations page, asking "Is this a scalable product sold nationally, or local services?"

#### F6. B2B SaaS or a tech-enabled service sold beyond one city (high)

- **Yes when:** the company sells software or a tech-based service to businesses anywhere in the country.
- **How we check:**
  1. **AI read:** the homepage and pricing page. This is usually easy to read.
- **Also feeds:** the `vertical` column.

#### F7. Local professional services firm (medium)

Marketing or creative agencies, IT or managed-service shops, staffing firms and consultancies serving one city. These do convert, so they get a smaller positive score.

- **Yes when:** the company sells billed labour in one metro area.
- **How we check:** the same AI read as F5. Each company gets F5 or F7, never both.
- **Note:** if F7 fires, the weak signals W1 and W2 are skipped, so the company isn't penalised twice.

#### F8. Publishes real paid pricing (high)

- **Yes when:** named plans with prices are shown.
- **No when:** the pricing page only says "Contact us".
- **How we check:**
  1. **Code:** dollar amounts next to words like "month", "year", "seat" or "user" on the pricing page, plus product price data in the page code.
  2. **AI read:** pricing shown in images or unusual layouts.

#### F9. Runs payroll on Gusto, Rippling or Deel (medium)

- **How we check:**
  1. **Code (job posts):** benefits sections that mention these tools, and careers pages hosted on Rippling's job board.
  2. **Search:** "company name Gusto", "company name Rippling" and so on.
- **Also fills:** `partners`.

#### F10. AI or machine learning is core to their own product (medium)

- **How we check:**
  1. **AI read:** "Is AI the main thing the product does, or just a feature mention?"
- **Watch out for:** almost every site says "AI" now, so keyword matches alone don't count.

#### F11. Backed by Y Combinator or a similar accelerator (medium)

- **How we check:**
  1. **Directory:**
     - the YC company list, downloaded weekly from YC's official directory
     - Techstars, 500 Global and a16z speedrun portfolio pages
  2. **Code:** "Backed by Y Combinator" badges, or the YC batch shown on the site.
  3. **Search:** press about the accelerator.
- **Also fills:** `partners` (Y Combinator, Techstars).

#### F12. Active careers page with several open roles (medium)

- **Yes when:** 3 or more open roles. This threshold can be changed.
- **How we check:**
  1. **Code:** find the job board (Greenhouse, Lever, Ashby, Workable, Rippling) and count roles using its free public feed. If the careers page is self-hosted, count the job links on it.

#### F13. No dedicated accounting or finance person visible (medium for SMB, low for MM, not scored for ENT)

- **Yes when:** we looked at a real list of the team, and no one has a finance or accounting title.
- **How we check:**
  1. **AI read:** the team page, listing names and titles.
  2. **Paid data:** a people search on the company (Clay or Apollo) for finance titles.
- **Watch out for:** if there's no team page and no people data, the answer is Unknown, not Yes. We need to have actually looked at a team list. Having a finance person is never a negative for mid-size or larger companies.

#### F14. Has a Controller, Head of Finance or finance team, at mid-size and larger companies only (medium)

- **How we check:**
  1. **Paid data:** people search for finance titles.
  2. **AI read:** the team page.
- **Note:** only scored for MM and ENT. Not used for SMB.

#### F15. Publishes revenue, customer count or growth numbers (medium)

- **How we check:**
  1. **Code:** phrases like "$X ARR", "X,000 customers" or "grew X%" on the about page, homepage and blog.
  2. **Search:** press releases and funding announcements.

#### F16. Remote-first or hybrid (medium)

- **How we check:**
  1. **Code:** "remote", "remote-first", "distributed" or "hybrid" on the careers page and in job posts.

#### F17. Runs several US companies under the same owner (medium)

Holding companies, real estate portfolios and startup studios.

- **How we check:**
  1. **Code:** words like "a portfolio company of", "Holdings" or several company names in the footer or legal pages.
  2. **Paid data:** OpenCorporates, to find other companies at the same address or with the same officers.
  3. **Search:** "company name subsidiary" or "holding company".
- **Watch out for:** this is hard to find. Expect it to be Unknown for most companies.

#### F18. Early adopter (medium)

Product Hunt launches, an engineering blog, or public writing about the tools they use.

- **How we check:**
  1. **Code:** links to Product Hunt, a `/blog/engineering` or similar page, and "built with" or "our stack" posts.
  2. **Directory:** Product Hunt listing.

#### F19. Banks with Mercury or Brex (low)

- **How we check:**
  1. **Code:** job posts, "how we work" posts and partner badges.
  2. **Search:** "company name Mercury".
- **Note:** the profile says this is rarely public. Unknown is the expected answer.

#### F20. Uses Ramp or Brex for company cards (low)

- **How we check:** same approach as F19.

#### F21. Cap table managed in Carta or Pulley (low)

- **How we check:**
  1. **Code:** job posts ("equity managed in Carta") and investor pages.
  2. **Search:** press releases.

### Buying signals

#### B1. Raised money in the last 6 months (high)

Includes seed extensions, bridge rounds and SAFEs, not only priced rounds.

- **How we check:**
  1. **Directory:** SEC Form D filings include the date of the first sale. A filing within the last 6 months is strong, free proof.
  2. **Search:** funding news from the last 6 months.
  3. **Code:** a recent "we raised" blog post or press page.
  4. **Paid data:** Crunchbase.

#### B2. New investor or accelerator has appeared (high)

- **How we check:**
  1. **Directory:** YC batch dates and new accelerator portfolio entries. Because we download these lists every week, we can see who has been newly added.
  2. **Search:** recent press naming a new investor.

#### B3. Hiring for several roles at once (medium)

- **How we check:**
  1. **Code:** the same job count as F12. On later runs we can also compare with the previous count to see growth.

#### B4. Hiring a Controller, Head of Finance or first finance person (medium)

- **How we check:**
  1. **Code (job posts):** titles containing Controller, Finance, Accounting, FP&A or Bookkeeper.

#### B5. Hiring for operations, business operations or chief of staff (medium)

- **How we check:**
  1. **Code (job posts):** titles containing Operations, BizOps, Business Operations or Chief of Staff.

#### B6. Recently converted from an LLC to a Delaware C-Corp (high)

- **How we check:**
  1. **Code (old snapshots):** compare the footer and terms today with copies from 6 to 12 months ago in the Internet Archive (Wayback Machine, free). "Acme LLC" becoming "Acme, Inc." is the giveaway.
  2. **Paid data:** OpenCorporates conversion filings.
  3. **Search:** "company name converted to C-Corp".

#### B7. Incorporated in the last 12 months, already funded, with a live product (medium)

- **How we check:**
  1. **Directory:** SEC Form D filings include the year of incorporation.
  2. **Paid data:** OpenCorporates incorporation date.
  3. **Code (clue only):** the age of the domain registration. This is an estimate and is never enough on its own.
- **Also needs:** F1 or B1 (funding) and M2 (live product) to be Yes.

#### B8. Registered a new subsidiary or another operating company (medium)

- **How we check:**
  1. **Paid data:** OpenCorporates, for new companies with the same officers or address.
  2. **Search:** news about the new company.

#### B9. Recently parted ways with a bookkeeper or fractional CFO (medium)

- **How we check:**
  1. **Code (job posts):** a new bookkeeper or finance role posted.
  2. **Search:** public posts about it.
- **Note:** rarely visible.

#### B10. Tax filing or extension deadline coming up (medium)

- **How we check:** **calendar rules, not research.** For US C-Corps (calendar year), it fires in the weeks before April 15 and before the October 15 extension deadline. The window size can be changed.
- **Watch out for:** this applies to almost every US company at the same time. So it works more as a "send now" flag than a way to tell companies apart.

#### B11. Publicly complaining about QuickBooks (medium)

- **How we check:**
  1. **Search:** "company name" or founder name with QuickBooks on X, Reddit, LinkedIn and review sites.
- **Also fills:** `competitors`.

#### B12. Recently launched paid pricing, a new revenue line or subscriptions (medium)

- **How we check:**
  1. **Code (old snapshots):** compare today's pricing page with Internet Archive copies. If there was no pricing page before and there is one now, it fires.
  2. **Code:** recent changelog or blog posts announcing pricing.
  3. **Directory:** a recent Product Hunt launch date.

#### B13. New board member, or started publishing investor updates (low)

- **How we check:**
  1. **Search:** press and LinkedIn announcements.

#### B14. Preparing for a first audit or due diligence (low)

- **How we check:**
  1. **Code (job posts):** finance roles mentioning "audit readiness" or "first audit".
  2. **Search:** news about it.
- **Watch out for:** a SOC 2 or security audit is not a financial audit.

---

## 4. Weak fit signals

These lower the score but **never disqualify**. A company with several of these is still a valid lead, just a lower priority one that suits self-serve rather than sales outreach.

Each weak signal answered Yes takes away **2 points**. Change this in [scoring.json](scoring.json) (`weak_signal_points`).

### Small-business markers

#### W1. Serves one city or a local service area

- **Skipped if:** F7 already fired.
- **How we check:**
  1. **Code:** "locations", "areas we serve" or "service area" pages, and lists of cities or zip codes.
  2. **AI read:** to confirm.

#### W2. Sells hours of labour, not a product

Business coaching, training and other local services not already covered by F7.

- **Skipped if:** F7 already fired.
- **How we check:**
  1. **AI read:** the same question as F5.

#### W3. Physical or single-location business

Restaurants, bars, cafés, bakeries, hotels, farms, shops, salons, gyms, studios, clinics and trades.

- **How we check:**
  1. **Code:** business data built into the page code that marks it as a restaurant, gym, clinic and so on. Also opening hours, menu pages and booking widgets.
  2. **AI read:** to confirm.

#### W4. LLC or sole owner with no venture funding found

- **Yes when:** `entity_type` is confirmed as LLC (or a sole owner), **and** the funding stage is Unknown or Bootstrapped.
- **How we check:** reuse the results from F3 and F1. No extra research needed.
- **Note:** the profile says this must never disqualify, because it's common among real customers.

### Product-fit limits

#### W5. Not making money yet (the "explorer" pattern)

- **Yes when:** there's a waitlist, "coming soon", "join the beta" or "request early access", with no pricing and no checkout.
- **How we check:**
  1. **Code:** those phrases, plus the absence of F8 and M4 proof.
  2. **AI read:** to confirm.

#### W6. Needs reporting in more than one currency, or not in dollars

- **How we check:**
  1. **Code:** prices in several currencies, offices or companies in other countries, and a currency switcher on the site.
  2. **AI read:** about and contact pages.

#### W7. Manufacturing or hardware

- **How we check:**
  1. **AI read:** "Does this company make physical products?"
  2. **Code (job posts):** manufacturing, hardware engineering or supply chain roles.
- **Note:** if they also hold stock for sale, exclusion E6 applies instead.

#### W8. Heavy invoicing or bill-paying workflows

- **How we check:**
  1. **AI read:** signs like wholesale, net-30 payment terms, "request an invoice" or large B2B contracts paid by invoice.
- **Note:** a low-confidence signal. Expect Unknown often.

#### W9. Shopify or similar store that doesn't hold stock

Digital goods, print-on-demand or dropshipping.

- **Yes when:** the store platform is detected **and** exclusion E6 found no proof of stock.
- **How we check:** reuse the E6 checks.

#### W10. Already running NetSuite, QuickBooks Enterprise or another ERP, with a full in-house accounting team

- **How we check:**
  1. **Code (job posts):** "NetSuite" or "QuickBooks Enterprise" in finance job posts.
  2. **Paid data:** size of the finance team.
- **Also fills:** `competitors`.

#### W11. An outside accountant or bookkeeper picks their tools, not the founder

- **How we check:**
  1. **Search:** mentions of "our accountant" or their CPA firm.
- **Note:** almost never public. Expect Unknown nearly every time.

#### W12. No fintech tools at all, just a traditional bank and spreadsheets

- **Yes when:** there's real proof, such as a job post asking for Excel bookkeeping with a traditional bank.
- **How we check:** reuse the M3 results.
- **Watch out for:** "we didn't find any fintech tools" is **not** proof. Without real proof this stays Unknown and doesn't count.

---

## Build status

| Signals | Answered by code today | Waiting on |
|---|---|---|
| All 4 must-haves, all 6 exclusions | Yes, with "Needs check" where unsure | AI step to settle the "Needs check" ones; SEC and IRS lists for Series C+ / public and non-profit |
| Fit: Stripe, Delaware C-Corp, paid pricing, open roles, growth numbers, remote, early adopter, payroll and banking tools, YC | Yes | Directories (SEC Form D, YC list) to confirm funding and Delaware status |
| Fit: startup selling nationally, B2B SaaS, local services, AI core, finance person, several companies | Clues only | AI step |
| Buying: several roles, finance hire, ops hire, bookkeeper, audit, tax deadline | Yes, from job posts and the calendar | |
| Buying: raised in last 6 months, new investor, LLC to C-Corp, incorporated recently, new subsidiary, QuickBooks complaints, launched pricing, board member | Not yet | Web search, SEC Form D, old site snapshots |
| Weak: local area, physical business, LLC with no funding, pre-revenue, multi-currency, manufacturing, Shopify without stock, NetSuite | Yes or clues | AI step for the clue-only ones |
| Weak: invoicing-heavy, accountant picks tools, no fintech tools | Not yet | AI step, web search |

Until the AI step is built, signals it decides count 0 points. So scores are lower than they will be, especially for real startups.

---

## How a score adds up

**Phase 1 (is it our ICP?)**

1. **Exclusions first.** If any exclusion is confirmed, ICP = **NO**, with the proof in "Why".
2. **Must-haves next.** If any must-have is a confirmed No, ICP = **NO**.
3. **Needs check.** If a must-have or exclusion is "Needs check", ICP = **PENDING** until the AI check settles it.
4. **Website not reached.** ICP = **PENDING**, retry later.
5. Otherwise ICP = **YES**. Unknown must-haves never make a company a NO.

**Phase 2 (lead score), only for YES and PENDING companies**

6. **Size band.** SMB, MM or ENT from the employee count in your list (SMB if missing). This decides how "No finance person visible" and "Has a finance lead" are weighted.
7. **Add points** for every fit and buying signal answered Yes: **high 3, medium 2, low 1**.
8. **Subtract 2** for every weak signal answered Yes, skipping "Serves one local area" and "Sells hours of labour" if the company is a local professional services firm.
9. **Tier:**
   - **Strong fit:** score at or above the cut-off (12 for now, in scoring.json).
   - **Weak fit:** below the cut-off.

   The cut-off should be set from about 100 hand-labelled companies.
10. **Later, AI writes** the reasoning (ending with the `Funding stage:` line) and the fit message, using only signals actually found.

Needs check and Unknown always count 0 points. The score breakdown in the final list shows exactly which signals added or took away points.
