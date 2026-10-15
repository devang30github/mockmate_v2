import os
import pytest

os.environ["APP_ENV"] = "test"
os.environ["PROVIDER_MODE"] = "mock"
os.environ["LLM_MODE"] = "mock"


@pytest.fixture
def anyio_backend():
    return "asyncio"