"""pytest 全局夹具：测试间环境隔离。

背景 (2026-08-25):
    多个测试文件 `import server` 会触发 load_dotenv(.env.local), 把
    QMT_BRIDGE_URL / QMT_BRIDGE_TOKEN / QMT_ENABLED 注入 os.environ。
    之后 fetcher.qmt_client.is_configured() 返回 True → qmt_data_api._use_bridge()
    为真 → 只 mock 了本地 xtdata 的测试被导到真实 QMT 桥 (拿到 5213 只真实
    股票 / 真实交易日历), 断言随机失败, 且结果依赖测试文件收集顺序。

    每个用例前清掉这批变量, 用例后恢复, 保证可重复、可乱序。
"""

import os

import pytest

_ENV_KEYS = (
    "QMT_BRIDGE_URL",
    "QMT_BRIDGE_TOKEN",
    "QMT_ENABLED",
    "QMT_PATH",
    "QUANT_DATA_ROOT",
    "QUANT_WORKERS",
)


@pytest.fixture(autouse=True)
def _isolate_qmt_env():
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}
    for key in _ENV_KEYS:
        os.environ.pop(key, None)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
