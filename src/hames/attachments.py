"""Durable user attachment admission and provider hydration."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from hames.blobs import BlobStore
from hames.providers.base import ProviderAttachment

IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".conf",
    ".cpp",
    ".css",
    ".csv",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".md",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
MAX_ATTACHMENTS = 8
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024


class AttachmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttachmentUpload(AttachmentModel):
    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=100)
    data_base64: str = Field(min_length=1, max_length=24_000_000)


class AttachmentReference(AttachmentModel):
    digest: str
    name: str
    media_type: str
    kind: Literal["image", "text"]
    size: int = Field(ge=0)


def admit_attachments(
    blobs: BlobStore,
    uploads: list[AttachmentUpload],
    *,
    image_input_supported: bool,
) -> list[AttachmentReference]:
    if len(uploads) > MAX_ATTACHMENTS:
        raise ValueError(f"a message may contain at most {MAX_ATTACHMENTS} attachments")
    admitted: list[AttachmentReference] = []
    total = 0
    for upload in uploads:
        try:
            content = base64.b64decode(upload.data_base64, validate=True)
        except binascii.Error as exc:
            raise ValueError(f"{upload.name} is not valid base64 data") from exc
        total += len(content)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("message attachments exceed the 32 MB total limit")
        media_type = upload.media_type.lower().split(";", 1)[0].strip()
        if media_type in IMAGE_TYPES:
            if not image_input_supported:
                raise ValueError("the selected model does not advertise image input support")
            if len(content) > MAX_IMAGE_BYTES:
                raise ValueError(f"{upload.name} exceeds the 16 MB image limit")
            _validate_image_signature(media_type, content, upload.name)
            kind: Literal["image", "text"] = "image"
        elif media_type.startswith("text/") or Path(upload.name).suffix.lower() in TEXT_EXTENSIONS:
            if len(content) > MAX_TEXT_BYTES:
                raise ValueError(f"{upload.name} exceeds the 2 MB text file limit")
            try:
                content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"{upload.name} must be UTF-8 text") from exc
            kind = "text"
            if not media_type.startswith("text/"):
                media_type = "text/plain"
        else:
            raise ValueError(f"unsupported attachment type for {upload.name}: {media_type}")
        admitted.append(
            AttachmentReference(
                digest=blobs.put(content),
                name=upload.name,
                media_type=media_type,
                kind=kind,
                size=len(content),
            )
        )
    return admitted


def hydrate_message_attachments(
    references: object,
    blobs: BlobStore | None,
) -> tuple[list[ProviderAttachment], list[str]]:
    if not isinstance(references, list):
        return [], []
    images: list[ProviderAttachment] = []
    texts: list[str] = []
    for raw in cast(list[object], references):
        try:
            reference = AttachmentReference.model_validate(raw)
        except ValueError:
            continue
        content = blobs.read(reference.digest) if blobs is not None else b""
        if reference.kind == "text":
            if blobs is not None:
                texts.append(
                    f"<attached-file name={reference.name!r} media_type={reference.media_type!r}>\n"
                    f"{content.decode('utf-8')}\n</attached-file>"
                )
            continue
        images.append(
            ProviderAttachment(
                digest=reference.digest,
                name=reference.name,
                media_type=reference.media_type,
                data_base64=base64.b64encode(content).decode("ascii") if content else "",
            )
        )
    return images, texts


def _validate_image_signature(media_type: str, content: bytes, name: str) -> None:
    valid = (
        (media_type == "image/png" and content.startswith(b"\x89PNG\r\n\x1a\n"))
        or (media_type == "image/jpeg" and content.startswith(b"\xff\xd8\xff"))
        or (media_type == "image/gif" and content.startswith((b"GIF87a", b"GIF89a")))
        or (
            media_type == "image/webp"
            and len(content) >= 12
            and content[:4] == b"RIFF"
            and content[8:12] == b"WEBP"
        )
    )
    if not valid:
        raise ValueError(f"{name} does not match its declared image type")
