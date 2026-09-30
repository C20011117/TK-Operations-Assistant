"""本机视频文件：保存（按 SHA-256 去重）与带签名的播放地址。

<video> 标签不能带 Authorization 头，所以播放地址用本次启动令牌做 HMAC 签名并设过期时间；
本机接口保护中间件只对 /api/v1/media/ 放行“签名有效”的 GET / HEAD 请求。
"""

import hashlib
import hmac
import os
import time
import uuid
from pathlib import Path

MEDIA_PREFIX = "/api/v1/media/"
URL_TTL_SECONDS = 3600
MAX_BYTES = 2 * 1024**3  # 单个文件 2 GB
ALLOWED_MIME = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-m4v": ".m4v",
}
EXT_MIME = {v: k for k, v in ALLOWED_MIME.items()}

_key: bytes | None = None


def set_key(token: str) -> None:
    global _key
    _key = hashlib.sha256(b"tkws-media:" + token.encode()).digest()


def _sig(asset_id: str, exp: int) -> str:
    if _key is None:
        raise RuntimeError("media key not initialised")
    return hmac.new(_key, f"{asset_id}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def signed_url(asset_id: str, now: float | None = None) -> str:
    # 过期时间取整到 10 分钟：同一个视频在短时间内地址不变，播放器不会因列表刷新而重新加载
    base = int(now if now is not None else time.time())
    exp = (base // 600 + 1) * 600 + URL_TTL_SECONDS
    return f"{MEDIA_PREFIX}{asset_id}?exp={exp}&sig={_sig(asset_id, exp)}"


def verify(path: str, query: str, now: float | None = None) -> bool:
    if _key is None or not path.startswith(MEDIA_PREFIX):
        return False
    asset_id = path[len(MEDIA_PREFIX) :]
    if not asset_id or "/" in asset_id:
        return False
    params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
    try:
        exp = int(params.get("exp", ""))
    except ValueError:
        return False
    if exp < (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(params.get("sig", ""), _sig(asset_id, exp))


def guess_mime(filename: str, content_type: str | None) -> str | None:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in ALLOWED_MIME:
        return ct
    return EXT_MIME.get(Path(filename).suffix.lower())


class TooLarge(Exception):
    pass


class Incoming:
    """把上传的字节流写进临时文件，同时计算 SHA-256；完成后按哈希移动到最终位置。"""

    def __init__(self, files_dir: Path) -> None:
        self.files_dir = files_dir
        tmp_dir = files_dir / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        self.tmp = tmp_dir / f"upload-{uuid.uuid4().hex}"
        self._f = open(self.tmp, "wb")  # noqa: SIM115 — 生命周期由 finish/discard 管理
        self._h = hashlib.sha256()
        self.size = 0

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > MAX_BYTES:
            raise TooLarge()
        self._h.update(chunk)
        self._f.write(chunk)

    def finish(self, mime: str) -> tuple[str, str]:
        """返回 (sha256, 相对 files_dir 的路径)。同一内容已存在时丢弃临时文件。"""
        self._f.close()
        sha = self._h.hexdigest()
        rel = Path("videos") / sha[:2] / f"{sha}{ALLOWED_MIME[mime]}"
        dest = self.files_dir / rel
        if dest.exists():
            self.tmp.unlink(missing_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(self.tmp, dest)
        return sha, rel.as_posix()

    def discard(self) -> None:
        try:
            self._f.close()
        finally:
            self.tmp.unlink(missing_ok=True)
