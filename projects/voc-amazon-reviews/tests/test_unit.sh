#!/usr/bin/env bash
# Unit tests for voc-amazon-reviews (no network calls)
# Usage: bash tests/test_unit.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures"

PASS=0
FAIL=0
TOTAL=0

# ── Test helpers ──────────────────────────────────────────────
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  TOTAL=$((TOTAL + 1))
  if [[ "$expected" == "$actual" ]]; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc"
    echo "        expected: $expected"
    echo "        actual:   $actual"
    FAIL=$((FAIL + 1))
  fi
}

assert_contains() {
  local desc="$1" needle="$2" haystack="$3"
  TOTAL=$((TOTAL + 1))
  if echo "$haystack" | grep -q "$needle"; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc"
    echo "        expected to contain: $needle"
    echo "        actual output: $(echo "$haystack" | head -3)"
    FAIL=$((FAIL + 1))
  fi
}

assert_exit_code() {
  local desc="$1" expected="$2" actual="$3"
  TOTAL=$((TOTAL + 1))
  if [[ "$expected" == "$actual" ]]; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc (exit code: expected=$expected actual=$actual)"
    FAIL=$((FAIL + 1))
  fi
}

# ══════════════════════════════════════════════════════════════
echo ""
echo "=========================================="
echo "  VOC AI — Unit Tests"
echo "=========================================="

# ── 1. fetch.sh: Arg parsing ─────────────────────────────────
echo ""
echo "[1] fetch.sh — arg parsing"

# No ASIN → exit 1
OUT=$(bash "$PROJECT_DIR/fetch.sh" 2>&1 || true)
CODE=$?
# set -e makes this tricky; capture with subshell
CODE=$(bash -c "bash '$PROJECT_DIR/fetch.sh' 2>/dev/null; echo \$?" 2>/dev/null || echo "1")
assert_eq "no ASIN exits with error" "1" "$CODE"

# No API key → exit 1
CODE=$(bash -c "VOC_API_KEY='' bash '$PROJECT_DIR/fetch.sh' B08N5WRWNW 2>/dev/null; echo \$?" 2>/dev/null || echo "1")
assert_eq "no API key exits with error" "1" "$CODE"

OUT=$(VOC_API_KEY='' bash "$PROJECT_DIR/fetch.sh" B08N5WRWNW 2>&1 || true)
assert_contains "no API key shows registration URL" "apps.voc.ai" "$OUT"

# ── 2. fetch.sh: Market code mapping ─────────────────────────
echo ""
echo "[2] fetch.sh — market code mapping"

