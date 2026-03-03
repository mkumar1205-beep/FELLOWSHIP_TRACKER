"""
AI-Powered Fellowship & Internship Scraper
==========================================
"""

import os
import re
import json
import asyncio
import time
from pathlib import Path
from datetime import datetime, timezone

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from groq import Groq

# ─────────────────────────── ENV SETUP ───────────────────────────
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

MONGO_URL  = os.getenv("MONGO_URL")
SERPER_KEY = os.getenv("SERPER_API_KEY")
GROQ_KEY    = os.getenv("GROQ_API_KEY")

mongo_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
db           = mongo_client.fellowship_tracker
collection   = db.fellowships

groq_client = Groq(api_key=GROQ_KEY)

# ── CONFIRMED WORKING MODEL from your list() output ──────────────
GROQ_MODEL  = "llama-3.3-70b-versatile"

STUDENT_PROFILE = {
    "location": "Bangalore, Karnataka, India",
    "education": "B.Tech / B.E. (undergraduate) or M.Tech (postgraduate)",
    "domains": ["computer science", "software engineering", "AI/ML", "open source", "research"],
    "year": "2025-2026 cycle",
}

MUST_HAVE_PROGRAMS = [
    "LFX Mentorship (Linux Foundation)",
    "GSoC - Google Summer of Code",
    "DWoC - Delta Winter of Code",
    "KWoC - Kharagpur Winter of Code",
    "CNCF Mentorship Program",
    "Summer of Bitcoin",
    "FOSS United Fellowship",
    "Reliance Foundation Undergraduate Scholarship",
    "Grace Hopper Celebration (GHC) Scholarship",
    "LIFT Fellowship",
    "IIT Research Internship (SURGE / SPARK / SRF)",
    "SRFP - JNCASR Summer Research Fellowship",
    "SRIP - IIT Gandhinagar Summer Research Internship",
    "MSR - Microsoft Research India Fellowship",
]

BLACKLISTED_DOMAINS = {
    "instagram.com", "facebook.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "pinterest.com", "reddit.com",
    "quora.com", "medium.com", "t.co", "bit.ly",
}

# ─────────────────────────── GEMINI WRAPPER ──────────────────────

