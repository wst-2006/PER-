import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """每次运行使用一个全新的临时目录（logs/pytest_tmp/<时间>_<进程号>），从不删除旧目录。

    有些机器把批量删除文件的操作拦截掉，pytest 默认"启动时清空临时目录"的做法会在那里失败。
    """
    if not config.option.basetemp:
        base = ROOT / "logs" / "pytest_tmp" / f"{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
        base.parent.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(base)
