import os
from datetime import datetime, timezone
from pathlib import Path

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parent / ".env")

now = datetime.now(timezone.utc)
actor = "anuroop"

products = [
    {
        "productId": "vt-001",
        "name": "Classic Black",
        "image": "/images/p-black.jpg",
        "price": 499,
        "originalPrice": 799,
        "category": "Everyday",
        "rating": 4.7,
        "reviews": 312,
    },
    {
        "productId": "vt-002",
        "name": "Navy Blue",
        "image": "/images/p-navy.jpg",
        "price": 499,
        "originalPrice": 799,
        "category": "Everyday",
        "rating": 4.6,
        "reviews": 248,
    },
    {
        "productId": "vt-003",
        "name": "Grey Melange",
        "image": "/images/p-grey.jpg",
        "price": 449,
        "originalPrice": 699,
        "category": "Everyday",
        "rating": 4.5,
        "reviews": 190,
    },
]

client = MongoClient(os.environ["MONGODB_URI"], tlsCAFile=certifi.where())
collection = client["valtoro"]["products"]
collection.create_index("productId", unique=True)
collection.create_index("category")

for product in products:
    collection.update_one(
        {"productId": product["productId"]},
        {
            "$set": {**product, "updatedBy": actor, "updatedAt": now},
            "$setOnInsert": {"createdBy": actor, "createdAt": now},
        },
        upsert=True,
    )

print(f"Upserted {len(products)} products into valtoro.products at {now.isoformat()}")
print(f"Documents now in collection: {collection.count_documents({})}")
client.close()