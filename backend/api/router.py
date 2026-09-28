# backend/api/router.py
# API 路由总入口：聚合本项目全部子路由，供 main.py 统一挂载到 /api/v1

from fastapi import APIRouter

from backend.api.v1 import (
    auth,
    qa,
    exam,
    lesson_prep,
    learning_analysis,
    homework,
    unified_chat,
)

api_router = APIRouter()                              # 总路由

# 把每个子 router 带前缀 + 标签聚合进来（前缀对齐原 main.py 的挂载方式）
api_router.include_router(auth.router,              prefix="/auth",              tags=["认证"])
api_router.include_router(unified_chat.router,      prefix="/chat",              tags=["AI助手"])   # 统一入口
api_router.include_router(qa.router,                prefix="/qa",                tags=["智能问答"])
api_router.include_router(exam.router,              prefix="/exam",              tags=["试卷批改"])
api_router.include_router(lesson_prep.router,       prefix="/lesson-prep",       tags=["智能备课"])
api_router.include_router(learning_analysis.router, prefix="/learning-analysis", tags=["学情报告"])
api_router.include_router(homework.router,          prefix="",                   tags=["作业"])   # 端点已含 /homework，不能再套前缀
