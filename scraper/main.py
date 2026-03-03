import os
from motor.motor_asyncio import AsyncIOMotorClient
from pathlib import Path
import random
import asyncio
import os
import re
import httpx
from urllib.parse import urljoin
from dotenv import load_dotenv
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from datetime import datetime
from scraper.discord import send_discord_notification

env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

client = AsyncIOMotorClient(os.getenv("MONGO_URL"))
db = client.fellowship_tracker
collection = db.fellowships
SERPER_KEY = os.getenv("SERPER_API_KEY")


SEARCH_QUERIES = [
    "site:gov.in tech fellowship 2026 application",
    "site:edu.in software engineering internship summer 2026",
    "MeitY digital india internship 2026 registration",
    "ISRO IIRS internship for students 2026",
    "IIT research internship 2026 computer science",
    "software developer internship india 2026 apply",
    "AI ML fellowship for indian students 2026",
    "Google India STEP internship 2026 deadline",
    "Microsoft India university internship 2026",
    "Qualcomm India technical internship 2026",
    'site:*.gov.in "internship" OR "fellowship" 2026 -site:instagram.com -site:facebook.com',
    'site:meity.gov.in "internship" 2026',
    'site:dst.gov.in "fellowship" 2026',
    'site:isro.gov.in "student project" OR "internship" 2026',
    'site:*.ac.in OR site:*.edu.in "summer internship" 2026 computer science',
    'site:iit*.ac.in "research internship" 2026',
    'site:google.com/about/careers "Software Engineering Intern" India 2026',
    'site:amazon.jobs "SDE Intern" India 2026',
    '"CS engineering internship" India 2026 -site:instagram.com -site:linkedin.com -site:twitter.com',
    "site:*.gov.in internship", 
    "site:*.res.in fellowship", 
    "site:nic.in recruitment 2026", 
    "CS student internship India 2026",
    "site:dic.gov.in Technical Internship 2026",
    "site:negd.gov.in Technical Internship 2026",
    "site:bharatdigital.io fellowship 2026",
    "site:spark.iitr.ac.in 2026",
    "site:intern.meity.gov.in 2026",
    "site:internship.aicte-india.org 2026",
    "site:skillindiadigital.gov.in internship 2026",
    "site:wcd.intern.nic.in 2026",
    "site:fellowship.tribal.gov.in 2026",
    "site:ashoka.edu.in Young India Fellowship 2026",
    "site:isve.in Summer Internship 2026",
    "site:sionsemi.com internship 2026",
    "site:sun.iitpkd.ac.in 2026",
    "site:surge.iitk.ac.in 2025 2026",
    "site:eapplication.nitrkl.ac.in 2026",
    "intitle:\"Internship\" site:gov.in 2026",
    "intitle:\"Fellowship\" site:gov.in 2026",
    "site:*.res.in \"Internship\" 2026",
    "site:*.nic.in recruitment 2026 intern",
    "site:*.ac.in \"Summer Research\" 2026 stipend"
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/110.0"
]

def get_domain_score(url):
    """Prioritizes official government, academic, and a refined list of top-tier global tech portals."""
    url = url.lower()
    
    if any(ext in url for ext in ['.gov.in', '.nic.in', '.res.in']): return 100
    if any(ext in url for ext in ['.ac.in', '.edu.in']): return 95
    
    trusted_brands = [
        'google', 'microsoft', 'amazon', 'apple', 'meta', 'adobe', 'salesforce', 
        'servicenow', 'oracle', 'sap.com', 'ibm.com', 'redhat', 'atlassian',
        'qualcomm', 'amd.com', 'nvidia', 'intel', 'samsung', 'analog.com', 
        'nxp.com', 'arm.com', 'broadcom', 'mediatek', 'ti.com', 'vlsi',
        'tcs.com', 'infosys', 'wipro', 'hcltech', 'techmahindra', 'ltimindtree', 
        'cognizant', 'capgemini', 'accenture', 'mphasis',
        'paytm', 'phonepe', 'razorpay', 'zerodha', 'upstox', 'slice', 'groww', 
        'paypal', 'stripe', 'visa.com', 'mastercard', 'goldmansachs', 'jpmorgan',
        'deloitte', 'ey.com', 'pwc', 'kpmg', 'mckinsey', 'bcg.com', 'bain.com',
        'intel', 'nxp', 'ti.com', 'samsung', 'cisco', 'mediatek', 'jio.com', 
        'tata.com', 'reliance', 'cred.club', 'zomato', 'swiggy', 'groww', 
        'serb.gov.in', 'aicte-india.org', 'niti.gov.in', 'mea.gov.in'
    ]
    
    if any(brand in url for brand in trusted_brands):
        return 85 

    if any(d in url for d in ['instagram.com', 'facebook.com', 'linkedin.com', 'youtube.com']): 
        return 0
        
    return 50



def clean_name(markdown, metadata_title):
    name = metadata_title.split('|')[0].split('-')[0].split('–')[0].split(':')[0].strip()
    
    circular_keywords = ['circular', 'notice', 'advertisement', 'office order', 'notification']
    if any(word in name.lower() for word in circular_keywords) or len(name) < 8:
        h1_match = re.search(r'^#\s+(.*)', markdown, re.MULTILINE)
        if h1_match:
            name = h1_match.group(1).strip()
    garbage = [
        'Apply Now', 'Registration', '2026', '2025', 'Official Website', 
        'Home', 'Login', 'Details', 'Form', 'Portal', 'Welcome to'
    ]
    
    for word in garbage:
        name = re.sub(rf'\b{word}\b', '', name, flags=re.IGNORECASE).strip()
   
    name = re.sub(r'\s+', ' ', name)
        
    return name[:80] if len(name) > 3 else "Technical Opportunity"
