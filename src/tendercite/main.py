from fastapi import FastAPI

from tendercite import __version__
from tendercite.api.routes.analyses import router as analyses_router
from tendercite.api.routes.documents import router as documents_router
from tendercite.api.routes.evidence import router as evidence_router
from tendercite.api.routes.health import router as health_router
from tendercite.api.routes.search import router as search_router

app = FastAPI(
    title="TenderCite API",
    version=__version__,
    description="Evidence-grounded analysis of public procurement documents.",
)

app.include_router(health_router)
app.include_router(documents_router, prefix="/api/v1")
app.include_router(evidence_router, prefix="/api/v1")


app.include_router(search_router, prefix="/api/v1")

app.include_router(analyses_router, prefix="/api/v1")
