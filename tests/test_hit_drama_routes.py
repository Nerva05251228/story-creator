import os
import sys
import tempfile
import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}",
)

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tests.env_defaults import apply_test_env_defaults  # noqa: E402

apply_test_env_defaults()

import database  # noqa: E402
import models  # noqa: E402
from api.routers import hit_dramas  # noqa: E402


AUTH_HEADERS = {"Authorization": "Bearer editor-token"}


def _excel_bytes(rows):
    output = BytesIO()
    pd.DataFrame(rows).to_excel(output, index=False)
    output.seek(0)
    return output.getvalue()


class HitDramaRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._httpx_client_init = httpx.Client.__init__
        if "app" not in cls._httpx_client_init.__code__.co_varnames:
            def compatible_client_init(self, *args, app=None, **kwargs):
                return cls._httpx_client_init(self, *args, **kwargs)

            httpx.Client.__init__ = compatible_client_init

    @classmethod
    def tearDownClass(cls):
        httpx.Client.__init__ = cls._httpx_client_init

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        os.chdir(self.tempdir.name)

        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

        def override_get_db():
            request_db = self.Session()
            try:
                yield request_db
            finally:
                request_db.close()

        self.app = FastAPI()
        self.app.include_router(hit_dramas.router)
        self.app.dependency_overrides[database.get_db] = override_get_db
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.user = self._seed_user()

    def tearDown(self):
        self.app.dependency_overrides.clear()
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        os.chdir(self.old_cwd)
        self.tempdir.cleanup()

    def _seed_user(self, username="editor", token="editor-token"):
        db = self.Session()
        try:
            user = models.User(username=username, token=token)
            db.add(user)
            db.commit()
            return user
        finally:
            db.close()

    def _seed_drama(
        self,
        drama_name="Seed Drama",
        view_count="10w",
        opening_15_sentences="Opening",
        first_episode_script="Script",
        online_time="2026.05.01",
        created_by="editor",
        is_deleted=False,
    ):
        db = self.Session()
        try:
            drama = models.HitDrama(
                drama_name=drama_name,
                view_count=view_count,
                opening_15_sentences=opening_15_sentences,
                first_episode_script=first_episode_script,
                online_time=online_time,
                created_by=created_by,
                is_deleted=is_deleted,
            )
            db.add(drama)
            db.commit()
            return drama.id
        finally:
            db.close()

    def _seed_history(
        self,
        drama_id,
        edited_by="editor",
        edited_at=datetime(2026, 5, 2, 9, 0, 0),
    ):
        db = self.Session()
        try:
            history = models.HitDramaEditHistory(
                drama_id=drama_id,
                action_type="update",
                field_name="\u5267\u540d",
                old_value="Old",
                new_value="New",
                edited_by=edited_by,
                edited_at=edited_at,
            )
            db.add(history)
            db.commit()
            return history.id
        finally:
            db.close()

    def test_create_and_list_normalize_payload_and_require_auth(self):
        invalid_auth = self.client.post(
            "/api/hit-dramas",
            json={"drama_name": "Blocked"},
            headers={"Authorization": "Bearer invalid-token"},
        )
        self.assertEqual(invalid_auth.status_code, 401)
        self.assertEqual(invalid_auth.json(), {"detail": "Invalid authentication token"})

        response = self.client.post(
            "/api/hit-dramas",
            json={
                "drama_name": "  Launch Drama  ",
                "view_count": " 100w ",
                "opening_15_sentences": " line 1\r\nline 2 ",
                "first_episode_script": " script\rbody ",
                "online_time": "2026-5-2",
            },
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["drama_name"], "Launch Drama")
        self.assertEqual(payload["view_count"], "100w")
        self.assertEqual(payload["opening_15_sentences"], "line 1\nline 2")
        self.assertEqual(payload["first_episode_script"], "script\nbody")
        self.assertEqual(payload["online_time"], "2026.05.02")
        self.assertIsNone(payload["video_filename"])
        self.assertEqual(payload["created_by"], "editor")

        list_response = self.client.get("/api/hit-dramas", headers=AUTH_HEADERS)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(
            [item["drama_name"] for item in list_response.json()],
            ["Launch Drama"],
        )

        db = self.Session()
        try:
            history = db.query(models.HitDramaEditHistory).one()
            self.assertEqual(history.action_type, "create")
            self.assertEqual(history.edited_by, "editor")
            self.assertEqual(history.new_value, "\u521b\u5efa\u8bb0\u5f55\uff1aLaunch Drama")
        finally:
            db.close()

    def test_update_tracks_changed_fields_and_rejects_invalid_online_time(self):
        drama_id = self._seed_drama()

        invalid_response = self.client.put(
            f"/api/hit-dramas/{drama_id}",
            json={"online_time": "2026/99/99"},
            headers=AUTH_HEADERS,
        )
        self.assertEqual(invalid_response.status_code, 400)
        self.assertEqual(invalid_response.json(), {"detail": "\u4e0a\u7ebf\u65f6\u95f4\u4e0d\u662f\u6709\u6548\u65e5\u671f"})

        response = self.client.put(
            f"/api/hit-dramas/{drama_id}",
            json={
                "drama_name": "  Updated Drama ",
                "view_count": "10w",
                "online_time": "2026.5.3",
            },
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["drama_name"], "Updated Drama")
        self.assertEqual(payload["view_count"], "10w")
        self.assertEqual(payload["online_time"], "2026.05.03")

        db = self.Session()
        try:
            histories = db.query(models.HitDramaEditHistory).order_by(
                models.HitDramaEditHistory.id
            ).all()
            self.assertEqual([item.field_name for item in histories], ["\u5267\u540d", "\u4e0a\u7ebf\u65f6\u95f4"])
            self.assertEqual(histories[0].old_value, "Seed Drama")
            self.assertEqual(histories[0].new_value, "Updated Drama")
        finally:
            db.close()

    def test_delete_soft_deletes_and_history_keeps_record_name(self):
        drama_id = self._seed_drama(drama_name="Delete Drama")

        response = self.client.delete(f"/api/hit-dramas/{drama_id}", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"message": "\u5220\u9664\u6210\u529f", "drama_id": drama_id})
        self.assertEqual(
            self.client.get("/api/hit-dramas", headers=AUTH_HEADERS).json(),
            [],
        )

        db = self.Session()
        try:
            drama = db.query(models.HitDrama).filter(models.HitDrama.id == drama_id).one()
            history = db.query(models.HitDramaEditHistory).one()
            self.assertTrue(drama.is_deleted)
            self.assertEqual(history.action_type, "delete")
            self.assertEqual(history.old_value, "\u5267\u540d\uff1aDelete Drama")
            self.assertEqual(history.new_value, "\u5df2\u5220\u9664")
        finally:
            db.close()

    def test_history_filters_by_user_drama_and_date(self):
        target_id = self._seed_drama(drama_name="Target Drama")
        other_id = self._seed_drama(drama_name="Other Drama")
        self._seed_history(
            target_id,
            edited_by="editor",
            edited_at=datetime(2026, 5, 2, 9, 0, 0),
        )
        self._seed_history(
            other_id,
            edited_by="other",
            edited_at=datetime(2026, 5, 2, 10, 0, 0),
        )

        response = self.client.get(
            "/api/hit-dramas/history",
            params={
                "user_filter": "edit",
                "drama_name_filter": "Target",
                "start_date": "2026-05-02T08:00:00",
                "end_date": "2026-05-02T09:30:00",
            },
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["drama_id"], target_id)
        self.assertEqual(payload[0]["drama_name"], "Target Drama")
        self.assertEqual(payload[0]["edited_by"], "editor")

    def test_upload_video_saves_file_updates_filename_and_history(self):
        drama_id = self._seed_drama(drama_name="Video Drama")

        response = self.client.post(
            "/api/hit-dramas/upload-video",
            data={"drama_id": str(drama_id)},
            files={"file": ("clip.mp4", b"video-data", "video/mp4")},
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["message"], "\u4e0a\u4f20\u6210\u529f")
        self.assertTrue(payload["filename"].endswith("_clip.mp4"))

        saved_path = Path("uploads") / "hit_drama_videos" / payload["filename"]
        self.assertEqual(saved_path.read_bytes(), b"video-data")

        db = self.Session()
        try:
            drama = db.query(models.HitDrama).filter(models.HitDrama.id == drama_id).one()
            history = db.query(models.HitDramaEditHistory).one()
            self.assertEqual(drama.video_filename, payload["filename"])
            self.assertEqual(history.field_name, "\u89c6\u9891")
            self.assertEqual(history.old_value, "\u65e0")
            self.assertEqual(history.new_value, payload["filename"])
        finally:
            db.close()

    def test_import_excel_appends_rows_normalizes_dates_and_skips_blank_names(self):
        excel = _excel_bytes([
            {
                "\u5267\u540d": " Drama A ",
                "\u64ad\u653e\u91cf": "1w",
                "\u5f00\u593415\u53e5": "A1\r\nA2",
                "\u7b2c\u4e00\u96c6\u6587\u6848": "Script A",
                "\u4e0a\u7ebf\u65f6\u95f4": "2026/5/2",
            },
            {
                "\u5267\u540d": "",
                "\u64ad\u653e\u91cf": "skip",
                "\u5f00\u593415\u53e5": "",
                "\u7b2c\u4e00\u96c6\u6587\u6848": "",
                "\u4e0a\u7ebf\u65f6\u95f4": "",
            },
            {
                "\u5267\u540d": "Drama B",
                "\u64ad\u653e\u91cf": "",
                "\u5f00\u593415\u53e5": "",
                "\u7b2c\u4e00\u96c6\u6587\u6848": "Script B",
                "\u4e0a\u7ebf\u65f6\u95f4": "",
            },
        ])

        response = self.client.post(
            "/api/hit-dramas/import-excel",
            data={"import_mode": "append"},
            files={
                "file": (
                    "hit-dramas.xlsx",
                    excel,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)
        self.assertEqual(response.json()["import_mode"], "append")

        db = self.Session()
        try:
            dramas = db.query(models.HitDrama).order_by(models.HitDrama.id).all()
            self.assertEqual([item.drama_name for item in dramas], ["Drama A", "Drama B"])
            self.assertEqual(dramas[0].opening_15_sentences, "A1\nA2")
            self.assertEqual(dramas[0].online_time, "2026.05.02")
            self.assertEqual(dramas[1].online_time, "")
        finally:
            db.close()

    def test_import_excel_overwrite_deletes_existing_rows_and_validates_columns(self):
        existing_id = self._seed_drama(drama_name="Old Drama")
        self._seed_history(existing_id)

        response = self.client.post(
            "/api/hit-dramas/import-excel",
            data={"import_mode": "overwrite"},
            files={
                "file": (
                    "hit-dramas.xlsx",
                    _excel_bytes([
                        {
                            "\u5267\u540d": "New Drama",
                            "\u64ad\u653e\u91cf": "9w",
                            "\u5f00\u593415\u53e5": "Opening",
                            "\u7b2c\u4e00\u96c6\u6587\u6848": "Script",
                            "\u4e0a\u7ebf\u65f6\u95f4": "2026.05.04",
                        }
                    ]),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["import_mode"], "overwrite")

        bad_columns = self.client.post(
            "/api/hit-dramas/import-excel",
            data={"import_mode": "append"},
            files={
                "file": (
                    "bad.xlsx",
                    _excel_bytes([{"missing": "value"}]),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            headers=AUTH_HEADERS,
        )

        self.assertEqual(bad_columns.status_code, 400)
        self.assertEqual(
            bad_columns.json(),
            {"detail": "Excel\u683c\u5f0f\u4e0d\u6b63\u786e\uff0c\u7f3a\u5c11\u5fc5\u8981\u7684\u5217"},
        )

        db = self.Session()
        try:
            dramas = db.query(models.HitDrama).all()
            histories = db.query(models.HitDramaEditHistory).all()
            self.assertEqual(len(dramas), 1)
            self.assertEqual(dramas[0].drama_name, "New Drama")
            self.assertEqual(histories, [])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
