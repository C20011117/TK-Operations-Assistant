"""本机接口保护（纯 ASGI 中间件）。

后端只监听 127.0.0.1，但本机其他程序、浏览器里的网页仍可能访问它，所以每个请求都要：
1. Host 必须是 127.0.0.1 / localhost（防 DNS 重绑定）；
2. 携带外壳本次启动生成的令牌 Authorization: Bearer <token>（常量时间比较）。
   例外：/api/v1/media/ 的 GET / HEAD 可以改用签名地址（<video> 标签不能带请求头，见 platform/media）。
CORS 预检（OPTIONS）由外层 CORSMiddleware 处理，不会到达这里。
"""

import hmac
import json

from starlette.types import ASGIApp, Receive, Scope, Send

from tk_workspace.platform import media

ALLOWED_HOSTNAMES = {"127.0.0.1", "localhost"}


def _hostname(host_header: str) -> str:
    if host_header.startswith("["):
        return host_header.split("]")[0] + "]"
    return host_header.rsplit(":", 1)[0] if ":" in host_header else host_header


class LocalAuthMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        if not token:
            raise ValueError("launch token must not be empty")
        self.app = app
        self._expected = f"Bearer {token}".encode()
        media.set_key(token)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        host = headers.get(b"host", b"").decode("latin-1")
        if _hostname(host) not in ALLOWED_HOSTNAMES:
            await _deny(send, 403, "host_not_allowed", "请求来源不被允许")
            return
        auth = headers.get(b"authorization", b"")
        if (
            not auth
            and scope.get("method") in ("GET", "HEAD")
            and media.verify(scope.get("path", ""), scope.get("query_string", b"").decode("latin-1"))
        ):
            await self.app(scope, receive, send)
            return
        if not hmac.compare_digest(auth, self._expected):
            await _deny(send, 401, "unauthorized", "缺少或错误的访问令牌")
            return
        await self.app(scope, receive, send)


async def _deny(send: Send, status: int, code: str, message: str) -> None:
    body = json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