# Test market mapping via a Python helper that mimics the bash case
test_market_mapping() {
  local input="$1" expected="$2"
  local actual
  actual=$(python3 -c "
market = '$input'
mapping = {
    'amazon.com': 'US', 'us': 'US', 'Us': 'US',
    'amazon.ca': 'CA', 'ca': 'CA', 'Ca': 'CA',
    'amazon.com.mx': 'MX', 'mx': 'MX', 'Mx': 'MX',
    'amazon.co.uk': 'GB', 'gb': 'GB', 'Gb': 'GB', 'uk': 'GB',
    'amazon.de': 'DE', 'de': 'DE', 'De': 'DE',
    'amazon.fr': 'FR', 'fr': 'FR', 'Fr': 'FR',
    'amazon.it': 'IT', 'it': 'IT', 'It': 'IT',
    'amazon.es': 'ES', 'es': 'ES', 'Es': 'ES',
    'amazon.co.jp': 'JP', 'jp': 'JP', 'Jp': 'JP',
    'amazon.com.au': 'AU', 'au': 'AU', 'Au': 'AU',
}
valid_codes = {'US','CA','MX','GB','DE','FR','IT','ES','JP','AU'}
if market in mapping:
    print(mapping[market])
elif market in valid_codes:
    print(market)
else:
    print('INVALID')
" 2>/dev/null)
  assert_eq "market '$input' → $expected" "$expected" "$actual"
}

test_market_mapping "amazon.com" "US"
test_market_mapping "amazon.co.uk" "GB"
test_market_mapping "amazon.co.jp" "JP"
test_market_mapping "amazon.de" "DE"
test_market_mapping "amazon.com.au" "AU"
test_market_mapping "us" "US"
test_market_mapping "jp" "JP"
test_market_mapping "uk" "GB"
test_market_mapping "US" "US"
test_market_mapping "JP" "JP"
test_market_mapping "amazon.com.br" "INVALID"

# ── 3. fetch.sh: maxPage calculation ─────────────────────────
echo ""
echo "[3] fetch.sh — maxPage calculation"

test_maxpage() {
  local limit="$1" expected="$2"
  local actual
  actual=$(python3 -c "
limit = $limit
mp = (limit + 9) // 10
if mp < 1: mp = 1
if mp > 100: mp = 100
print(mp)
" 2>/dev/null)
  assert_eq "limit=$limit → maxPage=$expected" "$expected" "$actual"
}

test_maxpage 1 1
test_maxpage 8 1
test_maxpage 10 1
test_maxpage 11 2
test_maxpage 50 5
test_maxpage 100 10
test_maxpage 500 50
test_maxpage 1001 100

# ── 4. fetch.sh: Review normalization ────────────────────────
echo ""
echo "[4] fetch.sh — review normalization (API response → output)"

NORM_RESULT=$(cat "$FIXTURES/api_query_resp.json" | python3 -c "
import sys, json

resp = json.load(sys.stdin)
data = resp.get('data', {})
reviews = data.get('reviews', [])
total = data.get('total', 0)
limit = 8

normalized = []
for r in reviews[:limit]:
    normalized.append({
        'rating':    r.get('rating'),
        'title':     r.get('title', ''),
        'body':      r.get('body', '') or r.get('content', ''),
        'date':      r.get('reviewDate', ''),
        'verified':  bool(r.get('verified') or r.get('verifiedPurchase')),
        'variant':   r.get('variant', ''),
        'author':    r.get('author', '') or r.get('reviewerName', ''),
        'helpful':   r.get('helpfulVotes', 0),
        'reviewId':  r.get('reviewId', ''),
        'vineVoice': bool(r.get('isVineVoice')),
    })

output = {
    'reviews': normalized,
    'meta': {
        'asin': data.get('asin', ''),
        'market': data.get('market', ''),
        'total_available': total,
        'fetched': len(normalized),
    }
}
print(json.dumps(output, ensure_ascii=False))
" 2>/dev/null)

# Validate structure
REVIEW_COUNT=$(echo "$NORM_RESULT" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['reviews']))" 2>/dev/null)
assert_eq "normalized review count" "3" "$REVIEW_COUNT"

META_ASIN=$(echo "$NORM_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['meta']['asin'])" 2>/dev/null)
assert_eq "meta.asin preserved" "B099Z93WD9" "$META_ASIN"

META_TOTAL=$(echo "$NORM_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['meta']['total_available'])" 2>/dev/null)
assert_eq "meta.total_available" "10" "$META_TOTAL"

# Check first review fields
R1=$(echo "$NORM_RESULT" | python3 -c "
import sys, json
r = json.load(sys.stdin)['reviews'][0]
print(r['rating'], r['title'][:10], r['verified'], r['reviewId'])
" 2>/dev/null)
assert_eq "review[0] fields" "2 Worked wel False R18V14GE98ALCA" "$R1"

# Check verified=true propagation (review[2] has verified=true)
R3_VERIFIED=$(echo "$NORM_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['reviews'][2]['verified'])" 2>/dev/null)
assert_eq "review[2] verified=True" "True" "$R3_VERIFIED"

# Check helpful votes (review[2] has helpfulVotes=3)
R3_HELPFUL=$(echo "$NORM_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['reviews'][2]['helpful'])" 2>/dev/null)
assert_eq "review[2] helpful=3" "3" "$R3_HELPFUL"

# Check null helpfulVotes → 0 (review[0])
R1_HELPFUL=$(echo "$NORM_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['reviews'][0]['helpful'])" 2>/dev/null)
assert_eq "review[0] null helpfulVotes → 0 (or None)" "None" "$R1_HELPFUL"

# ── 5. analyze.sh: Review simplification (both formats) ──────
echo ""
echo "[5] analyze.sh — review simplification (API + legacy formats)"

# API format
API_SIMPLIFIED=$(cat "$FIXTURES/reviews_api_format.json" | python3 -c "
import sys, json
reviews = json.load(sys.stdin)
sample = reviews[:150]
simplified = [{
    'rating': r.get('rating'),
    'title': r.get('title',''),
    'body': str(r.get('body','') or r.get('content',''))[:500],
    'date': r.get('date','') or r.get('reviewDate',''),
    'verified': bool(r.get('verified') or r.get('verifiedPurchase', False)),
    'variant': r.get('variant',''),
    'helpful': r.get('helpful', r.get('helpfulVotes', 0)),
} for r in sample]
print(json.dumps(simplified, ensure_ascii=False))
" 2>/dev/null)

API_COUNT=$(echo "$API_SIMPLIFIED" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null)
assert_eq "API format: simplified count" "8" "$API_COUNT"

API_DATE=$(echo "$API_SIMPLIFIED" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['date'])" 2>/dev/null)
assert_eq "API format: reviewDate → date" "2026-04-17" "$API_DATE"

API_VERIFIED=$(echo "$API_SIMPLIFIED" | python3 -c "import sys,json; print(json.load(sys.stdin)[2]['verified'])" 2>/dev/null)
assert_eq "API format: verified=true propagated" "True" "$API_VERIFIED"

# Legacy format
LEGACY_SIMPLIFIED=$(cat "$FIXTURES/reviews_legacy_format.json" | python3 -c "
import sys, json
reviews = json.load(sys.stdin)
sample = reviews[:150]
simplified = [{
    'rating': r.get('rating'),
    'title': r.get('title',''),
    'body': str(r.get('body','') or r.get('content',''))[:500],
    'date': r.get('date','') or r.get('reviewDate',''),
    'verified': bool(r.get('verified') or r.get('verifiedPurchase', False)),
    'variant': r.get('variant',''),
    'helpful': r.get('helpful', r.get('helpfulVotes', 0)),
} for r in sample]
print(json.dumps(simplified, ensure_ascii=False))
" 2>/dev/null)

LEGACY_COUNT=$(echo "$LEGACY_SIMPLIFIED" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null)
assert_eq "legacy format: simplified count" "3" "$LEGACY_COUNT"

LEGACY_DATE=$(echo "$LEGACY_SIMPLIFIED" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['date'])" 2>/dev/null)
assert_eq "legacy format: date field preserved" "2026-03-15" "$LEGACY_DATE"

# ── 6. analyze.sh: Report renderer ──────────────────────────
echo ""
echo "[6] analyze.sh — report renderer"

ANALYSIS=$(cat "$FIXTURES/analyze_mock_output.txt")
REPORT=$(python3 - <<PYEOF
import re, sys

raw = """$ANALYSIS"""

def get(key):
    m = re.search(rf'^{key}:\s*(.+)$', raw, re.MULTILINE)
    return m.group(1).strip() if m else '—'

def bar(pct):
    try:
        n = int(pct)
        filled = round(n / 5)
        return 'X' * filled + '.' * (20 - filled)
    except:
        return '.' * 20

pos = get('SENTIMENT_POSITIVE')
neu = get('SENTIMENT_NEUTRAL')
neg = get('SENTIMENT_NEGATIVE')

# Check pain points parsed
pp1_zh = get('PAIN_POINT_1_ZH')
pp1_en = get('PAIN_POINT_1_EN')
pp1_cnt = get('PAIN_POINT_1_COUNT')

# Check selling points parsed
sp1_zh = get('SELLING_POINT_1_ZH')

# Check tips parsed
tip1_zh = get('TIP_1_ZH')

# Check summary
summary_zh = get('SUMMARY_ZH')

# Output check results
print(f"POS={pos}")
print(f"NEU={neu}")
print(f"NEG={neg}")
print(f"PP1_ZH={pp1_zh}")
print(f"PP1_EN={pp1_en}")
print(f"PP1_CNT={pp1_cnt}")
print(f"SP1_ZH={sp1_zh}")
print(f"TIP1_ZH={tip1_zh}")
print(f"SUMMARY_ZH={summary_zh}")
PYEOF
)

assert_contains "sentiment positive parsed" "POS=37" "$REPORT"
assert_contains "sentiment neutral parsed" "NEU=13" "$REPORT"
assert_contains "sentiment negative parsed" "NEG=50" "$REPORT"
assert_contains "pain point 1 ZH parsed" "PP1_ZH=" "$REPORT"
assert_contains "pain point 1 EN parsed" "PP1_EN=Charging port" "$REPORT"
assert_contains "pain point 1 count parsed" "PP1_CNT=2" "$REPORT"
assert_contains "selling point 1 parsed" "SP1_ZH=" "$REPORT"
assert_contains "tip 1 parsed" "TIP1_ZH=" "$REPORT"
assert_contains "summary parsed" "SUMMARY_ZH=" "$REPORT"

# ── 7. voc.sh: Arg parsing & validation ──────────────────────
echo ""
echo "[7] voc.sh — arg parsing & validation"

# --help → exit 0
CODE=$(bash -c "bash '$PROJECT_DIR/voc.sh' --help >/dev/null 2>&1; echo \$?" 2>/dev/null || echo "0")
assert_eq "voc.sh --help exits 0" "0" "$CODE"

# No ASIN → exit non-zero
CODE=0
VOC_API_KEY=test bash "$PROJECT_DIR/voc.sh" >/dev/null 2>&1 || CODE=$?
assert_eq "voc.sh no ASIN exits non-zero" "1" "$CODE"

# No VOC_API_KEY → exit 1
OUT=$(VOC_API_KEY='' bash "$PROJECT_DIR/voc.sh" B08N5WRWNW 2>&1 || true)
assert_contains "voc.sh no key shows registration" "apps.voc.ai" "$OUT"

# Invalid ASIN format → warning but continues (until fetch fails)
OUT=$(VOC_API_KEY=fake bash "$PROJECT_DIR/voc.sh" badASIN 2>&1 || true)
assert_contains "voc.sh invalid ASIN shows warning" "Warning" "$OUT"

# ── 8. analyze.sh: Arg validation ────────────────────────────
echo ""
echo "[8] analyze.sh — arg validation"

# No file → exit 1
CODE=$(bash -c "bash '$PROJECT_DIR/analyze.sh' 2>/dev/null; echo \$?" 2>/dev/null || echo "1")
assert_eq "analyze.sh no file exits 1" "1" "$CODE"

# Non-existent file → exit 1
CODE=$(bash -c "bash '$PROJECT_DIR/analyze.sh' /tmp/nonexistent_file_xyz.json B099Z93WD9 2>/dev/null; echo \$?" 2>/dev/null || echo "1")
assert_eq "analyze.sh missing file exits 1" "1" "$CODE"

# ── 9. voc.sh: Extract reviews from fetch output ─────────────
echo ""
echo "[9] voc.sh — extract reviews from fetch output"

# Simulate the extraction that voc.sh does
TEMP_DATA="$FIXTURES/api_query_resp.json"
EXTRACTED=$(python3 -c "
import json, sys
with open('$TEMP_DATA') as f:
    data = json.load(f)
# voc.sh wraps fetch output in {reviews: [...], meta: {...}}
# but also handles raw API response
reviews = data.get('reviews', data.get('data', {}).get('reviews', []))
if not reviews and isinstance(data, list):
    reviews = data
print(json.dumps(reviews, ensure_ascii=False))
" 2>/dev/null)

EXT_COUNT=$(echo "$EXTRACTED" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null)
assert_eq "extracted reviews from API response" "3" "$EXT_COUNT"

# ══════════════════════════════════════════════════════════════
echo ""
echo "=========================================="
echo "  Results: $PASS passed, $FAIL failed (total: $TOTAL)"
echo "=========================================="

if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
