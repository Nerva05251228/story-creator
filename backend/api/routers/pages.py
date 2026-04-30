from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


router = APIRouter()

FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"


@router.get("/")
async def root():
    """Serve the login page."""
    return FileResponse(FRONTEND_DIR / "login.html")


@router.get("/app")
async def app_page():
    """Serve the main app page."""
    return FileResponse(FRONTEND_DIR / "index.html")


@router.get("/admin")
async def admin_page():
    """Serve the admin page."""
    return FileResponse(FRONTEND_DIR / "admin.html")


@router.get("/model-select")
async def model_select_page():
    """Serve the model selection page."""
    return FileResponse(FRONTEND_DIR / "model_select.html")


@router.get("/billing")
async def billing_page():
    """Serve the billing page."""
    return FileResponse(FRONTEND_DIR / "billing.html")


@router.get("/billing-rules")
async def billing_rules_page():
    """Serve the billing rules page."""
    return FileResponse(FRONTEND_DIR / "billing_rules.html")


@router.get("/dashboard")
async def dashboard_page():
    """Serve the dashboard page."""
    return FileResponse(FRONTEND_DIR / "dashboard.html")


@router.get("/manage")
async def manage_page():
    """Serve the prompt management page."""
    return FileResponse(FRONTEND_DIR / "manage.html")
