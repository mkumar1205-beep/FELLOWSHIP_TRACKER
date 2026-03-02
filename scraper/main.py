"""
AI-Powered Fellowship & Internship Scraper
==========================================
- Claude generates search queries dynamically (no hardcoded list)
- Must-have programs are always guaranteed
- AI extracts deadline, eligibility, stipend from page content
- Smart multi-tier link filtering (domain score + AI relevance check)
- Stores enriched data in MongoDB
"""

import os
import re
import json
import random
import asyncio
import hashlib
from pathlib import Path
from datetime import datetime

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from google import genai
from google.genai import types

# ─────────────────────────── ENV SETUP ───────────────────────────
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

MONGO_URL   = os.getenv("MONGO_URL")
SERPER_KEY  = os.getenv("SERPER_API_KEY")
GEMINI_KEY  = os.getenv("GEMINI_API_KEY")

mongo_client = AsyncIOMotorClient(MONGO_URL)
db           = mongo_client.fellowship_tracker
collection   = db.fellowships

gemini = genai.Client(api_key=GEMINI_KEY)

# To see all models available to your key, uncomment and run once:
# for m in gemini.models.list(): print(m.name)

def ask_gemini(prompt: str, max_tokens: int = 4000) -> str:
    """Call Gemini and return the text response."""
    resp = gemini.models.generate_content(
        model="gemini-2.0-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=max_tokens,
        ),
    )
    return resp.text.strip()

# ─────────────────────── STUDENT PROFILE ──────────────────────────
# Tweak this to change what Claude targets
STUDENT_PROFILE = {
    "location": "Bangalore, Karnataka, India",
    "education": "B.Tech / B.E. (undergraduate) or M.Tech (postgraduate)",
    "domains": ["computer science", "software engineering", "AI/ML", "open source", "research"],
    "year": "2025-2026 cycle",
}

# ── Must-have programs — Claude will ALWAYS generate queries for these ──
MUST_HAVE_PROGRAMS = [
    "LFX Mentorship (Linux Foundation)",
    "GSoC - Google Summer of Code",
    "DWoC - Delta Winter of Code",
    "KWoC - Kharagpur Winter of Code",
    "CNCF Mentorship Program",
    "Julia Season of Contributions",
    "Summer of Bitcoin",
    "Reliance Foundation Undergraduate Scholarship",
    "Grace Hopper Celebration (GHC) Scholarship",
    "LIFT Fellowship",
    "FOSS United Fellowship",
    "IIT Research Internship (SURGE / SPARK / SRF)",
    "SRFP - JNCASR Summer Research Fellowship",
    "SRIP - IIT Gandhinagar Summer Research Internship",
    "MSR - Microsoft Research India Fellowship",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Firefox/110.0",
]

# ─────────────────── BLACKLIST / SCORING ─────────────────────────
BLACKLISTED_DOMAINS = {
    "instagram.com", "facebook.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "pinterest.com", "reddit.com",
    "quora.com", "medium.com", "t.co", "bit.ly", "tinyurl.com",
    "telegram.me", "t.me", "whatsapp.com", "snapchat.com",
}

def get_domain_score(url: str) -> int:
    """
    Returns trust score 0–100.
    Higher = more likely an official/authoritative source.
    """
    u = url.lower()

    # Instant kill — social / link-shorteners
    if any(d in u for d in BLACKLISTED_DOMAINS):
        return 0

    # Tier 1 — Official Indian govt & research
    if any(e in u for e in [".gov.in", ".nic.in", ".res.in"]):
        return 100
    # Tier 1 — Indian academic
    if any(e in u for e in [".ac.in", ".edu.in"]):
        return 95
    # Tier 2 — Known global orgs / fellowship portals
    tier2 = [
        "summerofcode.withgoogle.com", "lfx.linuxfoundation.org",
        "cncf.io", "summerofbitcoin.org", "fossunited.org",
        "ashoka.edu.in", "microsoftresearch", "research.google",
        "aicte-india.org", "internship.aicte-india.org",
        "serb.gov.in", "jncasr.ac.in", "iitgn.ac.in",
        "ghc.anitab.org", "outreachy.org", "mlh.io",
    ]
    if any(t in u for t in tier2):
        return 98
    # Tier 3 — Reputable tech companies
    tier3 = [
        "google.com", "microsoft.com", "amazon.jobs", "apple.com",
        "meta.com", "qualcomm.com", "nvidia.com", "intel.com",
        "samsung.com", "ibm.com", "redhat.com", "cisco.com",
        "tcs.com", "infosys.com", "wipro.com", "hcltech.com",
        "zerodha.com", "razorpay.com", "phonepe.com", "cred.club",
        "zomato.com", "swiggy.in",
    ]
    if any(t in u for t in tier3):
        return 85
    # Generic link aggregators / job boards — lower trust
    aggregators = ["internshala", "unstop", "naukri", "glassdoor", "indeed", "angellist", "wellfound"]
    if any(a in u for a in aggregators):
        return 40

    return 50  # Unknown — process but flag