def ask_gemini(prompt: str, max_tokens: int = 2048) -> str:
    """Call Groq — fast free tier, no aggressive sleep needed."""
    for attempt in range(4):
        try:
            resp = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.2,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
                wait = (2 ** attempt) * 5  # 5, 10, 20, 40s — much shorter than Gemini
                print(f"  ⏳ Rate limited. Waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"  ❌ Groq error: {err[:300]}")
                return ""
    return ""


def safe_parse_json(raw: str):
    try:
        cleaned = re.sub(r"```json|```", "", raw).strip()
        match = re.search(r'(\{.*\}|\[.*\])', cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return None


# ─────────────────────────── DOMAIN SCORING ──────────────────────

def get_domain_score(url: str) -> int:
    u = url.lower()
    if any(d in u for d in BLACKLISTED_DOMAINS): return 0
    if any(e in u for e in [".gov.in", ".nic.in", ".res.in"]): return 100
    if any(e in u for e in [".ac.in", ".edu.in"]): return 95
    tier2 = ["lfx.linuxfoundation.org", "summerofcode.withgoogle.com",
             "cncf.io", "summerofbitcoin.org", "fossunited.org",
             "jncasr.ac.in", "iitgn.ac.in", "ghc.anitab.org"]
    if any(t in u for t in tier2): return 98
    if any(a in u for a in ["internshala", "unstop", "naukri", "glassdoor", "indeed"]): return 30
    return 50


def is_link_allowed(url: str) -> bool:
    u = url.lower()
    if any(d in u for d in BLACKLISTED_DOMAINS): return False
    if u.endswith((".pdf", ".doc", ".docx", ".zip")): return False
    return True


# ─────────────────────────── STEP 1: QUERY GENERATION ────────────

def generate_queries_with_ai() -> list[dict]:
    print("\n🤖 Gemini is generating search queries...")
    programs_list = "\n".join(f"- {p}" for p in MUST_HAVE_PROGRAMS)

    prompt = f"""You are helping find tech fellowships for Indian CS students in Bangalore.

For each program below, generate exactly 2 Google search queries:
1. One targeting the official application page
2. One targeting 2025 or 2026 deadlines

Programs:
{programs_list}

Also suggest 5 additional relevant programs for: {json.dumps(STUDENT_PROFILE)}

Return ONLY this JSON with no extra text or markdown:
{{
  "must_have": [
    {{"name": "Program Name", "queries": ["query 1", "query 2"], "official_domain_hint": "domain.com"}}
  ],
  "additional": [
    {{"name": "Program Name", "queries": ["query 1", "query 2"], "official_domain_hint": "domain.com"}}
  ]
}}"""

    raw = ask_gemini(prompt, max_tokens=3000)
    if not raw:
        print("  ⚠️  Gemini unavailable, using fallback queries.")
        return [{"name": p, "queries": [f"{p} 2026 official application", f"{p} deadline 2026"]}
                for p in MUST_HAVE_PROGRAMS]

    data = safe_parse_json(raw)
    if not data or not isinstance(data, dict):
        print("  ⚠️  JSON parse failed, using fallback queries.")
        return [{"name": p, "queries": [f"{p} 2026 official application", f"{p} deadline 2026"]}
                for p in MUST_HAVE_PROGRAMS]

    combined = data.get("must_have", []) + data.get("additional", [])
    print(f"  ✅ Generated queries for {len(combined)} programs.")
    return combined


# ─────────────────────────── STEP 2: SERPER SEARCH ───────────────

async def serper_search(query: str, client: httpx.AsyncClient) -> list[str]:
    headers = {"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"}
    try:
        resp = await client.post(
            "https://google.serper.dev/search",
            json={"q": query, "gl": "in", "num": 10},
            headers=headers, timeout=15,
        )
        results = resp.json().get("organic", [])
        return [
            r.get("link", "") for r in results
            if is_link_allowed(r.get("link", "")) and any(
                k in (r.get("title","") + r.get("snippet","")).lower()
                for k in ["intern", "fellow", "scholar", "mentorship", "apply", "stipend"]
            )
        ]
    except Exception as e:
        print(f"  ⚠️  Serper error: {e}")
        return []


async def collect_links(programs: list[dict]) -> list[tuple[int, str]]:
    seen, scored = set(), []
    async with httpx.AsyncClient() as http:
        for prog in programs:
            print(f"  🔍 Searching: {prog['name']}")
            for query in prog.get("queries", []):
                for link in await serper_search(query, http):
                    if link not in seen:
                        seen.add(link)
                        score = get_domain_score(link)
                        hint = prog.get("official_domain_hint", "")
                        if hint and hint.lower() in link.lower():
                            score = min(score + 15, 100)
                        scored.append((score, link))
                await asyncio.sleep(1.0)
    scored.sort(key=lambda x: x[0], reverse=True)
    print(f"\n  📦 Collected {len(scored)} unique links.\n")
    return scored


# ─────────────────────────── STEP 3: AI RELEVANCE FILTER ─────────

def ai_relevance_check(links: list[str]) -> list[str]:
    if not links:
        return []
    print(f"🤖 Gemini filtering {len(links)} links...")
    numbered = "\n".join(f"{i+1}. {url}" for i, url in enumerate(links))

    prompt = f"""Filter these URLs for a fellowship tracker for Indian CS students.

KEEP if: official fellowship/internship application or eligibility page.
SKIP if: social media, news article, blog, job aggregator (Naukri, Internshala, Unstop).

Return ONLY a JSON array of numbers to keep. Example: [1, 3, 5]
No explanation, no markdown.

URLs:
{numbered}"""

    raw = ask_gemini(prompt, max_tokens=300)
    if not raw:
        return links

    parsed = safe_parse_json(raw)
    if not isinstance(parsed, list):
        return links

    kept = [links[i-1] for i in parsed if isinstance(i, int) and 1 <= i <= len(links)]
    print(f"  ✅ Kept {len(kept)} / {len(links)} links.\n")
    return kept


# ─────────────────────────── STEP 4: EXTRACT + STORE ─────────────

def ai_extract_details(page_text: str, url: str) -> dict:
    prompt = f"""Extract data from this fellowship webpage.

URL: {url}
Content: {page_text[:5000]}

Return ONLY this JSON with no markdown:
{{
  "name": "Full program name",
  "organization": "Sponsoring org",
  "deadline": "YYYY-MM-DD or Check Website or Rolling",
  "stipend": "Amount or Unpaid or Not Specified",
  "eligibility": "1-2 sentence summary",
  "mode": "Remote or In-Person or Hybrid",
  "is_open": true or false,
  "tags": ["tag1", "tag2"]
}}"""

    raw = ask_gemini(prompt, max_tokens=800)
    if not raw:
        return {}
    result = safe_parse_json(raw)
    return result if isinstance(result, dict) else {}


async def process_link(crawler, run_cfg, link: str, score: int, semaphore: asyncio.Semaphore):
    async with semaphore:
        try:
            result = await asyncio.wait_for(
                crawler.arun(url=link, config=run_cfg), timeout=60.0
            )
            if not result.success or len(result.markdown) < 300:
                return
            if score < 80 and result.markdown.count("](") > 80:
                print(f"  🗑️  Skipping aggregator: {link}")
                return

            details = ai_extract_details(result.markdown, link)
            if not details:
                return

            is_open = details.get("is_open")
            if isinstance(is_open, str):
                is_open = is_open.lower() in ["true", "open", "yes"]
            else:
                is_open = bool(is_open)

            doc = {
                "name":         details.get("name") or "Unknown Opportunity",
                "organization": details.get("organization"),
                "deadline":     details.get("deadline", "Check Website"),
                "stipend":      details.get("stipend"),
                "eligibility":  details.get("eligibility"),
                "mode":         details.get("mode"),
                "is_open":      is_open,
                "tags":         details.get("tags", []),
                "apply_link":   link,
                "trust_score":  score,
                "last_updated": datetime.now(timezone.utc),
            }
            await collection.update_one({"apply_link": link}, {"$set": doc}, upsert=True)
            print(f"  ✅ Saved: {doc['name']}  |  Deadline: {doc['deadline']}")

        except asyncio.TimeoutError:
            print(f"  ⏱️  Timeout: {link}")
        except Exception as e:
            print(f"  ❌ Error ({link}): {e}")


# ─────────────────────────── MAIN ────────────────────────────────

async def main():
    print("=" * 60)
    print("  FELLOWSHIP TRACKER — AI MODE")
    print(f"  Model: {GROQ_MODEL}")
    print("=" * 60)

    programs     = generate_queries_with_ai()
    print("\n📡 Running web searches...\n")
    scored_links = await collect_links(programs)

    if not scored_links:
        print("❌ No links found. Check SERPER_API_KEY in .env")
        return

    top_urls   = [url for _, url in scored_links[:40]]
    final_urls = ai_relevance_check(top_urls)
    score_map  = {url: sc for sc, url in scored_links}

    print(f"\n🚀 Crawling {len(final_urls)} pages...\n")
    semaphore = asyncio.Semaphore(3)
    run_cfg   = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        exclude_all_images=True,
        page_timeout=60000,
        wait_for="body",
        delay_before_return_html=2.0,
    )

    async with AsyncWebCrawler() as crawler:
        for url in final_urls:
            await process_link(crawler, run_cfg, url, score_map.get(url, 50), semaphore)

    print("\n🎉 Done! Database updated.")


if __name__ == "__main__":
    asyncio.run(main())