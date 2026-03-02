from fastapi import FastAPI, Query
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv
import uvicorn

env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    print("❌ API: MONGO_URL not found")
else:
    print("✅ API: Connected to MongoDB")

client     = AsyncIOMotorClient(MONGO_URL)
db         = client.fellowship_tracker
collection = db.fellowships


@app.get("/api/fellowships")
async def get_fellowships(
    tag:     str = Query(None, description="Filter by tag e.g. open-source, research"),
    open:    bool = Query(None, description="Filter by is_open status"),
    search:  str = Query(None, description="Search by name or org"),
    limit:   int = Query(100, le=200),
):
    query_filter = {}

    if tag:
        query_filter["tags"] = {"$in": [tag.lower()]}
    if open is not None:
        query_filter["is_open"] = open
    if search:
        query_filter["$or"] = [
            {"name":         {"$regex": search, "$options": "i"}},
            {"organization": {"$regex": search, "$options": "i"}},
        ]

    cursor = (
        collection
        .find(query_filter)
        .sort("trust_score", -1)
        .limit(limit)
    )

    results = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        results.append(doc)

    return results


@app.get("/api/tags")
async def get_all_tags():
    """Returns all distinct tags in the DB — useful for filter UI."""
    tags = await collection.distinct("tags")
    return sorted(tags)


@app.get("/api/stats")
async def get_stats():
    total       = await collection.count_documents({})
    open_count  = await collection.count_documents({"is_open": True})
    with_deadline = await collection.count_documents({
        "deadline": {"$nin": ["Check Website", "Rolling", None]}
    })
    return {
        "total":         total,
        "open":          open_count,
        "with_deadline": with_deadline,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)