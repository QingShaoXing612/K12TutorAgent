# run_auth.py（临时，仅用于本节测试）
from fastapi import FastAPI
from backend.api.v1.auth import router

app = FastAPI()
app.include_router(router)
