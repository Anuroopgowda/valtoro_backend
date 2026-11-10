
import os
from pathlib import Path

import certifi
from dotenv import load_dotenv
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from pymongo.errors import PyMongoError

# Load .env locally
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import os
from urllib.parse import urlsplit

uri = os.environ.get("MONGODB_URI")

if uri:
    parsed = urlsplit(uri)
    print("MONGODB_URI exists:", True)
    print("Username:", parsed.username)
    print("Host:", parsed.hostname)
    print("Database:", parsed.path.lstrip("/").split("?")[0])
else:
    print("MONGODB_URI exists:", False)

# IMPORTANT: Must be at module level
app = FastAPI(title="Valtoro API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://valtoro.vercel.app",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

PUBLIC_FIELDS = {
    "_id": 0,
    "productId": 1,
    "name": 1,
    "image": 1,
    "price": 1,
    "originalPrice": 1,
    "category": 1,
    "rating": 1,
    "reviews": 1,
}

_client = None


def products_collection():
    global _client

    uri = os.environ.get("MONGODB_URI")

    if not uri:
        raise HTTPException(
            status_code=500,
            detail="MONGODB_URI is not set"
        )

    if _client is None:
        _client = MongoClient(
            uri,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=8000
        )

    return _client["valtoro"]["products"]


def analytics_collection():
    global _client

    uri = os.environ.get("MONGODB_URI")
    if not uri:
        raise HTTPException(status_code=500, detail="MONGODB_URI is not set")

    if _client is None:
        _client = MongoClient(
            uri,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=8000,
        )

    return _client["valtoro"]["app_analytics"]


@app.get("/")
def root():
    return {"message": "Valtoro API is running"}


@app.get("/api/products")
def list_products(category: str | None = Query(default=None)):
    query = {"category": category} if category else {}

    try:
        products = list(
            products_collection()
            .find(query, PUBLIC_FIELDS)
            .sort("productId", 1)
        )

        return {"products": products}

    except PyMongoError as error:
        print(f"MongoDB Error: {repr(error)}")

        raise HTTPException(
            status_code=503,
            detail="Could not read products"
        ) from error


@app.post("/api/track-visit")
def track_visit():
    timestamp = datetime.now(timezone.utc)

    try:
        result = analytics_collection().find_one_and_update(
            {"_id": "visitor_stats"},
            {
                "$inc": {"total_visitors": 1},
                "$set": {"last_updated": timestamp},
            },
            upsert=True,
            return_document=True,
            projection={"_id": 0, "total_visitors": 1, "last_updated": 1},
        )
        return {
            "total_visitors": result["total_visitors"],
            "last_updated": timestamp.isoformat().replace("+00:00", "Z"),
        }
    except PyMongoError as error:
        print(f"MongoDB Error: {repr(error)}")
        raise HTTPException(status_code=503, detail="Could not track visit") from error


@app.get("/api/visitor-count")
def visitor_count():
    try:
        document = analytics_collection().find_one(
            {"_id": "visitor_stats"},
            {"_id": 0, "total_visitors": 1, "last_updated": 1},
        )
        if document is None:
            return {"total_visitors": 0, "last_updated": None}

        last_updated = document.get("last_updated")
        if last_updated is not None:
            last_updated = last_updated.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "total_visitors": document.get("total_visitors", 0),
            "last_updated": last_updated,
        }
    except PyMongoError as error:
        print(f"MongoDB Error: {repr(error)}")
        raise HTTPException(status_code=503, detail="Could not read visitor count") from error
