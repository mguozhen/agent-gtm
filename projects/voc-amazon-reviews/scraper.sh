#!/usr/bin/env bash
# Amazon 评论抓取脚本 - 基于 browse CLI (browser skill)
# Usage: scraper.sh <ASIN> [--limit N] [--market amazon.com]

set -euo pipefail

ASIN="${1:-}"
LIMIT=100
MARKET="amazon.com"
OUTPUT_FILE=""

# 解析参数
shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit) LIMIT="$2"; shift 2 ;;
    --market) MARKET="$2"; shift 2 ;;
    --output) OUTPUT_FILE="$2"; shift 2 ;;
    *) shift ;;
  esac
done

if [[ -z "$ASIN" ]]; then
  echo "Usage: scraper.sh <ASIN> [--limit N] [--market amazon.com]" >&2
  exit 1
fi

# 检查 browse CLI
if ! command -v browse &>/dev/null; then
  echo "❌ 未找到 browse CLI，请先安装 browser skill:" >&2
  echo "   npx skills add browserbase/skills@browser" >&2
  exit 1
fi

REVIEWS=()
PAGE=1
COLLECTED=0
MAX_PAGES=$(( (LIMIT + 9) / 10 ))  # 每页10条评论

echo "🔍 开始抓取 ASIN: $ASIN (目标: $LIMIT 条)" >&2
echo "   市场: https://www.$MARKET" >&2

# 启动浏览器，打开评论页
REVIEW_URL="https://www.${MARKET}/product-reviews/${ASIN}/ref=cm_cr_dp_d_show_all_btm?ie=UTF8&reviewerType=all_reviews&sortBy=recent"

browse open "$REVIEW_URL" 2>/dev/null || {
  echo "❌ 无法打开评论页面，请检查 ASIN 是否正确" >&2
  exit 1
}

sleep 3

# 逐页抓取
while [[ $PAGE -le $MAX_PAGES && $COLLECTED -lt $LIMIT ]]; do
  echo "   📄 正在抓取第 $PAGE 页..." >&2

  # 获取页面文本内容
  PAGE_TEXT=$(browse get text "body" 2>/dev/null || echo "")

  if [[ -z "$PAGE_TEXT" ]]; then
    echo "   ⚠️  第 $PAGE 页内容为空，停止抓取" >&2
    break
  fi

  # 检查是否被反爬拦截
  if echo "$PAGE_TEXT" | grep -qi "robot\|captcha\|verify you are human\|automated access"; then
    echo "   ⚠️  检测到反爬拦截，请配置 BROWSERBASE_API_KEY 使用远程浏览器" >&2
    echo "   browse env remote" >&2
    break
  fi

  # 提取评论区 HTML 用于解析
  PAGE_HTML=$(browse get html "#cm_cr-review_list" 2>/dev/null || echo "")

  if [[ -n "$PAGE_HTML" ]]; then
    # 将 HTML 内容暂存用于 Claude 解析
    TEMP_FILE=$(mktemp /tmp/voc_page_XXXXXX.html)
    echo "$PAGE_HTML" > "$TEMP_FILE"

    # 用 Claude 从 HTML 中提取结构化评论数据
    PAGE_REVIEWS=$(infsh app run openrouter/claude-haiku-45 \
      --input "{\"prompt\": \"从以下亚马逊评论页面 HTML 中提取所有评论，以 JSON 数组格式输出。每个评论包含：rating(1-5整数), title(标题), body(正文), date(日期字符串), verified(是否verified purchase, true/false)。只输出 JSON 数组，不要其他文字。\\n\\nHTML:\\n$(cat "$TEMP_FILE" | head -c 8000)\"}" \
      2>/dev/null || echo "[]")

    rm -f "$TEMP_FILE"

    # 验证是 JSON 数组
    if echo "$PAGE_REVIEWS" | python3 -c "import sys,json; data=json.load(sys.stdin); assert isinstance(data, list)" 2>/dev/null; then
      COUNT=$(echo "$PAGE_REVIEWS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
      REVIEWS+=("$PAGE_REVIEWS")
      COLLECTED=$((COLLECTED + COUNT))
      echo "   ✓ 第 $PAGE 页获取 $COUNT 条评论（累计 $COLLECTED 条）" >&2
    else
      echo "   ⚠️  第 $PAGE 页解析失败，跳过" >&2
    fi
  fi

  # 翻页
  PAGE=$((PAGE + 1))
  if [[ $PAGE -le $MAX_PAGES && $COLLECTED -lt $LIMIT ]]; then
    # 点击下一页
    NEXT_CLICKED=$(browse click "a[data-hook='pagination-bar-anchor']:last-child" 2>/dev/null && echo "ok" || echo "fail")
    if [[ "$NEXT_CLICKED" == "fail" ]]; then
      # 尝试备用选择器
      browse click "li.a-last a" 2>/dev/null || {
        echo "   ℹ️  已到最后一页" >&2
        break
      }
    fi
    sleep 2
  fi
done

browse stop 2>/dev/null || true

# 合并所有页面的评论
echo "📦 合并评论数据..." >&2

MERGED=$(python3 - <<'PYEOF'
import sys, json, os

reviews_data = os.environ.get('REVIEWS_JSON', '[]')
all_reviews = []

try:
    pages = json.loads(reviews_data)
    for page in pages:
        if isinstance(page, list):
            all_reviews.extend(page)
        elif isinstance(page, dict):
            all_reviews.append(page)
except:
    pass

# 去重（按 body 前100字符）
seen = set()
unique = []
for r in all_reviews:
    key = str(r.get('body', ''))[:100]
    if key not in seen:
        seen.add(key)
        unique.append(r)

print(json.dumps(unique, ensure_ascii=False, indent=2))
PYEOF
)

TOTAL=$(echo "$MERGED" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

echo "✅ 抓取完成，共获取 $TOTAL 条有效评论" >&2

if [[ -n "$OUTPUT_FILE" ]]; then
  echo "$MERGED" > "$OUTPUT_FILE"
  echo "💾 数据已保存到: $OUTPUT_FILE" >&2
else
  echo "$MERGED"
fi
