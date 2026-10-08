"""Local-only demo, with an isolated SQLite database and integrated AI worker."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


if __name__ == "__main__":
    from dotenv import dotenv_values
    # Read-only map catalog may use Supabase; consultation demo remains isolated.
    catalog_url = os.getenv("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if catalog_url:
        os.environ["MAP_DATABASE_URL"] = catalog_url
        os.environ["CATALOG_DATABASE_URL"] = catalog_url
    (ROOT / "data").mkdir(exist_ok=True)
    os.environ["DATABASE_URL"] = f"sqlite:///{ROOT / 'data' / 'consult-demo.db'}"
    os.environ["MOYAK_LOCAL_DEMO"] = "1"
    import uvicorn
    uvicorn.run("src.api.demo:app", host="127.0.0.1", port=8001, proxy_headers=False)
