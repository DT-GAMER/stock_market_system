import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from ngx_research.config import settings


async def save_upload(upload: UploadFile) -> tuple[str, int, str]:
    filename = Path(upload.filename or "report").name
    suffix = Path(filename).suffix.lower()
    raw = await upload.read()
    digest = hashlib.sha256(raw).hexdigest()

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{digest[:12]}-{uuid4().hex}{suffix}"
    stored_path = upload_dir / stored_name
    stored_path.write_bytes(raw)

    return str(stored_path), len(raw), digest
