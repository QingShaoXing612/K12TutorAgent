# test_exceptions.py（项目根目录）
from backend.core.exceptions import TeachMateBaseError, LLMAPIError

# 抛出一个带上下文的异常，并用基类捕获
try:
    raise LLMAPIError("DeepSeek 超时", agent_type="qa", details={"timeout": 30})
except TeachMateBaseError as e:
    print("捕获:", type(e).__name__, "| msg:", e, "| agent:", e.agent_type, "| details:", e.details)

print("LLMAPIError 是 TeachMateBaseError 子类:", issubclass(LLMAPIError, TeachMateBaseError))
