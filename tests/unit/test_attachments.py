from __future__ import annotations

import base64
from pathlib import Path

import pytest

from hames.attachments import AttachmentUpload, admit_attachments, hydrate_message_attachments
from hames.blobs import BlobStore


def upload(name: str, media_type: str, content: bytes) -> AttachmentUpload:
    return AttachmentUpload(
        name=name,
        media_type=media_type,
        data_base64=base64.b64encode(content).decode("ascii"),
    )


def test_text_attachment_is_durable_and_hydrates_into_context(tmp_path: Path) -> None:
    blobs = BlobStore(tmp_path / "blobs")
    references = admit_attachments(
        blobs,
        [upload("notes.md", "text/markdown", b"hello from file")],
        image_input_supported=False,
    )

    images, texts = hydrate_message_attachments(
        [reference.model_dump(mode="json") for reference in references], blobs
    )

    assert images == []
    assert "hello from file" in texts[0]
    assert blobs.read(references[0].digest) == b"hello from file"


def test_image_attachment_requires_model_capability(tmp_path: Path) -> None:
    blobs = BlobStore(tmp_path / "blobs")
    image = upload("pixel.png", "image/png", b"\x89PNG\r\n\x1a\nrest")

    with pytest.raises(ValueError, match="does not advertise image"):
        admit_attachments(blobs, [image], image_input_supported=False)

    references = admit_attachments(blobs, [image], image_input_supported=True)
    images, _ = hydrate_message_attachments(
        [reference.model_dump(mode="json") for reference in references], blobs
    )
    assert images[0].data_base64 == image.data_base64
