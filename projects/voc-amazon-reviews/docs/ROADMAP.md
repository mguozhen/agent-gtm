# VOC AI Skill — Product Roadmap

## Core Growth Logic

```
Free Tier (8 reviews, product page sample)
    → User sees value, wants more
    → Guided to voc.ai to subscribe
    → Paid plan unlocks full review analysis
```

---

## Phase 1 — Free Hook (Now → Week 4)

**Goal: Let users experience VOC analysis value quickly, creating demand for more.**

| Feature | Notes |
|---|---|
| Self-built scraper — product page Top Reviews | Stable 8-review capture, no auth required |
| Full bilingual analysis report | Sentiment, pain points, selling points, Listing tips |
| Upgrade CTA at report end | Auto-displayed after every analysis run |
| Limitation disclosure | Clearly state "based on 8 reviews — accuracy is limited" |

**Upgrade CTA (displayed at end of every report):**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  This report is based on 8 reviews (product page sample).
    This product has 324 total reviews.

🔓  Unlock full analysis on voc.ai:
    → Analyze all reviews
    → Competitor comparison (up to 5 ASINs)
    → Weekly automated monitoring
    → Export to CSV / PDF

    👉  voc.ai/start?asin={ASIN}&ref=skill
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Phase 2 — Conversion Optimization (Week 4 → Week 8)

**Goal: Improve conversion from Skill → voc.ai paid subscription.**

| Feature | Notes |
|---|---|
| ASIN pre-fill on landing | Jump URL carries ASIN, website pre-fills it — reduces friction |
| UTM tracking | `ref=skill&utm_source=clawhub` to attribute Skill-driven subscriptions |
| Review count teaser | Scrape and display total review count, create "you're missing 316 reviews" gap |
| Report sharing | Export report as Markdown/image — sellers sharing = free distribution |

---

## Phase 3 — Ecosystem Lock-in (Week 8 → Week 16)

**Goal: Make the Skill the daily workflow entry point for paid users.**

| Feature | Notes |
|---|---|
| Paid user direct connect | Enter voc.ai API Key — Skill pulls full review dataset automatically |
| Multi-ASIN batch analysis | Paid-only: analyze up to 5 competitor ASINs in one run |
| Weekly monitoring alerts | Auto-diff new reviews, proactive alert on new 1-star spikes |
| Team report sharing | Reports sync to voc.ai dashboard for team collaboration |

---

## Key Metrics

| Phase | Metric | Target |
|---|---|---|
| Phase 1 | Skill installs | 500 |
| Phase 1 | Reports generated | 1,000 / month |
| Phase 2 | Report → voc.ai click rate | ≥ 15% |
| Phase 2 | Click → signup rate | ≥ 30% |
| Phase 3 | Paid users with API Key bound | ≥ 40% of paid base |
