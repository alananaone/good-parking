#!/usr/bin/env python3
"""
臺北市路邊停車智慧導航與管理系統 - 全功能自動化測試套件 (run_tests.py)
檢驗項目：
1. 本地 SQLite 資料庫 (parking.db) 資料完整度
2. 5 大核心 REST API 端點回應格式與邏輯
3. 費用試算引擎之費率、收費時段與免收費日判斷
4. 前端 index.html 視覺規範合規性 (零 Emoji、零單向邊框、全形標點符號)
"""

import os
import io
import re
import json
import sqlite3
import unittest
from server import ParkingAPIHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "parking.db")
INDEX_PATH = os.path.join(BASE_DIR, "index.html")


class MockHandler(ParkingAPIHandler):
    """用於單元測試之 HTTP Request Handler 模擬器"""
    def __init__(self):
        self.wfile = io.BytesIO()
        self.headers_sent = {}
        self.status_code = None

    def send_response(self, code):
        self.status_code = code

    def send_header(self, k, v):
        self.headers_sent[k] = v

    def end_headers(self):
        pass


class TestTaipeiParkingSystem(unittest.TestCase):

    def setUp(self):
        self.assertTrue(os.path.exists(DB_PATH), "SQLite 資料庫檔案 parking.db 必須存在")
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()

    def test_database_tables_and_counts(self):
        """測試資料庫各資料表紀錄數是否符合實證資料規格"""
        cursor = self.conn.cursor()

        # 1. roads 表
        cursor.execute("SELECT count(*) FROM roads")
        road_count = cursor.fetchone()[0]
        self.assertGreaterEqual(road_count, 2000, "收費路段數應大於 2,000 條（實證 2,342 條）")

        # 2. cells 表
        cursor.execute("SELECT count(*) FROM cells")
        cell_count = cursor.fetchone()[0]
        self.assertGreaterEqual(cell_count, 20000, "在席監測車格數應大於 20,000 格（實證 26,438 格）")

        # 3. loading_zones 表
        cursor.execute("SELECT count(*) FROM loading_zones")
        loading_count = cursor.fetchone()[0]
        self.assertEqual(loading_count, 225, "營業用共用臨停區應精確為 225 筆")

        # 4. fee_rules 表
        cursor.execute("SELECT count(*) FROM fee_rules")
        fee_count = cursor.fetchone()[0]
        self.assertGreaterEqual(fee_count, 2500, "收費規則數應大於 2,500 筆")

        # 5. district_stats 表
        cursor.execute("SELECT count(*) FROM district_stats")
        dist_count = cursor.fetchone()[0]
        self.assertEqual(dist_count, 12, "臺北市行政區統計應涵蓋完整 12 區")

        # 6. historical_stats 表
        cursor.execute("SELECT count(*) FROM historical_stats")
        hist_count = cursor.fetchone()[0]
        self.assertGreaterEqual(hist_count, 35, "歷史統計期數應涵蓋 35 年以上")

    def test_api_roads(self):
        """測試 /api/roads 路段查詢與過濾 API"""
        h = MockHandler()
        h.handle_get_roads({"limit": ["10"], "district": ["中正區"]})
        self.assertEqual(h.status_code, 200)

        data = json.loads(h.wfile.getvalue().decode("utf-8"))
        self.assertIn("roads", data)
        self.assertGreaterEqual(len(data["roads"]), 1)
        first = data["roads"][0]
        self.assertEqual(first["district"], "中正區")
        self.assertIn("lat", first)
        self.assertIn("lon", first)
        self.assertTrue(24.9 <= first["lat"] <= 25.3, "緯度應在臺北市範圍內")
        self.assertTrue(121.4 <= first["lon"] <= 121.7, "經度應在臺北市範圍內")

    def test_api_road_detail(self):
        """測試 /api/road/<id> 單一路段車格與在席 API"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT road_id FROM roads WHERE cell_count > 0 LIMIT 1")
        test_id = cursor.fetchone()[0]

        h = MockHandler()
        h.handle_get_road_detail(test_id)
        self.assertEqual(h.status_code, 200)

        data = json.loads(h.wfile.getvalue().decode("utf-8"))
        self.assertIn("road", data)
        self.assertIn("cells", data)
        self.assertGreaterEqual(len(data["cells"]), 1)
        self.assertIn(data["cells"][0]["cell_status"], [1, 2], "車格狀態碼應為 1（佔用）或 2（空位）")

    def test_api_loading_zones(self):
        """測試 /api/loading-zones 營業臨停 API"""
        h = MockHandler()
        h.handle_get_loading_zones({"limit": ["20"]})
        self.assertEqual(h.status_code, 200)

        data = json.loads(h.wfile.getvalue().decode("utf-8"))
        self.assertEqual(data["total"], 225)
        self.assertGreaterEqual(len(data["loading_zones"]), 20)

    def test_api_fee_estimate(self):
        """測試 /api/fee-estimate 費用試算邏輯"""
        # 測試場景 1：平日停放 2 小時（收費時段內）
        h1 = MockHandler()
        h1.handle_fee_estimate({"duration": ["2.0"], "start_hour": ["10"], "day": ["2"]})
        self.assertEqual(h1.status_code, 200)
        res1 = json.loads(h1.wfile.getvalue().decode("utf-8"))
        self.assertEqual(res1["estimated_fee"], 60, "預設每小時 30 元停放 2 小時應為 60 元")
        self.assertTrue(res1["is_charged_today"])

        # 測試場景 2：週日停放（免收費日）
        h2 = MockHandler()
        h2.handle_fee_estimate({"duration": ["3.0"], "start_hour": ["10"], "day": ["7"]})
        self.assertEqual(h2.status_code, 200)
        res2 = json.loads(h2.wfile.getvalue().decode("utf-8"))
        self.assertEqual(res2["estimated_fee"], 0, "週日免收費日時停車費應為 0 元")
        self.assertFalse(res2["is_charged_today"])

    def test_api_stats(self):
        """測試 /api/stats 總體數據 API"""
        h = MockHandler()
        h.handle_get_stats()
        self.assertEqual(h.status_code, 200)
        data = json.loads(h.wfile.getvalue().decode("utf-8"))
        self.assertIn("overview", data)
        self.assertIn("districts", data)
        self.assertIn("historical", data)
        self.assertEqual(len(data["districts"]), 12)

    def test_frontend_ui_constraints(self):
        """測試前端 index.html 視覺規範合規性"""
        self.assertTrue(os.path.exists(INDEX_PATH), "前端 index.html 檔案必須存在")
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        # 1. 絕對禁止使用單向邊框
        unidir_borders = re.findall(r"border-(?:left|right|top|bottom)\s*:", content, re.IGNORECASE)
        self.assertEqual(len(unidir_borders), 0, f"嚴格禁止使用單向邊框，發現：{unidir_borders}")

        # 2. 絕對禁止使用 Emoji
        emoji_pattern = re.compile(
            "[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50\u2b55\u2934\u2935]"
        )
        emojis = emoji_pattern.findall(content)
        self.assertEqual(len(emojis), 0, f"嚴格禁止使用 Emoji，發現：{emojis}")

        # 3. 確保中文文本標點符號包含括號均為全形
        half_brackets = re.findall(r"[\u4e00-\u9fa5]+[()]|[()][\u4e00-\u9fa5]+", content)
        self.assertEqual(len(half_brackets), 0, f"中文周圍不應使用半形括號，發現：{half_brackets}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
