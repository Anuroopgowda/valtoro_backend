import os
from dotenv import load_dotenv
from pymongo import MongoClient
import certifi

load_dotenv()

uri = os.getenv("MONGODB_URI")

try:
    client = MongoClient(
        uri,
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=10000
    )

    result = client.admin.command("ping")

    print("MongoDB connection successful!")
    print("Ping result:", result)

    db = client["valtoro"]

    print("Database:", db.name)
    print("Collections:", db.list_collection_names())

    client.close()

except Exception as e:
    print("MongoDB connection failed!")
    print(repr(e))