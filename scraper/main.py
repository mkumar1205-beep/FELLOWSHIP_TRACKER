import os
import asyncio
import random
import json
import httpx
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
import google.generativeai as genai
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

# Load Environment Variables
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# Database Setup
MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.fellowship_tracker
collection = db.fellowships

# API Keys
SERPER_KEY = os.getenv("SERPER_API_KEY")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_KEY:
    print("⚠️  WARNING: GEMINI_API_KEY not found. AI features will fail.")
else:
    genai.configure(api_key=GEMINI_KEY)

# Configuration
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/110.0"
]

FIXED_QUERIES = [
    # Open Source
    "Google Summer of Code 2026", "LFX Mentorship 2026", "Outreachy 2026", 
    "Summer of Bitcoin 2026", "MLH Fellowship 2026", "Linux Foundation Mentorship 2026",
    "FOSS United Fellowship 2026", "Hyperledger Mentorship 2026", "CNCF Mentorship 2026",
    "Julia Season of Contributions 2026", "GirlScript Summer of Code 2026",
    "KWoC 2026 Kharagpur Winter of Code", "DWoC 2026 Delta Winter of Code",
    
    # Research / Fellowships
    "Reliance Foundation Scholarship 2026", "Grace Hopper Celebration India 2026 Student Scholarship",
    "LIFT Fellowship 2026", "Adobe India Women in Technology Scholarship 2026",
    "Western Digital Scholarship for STEM 2026",
    "IIT Madras Summer Fellowship 2026", "IIT Bombay Research Internship 2026",
    "IIT Delhi SIP 2026", "IIT Roorkee SPARK 2026", "IIT Gandhinagar SRIP 2026",
    "IASc-INSA-NASI Summer Research Fellowship 2026", "TIFR VSRP 2026",
    "Microsoft Research India Intern 2026", "CERN Summer Student Programme 2026",
    "Mitacs Globalink Research Internship 2026"
]

async def generate_search_queries():
    """Uses Gemini to generate dynamic queries + includes fixed high-priority targets."""
    print("🤖 AI: Generating smart search queries...")
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    current_year = datetime.now().year
    next_year = current_year + 1

    prompt = f"""
    Generate 50 advanced Google search queries to find *current* and *upcoming* fellowship, internship, and student research programs in India for {current_year}.
    
    CRITICAL FOCUS:
    - Prioritize opportunities in **Hyderabad** and **Bangalore**.
    - Include major tech companies (Google, Microsoft, Amazon).
    - Exclude courses, trainings, aggregators, and exams.
    - Use smart operators like: site:jobs.lever.co or site:boards.greenhouse.io
    
    Return ONLY a JSON array of strings. Example: ["query 1", "query 2"]
    """
    
    ai_queries = []
    for attempt in range(3):
        try:
            response = await model.generate_content_async(prompt)
            text = response.text.replace("```json", "").replace("```", "").strip()
            ai_queries = json.loads(text)
            print(f"✅ AI: Generated {len(ai_queries)} queries.")
            break
        except Exception as e:
            if "429" in str(e):
                print(f"⏳ Quota limit hit (Query Gen). Waiting {20 * (attempt + 1)}s...")
                await asyncio.sleep(20 * (attempt + 1))
            else:
                print(f"❌ AI Error generating queries: {e}. Using fallback.")
                break
    
    # Combine AI queries with Fixed queries
    combined_queries = list(set(ai_queries + [q.replace('2026', str(current_year)) for q in FIXED_QUERIES]))
    random.shuffle(combined_queries)
    return combined_queries

async def analyze_opportunity(markdown_content, url):
    """Uses Gemini to extract structured data and filter relevance."""
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    current_year = datetime.now().year
    next_year = current_year + 1

    prompt = f"""
    Analyze this webpage content (Markdown) describing a potential opportunity:
    
    URL: {url}
    CONTENT START:
    {markdown_content[:25000]} 
    CONTENT END
    
    Task: Extract details for a Student Fellowship, Internship, or Research Program.
    
    CRITICAL FILTERS: 
    1. Year: MUST be for the year **{current_year}** (or late {current_year}/{next_year} intake). 
       - If it is for {current_year - 2} or {current_year - 1}: Set "is_relevant": false.
    2. Dates: Look closely for DATES (Deadline, Last Date, Apply By). 
       - If exact date is not found, look for "End of [Month]" or "Rolling".
    3. SCAM & AGGREGATOR FILTERS (Reject these, set "is_relevant": false):
       - Reject "Pay-to-work" bootcamps disguised as internships.
       - Reject SEO spam articles (e.g., "Top 10 internships in India" or "Apply now for 500+ jobs") - we only want the direct application page or official organization page.
       - If the page mentions an application fee: Set "is_relevant": false.
       - Reject news articles discussing opportunities rather than offering them.
    
    Output JSON ONLY:
    {{
        "is_relevant": boolean, // True ONLY if for {current_year}/{next_year} AND relevant for students AND NOT spam/fee-based. False otherwise.
        "name": "string", // Clean title, e.g., "Google STEP Intern" or "IASc Summer Fellowship"
        "company_org": "string", // e.g., "Google", "Indian Academy of Sciences", "LFX"
        "location": "string", // Specific City (e.g., "Hyderabad", "Bangalore") or "Remote" or "Pan India". default "India"
        "category": "string", // ONE OF: "Open Source", "Research", "Corporate Internship", "Government Fellowship", "Conference", "Scholarship", "Other"
        "deadline": "string", // FORMAT: "YYYY-MM-DD" or "DD MMM YYYY". If not found, use "Check Website".
        "confidence_score": float // 0.0 to 1.0
    }}
    """
    
    for attempt in range(5):
        try:
            response = await model.generate_content_async(prompt)
            text = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(text)
            return data
        except Exception as e:
            if "429" in str(e):
                wait = 20 * (attempt + 1)
                print(f"⏳ Rate limit hit. Pausing for {wait}s to cool down... (Attempt {attempt+1}/5)")
                await asyncio.sleep(wait)
            else:
                # print(f"❌ Analysis Err: {e}")
                return None
    return None