def is_link_allowed(url: str) -> bool:
    """Quick pre-filter before scoring."""
    u = url.lower()
    if any(d in u for d in BLACKLISTED_DOMAINS):
        return False
    if u.endswith((".pdf", ".doc", ".docx", ".xls", ".zip")):
        return False
    return True


# ──────────────────── STEP 1 — AI QUERY GENERATION ───────────────────

def generate_queries_with_ai() -> list[dict]:
    """
    Calls Gemini to generate targeted search queries per program.
    Returns list of { name, queries[], official_domain_hint }
    """
    print("\n🤖 Asking Gemini to generate search queries...\n")

    programs_block = "\n".join(f"- {p}" for p in MUST_HAVE_PROGRAMS)
    profile_block  = json.dumps(STUDENT_PROFILE, indent=2)

    prompt = f"""
You are an expert at discovering tech fellowships, research internships, and open-source
mentorship programs for Indian students.

Student Profile:
{profile_block}

Your task has TWO parts:

PART A — For each program in the MUST-HAVE list below, generate exactly 3 search queries:
  1. A query targeting the OFFICIAL application page or portal
  2. A query targeting 2025 or 2026 deadlines / dates / timeline
  3. A query targeting eligibility criteria for Indian / Bangalore students

PART B — Suggest 10 ADDITIONAL relevant programs this student profile might not know about
(open-source, research, fellowships, stipended internships in India or remote). 
Generate 2 queries each.

MUST-HAVE PROGRAMS:
{programs_block}

RULES:
- Queries must be specific and Google-ready (3–8 words each)
- Prefer official domains over aggregators
- Include year (2025 or 2026) in deadline queries
- Do NOT include LinkedIn, Instagram, YouTube in queries

Return ONLY valid JSON (no markdown, no explanation):
{{
  "must_have": [
    {{
      "name": "Program Name",
      "queries": ["query1", "query2", "query3"],
      "official_domain_hint": "domain.com"
    }}
  ],
  "additional": [
    {{
      "name": "Program Name",
      "queries": ["query1", "query2"],
      "official_domain_hint": "domain.com"
    }}
  ]
}}
"""

    raw = ask_gemini(prompt, max_tokens=4000)
    raw = re.sub(r"```json|```", "", raw).strip()

    data     = json.loads(raw)
    combined = data.get("must_have", []) + data.get("additional", [])
    print(f"✅ Gemini generated queries for {len(combined)} programs "
          f"({len(data.get('must_have',[]))} must-have + "
          f"{len(data.get('additional',[]))} additional)\n")
    return combined


# ──────────────────── STEP 2 — WEB SEARCH ────────────────────────

async def serper_search(query: str, client: httpx.AsyncClient, num: int = 10) -> list[str]:
    """Search via Serper API, return list of scored (score, url) tuples."""
    payload = {"q": query, "gl": "in", "num": num}
    headers = {"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"}
    try:
        resp = await client.post(
            "https://google.serper.dev/search",
            json=payload,
            headers=headers,
            timeout=15,
        )
        results = resp.json().get("organic", [])
        links = []
        for r in results:
            link    = r.get("link", "")
            snippet = (r.get("title", "") + " " + r.get("snippet", "")).lower()
            # Only keep if context looks relevant
            if is_link_allowed(link) and any(
                k in snippet for k in ["intern", "fellow", "scholar", "mentorship",
                                       "research", "stipend", "apply", "program"]
            ):
                links.append(link)
        return links
    except Exception as e:
        print(f"  ⚠️  Search error for '{query}': {e}")
        return []


