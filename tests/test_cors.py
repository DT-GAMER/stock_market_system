import anyio
from httpx import ASGITransport, AsyncClient

from ngx_research.main import app


def test_signup_preflight_allows_configured_origin() -> None:
    async def request_preflight():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.options(
                "/auth/signup",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )

    response = anyio.run(request_preflight)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
