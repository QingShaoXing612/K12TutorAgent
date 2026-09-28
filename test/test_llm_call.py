# test_llm_call.py（项目根目录，需要真实 DEEPSEEK_API_KEY）
import asyncio
from langchain_core.messages import HumanMessage
from backend.core.llm_factory import get_llm

async def main():
    llm = get_llm("qa")                                   # 通过工厂拿模型
    resp = await llm.ainvoke([HumanMessage(content="用一句话介绍 Python")])
    print("DeepSeek 回复:", resp.text)                     # .text 取文本（回顾 2.3）

asyncio.run(main())
