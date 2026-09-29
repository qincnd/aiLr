import sys
import unittest
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app
from app.services.lightroom_bridge import MAX_ACTIVE_JOBS, lightroom_bridge


class LightroomBridgeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        lightroom_bridge._jobs.clear()
        lightroom_bridge._queue.clear()
        lightroom_bridge._last_seen = 0.0
        lightroom_bridge._selected_filename = ""
        self.client = TestClient(app)

    def test_cors_allows_local_vite_fallback_port_only(self) -> None:
        local = self.client.get(
            "/api/model/config",
            headers={"Origin": "http://127.0.0.1:5174"},
        )
        external = self.client.get(
            "/api/model/config",
            headers={"Origin": "https://example.com"},
        )

        self.assertEqual(local.headers.get("access-control-allow-origin"), "http://127.0.0.1:5174")
        self.assertNotIn("access-control-allow-origin", external.headers)

    def test_requires_online_plugin_and_selected_photo(self) -> None:
        payload = {"action": "preview", "settings": {"Exposure2012": 0.2}}
        self.assertEqual(self.client.post("/api/lightroom/jobs", json=payload).status_code, 409)

        self.client.post("/api/lightroom/heartbeat", content="")
        response = self.client.post("/api/lightroom/jobs", json=payload)
        self.assertEqual(response.status_code, 409)
        self.assertIn("选中一张照片", response.json()["detail"])

    def test_preview_job_round_trip_returns_rendered_image(self) -> None:
        self.client.post("/api/lightroom/heartbeat", content="DSC_1001.NEF")
        created = self.client.post(
            "/api/lightroom/jobs",
            json={
                "action": "preview",
                "settings": {"Exposure2012": 0.2, "Temperature": 5600},
            },
        )
        self.assertEqual(created.status_code, 200)
        job_id = created.json()["job_id"]

        claimed = self.client.get("/api/lightroom/jobs/next")
        self.assertEqual(claimed.status_code, 200)
        lines = claimed.text.splitlines()
        self.assertEqual(lines[:5], [job_id, "preview", "JPEG", "90", "2560"])
        self.assertIn("Temperature=5600", lines)

        completed = self.client.post(
            f"/api/lightroom/jobs/{job_id}/result",
            content=b"rendered-jpeg",
            headers={
                "Content-Type": "image/jpeg",
                "X-aiLr-Filename": quote("DSC_日落.jpg", safe=""),
            },
        )
        self.assertEqual(completed.status_code, 200)
        status = self.client.get(f"/api/lightroom/jobs/{job_id}").json()
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["filename"], "DSC_日落.jpg")

        image = self.client.get(f"/api/lightroom/jobs/{job_id}/image")
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers["content-type"], "image/jpeg")
        self.assertIn(quote("DSC_日落.jpg", safe=""), image.headers["content-disposition"])
        self.assertEqual(image.content, b"rendered-jpeg")
        self.assertEqual(self.client.get(f"/api/lightroom/jobs/{job_id}/image").status_code, 410)

    def test_invalid_settings_and_export_options_are_rejected(self) -> None:
        self.client.post("/api/lightroom/heartbeat", content="DSC_1001.NEF")
        invalid_temperature = self.client.post(
            "/api/lightroom/jobs",
            json={"action": "preview", "settings": {"Temperature": 0.5}},
        )
        self.assertEqual(invalid_temperature.status_code, 422)

        invalid_quality = self.client.post(
            "/api/lightroom/jobs",
            json={"action": "export", "format": "GIF", "quality": 120, "settings": {"Exposure2012": 0.2}},
        )
        self.assertEqual(invalid_quality.status_code, 422)

    def test_plugin_failure_is_reported_to_frontend(self) -> None:
        self.client.post("/api/lightroom/heartbeat", content="DSC_1001.NEF")
        created = self.client.post(
            "/api/lightroom/jobs",
            json={"action": "export", "format": "TIFF", "settings": {"Exposure2012": 0.2}},
        )
        job_id = created.json()["job_id"]
        self.client.get("/api/lightroom/jobs/next")
        self.client.post(f"/api/lightroom/jobs/{job_id}/failed", content="No selected Lightroom photo")

        result = self.client.get(f"/api/lightroom/jobs/{job_id}").json()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "No selected Lightroom photo")


    def test_develop_controls_endpoint_lists_every_group(self) -> None:
        payload = self.client.get("/api/develop/controls").json()
        groups = {group["id"]: group for group in payload["groups"]}
        keys = {control["key"] for group in payload["groups"] for control in group["controls"]}

        self.assertEqual(groups["basic"]["label"], "基本")
        self.assertIn("ToneCurvePV2012", keys)
        self.assertIn("ColorGradeHighlightSat", keys)
        self.assertEqual(payload["total"], len(keys))

    def test_job_lines_encode_numbers_enums_booleans_and_curves(self) -> None:
        self.client.post("/api/lightroom/heartbeat", content="DSC_1001.NEF")
        created = self.client.post(
            "/api/lightroom/jobs",
            json={
                "action": "preview",
                "settings": {
                    "Exposure2012": 0.35,
                    "ToneCurveName": "Medium Contrast",
                    "AutoTone": True,
                    "ToneCurvePV2012": "0,0;64,52;255,255",
                },
            },
        )
        self.assertEqual(created.status_code, 200)

        lines = self.client.get("/api/lightroom/jobs/next").text.splitlines()
        self.assertIn("Exposure2012=0.35", lines)
        self.assertIn("ToneCurveName=Medium%20Contrast", lines)
        self.assertIn("AutoTone=true", lines)
        self.assertIn("ToneCurvePV2012=0%2C0%3B64%2C52%3B255%2C255", lines)

    def test_probe_job_needs_no_settings_and_reports_host_capabilities(self) -> None:
        offline = self.client.post("/api/lightroom/jobs", json={"action": "probe", "settings": {}})
        self.assertEqual(offline.status_code, 409)

        # A probe only reads develop values, so it works without a selected photo.
        self.client.post("/api/lightroom/heartbeat", content="")
        created = self.client.post("/api/lightroom/jobs", json={"action": "probe", "settings": {}})
        self.assertEqual(created.status_code, 200)
        job_id = created.json()["job_id"]

        claimed = self.client.get("/api/lightroom/jobs/next").text.splitlines()
        self.assertEqual(claimed[:5], [job_id, "probe", "JPEG", "90", "2560"])
        self.assertEqual(len(claimed), 5)

        report = "SUPPORTED=Exposure2012,Dehaze\nUNSUPPORTED=PerspectiveScale: unknown parameter"
        reported = self.client.post(f"/api/lightroom/jobs/{job_id}/report", content=report)
        self.assertEqual(reported.status_code, 200)
        status = self.client.get(f"/api/lightroom/jobs/{job_id}").json()
        self.assertEqual(status["status"], "processing")
        self.assertEqual(status["report"], report)

    def test_rejected_settings_report_keeps_the_render_completed(self) -> None:
        self.client.post("/api/lightroom/heartbeat", content="DSC_1001.NEF")
        created = self.client.post(
            "/api/lightroom/jobs",
            json={"action": "preview", "settings": {"Exposure2012": 0.2, "PerspectiveScale": 120}},
        )
        job_id = created.json()["job_id"]
        self.client.get("/api/lightroom/jobs/next")
        self.client.post(
            f"/api/lightroom/jobs/{job_id}/report",
            content="APPLIED=1\nREJECTED=PerspectiveScale\nPerspectiveScale: unknown parameter",
        )
        self.client.post(
            f"/api/lightroom/jobs/{job_id}/result",
            content=b"rendered-jpeg",
            headers={"Content-Type": "image/jpeg"},
        )

        status = self.client.get(f"/api/lightroom/jobs/{job_id}").json()
        self.assertEqual(status["status"], "completed")
        self.assertIn("REJECTED=PerspectiveScale", status["report"])

    def test_render_jobs_require_settings(self) -> None:
        self.client.post("/api/lightroom/heartbeat", content="DSC_1001.NEF")
        response = self.client.post("/api/lightroom/jobs", json={"action": "preview", "settings": {}})
        self.assertEqual(response.status_code, 422)

    def test_stale_processing_job_fails_instead_of_polling_forever(self) -> None:
        """插件领取后不回传：任务被判成超时失败，而不是永远停在 processing。"""
        self.client.post("/api/lightroom/heartbeat", content="DSC_1001.NEF")
        created = self.client.post(
            "/api/lightroom/jobs",
            json={"action": "preview", "settings": {"Exposure2012": 0.2}},
        )
        job_id = created.json()["job_id"]
        self.client.get("/api/lightroom/jobs/next")

        # 把领取时间挪到很久以前，等价于插件领走任务后再也没有回应。
        lightroom_bridge._jobs[job_id].started_at = 1.0

        status = self.client.get(f"/api/lightroom/jobs/{job_id}").json()
        self.assertEqual(status["status"], "failed")
        self.assertIn("超时", status["error"])

    def test_finished_jobs_are_reclaimed_after_the_retention_window(self) -> None:
        """已完成的任务在保留窗口后从任务表移除，长会话不会累积任务与图片内存。"""
        self.client.post("/api/lightroom/heartbeat", content="DSC_1001.NEF")
        created = self.client.post(
            "/api/lightroom/jobs",
            json={"action": "preview", "settings": {"Exposure2012": 0.2}},
        )
        job_id = created.json()["job_id"]
        self.client.get("/api/lightroom/jobs/next")
        self.client.post(
            f"/api/lightroom/jobs/{job_id}/result",
            content=b"rendered-jpeg",
            headers={"Content-Type": "image/jpeg", "X-aiLr-Filename": "DSC_1001.jpg"},
        )
        self.assertIn(job_id, lightroom_bridge._jobs)

        lightroom_bridge._jobs[job_id].finished_at = 1.0

        # 任何一次读取都会顺手回收：再问这个任务只剩 404。
        self.assertEqual(self.client.get(f"/api/lightroom/jobs/{job_id}").status_code, 404)
        self.assertNotIn(job_id, lightroom_bridge._jobs)

    def test_active_job_cap_counts_claimed_jobs_too(self) -> None:
        """上限按「排队中 + 执行中」合计，插件领走任务后不能继续无限排入。"""
        self.client.post("/api/lightroom/heartbeat", content="DSC_1001.NEF")
        payload = {"action": "preview", "settings": {"Exposure2012": 0.2}}
        for _ in range(MAX_ACTIVE_JOBS):
            self.assertEqual(self.client.post("/api/lightroom/jobs", json=payload).status_code, 200)

        # 领走一个：队列变短了，但它仍然占着「执行中」的额度。
        self.assertEqual(self.client.get("/api/lightroom/jobs/next").status_code, 200)
        self.assertEqual(self.client.post("/api/lightroom/jobs", json=payload).status_code, 409)


if __name__ == "__main__":
    unittest.main()
