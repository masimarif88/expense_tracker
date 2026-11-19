from collections.abc import Awaitable, Callable
from typing import Protocol


class ReceiptStorage(Protocol):
    async def put(self, key: str, content: bytes, content_type: str) -> None: ...


class MemoryReceiptStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, content: bytes, content_type: str) -> None:
        self.objects[key] = (content, content_type)


def detect_image_type(content: bytes) -> tuple[str, str] | None:
    signatures: tuple[tuple[bytes, str, str], ...] = (
        (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
        (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
        (b"%PDF-", "application/pdf", ".pdf"),
    )
    for signature, content_type, extension in signatures:
        if content.startswith(signature):
            return content_type, extension
    return None


def validate_receipt(
    content: bytes, filename: str | None, max_bytes: int = 5 * 1024 * 1024
) -> tuple[str, str]:
    if not content:
        raise ValueError("Receipt file is empty")
    if len(content) > max_bytes:
        raise ValueError("Receipt file exceeds the 5 MB limit")
    detected = detect_image_type(content)
    if detected is None:
        raise ValueError("Receipt must be a PNG, JPEG, or PDF file")
    extension = (filename or "").lower().rsplit(".", 1)
    expected_extension = detected[1][1:]
    if len(extension) != 2 or extension[1] != expected_extension and not (
        expected_extension == "jpg" and extension[1] == "jpeg"
    ):
        raise ValueError("Receipt filename extension does not match its file contents")
    return detected


async def store_receipt(
    storage: ReceiptStorage,
    key: str,
    content: bytes,
    content_type: str,
) -> None:
    await storage.put(key, content, content_type)