async def collect_all_links(programs: list[dict]) -> list[tuple[int, str]]:
    """
    Runs all queries, deduplicates, scores and returns sorted list.
    Guarantees must-have program pages get top priority.
    """
    seen:   set              = set()
    scored: list[tuple[int,str]] = []

    async with httpx.AsyncClient() as http:
        for prog in programs:
            print(f"🔍  Searching: {prog['name']}")
            # Boost score for must-have programs
            is_must_have = any(
                prog["name"].lower() in m.lower() or m.lower() in prog["name"].lower()
                for m in MUST_HAVE_PROGRAMS
            )
            base_boost = 10 if is_must_have else 0

            for query in prog.get("queries", []):
                links = await serper_search(query, http)
                for link in links:
                    if link not in seen:
                        seen.add(link)
                        score = get_domain_score(link) + base_boost
                        # Extra boost if the link is on the program's known domain
                        hint = prog.get("official_domain_hint", "")
                        if hint and hint.lower() in link.lower():
                            score += 15
                        scored.append((min(score, 100), link))
                await asyncio.sleep(0.4)  # be polite to Serper

    # Sort highest trust first
    scored.sort(key=lambda x: x[0], reverse=True)
    print(f"\n📦  Total unique links collected: {len(scored)}\n")
    return scored


# ──────────────────── STEP 3 — AI RELEVANCE FILTER ───────────────

async def ai_relevance_check(links_batch: list[str]) -> list[str]:
    """
    Ask Claude to filter a batch of URLs and keep only real opportunity pages.
    Runs in batches of 30 to stay within token limits.
    """
    kept = []
    batch_size = 30

    for i in range(0, len(links_batch), batch_size):
        batch = links_batch[i : i + batch_size]
        urls_block = "\n".join(f"{j+1}. {u}" for j, u in enumerate(batch))

        prompt = f"""
You are filtering URLs for a fellowship/internship tracker for Indian CS students.

Student context: {json.dumps(STUDENT_PROFILE)}

Below are {len(batch)} URLs found via web search. 
For each URL, decide: KEEP or SKIP.

KEEP if the URL likely leads to:
- An official fellowship / internship / mentorship program page
- An application portal or eligibility page
- A research internship at an Indian institute or global remote program

SKIP if the URL is:
- A social media post, news article, or blog that just mentions the program
- A job aggregator (Naukri, Internshala, Unstop, Glassdoor, Indeed) — SKIP these
- Clearly unrelated to tech/CS fellowships for students

Return ONLY a JSON array of the numbers to KEEP, e.g. [1, 3, 5, 7]

URLs:
{urls_block}
"""
        try:
            raw = ask_gemini(prompt, max_tokens=500)
            raw = re.sub(r"```json|```", "", raw).strip()
            indices = json.loads(raw)
            for idx in indices:
                if 1 <= idx <= len(batch):
                    kept.append(batch[idx - 1])
        except Exception as e:
            print(f"  ⚠️  AI filter batch error: {e} — keeping batch as-is")
            kept.extend(batch)

    print(f"🤖  AI filter: {len(links_batch)} → {len(kept)} links kept\n")
    return kept


# ──────────────────── STEP 4 — AI DATA EXTRACTION ────────────────

def ai_extract_details(page_text: str, url: str) -> dict:
    """
    Claude reads the crawled page and extracts structured opportunity data.
    """
    # Truncate to ~6000 chars to stay within token budget
    truncated = page_text[:6000]

    prompt = f"""
You are extracting structured data from a fellowship/internship webpage.

Page URL: {url}
Page Content (truncated):
\"\"\"
{truncated}
\"\"\"

Extract the following fields. If a field is not found, use null.

Return ONLY valid JSON (no markdown):
{{
  "name": "Full official program name",
  "organization": "Sponsoring org or institution",
  "deadline": "Application deadline as YYYY-MM-DD or 'Check Website' or 'Rolling'",
  "stipend": "Monthly stipend amount or 'Unpaid' or 'Not Specified'",
  "duration": "Duration e.g. '10 weeks' or '3 months'",
  "eligibility": "Short eligibility summary (1–2 sentences)",
  "mode": "Remote / In-Person / Hybrid",
  "location": "City/Country if in-person",
  "is_open": true or false (based on whether applications are currently open),
  "tags": ["list", "of", "relevant", "tags"]
}}

Tags should include relevant keywords like: open-source, research, government, stipend,
AI-ML, undergraduate, postgraduate, India, remote, etc.
"""
    try:
        raw = ask_gemini(prompt, max_tokens=800)
        raw = re.sub(r"```json|```", "", raw).strip()
        return json.loads(raw)
    except Exception as e:
        print(f"  ⚠️  AI extraction failed for {url}: {e}")
        return {}


