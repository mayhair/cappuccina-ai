import pytest
from fastapi import HTTPException

from app import ratelimit


@pytest.fixture(autouse=True)
def _clean():
    ratelimit.reset()
    yield
    ratelimit.reset()


def test_allows_ten_then_blocks_the_eleventh():
    for i in range(10):
        ratelimit.check("u1", limit=10, now=100 + i)
    with pytest.raises(HTTPException) as exc:
        ratelimit.check("u1", limit=10, now=110)
    assert exc.value.status_code == 429
    assert int(exc.value.headers["Retry-After"]) >= 1


def test_window_slides_and_users_are_independent():
    for i in range(10):
        ratelimit.check("u1", limit=10, now=100)
    ratelimit.check("u2", limit=10, now=100)
    ratelimit.check("u1", limit=10, now=161)
