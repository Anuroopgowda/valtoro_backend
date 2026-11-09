# Valtoro API

Read-only catalog for the storefront. Images stay in `valtoro/public/images`; Mongo stores the path.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Put the Atlas connection string in `.env` as `MONGODB_URI`, including the database name `/valtoro`.

```bash
python seed_products.py
uvicorn app.main:app --reload --port 8000
```

`GET http://localhost:8000/api/products` returns `{ "products": [...] }`. Optional filter: `?category=Everyday`.
