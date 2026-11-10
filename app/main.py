
import base64
import os
import re
import logging
import secrets
import uuid
from urllib.parse import quote
from pathlib import Path

import certifi
from dotenv import load_dotenv
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
import requests
from requests import RequestException

# Load .env locally
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# IMPORTANT: Must be at module level
app = FastAPI(title="Valtoro API")
logger = logging.getLogger("valtoro.inventory")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://valtoro.vercel.app",
    ],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
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


def env_value(name: str, default: str | None = None) -> str | None:
    """Trim whitespace accidentally included in deployment environment values."""
    value = os.environ.get(name)
    return value.strip() if value is not None else default


def log_mongo_error(operation: str, error: PyMongoError) -> None:
    """Log operation and safe write-concern metadata without logging the URI."""
    details = getattr(error, "details", None) or {}
    write_concern = (details.get("errInfo") or {}).get("writeConcern")
    if write_concern:
        logger.error("%s failed: %s; write concern=%s", operation, type(error).__name__, write_concern)
    else:
        logger.error("%s failed: %s", operation, type(error).__name__)


def products_collection():
    global _client

    uri = env_value("MONGODB_URI")

    if not uri:
        raise HTTPException(
            status_code=500,
            detail="Database is not configured"
        )

    if _client is None:
        _client = MongoClient(
            uri,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=8000
        )

    return _client["valtoro"]["products"]


