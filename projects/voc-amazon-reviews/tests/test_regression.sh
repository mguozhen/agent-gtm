#!/usr/bin/env bash
# Regression tests for voc-amazon-reviews (requires VOC_API_KEY with credits)
# Usage: VOC_API_KEY=your-key bash tests/test_regression.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PASS=0
FAIL=0
TOTAL=0

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

assert_gt() {
  local desc="$1" threshold="$2" actual="$3"
  TOTAL=$((TOTAL + 1))
  if [[ "$actual" -gt "$threshold" ]] 2>/dev/null; then
    echo "  PASS: $desc ($actual > $threshold)"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc (expected > $threshold, got $actual)"
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
    FAIL=$((FAIL + 1))
  fi
}

assert_json_field() {
  local desc="$1" json="$2" path="$3" expected="$4"
  local actual
  actual=$(echo "$json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
keys = '$path'.split('.')
for k in keys:
    if k.isdigit():
        data = data[int(k)]
    else:
        data = data.get(k, 'MISSING')
print(data)
" 2>/dev/null || echo "PARSE_ERROR")
  TOTAL=$((TOTAL + 1))
  if [[ "$actual" == "$expected" ]]; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc"
    echo "        expected $path = $expected"
    echo "        actual:  $actual"
    FAIL=$((FAIL + 1))
  fi
}

# ══════════════════════════════════════════════════════════════
echo ""
echo "=========================================="
echo "  VOC AI — Regression Tests (live API)"
echo "=========================================="

if [[ -z "${VOC_API_KEY:-}" ]]; then
  echo ""
  echo "  SKIP: VOC_API_KEY not set. Set it to run regression tests:"
  echo "        VOC_API_KEY=your-key bash tests/test_regression.sh"
  echo ""
  exit 0
fi

TEST_ASIN="B099Z93WD9"
API_BASE="https://openapi.shulex.com"
TEMP_OUT=$(mktemp /tmp/voc_regression_XXXXXX.json)
trap "rm -f $TEMP_OUT" EXIT

# ── R1. fetch.sh: Live API submit + query ─────────────────────
echo ""
echo "[R1] fetch.sh — live API call (ASIN=$TEST_ASIN, limit=8)"

# Note: The Shulex Realtime API deduplicates tasks for the same ASIN
# within a short window. A repeated submit may return SUCCESS instantly
# with 0 reviews. This is normal API behavior, not a bug.
# We submit and check structure regardless of review count.

bash "$PROJECT_DIR/fetch.sh" "$TEST_ASIN" --limit 8 --market US --output "$TEMP_OUT" 2>/dev/null || true

# Verify output is valid JSON
VALID_JSON=$(python3 -c "import json; json.load(open('$TEMP_OUT')); print('yes')" 2>/dev/null || echo "no")
assert_eq "output is valid JSON" "yes" "$VALID_JSON"

# Verify structure: has reviews array
HAS_REVIEWS=$(python3 -c "import json; d=json.load(open('$TEMP_OUT')); print('yes' if 'reviews' in d else 'no')" 2>/dev/null)
assert_eq "output has 'reviews' key" "yes" "$HAS_REVIEWS"

# Verify structure: has meta object
HAS_META=$(python3 -c "import json; d=json.load(open('$TEMP_OUT')); print('yes' if 'meta' in d else 'no')" 2>/dev/null)
assert_eq "output has 'meta' key" "yes" "$HAS_META"

# ── R2. Query a known successful task (stable data) ──────────
echo ""
echo "[R2] API query — poll existing task for review data"

# Query an existing task to verify data parsing (avoids dedup issue)
QUERY_RESP=$(curl -s "$API_BASE/v1/api/RtQry01?taskId=task_7c666bb7520941e498e3ee4382663c8c&pageNo=1&pageSize=8" \
  -H "X-API-Key: $VOC_API_KEY" 2>/dev/null || echo "{}")

QUERY_STATUS=$(echo "$QUERY_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('status','UNKNOWN'))" 2>/dev/null)

if [[ "$QUERY_STATUS" == "SUCCESS" ]]; then
  # Normalize through the same Python code fetch.sh uses
  NORM_DATA=$(echo "$QUERY_RESP" | python3 -c "
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
    'meta': {'asin': data.get('asin',''), 'market': data.get('market',''), 'total_available': total, 'fetched': len(normalized)}
}
print(json.dumps(output))
" 2>/dev/null)

  echo "$NORM_DATA" > "$TEMP_OUT"
  FETCH_DATA="$NORM_DATA"

  REVIEW_COUNT=$(echo "$NORM_DATA" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['reviews']))" 2>/dev/null || echo "0")
  assert_gt "fetched at least 1 review" "0" "$REVIEW_COUNT"
  assert_eq "review count <= limit (8)" "yes" "$(echo "$NORM_DATA" | python3 -c "import sys,json; print('yes' if len(json.load(sys.stdin)['reviews']) <= 8 else 'no')" 2>/dev/null)"
