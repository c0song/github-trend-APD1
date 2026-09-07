import os
import re
import sys
import time
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from datetime import datetime, timezone, timedelta

# 이모지·한글 출력을 위해 콘솔 인코딩을 UTF-8로 고정합니다 (로컬 CP949 대비).
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --- 설정: 요일별 테마 (0=월, 1=화, 2=수, 3=목, 4=금) ---
# GitHub Trending은 언어 필터만 지원하므로, 각 테마의 시드 언어 trending을 크롤링한 뒤
# 저장소 이름+설명에서 키워드로 해당 카테고리에 맞는 저장소만 걸러냅니다.
CATEGORIES = {
    0: {
        "key": "agents", "label": "AI 에이전트·MCP", "emoji": "🧩",
        "languages": ["python", "typescript", "rust"],
        "keywords": ["claude", "claude-code", "mcp", "anthropic", "agent", "agents", "skill", "skills", "langchain", "autogen", "crewai", "a2a"],
    },
    1: {
        "key": "frontend", "label": "프론트엔드", "emoji": "🎨",
        "languages": ["javascript", "typescript"],
        "keywords": ["react", "vue", "next.js", "nextjs", "nuxt", "svelte", "astro", "angular", "tailwind", "css", "vite", "frontend", "front-end", "ui", "component", "web", "browser", "user interface", "remix", "react native", "react-native", "expo", "flutter"],
    },
    2: {
        "key": "backend", "label": "백엔드", "emoji": "⚙️",
        "languages": ["python", "javascript", "go"],
        "keywords": ["fastapi", "node", "nodejs", "express", "nest", "django", "flask", "api", "backend", "server", "microservice", "graphql", "grpc"],
    },
    3: {
        "key": "ml", "label": "머신러닝·DB", "emoji": "🧠",
        "languages": ["python", "rust", "go", "c++"],
        "keywords": ["machine learning", "deep learning", "pytorch", "tensorflow", "transformer", "diffusion", "neural", "dataset", "training", "vision", "nlp", "reinforcement", "yolo", "postgres", "sql", "database", "sqlite", "redis", "mongo", "duckdb", "supabase", "vector", "nosql", "orm", "migration", "mysql", "clickhouse", "influxdb"],
    },
    4: {
        "key": "llm", "label": "LLM 인프라·로컬 LLM", "emoji": "⚡",
        "languages": ["python", "rust"],
        "keywords": ["llm", "inference", "vllm", "ollama", "quantization", "fine-tuning", "finetune", "serving", "embedding", "weights", "gguf", "lora", "rag"],
    },
}
KST = timezone(timedelta(hours=9))
WEEKDAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 요일 테마별 임베드 좌측 색상
THEME_COLORS = {
    "agents": 0x9B59B6,
    "frontend": 0xF1C40F,
    "backend": 0x3498DB,
    "ml": 0x2ECC71,
    "llm": 0x1ABC9C,
}

# 키워드 정규식 미리 컴파일. \b 단어 경계로 'html' 안의 'ml' 같은 오매칭을 방지합니다.
for _cat in CATEGORIES.values():
    _cat["pattern"] = re.compile("|".join(rf"\b{re.escape(k)}\b" for k in _cat["keywords"]), re.IGNORECASE)


def select_categories():
    """실행 대상 카테고리를 고릅니다. TREND_CATEGORY(agents/frontend/backend/ml/llm/all)로
    오늘 요일과 무관하게 강제 지정할 수 있습니다."""
    override = os.environ.get("TREND_CATEGORY", "").strip().lower()
    if override:
        if override == "all":
            return list(CATEGORIES.values())
        by_key = {c["key"]: c for c in CATEGORIES.values()}
        if override not in by_key:
            valid = "/".join(by_key) + "/all"
            print(f"⚠️ 알 수 없는 TREND_CATEGORY='{override}' (가능: {valid})")
            return []
        return [by_key[override]]

    weekday = datetime.now(KST).weekday()
    if weekday >= 5:
        print(f"오늘은 {WEEKDAY_NAMES[weekday]}요일 — 발송할 테마가 없습니다. (테스트: TREND_CATEGORY=ai|frontend|backend|db|ml|all)")
        return []
    return [CATEGORIES[weekday]]


def get_github_trends(language):
    """특정 언어의 GitHub Trending 저장소를 크롤링합니다."""
    url = f"https://github.com/trending/{language}?since=daily"
    print(f"[{language}] 데이터 수집 중...")

    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200: return []

        soup = BeautifulSoup(response.text, 'html.parser')
        repos = []

        # 필터링 전 후보 풀이므로 페이지 전체(~25개)를 수집합니다.
        for item in soup.select('article.Box-row')[:25]:
            try:
                h1 = item.select_one('h2.h3 a')
                name = h1.text.strip().replace('\n', '').replace(' ', '')
                link = f"https://github.com{h1['href']}"

                stats = item.select('a.Link--muted')
                stars = stats[0].text.strip() if len(stats) > 0 else "0"
                forks = stats[1].text.strip() if len(stats) > 1 else "0"

                desc_tag = item.select_one('p.col-9')
                description = desc_tag.text.strip() if desc_tag else "No description."

                repos.append({'name': name, 'link': link, 'stars': stars, 'forks': forks, 'desc': description})
            except: continue
        return repos
    except Exception as e:
        print(f"[{language}] 에러: {e}")
        return []