# ──────────────── STEP 5 — CRAWL + STORE ─────────────────────────

async def process_link(crawler, run_cfg, link: str, score: int, semaphore: asyncio.Semaphore):
    async with semaphore:
        try:
            # Heavier wait for .gov.in / .ac.in pages
            if any(ext in link.lower() for ext in [".gov.in", ".ac.in", ".nic.in"]):
                run_cfg.wait_for = "css:body"
                run_cfg.delay_before_return_html = 2.5
            else:
                run_cfg.wait_for = "body"
                run_cfg.delay_before_return_html = 1.5

            run_cfg.headers = {"User-Agent": random.choice(USER_AGENTS)}

            result = await asyncio.wait_for(
                crawler.arun(url=link, config=run_cfg), timeout=60.0
            )

            if not result.success or len(result.markdown) < 300:
                return

            # Drop link-farm pages (too many outbound links = aggregator)
            link_density = result.markdown.count("](")
            if score < 80 and link_density > 80:
                print(f"  🗑️  Skipping aggregator ({link_density} links): {link}")
                return

            # ── AI extraction ──────────────────────────────────────────
            details = ai_extract_details(result.markdown, link)

            if not details:
                return

            name = details.get("name") or "Unknown Opportunity"

            doc = {
                "name":         name,
                "organization": details.get("organization"),
                "deadline":     details.get("deadline", "Check Website"),
                "stipend":      details.get("stipend"),
                "duration":     details.get("duration"),
                "eligibility":  details.get("eligibility"),
                "mode":         details.get("mode"),
                "location":     details.get("location"),
                "is_open":      details.get("is_open"),
                "tags":         details.get("tags", []),
                "apply_link":   link,
                "trust_score":  score,
                "last_updated": datetime.now(),
            }

            await collection.update_one(
                {"apply_link": link},
                {"$set": doc},
                upsert=True,
            )
            print(f"  ✅  Saved: {name}  |  Deadline: {doc['deadline']}")

        except asyncio.TimeoutError:
            print(f"  ⏱️  Timeout: {link}")
        except Exception as e:
            print(f"  ❌  Error ({link}): {e}")


async def main():
    print("=" * 60)
    print("  AI Fellowship Tracker — Scraper")
    print("=" * 60)

    # ── 1. Gemini generates queries ──────────────────────────────
    programs = generate_queries_with_ai()

    # ── 2. Search & collect links ────────────────────────────────
    scored_links = await collect_all_links(programs)

    if not scored_links:
        print("❌  No links found. Check your SERPER_API_KEY.")
        return

    # ── 3. AI relevance filter (on top-300 links) ────────────────
    top_links = [url for _, url in scored_links[:300]]
    filtered  = await ai_relevance_check(top_links)

    # Re-attach scores for filtered links
    score_map     = {url: sc for sc, url in scored_links}
    final_links   = [(score_map.get(u, 50), u) for u in filtered]
    final_links.sort(key=lambda x: x[0], reverse=True)

    print(f"🚀  Processing {len(final_links)} links...\n")

    # ── 4. Crawl + AI extract + store ────────────────────────────
    semaphore  = asyncio.Semaphore(3)
    browser_cfg = BrowserConfig(
        headless=True,
        extra_args=["--disable-gpu", "--no-sandbox"],
    )
    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        exclude_all_images=True,
        page_timeout=60000,
        wait_for="body",
        delay_before_return_html=2.0,
    )

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        tasks = [
            process_link(crawler, run_cfg, url, score, semaphore)
            for score, url in final_links
        ]
        await asyncio.gather(*tasks)

    print("\n🎉  Scrape complete. Database updated.")


if __name__ == "__main__":
    asyncio.run(main())