async def discover_links(queries):
    """Searches using Serper and returns unique links."""
    links = set()
    bad_domains = [
        'linkedin.com', 'naukri.com', 'indeed.com', 'glassdoor.co.in', 
        'internshala.com', 'unstop.com', 'cuvette.tech', 'scholarshipsinindia.com',
        'quora.com', 'reddit.com', 'medium.com', 'youtube.com', 'facebook.com',
        'instagram.com', 'glassdoor.com', 'geeksforgeeks.org', 'ycombinator.com/companies/'
    ]
    
    async with httpx.AsyncClient() as client:
        for query in queries:
            print(f"🔎 Searching: {query}")
            payload = {"q": query, "gl": "in", "num": 10} # Reduced num per query since we have many queries
            headers = {'X-API-KEY': SERPER_KEY, 'Content-Type': 'application/json'}
            try:
                resp = await client.post("https://google.serper.dev/search", json=payload, headers=headers)
                results = resp.json().get('organic', [])
                for r in results:
                    link = r.get('link')
                    # Basic filters
                    if link and not any(bad in link for bad in bad_domains):
                         links.add(link)
            except Exception as e:
                print(f"⚠️ Search Error: {e}")
            await asyncio.sleep(0.5) # Rate limit
    return list(links)

async def process_link(crawler, run_cfg, link, semaphore):
    async with semaphore:
        try:
            # print(f"🕷️ Crawling: {link}")
            result = await crawler.arun(url=link, config=run_cfg)
            
            if result.success and len(result.markdown) > 500:
                # Relaxed Filter: Skip if massive link farm (>250 links) 
                # We removed get_domain_score to rely more on AI, so just check density.
                link_density = result.markdown.count('](') 
                
                # Check link-to-text ratio (if it's mostly just a giant list of links)
                text_len = len(result.markdown)
                if link_density > 200 or (link_density > 50 and text_len < 2000): 
                    # print(f"🗑️ Skipping likely link farm/index page: {link}")
                    return
                
                # Quick Keyword Check before AI (Saves AI parsing cost if obviously junk)
                target_words = ['apply', 'deadline', 'eligibility', 'stipend', 'internship', 'fellowship', 'students', 'research', 'open source', 'mentorship', 'summer', 'winter', 'program']
                text_lower = result.markdown.lower()
                if not any(word in text_lower for word in target_words):
                    # print(f"🗑️ Skipping contextless page: {link}")
                    return

                analysis = await analyze_opportunity(result.markdown, link)
                
                if analysis and analysis.get('is_relevant') and analysis.get('confidence_score', 0) > 0.6:
                    doc = {
                        "name": analysis['name'],
                        "org": analysis.get('company_org', 'Unknown'),
                        "location": analysis.get('location', 'India'),
                        "category": analysis.get('category', 'Other'),
                        "deadline": analysis.get('deadline', 'Check Website'),
                        "apply_link": link,
                        "last_updated": datetime.now(),
                        "ai_confidence": analysis['confidence_score']
                    }
                    
                    await collection.update_one(
                        {"apply_link": link},
                        {"$set": doc},
                        upsert=True
                    )
                    print(f"✅ FOUND: {doc['name']} [{doc['category']}]")
                # else:
                #     print(f"🗑️ Irrelevant: {link} ({analysis.get('name') if analysis else 'Unknown'})")
            
        except Exception as e:
            # print(f"⚠️ Crawl Error {link}: {e}")
            pass

async def main():
    if not SERPER_KEY:
        print("❌ SERPER_API_KEY missing. Cannot search.")
        return

    queries = await generate_search_queries()
    links = await discover_links(queries)
    
    print(f"🚀 Found {len(links)} potential links. Starting AI analysis...")
    
    semaphore = asyncio.Semaphore(3) # Reduced from 5 to 3 to be gentler on Rate Limits
    
    browser_cfg = BrowserConfig(headless=True, extra_args=["--disable-gpu", "--no-sandbox", "--disable-images"])
    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=30000,
        wait_for="body",
        delay_before_return_html=1.5
    )

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        tasks = [process_link(crawler, run_cfg, link, semaphore) for link in links]
        await asyncio.gather(*tasks)
        
    print("🎉 Sync Complete.")

if __name__ == "__main__":
    asyncio.run(main())