MAX_IMAGE_SIZE = 5 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def inventory_admin(authorization: str | None = Header(default=None)):
    """Identify the administrator from a server-configured bearer credential.

    Set INVENTORY_ADMIN_ANUROOP_KEY, INVENTORY_ADMIN_VARUN_KEY, and
    INVENTORY_ADMIN_VINIT_KEY. The credential itself is never returned or logged.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Admin authorization required")
    supplied = authorization[7:]
    configured = (
        ("Anuroop", env_value("INVENTORY_ADMIN_ANUROOP_KEY")),
        ("Varun", env_value("INVENTORY_ADMIN_VARUN_KEY")),
        ("Vinit", env_value("INVENTORY_ADMIN_VINIT_KEY")),
    )
    if not any(key for _, key in configured):
        raise HTTPException(status_code=503, detail="Inventory administrator keys are not configured")
    for name, key in configured:
        if key and secrets.compare_digest(supplied, key):
            return name
    raise HTTPException(status_code=401, detail="Admin authorization required")


def inventory_view(document):
    inventory = document.get("inventory") or {}
    image_path = inventory.get("image_path", "")
    image_url = f"/api/images/{image_path.rsplit('/', 1)[-1]}" if image_path else inventory.get("image_url", document.get("image", ""))
    return {
        "product_id": inventory.get("product_id", document.get("productId", "")),
        "name": document.get("name", ""),
        "category": document.get("category", ""),
        "description": inventory.get("description", ""),
        "price": document.get("price", 0),
        "stock": inventory.get("stock", 0),
        "image_url": image_url,
        "image_path": image_path,
        "created_at": inventory.get("created_at"),
        "updated_at": inventory.get("updated_at"),
        "created_by": inventory.get("created_by"),
        "updated_by": inventory.get("updated_by"),
        "rating": document.get("rating", 0),
        "reviews": document.get("reviews", 0),
        "originalPrice": document.get("originalPrice"),
    }


def github_config():
    values = [env_value(name) for name in ("GITHUB_TOKEN", "GITHUB_REPO_OWNER", "GITHUB_REPO_NAME")]
    if not all(values):
        raise HTTPException(status_code=503, detail="GitHub image storage is not configured")
    ui_owner = env_value("GITHUB_UI_REPO_OWNER", values[1])
    ui_repo = env_value("GITHUB_UI_REPO_NAME", values[2])
    ui_branch = env_value("GITHUB_UI_BRANCH", env_value("GITHUB_BRANCH", "main"))
    return values[0], ui_owner, ui_repo, ui_branch, (env_value("GITHUB_IMAGE_FOLDER", "products") or "products").strip("/")


def github_request(method, path, **kwargs):
    token, owner, repo, branch, _ = github_config()
    try:
        response = requests.request(method, f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/contents/{quote(path, safe='/')}", headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}, params={"ref": branch} if method == "GET" else None, timeout=20, **kwargs)
    except RequestException as error:
        raise HTTPException(status_code=502, detail="GitHub image storage is unavailable") from error
    if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
        raise HTTPException(status_code=503, detail="GitHub API rate limit reached; try again later")
    if response.status_code == 401:
        logger.error("GitHub API rejected GITHUB_TOKEN (401)")
        raise HTTPException(status_code=503, detail="GitHub rejected GITHUB_TOKEN. Update it in the backend deployment environment.")
    if response.status_code == 403:
        logger.error("GitHub API denied repository content access (403)")
        raise HTTPException(status_code=503, detail="GITHUB_TOKEN lacks repository contents permission")
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="GitHub image was not found")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="GitHub image storage rejected the request")
    return response.json()


def github_image_response(filename: str):
    """Read one UUID-named image from the private GitHub image folder."""
    stem, separator, extension = filename.rpartition(".")
    if not separator or extension.lower() not in IMAGE_TYPES.values():
        raise HTTPException(status_code=404, detail="Image not found")
    try:
        if str(uuid.UUID(stem)) != stem.lower():
            raise ValueError("Invalid image UUID")
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Image not found") from error

    token, owner, repo, branch, folder = github_config()
    path = f"{folder}/{filename}"
    try:
        response = requests.get(
            f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/contents/{quote(path, safe='/')}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.raw+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params={"ref": branch},
            timeout=20,
        )
    except RequestException as error:
        logger.error("GitHub image read failed: %s", type(error).__name__)
        raise HTTPException(status_code=502, detail="Image storage is unavailable") from error
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Image not found in GitHub repository")
    if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
        raise HTTPException(status_code=503, detail="GitHub API rate limit reached")
    if response.status_code == 401:
        logger.error("GitHub API rejected GITHUB_TOKEN while reading image (401)")
        raise HTTPException(status_code=503, detail="GitHub rejected GITHUB_TOKEN. Update it in the backend deployment environment.")
    if response.status_code == 403:
        logger.error("GitHub API denied private image access (403)")
        raise HTTPException(status_code=503, detail="GITHUB_TOKEN lacks repository contents permission")
    if response.status_code >= 400:
        logger.error("GitHub image read returned HTTP %s", response.status_code)
        raise HTTPException(status_code=502, detail="Could not retrieve image from GitHub")

    media_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    expected_type = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[extension.lower()]
    if media_type not in (expected_type, "application/octet-stream", "application/vnd.github.raw+json"):
        raise HTTPException(status_code=502, detail="GitHub returned an unexpected image type")
    return Response(content=response.content, media_type=expected_type, headers={"Cache-Control": "public, max-age=3600, stale-while-revalidate=86400", "X-Content-Type-Options": "nosniff"})


def upload_github_image(image: UploadFile):
    content_type = (image.content_type or "").lower()
    if content_type not in IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Image must be JPG, PNG, or WEBP")
    data = image.file.read(MAX_IMAGE_SIZE + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Image file is empty")
    if len(data) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail="Image must be smaller than 5 MB")
    folder = github_config()[4]
    filename = f"{uuid.uuid4()}.{IMAGE_TYPES[content_type]}"
    path = f"{folder}/{filename}"
    try:
        result = github_request("PUT", path, json={"message": f"Add inventory image {filename}", "content": base64.b64encode(data).decode("ascii"), "branch": github_config()[3]})
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=502, detail="Could not upload inventory image") from error
    return f"/api/images/{filename}", path


def delete_github_image(path):
    if not path:
        return
    existing = github_request("GET", path)
    github_request("DELETE", path, json={"message": f"Remove inventory image {path.rsplit('/', 1)[-1]}", "sha": existing["sha"], "branch": github_config()[3]})


def clean_inventory_id(value):
    value = value.strip()
    if not value or len(value) > 80 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise HTTPException(status_code=422, detail="Product ID must use letters, numbers, hyphens, or underscores")
    return value


def analytics_collection():
    global _client

    uri = env_value("MONGODB_URI")
    if not uri:
        raise HTTPException(status_code=500, detail="Database is not configured")

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
            .find(query, {**PUBLIC_FIELDS, "inventory.image_path": 1})
            .sort("productId", 1)
        )
        for product in products:
            inventory = product.pop("inventory", None) or {}
            image_path = inventory.get("image_path", "")
            if image_path:
                product["image"] = f"/api/images/{image_path.rsplit('/', 1)[-1]}"
        return {"products": products}

    except PyMongoError as error:
        raise HTTPException(
            status_code=503,
            detail="Could not read products"
        ) from error


@app.get("/api/inventory/images/{filename}")
def inventory_image(filename: str):
    return github_image_response(filename)

@app.get("/api/inventory/products")
def inventory_products(admin: str = Depends(inventory_admin)):
    try:
        docs = products_collection().find({"inventory.product_id": {"$exists": True}}).sort("inventory.updated_at", -1)
        return {"products": [inventory_view(doc) for doc in docs], "admin": admin}
    except PyMongoError as error:
        logger.error("Inventory list failed: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="Could not read inventory. Check backend MongoDB connection and Atlas network access.") from error


@app.get("/api/inventory/products/{product_id}")
def inventory_product(product_id: str, _admin: str = Depends(inventory_admin)):
    try:
        doc = products_collection().find_one({"inventory.product_id": product_id})
    except PyMongoError as error:
        logger.error("Inventory product read failed: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="Could not read inventory product") from error
    if doc is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return inventory_view(doc)


def inventory_fields(product_id, name, category, description, price, stock, image):
    product_id = clean_inventory_id(product_id)
    if not name.strip() or len(name.strip()) > 160:
        raise HTTPException(status_code=422, detail="Product name is required and must be under 160 characters")
    if price < 0 or stock < 0:
        raise HTTPException(status_code=422, detail="Price and stock must be zero or greater")
    if len(category) > 100 or len(description) > 3000:
        raise HTTPException(status_code=422, detail="Category or description is too long")
    if image is not None and image.filename:
        return product_id, image
    return product_id, None


@app.post("/api/inventory/products", status_code=201)
def create_inventory_product(
    product_id: str = Form(...), name: str = Form(...), price: float = Form(..., ge=0), stock: int = Form(..., ge=0),
    category: str = Form(default=""), description: str = Form(default=""), image: UploadFile = File(...),
    admin: str = Depends(inventory_admin),
):
    product_id, _ = inventory_fields(product_id, name, category, description, price, stock, image)
    collection = products_collection()
    try:
        collection.create_index("inventory.product_id", unique=True, sparse=True)
        if collection.find_one({"inventory.product_id": product_id}, {"_id": 1}):
            raise HTTPException(status_code=409, detail="Product ID already exists")
    except PyMongoError as error:
        log_mongo_error("Inventory create database operation", error)
        raise HTTPException(status_code=503, detail="Inventory database is unavailable") from error
    image_url, image_path = upload_github_image(image)
    now = datetime.now(timezone.utc)
    try:
        collection.insert_one({"productId": product_id, "name": name.strip(), "category": category.strip(), "price": price, "image": image_url, "rating": 0, "reviews": 0, "inventory": {"product_id": product_id, "description": description.strip(), "stock": stock, "image_url": image_url, "image_path": image_path, "created_at": now, "updated_at": now, "created_by": admin, "updated_by": admin}})
    except DuplicateKeyError as error:
        try:
            delete_github_image(image_path)
        except HTTPException:
            pass
        raise HTTPException(status_code=409, detail="Product ID already exists") from error
    except PyMongoError as error:
        log_mongo_error("Inventory create database operation", error)
        try:
            delete_github_image(image_path)
        except HTTPException:
            pass
        raise HTTPException(status_code=503, detail="Could not save product to inventory") from error
    return inventory_view({"name": name, "category": category, "price": price, "inventory": {"product_id": product_id, "description": description, "stock": stock, "image_url": image_url, "image_path": image_path, "created_at": now, "updated_at": now, "created_by": admin, "updated_by": admin}})


@app.put("/api/inventory/products/{product_id}")
def update_inventory_product(
    product_id: str, name: str = Form(...), price: float = Form(..., ge=0), stock: int = Form(..., ge=0),
    category: str = Form(default=""), description: str = Form(default=""), image: UploadFile | None = File(default=None),
    admin: str = Depends(inventory_admin),
):
    product_id = clean_inventory_id(product_id)
    inventory_fields(product_id, name, category, description, price, stock, image)
    try:
        old = products_collection().find_one({"inventory.product_id": product_id})
    except PyMongoError as error:
        logger.error("Inventory update lookup failed: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="Could not read inventory product") from error
    if old is None:
        raise HTTPException(status_code=404, detail="Product not found")
    old_image = (old.get("inventory") or {}).get("image_path", "")
    image_url, image_path = ((old.get("inventory") or {}).get("image_url", old.get("image", "")), old_image)
    replacement = image is not None and bool(image.filename)
    if replacement:
        image_url, image_path = upload_github_image(image)
    now = datetime.now(timezone.utc)
    try:
        products_collection().update_one({"_id": old["_id"]}, {"$set": {"name": name.strip(), "category": category.strip(), "price": price, "image": image_url, "inventory.description": description.strip(), "inventory.stock": stock, "inventory.image_url": image_url, "inventory.image_path": image_path, "inventory.updated_at": now, "inventory.updated_by": admin}})
    except PyMongoError as error:
        log_mongo_error("Inventory update", error)
        if replacement:
            try:
                delete_github_image(image_path)
            except HTTPException:
                pass
        raise HTTPException(status_code=503, detail="Could not update product in inventory") from error
    if replacement and old_image:
        try:
            delete_github_image(old_image)
        except HTTPException:
            pass
    return inventory_view({"name": name, "category": category, "price": price, "inventory": {"product_id": product_id, "description": description, "stock": stock, "image_url": image_url, "image_path": image_path, "created_at": (old.get("inventory") or {}).get("created_at"), "updated_at": now, "created_by": (old.get("inventory") or {}).get("created_by"), "updated_by": admin}})


@app.delete("/api/inventory/products/{product_id}")
def delete_inventory_product(product_id: str, admin: str = Depends(inventory_admin)):
    product_id = clean_inventory_id(product_id)
    products = products_collection()
    try:
        old = products.find_one({"inventory.product_id": product_id})
    except PyMongoError as error:
        logger.error("Inventory delete lookup failed: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="Could not read inventory product") from error
    if old is None:
        raise HTTPException(status_code=404, detail="Product not found")
    try:
        result = products.delete_one({"_id": old["_id"]})
    except PyMongoError as error:
        log_mongo_error(f"Inventory delete for admin {admin}", error)
        raise HTTPException(status_code=503, detail="Could not delete product") from error
    if result.deleted_count and (old.get("inventory") or {}).get("image_path"):
        try:
            delete_github_image(old["inventory"]["image_path"])
        except HTTPException:
            raise HTTPException(status_code=502, detail="Product was deleted, but its GitHub image could not be removed")
    try:
        _client["valtoro"]["inventory_audit"].insert_one({
            "action": "delete", "product_id": product_id,
            "product_name": old.get("name", ""), "admin": admin,
            "deleted_at": datetime.now(timezone.utc),
        })
    except PyMongoError as error:
        logger.error("Inventory delete audit write failed: %s", type(error).__name__)
    return {"deleted": True, "product_id": product_id}


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
        log_mongo_error("Visitor tracking", error)
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
        raise HTTPException(status_code=503, detail="Could not read visitor count") from error
