from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response

from tendercite.api.dependencies import get_repository
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.export import export_records, render_export

router = APIRouter(tags=["exports"])


@router.get("/exports")
def export(
    repository: Annotated[SqliteRepository, Depends(get_repository)],
    format: Literal["json", "csv", "markdown"] = "json",
    analysis_run_id: str | None = None,
):
    media, suffix = {
        "json": ("application/json", "json"),
        "csv": ("text/csv", "csv"),
        "markdown": ("text/markdown", "md"),
    }[format]
    return Response(
        render_export(export_records(repository, analysis_run_id), format),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="tendercite.{suffix}"'},
    )
