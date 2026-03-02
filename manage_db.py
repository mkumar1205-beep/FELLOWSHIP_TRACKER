import asyncio
import os
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

# Load Env
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

MONGO_URL = os.getenv("MONGO_URL")

async def get_db():
    if not MONGO_URL:
        print("❌ Error: MONGO_URL not found in .env")
        return None
    client = AsyncIOMotorClient(MONGO_URL)
    return client.fellowship_tracker.fellowships

async def list_opportunities(collection):
    print("\n📋 Latest 20 Opportunities:")
    print(f"{'ID':<25} | {'Name':<30} | {'Category':<15} | {'Deadline'}")
    print("-" * 90)
    
    cursor = collection.find().sort("last_updated", -1).limit(20)
    async for doc in cursor:
        name = (doc.get('name') or "Unknown")[:28]
        cat = (doc.get('category') or "Other")[:15]
        print(f"{str(doc['_id']):<25} | {name:<30} | {cat:<15} | {doc.get('deadline')}")
    print("-" * 90)

async def add_opportunity(collection):
    print("\n➕ Add New Opportunity")
    name = input("Name: ").strip()
    org = input("Organization: ").strip()
    location = input("Location (default: India): ").strip() or "India"
    
    print("Categories: [1] Open Source, [2] Research, [3] Internship, [4] Fellowship, [5] Scholarship")
    cat_map = {"1": "Open Source", "2": "Research", "3": "Corporate Internship", "4": "Government Fellowship", "5": "Scholarship"}
    cat_choice = input("Category (1-5): ").strip()
    category = cat_map.get(cat_choice, "Other")
    
    deadline = input("Deadline (YYYY-MM-DD or 'Check Website'): ").strip()
    link = input("Apply Link: ").strip()
    
    doc = {
        "name": name,
        "org": org,
        "location": location,
        "category": category,
        "deadline": deadline,
        "apply_link": link,
        "last_updated": datetime.now(),
        "is_manual": True
    }
    
    await collection.insert_one(doc)
    print("✅ Opportunity Added Successfully!")

async def delete_opportunity(collection):
    target_id = input("\n🗑️ Enter ID to delete: ").strip()
    try:
        result = await collection.delete_one({"_id": ObjectId(target_id)})
        if result.deleted_count > 0:
            print("✅ Deleted successfully.")
        else:
            print("❌ ID not found.")
    except Exception as e:
        print(f"❌ Error: {e}")

async def main():
    collection = await get_db()
    if collection is None:
        return

    while True:
        print("\n🔧 Fellowship Tracker Manager")
        print("1. List Recent")
        print("2. Add Opportunity")
        print("3. Delete Opportunity")
        print("4. Exit")
        
        choice = input("Select option: ")
        
        if choice == '1':
            await list_opportunities(collection)
        elif choice == '2':
            await add_opportunity(collection)
        elif choice == '3':
            await delete_opportunity(collection)
        elif choice == '4':
            print("👋 Bye!")
            break
        else:
            print("Invalid option.")

if __name__ == "__main__":
    asyncio.run(main())
