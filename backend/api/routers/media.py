from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


router = APIRouter()

FILE_SERVING_ROOTS = (
    Path("uploads") / "hit_drama_videos",
    Path("uploads"),
    Path("videos"),
)


def _resolve_file_serving_candidate(root: Path, filename: str) -> Optional[Path]:
    root_path = root.resolve()
    candidate = (root / filename).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError:
        return None
    return candidate


@router.get("/files/{filename:path}")
async def get_file(filename: str):
    """Unified file access endpoint."""
    for root in FILE_SERVING_ROOTS:
        file_path = _resolve_file_serving_candidate(root, filename)
        if file_path and file_path.is_file():
            return FileResponse(str(file_path))

    raise HTTPException(status_code=404, detail="文件不存在")
