# Valtoro API

Read-only catalog for the storefront. Images stay in `valtoro/public/images`; Mongo stores the path.

Inventory administration is available at `POST/GET /api/inventory/products` and `GET/PUT/DELETE /api/inventory/products/{product_id}`. Mutations and inventory reads require one of three server-configured admin keys. Inventory records are stored in the existing `valtoro.products` collection under an `inventory` subdocument, preserving the legacy storefront fields. Create/update audit fields store the matching admin name; deletion events are recorded in `valtoro.inventory_audit`. Images are uploaded to the configured UI repository over the GitHub Contents API. When that repository is private, images are served through the constrained `/api/inventory/images/{filename}` endpoint so the GitHub token stays on the server; the UI provides a same-origin `/api/images/{filename}` proxy.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Put the Atlas connection string in `.env` as `MONGODB_URI`, including the database name `/valtoro`.
Configure `INVENTORY_ADMIN_ANUROOP_KEY`, `INVENTORY_ADMIN_VARUN_KEY`, `INVENTORY_ADMIN_VINIT_KEY`, `GITHUB_TOKEN` (Contents read/write access), `GITHUB_REPO_OWNER`, `GITHUB_REPO_NAME`, `GITHUB_BRANCH`, and `GITHUB_IMAGE_FOLDER` using `.env.example`. `GITHUB_UI_REPO_OWNER`, `GITHUB_UI_REPO_NAME`, and `GITHUB_UI_BRANCH` optionally identify the repository and branch used to build the raw image URL; defaults follow the storage repository configuration. Never expose these values to the browser.

For inventory 503 errors, check backend logs for the safe MongoDB exception class. Verify `MONGODB_URI` is set in the backend process environment and that credentials, URI encoding, Atlas database access, and Atlas Network Access/IP allowlisting are correct. A successful `OPTIONS` response only confirms browser CORS preflight, not Atlas connectivity.

When configuring `MONGODB_URI` in Vercel, paste it as one line with no trailing newline or whitespace. The backend trims surrounding whitespace defensively, but the deployment setting should still be corrected.

For Vercel deployments, set `MONGODB_URI` and the GitHub variables directly in the backend project's **Settings → Environment Variables** for the environment serving the API; `.env.example` is only a template and is not loaded by Vercel. A MongoDB `WriteConcernError` mentioning `majority\n` means the deployed backend is still receiving a newline or has not yet been redeployed with the URI trimming fix. A GitHub HTTP 401 means `GITHUB_TOKEN` is invalid/expired or includes extra text; set it to the token value alone (no `Bearer ` prefix) and grant that token access to the private UI repo with Contents read/write permission. Then redeploy the backend.

The local app loads environment variables from `.env`; editing `.env.example` alone does not configure the running API. Copy the relevant values into `.env` (or set them in the service environment) and restart FastAPI. For upload failures, inspect the HTTP response status and JSON `detail`; a request must include non-empty image bytes in the multipart `image` part.

```bash
python seed_products.py
uvicorn app.main:app --reload --port 8000
```

`GET http://localhost:8000/api/products` returns `{ "products": [...] }`. Optional filter: `?category=Everyday`.

The inventory upload form uses multipart fields `product_id`, `name`, `price`, `stock`, optional `category`/`description`, and `image`. The backend checks the image content type and size (maximum 5 MB). Validate the full content in a production threat model before allowing untrusted administrators to upload files.
