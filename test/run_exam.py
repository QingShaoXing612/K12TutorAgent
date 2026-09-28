# run_exam.py（临时，仅用于 exam 端到端测试）
# 启动：python run_exam.py
# 挂载 auth + exam 两个 router，前缀 /api/v1/auth 和 /api/v1/exam
# 端口 8000（scripts/manual_tests/test_exam.py 打的就是这个）

import uvicorn
from fastapi import FastAPI

from backend.api.v1.auth import router as auth_router
from backend.api.v1.exam import router as exam_router

app = FastAPI()

app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(exam_router, prefix="/api/v1/exam")


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