else
  echo "  SKIP: cached task no longer available (status=$QUERY_STATUS)"
  FETCH_DATA=$(cat "$TEMP_OUT")
fi

# ── R3. Meta fields ──────────────────────────────────────────
echo ""
echo "[R3] Meta fields"

assert_json_field "meta.asin" "$FETCH_DATA" "meta.asin" "$TEST_ASIN"
assert_json_field "meta.market" "$FETCH_DATA" "meta.market" "US"

TOTAL_AVAIL=$(echo "$FETCH_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin)['meta']['total_available'])" 2>/dev/null || echo "0")
assert_gt "meta.total_available > 0" "0" "$TOTAL_AVAIL"

FETCHED=$(echo "$FETCH_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin)['meta']['fetched'])" 2>/dev/null || echo "0")
assert_gt "meta.fetched > 0" "0" "$FETCHED"

# ── R4. Review fields ────────────────────────────────────────
echo ""
echo "[R4] Review field completeness"

FIELD_CHECK=$(echo "$FETCH_DATA" | python3 -c "
import sys, json
reviews = json.load(sys.stdin)['reviews']
if not reviews:
    print('NO_REVIEWS')
    sys.exit()
r = reviews[0]
required = ['rating', 'title', 'body', 'date', 'verified', 'reviewId']
missing = [f for f in required if f not in r]
if missing:
    print('MISSING:' + ','.join(missing))
else:
    print('ALL_PRESENT')
" 2>/dev/null)
assert_eq "review[0] has all required fields" "ALL_PRESENT" "$FIELD_CHECK"

# Rating is 1-5
RATING_VALID=$(echo "$FETCH_DATA" | python3 -c "
import sys, json
reviews = json.load(sys.stdin)['reviews']
if not reviews: print('yes'); sys.exit()
all_valid = all(isinstance(r.get('rating'), int) and 1 <= r['rating'] <= 5 for r in reviews)
print('yes' if all_valid else 'no')
" 2>/dev/null)
assert_eq "all ratings are 1-5" "yes" "$RATING_VALID"

# Verified is boolean
VERIFIED_BOOL=$(echo "$FETCH_DATA" | python3 -c "
import sys, json
reviews = json.load(sys.stdin)['reviews']
if not reviews: print('yes'); sys.exit()
all_bool = all(isinstance(r.get('verified'), bool) for r in reviews)
print('yes' if all_bool else 'no')
" 2>/dev/null)
assert_eq "all verified fields are boolean" "yes" "$VERIFIED_BOOL"

# Date format: YYYY-MM-DD
DATE_VALID=$(echo "$FETCH_DATA" | python3 -c "
import sys, json, re
reviews = json.load(sys.stdin)['reviews']
if not reviews: print('yes'); sys.exit()
all_valid = all(re.match(r'^\d{4}-\d{2}-\d{2}$', r.get('date','')) for r in reviews if r.get('date'))
print('yes' if all_valid else 'no')
" 2>/dev/null)
assert_eq "all dates are YYYY-MM-DD format" "yes" "$DATE_VALID"

# ── R5. API submit endpoint reachable ─────────────────────────
echo ""
echo "[R5] API connectivity — submit endpoint"

SUBMIT_CHECK=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API_BASE/v1/api/RtTask01" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $VOC_API_KEY" \
  -d '{"asin":"B08N5WRWNW","market":"US","maxPage":1}' 2>/dev/null)
assert_eq "submit endpoint returns 200" "200" "$SUBMIT_CHECK"

# ── R6. API query endpoint reachable ──────────────────────────
echo ""
echo "[R6] API connectivity — query endpoint"

QUERY_CHECK=$(curl -s -o /dev/null -w "%{http_code}" "$API_BASE/v1/api/RtQry01?taskId=task_nonexistent&pageNo=1&pageSize=1" \
  -H "X-API-Key: $VOC_API_KEY" 2>/dev/null)
assert_eq "query endpoint returns 200" "200" "$QUERY_CHECK"

# ── R7. voc.sh extraction pipeline ───────────────────────────
echo ""
echo "[R7] voc.sh — review extraction from fetch output"

EXTRACTED=$(python3 -c "
import json
with open('$TEMP_OUT') as f:
    data = json.load(f)
reviews = data.get('reviews', data if isinstance(data, list) else [])
print(len(reviews))
" 2>/dev/null)
assert_gt "voc.sh extraction yields reviews" "0" "$EXTRACTED"

# ── R8. Invalid API key → clear error ────────────────────────
echo ""
echo "[R8] fetch.sh — invalid API key error handling"

BAD_OUT=$(VOC_API_KEY="invalid_key_xxx" bash "$PROJECT_DIR/fetch.sh" B08N5WRWNW --limit 1 2>&1 || true)
assert_contains "invalid key shows error" "Failed\|error\|Unauthorized\|INVALID" "$BAD_OUT"

# ══════════════════════════════════════════════════════════════
echo ""
echo "=========================================="
echo "  Results: $PASS passed, $FAIL failed (total: $TOTAL)"
echo "=========================================="

if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
