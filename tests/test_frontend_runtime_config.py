import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class FrontendRuntimeConfigTests(unittest.TestCase):
    def test_app_js_uses_local_provider_stats_proxy(self):
        source = (ROOT_DIR / "frontend" / "js" / "app.js").read_text(encoding="utf-8")

        self.assertIn("apiRequest('/api/video/provider-stats')", source)
        self.assertNotIn("api_sora/" + "stats/providers", source)
        self.assertNotRegex(source, r"Bearer\s+sk-[A-Za-z0-9_-]{16,}")

    def test_admin_pages_do_not_embed_admin_password(self):
        for relative_path in (
            "frontend/admin.html",
            "frontend/billing.html",
            "frontend/billing_rules.html",
            "frontend/dashboard.html",
            "frontend/manage.html",
        ):
            with self.subTest(relative_path=relative_path):
                source = (ROOT_DIR / relative_path).read_text(encoding="utf-8")

                self.assertNotRegex(source, r"admin\d{4}")
                self.assertNotIn("const ADMIN_PASSWORD", source)
                self.assertNotIn("=== ADMIN_PASSWORD", source)

    def test_dashboard_reprompts_when_backend_rejects_admin_password(self):
        source = (ROOT_DIR / "frontend" / "dashboard.html").read_text(encoding="utf-8")

        self.assertIn("function handleAdminAuthFailure", source)
        self.assertIn("response.status===403", source)
        self.assertIn("clearAdminAuth();showAdminAuthModal()", source)

    def test_admin_page_has_no_malformed_toolbar_markup(self):
        source = (ROOT_DIR / "frontend" / "admin.html").read_text(encoding="utf-8")

        for malformed in ("?/button>", "?/th>", "?/h3>", "?/label>"):
            self.assertNotIn(malformed, source)
        self.assertIn('id="usernameInput"', source)
        self.assertIn("function handleAdminAuthFailure", source)

    def test_manage_page_reprompts_when_backend_rejects_admin_password(self):
        source = (ROOT_DIR / "frontend" / "manage.html").read_text(encoding="utf-8")

        self.assertIn("function handleAdminAuthFailure", source)
        self.assertIn("async function adminFetch(url, options = {})", source)
        self.assertIn("headers: getAdminAuthHeaders(options.headers || {})", source)
        self.assertIn("'X-Admin-Password': adminPassword", source)
        self.assertIn("response.status === 403", source)
        self.assertIn("clearAdminAuth();", source)
        self.assertIn("showAdminAuthModal();", source)
        self.assertNotRegex(source, r"fetch\((?:'|`)/api")

    def test_admin_history_reprompts_when_backend_rejects_admin_password(self):
        source = (ROOT_DIR / "frontend" / "admin.html").read_text(encoding="utf-8")
        load_start = source.index("async function loadAdminHitDramaHistory()")
        revert_start = source.index("async function revertHitDramaHistory")
        script_end = source.index("</script>", revert_start)

        for section in (source[load_start:revert_start], source[revert_start:script_end]):
            with self.subTest(section=section.splitlines()[0].strip()):
                self.assertIn("headers: getAdminAuthHeaders()", section)
                self.assertIn("response.status === 403", section)
                self.assertIn("handleAdminAuthFailure();", section)


if __name__ == "__main__":
    unittest.main()
