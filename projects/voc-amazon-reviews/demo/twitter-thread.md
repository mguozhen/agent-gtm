# Twitter Launch Thread

Post time: Tuesday 10am ET
Account: @mguozhen

---

## Tweet 1 (Hook + GIF)

I built a free tool that analyzes Amazon product reviews in 5 seconds.

Paste an ASIN → get pain points, selling points, and listing optimization tips.

Real API data. 10 marketplaces. AI semantic analysis.

Here's what it looks like:

[attach: demo/voc-demo.gif]

---

## Tweet 2 (Problem)

If you sell on Amazon, you know the drill:

- Manually reading hundreds of reviews
- Trying to spot patterns in complaints
- Guessing what to put in your listing bullets

Tools like Helium 10 do word frequency. "battery" appeared 47 times.

But is that praise or complaints? They don't tell you.

---

## Tweet 3 (How it works)

How it works:

1. You give it an ASIN
2. It fetches real reviews via Shulex VOC API (not scraping)
3. AI analyzes sentiment, pain points, selling points
4. You get a bilingual report (EN + CN)

Default: 8 reviews free. Enough to validate any product.

---

## Tweet 4 (Real output)

Real output for Amazon Fire HD 8 Plus (B099Z93WD9):

Pain points AI found:
- "Moisture in charging port" — known bug, 2 reviews
- "Stalls out pausing videos" — 2 reviews
- "APP store offers nothing" — 1 review

Selling points:
- "Budget friendly, entertainment on the go" — 3 reviews
- "Perfect size, light and easy" — 2 reviews

[attach: report screenshot]

---

## Tweet 5 (10 marketplaces)

Works across 10 Amazon marketplaces:

🇺🇸 US  🇨🇦 CA  🇲🇽 MX  🇬🇧 GB  🇩🇪 DE
🇫🇷 FR  🇮🇹 IT  🇪🇸 ES  🇯🇵 JP  🇦🇺 AU

Just change one flag:

  voc.sh B099Z93WD9 --market JP

Japanese reviews → analyzed in English + Chinese.

Perfect for cross-border sellers who can't read every language.

---

## Tweet 6 (Zero setup)

Zero dependency setup:

- Needs only curl + python3 (already on your Mac/Linux)
- No Docker, no npm, no config files
- Get a free API key in 30 seconds

From zero to first report in under 2 minutes.

---

## Tweet 7 (CTA)

Try it now:

1. Get free API key: apps.voc.ai/openapi
2. Clone: git clone github.com/mguozhen/voc-amazon-reviews
3. Run: bash voc.sh B08N5WRWNW

Free tier = 8 reviews per analysis.

Star ⭐ if useful: github.com/mguozhen/voc-amazon-reviews

#AmazonFBA #AmazonSeller #AITools #ecommerce #ProductResearch