def extract_deadline(text):
    current_year = datetime.now().year # 2026
    
    year_match = re.search(r'202[4-7]', text) 
    found_year = int(year_match.group(0)) if year_match else None
    
    month_pattern = r'(\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*)'
    match = re.search(month_pattern, text, re.IGNORECASE)
    
    if match:
        date_str = match.group(0).strip()
        clean_date = re.sub(r'(st|nd|rd|th)', '', date_str, flags=re.IGNORECASE)
        
        try:
            target_year = found_year if found_year else current_year
            parsed_date = datetime.strptime(f"{clean_date} {target_year}", "%d %b %Y")
            
            if not found_year and (datetime.now() - parsed_date).days > 180:
                return "Likely Expired (Old Date Found)"
                
            return parsed_date.strftime("%Y-%m-%d") # Standard Format: 2026-03-31
        except:
            pass

    numeric_pattern = r'(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'
    num_match = re.search(numeric_pattern, text)
    if num_match:
        return num_match.group(0)
    return "Check Website"

def is_valid_source(url):
    """Filters out known social media and low-quality domains."""
    blacklist = [
        'instagram.com', 'facebook.com', 'twitter.com', 'linkedin.com', 
        'youtube.com', 'medium.com', 'pinterest.com'
    ]
    
    url_lower = url.lower()
    return not any(domain in url_lower for domain in blacklist)

async def discover_300_links():
    """Loops through queries and pages to find a massive link set."""
    all_links_with_scores = []
    seen_links = set()
    async with httpx.AsyncClient() as client:
        for query in SEARCH_QUERIES:
            for page in range(1, 4): # Get 3 pages per query for wider reach
                print(f"Searching: '{query}' (Page {page})")
                payload = {"q": query, "gl": "in", "num": 50, "page": page}
                headers = {'X-API-KEY': SERPER_KEY, 'Content-Type': 'application/json'}
                try:
                    resp = await client.post("https://google.serper.dev/search", json=payload, headers=headers)
                    results = resp.json().get('organic', [])
                    for r in results:
                        link = r['link']
                        
                        # Apply Point 2: Filter out blacklist and duplicates
                        if link not in seen_links and is_valid_source(link) and not link.lower().endswith('.pdf'):
                            context = (r.get('title', '') + r.get('snippet', '')).lower()
                            keywords = ['intern', 'fellow', 'scholar', 'trainee', 'opportunity']
                            
                            if any(k in context for k in keywords):
                                score = get_domain_score(link) # Use your scoring logic
                                all_links_with_scores.append((score, link))
                                seen_links.add(link)
                    await asyncio.sleep(0.5)
                except Exception as e: print(f"Search Error: {e}")

    all_links_with_scores.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in all_links_with_scores]

async def process_link(crawler, link, semaphore):
    async with semaphore:
        try:
            is_official = any(ext in link.lower() for ext in ['.gov.in', '.ac.in', '.nic.in'])
            run_cfg = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                exclude_all_images=True,
                page_timeout=60000,
                wait_for="css:#notice-board, .announcement, #latest-news, .content-area, body" if is_official else "body",
                delay_before_return_html=2.0,
                headers={"User-Agent": random.choice(USER_AGENTS)}
            )

            result = await asyncio.wait_for(
                crawler.arun(url=link, config=run_cfg),
                timeout=60.0
            )

            # Validate crawl result
            if not result.success:
                return

            if len(result.markdown) <= 300:
                return

            score = get_domain_score(link)
            link_density = result.markdown.count('](')

            # Skip low trust aggregators
            if score < 80 and link_density > 80:
                print(f"Skipping low-trust aggregator: {link}")
                return

            # Extract data
            name = clean_name(result.markdown, result.metadata.get('title', ''))
            deadline = extract_deadline(result.markdown)

            doc = {
                "name": name,
                "deadline": deadline,
                "apply_link": link,
                "trust_score": score,
                "last_updated": datetime.now()
            }

            # Check if exists
            existing = await collection.find_one({"apply_link": link})

            if not existing:
                await collection.insert_one(doc)
                print(f"New Added: {name}")
                await send_discord_notification(doc)
            else:
                await collection.update_one(
                    {"apply_link": link},
                    {"$set": doc}
                )
                print(f"Updated: {name}")

        except Exception as e:
            print(f"Error processing {link}: {e}")

async def main():
    links = await discover_300_links()
    if not links: return
    semaphore = asyncio.Semaphore(3)

    # Browser config to block heavy assets and prevent "Sticking"
    browser_cfg = BrowserConfig(headless=True, extra_args=["--disable-gpu", "--no-sandbox"])

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        print(f"Processing {len(links)} links in parallel...")

        tasks = [process_link(crawler, link, semaphore) for link in links]

        await asyncio.gather(*tasks)
        print("Database sync complete.")

if __name__ == "__main__":
    asyncio.run(main())