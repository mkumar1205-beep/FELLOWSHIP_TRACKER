import os
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

async def check_db():
    mongo_url = os.getenv("MONGO_URL")
    if not mongo_url:
        print("❌ MONGO_URL not found in .env")
        return

    client = AsyncIOMotorClient(mongo_url)
    db = client.fellowship_tracker
    collection = db.fellowships

    count = await collection.count_documents({})
    print(f"📊 Total Opportunities in DB: {count}")

    print("\n🔎 Latest 10 Entries:")
    cursor = collection.find().sort("last_updated", -1).limit(10)
    
    async for doc in cursor:
        print(f"------------------------------------------------")
        print(f"Title:    {doc.get('name', 'N/A')}")
        print(f"Org:      {doc.get('org', 'N/A')}")
        print(f"Location: {doc.get('location', 'N/A')}")
        print(f"Deadline: {doc.get('deadline', 'N/A')}")
        print(f"AI Conf:  {doc.get('ai_confidence', 'N/A')}")
        print(f"Link:     {doc.get('apply_link', 'N/A')}")

if __name__ == "__main__":
    asyncio.run(check_db())
