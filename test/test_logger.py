# test_logger.py（项目根目录）
from backend.core.logger import configure_logging, get_logger

configure_logging()                 # 全局配置一次
log = get_logger("demo")
log.info("user.login", user_id="u1", role="student")
log.warning("cache.miss", key="abc")
