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


def to_korean(text):
    """Google 번역은 에러를 던지지 않고 에러 페이지 텍스트를 반환할 때가 있어,
    결과에 한글이 있는지 확인하고 실패 시 원문(영문)을 유지합니다."""
    for _ in range(2):
        try:
            ko = GoogleTranslator(source='auto', target='ko').translate(text)
            if any('가' <= ch <= '힣' for ch in (ko or "")):
                return ko
        except Exception:
            pass
        time.sleep(1.5)
    return text


def send_discord_message(repos, category):
    """단일 웹후크로 카테고리 테마 메시지를 전송합니다. DRY_RUN=1이면 출력만 합니다."""
    now = datetime.now(KST)
    content = f"## {category['emoji']} 오늘의 {category['label']} 트렌드 ({now.strftime('%Y-%m-%d')} {WEEKDAY_NAMES[now.weekday()]})\n"
    for idx, repo in enumerate(repos, 1):
        content += f"**{idx}. {repo['name']}** (⭐️`{repo['stars']}` | 🍴`{repo['forks']}`)\n"
        content += f"> {to_korean(repo['desc'])}\n"
        content += f"- <{repo['link']}>\n\n"

    if os.environ.get("DRY_RUN") == "1":
        print(f"[DRY RUN] [{category['label']}] 메시지 미리보기:")
        print(content)
        return

    webhook_url = os.environ.get("WEBHOOK_URL")
    if not webhook_url:
        print(f"⚠️ [{category['label']}] 전송 실패: WEBHOOK_URL이 설정되지 않았습니다.")
        return

    requests.post(webhook_url, json={"content": content})
    print(f"✅ [{category['label']}] 전송 완료")
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
