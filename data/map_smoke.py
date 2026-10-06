import os
import sys
from pathlib import Path
from dotenv import dotenv_values
import uvicorn
from fastapi.staticfiles import StaticFiles

root = Path(__file__).resolve().parents[1]
os.environ['MAP_DATABASE_URL'] = dotenv_values(root / '.env')['DATABASE_URL']
sys.path.insert(0, str(root / 'data' / 'map-backend-release'))
from src.api.map_app import app
app.mount('/', StaticFiles(directory=root / 'data' / 'map-release' / 'build' / 'web', html=True))
uvicorn.run(app, host='127.0.0.1', port=8002)
