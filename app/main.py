import os
from pathlib import Path

import certifi
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from pymongo.errors import PyMongoError

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

app = FastAPI(title="Valtoro API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET"],
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
        raise HTTPException(status_code=500, detail="MONGODB_URI is not set")
    if _client is None:
        _client = MongoClient(uri, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=8000)
    return _client["valtoro"]["products"]


@app.get("/api/products")
def list_products(category: str | None = Query(default=None)):
    query = {"category": category} if category else {}
    try:
        products = list(products_collection().find(query, PUBLIC_FIELDS).sort("productId", 1))
    except PyMongoError as error:
        raise HTTPException(status_code=503, detail="Could not read products") from error
    return {"products": products}
