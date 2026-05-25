# Reddit Post — r/FulfillmentByAmazon

## Title

I built a free CLI tool that analyzes Amazon reviews with AI — finds pain points, selling points, and listing optimization tips in 5 seconds

## Body

Hey everyone,

I've been working on a tool that does VOC (Voice of Customer) analysis on Amazon reviews. Instead of manually reading through hundreds of reviews or paying $50+/mo for tools that just count keywords, this uses a real review API and runs AI semantic analysis.

**What you get in 5 seconds:**

- Sentiment breakdown (positive / neutral / negative %)
- Top 5 pain points with actual customer quotes and mention counts
- Top 5 selling points with real quotes
- Listing optimization suggestions based on what customers actually say
- Everything in English + Chinese (great for cross-border teams)

**Example output** — I ran it on the Amazon Fire HD 8 Plus (B099Z93WD9):

```
📊 Sentiment: 37% positive, 13% neutral, 50% negative

🔴 Pain Points:
1. Charging port "moisture" error — known software bug (2 mentions)
   "A week in and it hasn't dried out"
2. Video stalling and buffering (2 mentions)
   "Stalls out, really annoying when entertaining a toddler"

🟢 Selling Points:
1. Great value for money (3 mentions)
   "Budget friendly, entertainment on the go"
2. Perfect portable size (2 mentions)
   "Light and easy to fit in my purse"

💡 Optimization:
- Highlight "budget-friendly" and portability in title
- Add charging port care instructions in A+ Content
```

**How it works:**

Reviews come from the Shulex VOC API (legitimate data provider, not scraping). Default is 8 reviews for free — costs 5 API credits and new accounts include starter credits. You can bump it to 100+ reviews for deeper analysis.

Works on all 10 Amazon marketplaces: US, CA, MX, GB, DE, FR, IT, ES, JP, AU.

**How to try:**

1. Get a free API key at apps.voc.ai/openapi
2. `git clone https://github.com/mguozhen/voc-amazon-reviews`
3. `export VOC_API_KEY=your-key`
4. `bash voc.sh B08N5WRWNW`

Only needs curl + python3. No Docker, no npm, no browser automation.

Happy to run an analysis on any ASIN if you want to see your product or a competitor. Just drop it in the comments.

Would love feedback from actual sellers — what would make this more useful for your workflow?

---

# Reddit Post — r/AmazonSeller

## Title

Free tool: paste an Amazon ASIN, get AI analysis of customer pain points + listing optimization tips (10 marketplaces)

## Body

Hey r/AmazonSeller,

Quick share — I made a free tool that analyzes Amazon reviews using AI. You give it an ASIN, it pulls real review data and tells you:

- What customers hate (pain points with exact quotes)
- What they love (selling points with mention counts)
- How to improve your listing based on real feedback

It's not keyword counting — it actually reads and understands the reviews semantically.

**Works on 10 Amazon marketplaces** (US, UK, DE, JP, CA, FR, IT, ES, MX, AU), so you can analyze competitors in any market even if you don't speak the language. Output is always bilingual (EN + CN).

Default analysis is free (8 reviews, takes 5 seconds). You can go deeper with more reviews if needed.

GitHub: github.com/mguozhen/voc-amazon-reviews

Drop an ASIN in the comments and I'll run it for you.

---

# Reddit Post — r/SideProject

## Title

I built an Amazon review analyzer as a Claude Code skill — 50 unit tests, zero dependencies, real API data [bash + python3 only]

## Body

**What it does:** Input an Amazon ASIN, get a structured bilingual report — sentiment analysis, top 5 pain points, top 5 selling points, listing optimization tips. All in 5 seconds.

**Tech stack (intentionally minimal):**
- Pure bash scripts (bash 3.2 compatible)
- Python3 for JSON processing
- curl for API calls
- That's it. No npm. No Docker. No framework.

**Architecture:**
- `voc.sh` — orchestrator (arg parsing, flow control)
- `fetch.sh` — Shulex VOC API client (submit task → poll → normalize)
- `analyze.sh` — AI analysis + report renderer
- 50 unit tests + 17 live API regression tests

**Interesting decisions:**
- Used the Shulex VOC Realtime API instead of scraping. Submit a task, poll until SUCCESS, query paginated results. 5 credits per page, query is free.
- Market code mapping handles both domain format (`amazon.co.uk`) and country codes (`GB`, `uk`). Case-insensitive.
- Review normalization layer makes both the old scraper format and new API format work with the same analyze.sh.
- bash 3.2 compatibility: no `${VAR^^}` for uppercase, use `tr` instead.

**Business model:** Free tier (8 reviews = 5 credits) → upgrade prompt in report → user registers for API key at voc.ai → buys more credits for deeper analysis.

GitHub: github.com/mguozhen/voc-amazon-reviews

[demo GIF]