def filter_by_category(pool, category):
    """시드 언어 후보 중 키워드에 매칭되는 저장소를 최대 5개까지 반환합니다."""
    seen, matched = set(), []
    for lang in category["languages"]:
        for repo in pool.get(lang, []):
            if repo["link"] in seen:
                continue
            seen.add(repo["link"])
            if category["pattern"].search(f"{repo['name']} {repo['desc']}"):
                matched.append(repo)
                if len(matched) == 5:
                    return matched
    return matched


def has_hangul(s):
    return any('가' <= ch <= '힣' for ch in (s or ""))


def to_korean(text):
    """Google 번역은 에러를 던지지 않고 에러 페이지 텍스트를 반환할 때가 있어,
    결과에 한글이 있는지 확인하고 실패 시 원문(영문)을 유지합니다."""
    for _ in range(2):
        try:
            ko = GoogleTranslator(source='auto', target='ko').translate(text)
            if has_hangul(ko):
                return ko
        except Exception:
            pass
        time.sleep(1.5)
    return text


def translate_batch(texts):
    """Gemini 무료 API로 설명을 일괄 번역합니다. 키가 없거나 실패하면
    GoogleTranslator 개별 번역으로, 그것도 실패하면 원문으로 폴백합니다."""
    if not texts:
        return []

    if GEMINI_API_KEY:
        numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(texts, 1))
        prompt = (
            "다음은 GitHub 인기 저장소 설명 목록입니다. 각 항목을 개발자가 읽기 자연스러운 한국어로 번역하세요.\n"
            "규칙: 제품명과 기술 용어(MCP, RAG, LLM, 디퓨전 등)는 원래 널리 쓰이는 표현을 유지하고, "
            "설명이 비어 있으면 '설명 없음'으로 출력하며, 마크다운 없이 '1. 번역문' 형식으로 한 줄씩만 출력하세요.\n\n"
            + numbered
        )
        parsed = None
        for model in ("gemini-2.5-flash", "gemini-2.0-flash"):
            try:
                resp = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    params={"key": GEMINI_API_KEY},
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    timeout=30,
                )
                if resp.status_code == 404:
                    continue  # 모델 없음 → 다음 후보
                resp.raise_for_status()
                out = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                by_num = {}
                for line in out.splitlines():
                    m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
                    if m:
                        by_num[int(m.group(1))] = m.group(2).strip().strip('*').strip()
                parsed = [by_num.get(i, "") for i in range(1, len(texts) + 1)]
                break
            except Exception as e:
                print(f"⚠️ [{model}] Gemini 번역 실패: {e}")
        if parsed:
            final = [r if has_hangul(r) else to_korean(t) for r, t in zip(parsed, texts)]
            print(f"🌐 Gemini 번역 {sum(1 for r in final if has_hangul(r))}/{len(texts)} 적용")
            return final

    print("🌐 GoogleTranslator 사용")
    return [to_korean(t) for t in texts]


def send_discord_message(repos, category):
    """단일 웹후크로 카테고리 테마 임베드를 전송합니다. DRY_RUN=1이면 출력만 합니다."""
    descs = translate_batch([r["desc"] for r in repos])

    now = datetime.now(KST)
    fields = []
    for idx, (repo, desc) in enumerate(zip(repos, descs), 1):
        value = f"⭐️ {repo['stars']} · 🍴 {repo['forks']}\n{desc}\n[GitHub에서 보기]({repo['link']})"
        if len(value) > 1024:
            value = value[:1021] + "..."
        fields.append({"name": f"{idx}. {repo['name']}", "value": value})

    embed = {
        "title": f"{category['emoji']} 오늘의 {category['label']} 트렌드",
        "description": f"GitHub Daily Trending · {now.strftime('%Y-%m-%d')} ({WEEKDAY_NAMES[now.weekday()]})",
        "color": THEME_COLORS.get(category["key"], 0x95A5A6),
        "fields": fields,
        "footer": {"text": "GitHub Trend Bot"},
        "timestamp": now.isoformat(),
    }

    if os.environ.get("DRY_RUN") == "1":
        print(f"[DRY RUN] [{category['label']}] 임베드 미리보기:")
        print(f"# {embed['title']}  |  {embed['description']}  |  color=#{embed['color']:06x}")
        for f in fields:
            print(f"**{f['name']}**")
            print(f["value"])
            print()
        return

    webhook_url = os.environ.get("WEBHOOK_URL")
    if not webhook_url:
        print(f"⚠️ [{category['label']}] 전송 실패: WEBHOOK_URL이 설정되지 않았습니다.")
        return

    resp = requests.post(webhook_url, json={"embeds": [embed]})
    if resp.status_code in (200, 204):
        print(f"✅ [{category['label']}] 전송 완료")
    else:
        print(f"⚠️ [{category['label']}] 전송 실패: HTTP {resp.status_code} {resp.text[:200]}")
    time.sleep(1)

if __name__ == "__main__":
    print("=== GitHub Trend Bot 시작 ===")

    categories = select_categories()

    # 시드 언어는 여러 카테고리가 겹쳐도 1회씩만 크롤링합니다.
    pool = {}
    for lang in sorted({lang for cat in categories for lang in cat["languages"]}):
        pool[lang] = get_github_trends(lang)

    for cat in categories:
        repos = filter_by_category(pool, cat)
        if repos:
            send_discord_message(repos, cat)
        else:
            print(f"⚠️ [{cat['label']}] 오늘 매칭된 저장소가 없어 전송을 생략합니다.")

    print("=== 모든 작업 완료 ===")
