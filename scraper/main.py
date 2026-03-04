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
ai_lock = asyncio.Lock()

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

def ask_ai(prompt: str, max_tokens: int = 2048) -> str:
    """Call Groq with automatic retry on rate limits."""
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
                wait = (2 ** attempt) * 5 
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

async def get_existing_urls() -> set:
    """Fetch all URLs already stored in MongoDB."""
    cursor = collection.find({}, {"apply_link": 1, "_id": 0})
    existing = set()
    async for doc in cursor:
        if doc.get("apply_link"):
            existing.add(doc["apply_link"])
    print(f"  📋 Found {len(existing)} already-scraped URLs in DB.")
    return existing


# ─────────────────────────── DOMAIN SCORING ──────────────────────

def get_domain_score(url: str) -> int:
    u = url.lower()
    if any(d in u for d in BLACKLISTED_DOMAINS): return 0
    if any(e in u for e in [".gov.in", ".nic.in", ".res.in"]): return 100
    if any(e in u for e in [".ac.in", ".edu.in"]): return 95
    tier2 = ["lfx.linuxfoundation.org", "summerofcode.withgoogle.com",
             "cncf.io", "summerofbitcoin.org", "fossunited.org",
             "jncasr.ac.in", "iitgn.ac.in", "ghc.anitab.org",
             "outreachy.org", "mlh.io", "anitab.org"]
    if any(t in u for t in tier2): return 98
    if any(a in u for a in ["internshala", "unstop", "naukri", "glassdoor", "indeed"]): return 30
    return 50


def is_link_allowed(url: str) -> bool:
    u = url.lower()
    if any(d in u for d in BLACKLISTED_DOMAINS): return False
    if u.endswith((".pdf", ".doc", ".docx", ".zip")): return False
    return True

def generate_queries_with_ai() -> list[dict]:
    print("\n🤖 Gemini is generating search queries...")
    programs_list = "\n".join(f"- {p}" for p in MUST_HAVE_PROGRAMS)

    prompt = f"""You are helping find tech fellowships for Indian CS students in Bangalore.

For each program below, generate exactly 3 Google search queries:
1. One targeting the official application page
2. One targeting 2026 or 2027 deadlines
3. One targeting eligibility for Indian students
Programs:
{programs_list}

Also suggest 15 additional relevant programs
Generate 3 queries each for the additional programs too.

Return ONLY this JSON with no extra text or markdown:
{{
  "must_have": [
    {{"name": "Program Name", "queries": ["query 1", "query 2"], "official_domain_hint": "domain.com"}}
  ],
  "additional": [
    {{"name": "Program Name", "queries": ["query 1", "query 2"], "official_domain_hint": "domain.com"}}
  ]
}}"""

    raw = ask_ai(prompt, max_tokens=3000)
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

async def serper_search(query: str, client: httpx.AsyncClient) -> list[str]:
    headers = {"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"}
    try:
        resp = await client.post(
            "https://google.serper.dev/search",
            json={"q": query, "gl": "in", "num": 20},
            headers=headers, timeout=15,
        )
        results = resp.json().get("organic", [])
        return [
            r.get("link", "") for r in results
            if is_link_allowed(r.get("link", ""))
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
                await asyncio.sleep(0.5)
    scored.sort(key=lambda x: x[0], reverse=True)
    print(f"\n  📦 Collected {len(scored)} unique links.\n")
    return scored

def deduplicate_by_domain(scored_links: list[tuple[int, str]], max_per_domain: int = 1) -> list[tuple[int, str]]:
    """
    Keep only the top N URLs per domain.
    Prevents 5 links from summerofbitcoin.org, 4 from cncf.io etc.
    """
    from urllib.parse import urlparse
    domain_count = {}
    deduped = []

    for score, url in scored_links:
        domain = urlparse(url).netloc.replace("www.", "")
        count = domain_count.get(domain, 0)
        if count < max_per_domain:
            deduped.append((score, url))
            domain_count[domain] = count + 1

    print(f"  🧹 Deduplicated: {len(scored_links)} → {len(deduped)} links (max {max_per_domain} per domain)\n")
    return deduped

def ai_relevance_check(links: list[str]) -> list[str]:
    if not links:
        return []

    print(f"🤖 AI filtering {len(links)} links in batches...")
    kept = []
    batch_size = 25

    for i in range(0, len(links), batch_size):
        batch = links[i:i + batch_size]
        numbered = "\n".join(f"{i+1}. {url}" for i, url in enumerate(batch))

        prompt = f"""You are filtering URLs for a fellowship/internship tracker for Indian CS students.

Be LENIENT — when in doubt, KEEP the link.

KEEP if the URL could lead to:
- An official fellowship, internship, mentorship, or scholarship page
- A program timeline, eligibility, or how-to-apply page
- A research internship at any university or institute
- A blog post or announcement FROM the official program org (e.g. cncf.io/blog)

SKIP ONLY if clearly:
- A job aggregator listing (Naukri, Internshala, Unstop, Glassdoor, Indeed)
- Pure social media post
- Completely unrelated to fellowships/internships

Return ONLY a JSON array of numbers to keep. Example: [1, 2, 4, 5, 7]
No explanation, no markdown.

URLs:
{numbered}"""

        raw = ask_ai(prompt, max_tokens=200)
        if not raw:
            kept.extend(batch)
            continue

        parsed = safe_parse_json(raw)
        if not isinstance(parsed, list):
            kept.extend(batch)
            continue

        batch_kept = [batch[i-1] for i in parsed if isinstance(i, int) and 1 <= i <= len(batch)]
        kept.extend(batch_kept)
        print(f"  Batch {i//batch_size + 1}: kept {len(batch_kept)}/{len(batch)}")

    print(f"  ✅ Total kept: {len(kept)} / {len(links)} links.\n")
    return kept

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

    raw = ask_ai(prompt, max_tokens=800)
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

            async with ai_lock:
                details = ai_extract_details(result.markdown, link)
                await asyncio.sleep(1)
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

async def ensure_indexes():
    await collection.create_index("apply_link", unique=True)
    await collection.create_index("last_updated")

async def ping_mongo():
    await mongo_client.admin.command("ping")

async def main():
    await ping_mongo()
    await ensure_indexes()
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

    scored_links  = deduplicate_by_domain(scored_links, max_per_domain=2)
    existing_urls = await get_existing_urls()
    
    # Remove already-scraped links before AI filtering
    fresh_links = [(sc, url) for sc, url in scored_links if url not in existing_urls]
    print(f"  🆕 {len(fresh_links)} new links to process ({len(scored_links) - len(fresh_links)} already in DB, skipping)\n")

    top_urls   = [url for _, url in fresh_links[:150]]
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
        tasks = [
            process_link(crawler, run_cfg, url, score_map.get(url, 50), semaphore)
            for url in final_urls
        ]
        await asyncio.gather(*tasks)

    print("\n🎉 Done! Database updated.")


if __name__ == "__main__":
    asyncio.run(main())