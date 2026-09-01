from fastapi.testclient import TestClient
from app.api.server import create_app
from app.config.settings import Settings
from app.core.database import Database
from app.core.engine import RouterEngine

settings = Settings()
settings.update("orthanc", {"enabled": False})
database = Database(settings.paths)
database.initialize()
engine = RouterEngine(settings, database)
with TestClient(create_app(engine, start_engine=False)) as client:
    print(client.post("/api/auth/provision", json={"username": "voxeladmin", "password": "SenhaInicial@2026"}).status_code)
    print(client.post("/api/auth/login", json={"username": "voxeladmin", "password": "SenhaInicial@2026"}).status_code)
    response = client.get("/api/system")
    print(response.status_code, response.text)
