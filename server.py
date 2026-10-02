#!/usr/bin/env python3
"""
臺北市路邊停車智慧導航與管理系統 - 後端 REST API 伺服器 (server.py)
採用 Python 標準庫與 SQLite 高效能驅動，支援：
1. /api/roads : 查詢收費路段與在席狀態 (支援搜尋、分區與空位過濾)
2. /api/road/<id> : 查詢路段即時在席明細 (含車格在席代碼與收費規則)
3. /api/loading-zones : 查詢 225 筆營業用共用臨停點位
4. /api/fee-estimate : 智慧費用試算引擎 (支援時段、星期與累進費率)
5. /api/stats : 供給大數據統計 (12 行政區分佈與 35 年歷史數列)
6. /api/refresh : 觸發即時資料同步更新
7. / : 前端 Web GIS 應用程式靜態託管
"""

import os
import json
import sqlite3
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "parking.db")
INDEX_PATH = os.path.join(BASE_DIR, "index.html")

PORT = 8000


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """支援多執行緒並發處理的 HTTP 伺服器"""
    daemon_threads = True


class ParkingAPIHandler(BaseHTTPRequestHandler):

    def _set_headers(self, status=200, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        # 靜態首頁服務
        if path in ["/", "/index.html"]:
            if os.path.exists(INDEX_PATH):
                with open(INDEX_PATH, "rb") as f:
                    content = f.read()
                self._set_headers(200, "text/html")
                self.wfile.write(content)
            else:
                self._set_headers(404, "text/plain")
                self.wfile.write("找不到前端頁面 index.html".encode("utf-8"))
            return

        # API 1: 路段清冊與空位
        if path == "/api/roads":
            self.handle_get_roads(query)
            return

        # API 2: 單一路段明細與格位
        if path.startswith("/api/road/"):
            road_id = path.split("/api/road/")[1].strip()
            self.handle_get_road_detail(road_id)
            return

        # API 3: 營業用共用臨停區
        if path == "/api/loading-zones":
            self.handle_get_loading_zones(query)
            return

        # API 4: 智慧停車費用試算
        if path == "/api/fee-estimate":
            self.handle_fee_estimate(query)
            return

        # API 5: 統計大數據
        if path == "/api/stats":
            self.handle_get_stats()
            return

        # 404 Not Found
        self._set_headers(404, "application/json")
        resp = {"error": "找不到該 API 端點", "path": path}
        self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/refresh":
            self.handle_refresh()
            return

        self._set_headers(404, "application/json")
        resp = {"error": "未定義之 POST 端點", "path": path}
        self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))

    def get_db(self):
        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        except Exception:
            conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    # -------------------------------------------------------------
    # 處理邏輯
    # -------------------------------------------------------------

    def handle_get_roads(self, query):
        district = query.get("district", [None])[0]
        search = query.get("search", [None])[0]
        avail_only = query.get("avail_only", ["false"])[0].lower() == "true"
        limit = int(query.get("limit", [2500])[0])

        conn = self.get_db()
        cursor = conn.cursor()

        sql = "SELECT * FROM roads WHERE 1=1"
        params = []

        if district and district != "全部" and district != "全區":
            sql += " AND district = ?"
            params.append(district)

        if search:
            sql += " AND (name LIKE ? OR road_id LIKE ?)"
            term = f"%{search.strip()}%"
            params.extend([term, term])

        if avail_only:
            sql += " AND avail_spaces > 0"

        # 排序：有空位且可用數多者優先
        sql += " ORDER BY CASE WHEN avail_spaces > 0 THEN 0 WHEN avail_spaces = 0 THEN 1 ELSE 2 END, avail_spaces DESC LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()

        results = []
        for r in rows:
            results.append({
                "road_id": r["road_id"],
                "name": r["name"],
                "district": r["district"],
                "lat": r["lat"],
                "lon": r["lon"],
                "total_spaces": r["total_spaces"],
                "avail_spaces": r["avail_spaces"],
                "fee_str": r["fee_str"],
                "usage_rate": r["usage_rate"],
                "update_time": r["update_time"],
                "cell_count": r["cell_count"]
            })

        conn.close()
        self._set_headers(200)
        self.wfile.write(json.dumps({"total": len(results), "roads": results}, ensure_ascii=False).encode("utf-8"))

    def handle_get_road_detail(self, road_id):
        conn = self.get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM roads WHERE road_id = ?", (road_id,))
        road_row = cursor.fetchone()
        if not road_row:
            conn.close()
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "找不到該路段代碼"}, ensure_ascii=False).encode("utf-8"))
            return

        road_data = dict(road_row)

        # 取得格位在席狀態 (1: 佔用, 2: 空位)
        cursor.execute("SELECT ps_id, cell_status, detect_time FROM cells WHERE road_id = ? ORDER BY ps_id ASC", (road_id,))
        cells = [dict(c) for c in cursor.fetchall()]

        # 比對收費規則
        norm_name = road_data["name"].split("（")[0].split("(")[0].replace(" ", "").strip()
        cursor.execute("SELECT * FROM fee_rules WHERE road_name LIKE ? OR ? LIKE '%' || road_name || '%' LIMIT 5", (f"%{norm_name}%", norm_name))
        rules = [dict(r) for r in cursor.fetchall()]

        conn.close()
        resp = {
            "road": road_data,
            "cells": cells,
            "fee_rules": rules
        }
        self._set_headers(200)
        self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))

    def handle_get_loading_zones(self, query):
        district = query.get("district", [None])[0]
        search = query.get("search", [None])[0]

        conn = self.get_db()
        cursor = conn.cursor()

        sql = "SELECT * FROM loading_zones WHERE 1=1"
        params = []

        if district and district != "全部" and district != "全區":
            sql += " AND district = ?"
            params.append(district)

        if search:
            sql += " AND (address LIKE ? OR nearby LIKE ?)"
            term = f"%{search.strip()}%"
            params.extend([term, term])

        sql += " ORDER BY id ASC"
        cursor.execute(sql, params)
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        self._set_headers(200)
        self.wfile.write(json.dumps({"total": len(rows), "loading_zones": rows}, ensure_ascii=False).encode("utf-8"))

    def handle_fee_estimate(self, query):
        road_name = query.get("road_name", [""])[0]
        road_id = query.get("road_id", [""])[0]
        duration = float(query.get("duration", [2.0])[0])  # 停放時數 (小時)
        start_hour_str = query.get("start_hour", [None])[0]
        day_str = query.get("day", [None])[0]

        now = datetime.datetime.now()
        cur_day = int(day_str) if day_str else now.isoweekday()  # 1: 週一 ... 7: 週日
        cur_hour = float(start_hour_str) if start_hour_str is not None else (now.hour + now.minute / 60.0)

        conn = self.get_db()
        cursor = conn.cursor()

        target_road = None
        if road_id:
            cursor.execute("SELECT * FROM roads WHERE road_id = ?", (road_id,))
            target_road = cursor.fetchone()

        if not target_road and road_name:
            cursor.execute("SELECT * FROM roads WHERE name LIKE ? LIMIT 1", (f"%{road_name}%",))
            target_road = cursor.fetchone()

        # 預設基本費率
        base_rate = 30
        fee_str = "每小時30元"
        charge_start = 9.0
        charge_end = 17.0
        charge_days = "1-6"
        is_progressive = False

        if target_road:
            fee_str = target_road["fee_str"] or "30元"
            if "累進" in fee_str or "差別" in fee_str:
                is_progressive = True
            elif "20" in fee_str:
                base_rate = 20
            elif "40" in fee_str:
                base_rate = 40
            elif "50" in fee_str:
                base_rate = 50
            elif "60" in fee_str:
                base_rate = 60

            # 查詢細部規則表
            clean_name = target_road["name"].split("（")[0].split("(")[0].replace(" ", "").strip()
            cursor.execute("SELECT * FROM fee_rules WHERE road_name LIKE ? LIMIT 1", (f"%{clean_name}%",))
            rule_row = cursor.fetchone()
            if rule_row:
                c_time = rule_row["charge_time"]  # 例如 "9-17" 或 "07:00-20:00"
                if "-" in c_time:
                    parts = c_time.split("-")
                    try:
                        p0 = float(parts[0].replace(":", ".").strip())
                        p1 = float(parts[1].replace(":", ".").strip())
                        charge_start = p0
                        charge_end = p1
                    except ValueError:
                        pass
                charge_days = rule_row["charge_days"] or "1-6"

        conn.close()

        # 判斷收費星期 (例如 1-6 代表週一至週六，週日免費)
        is_charged_day = True
        if charge_days == "1-6" and cur_day == 7:
            is_charged_day = False
        elif charge_days == "1-5" and cur_day in [6, 7]:
            is_charged_day = False

        # 計算落入收費時段的有效時數
        effective_hours = 0.0
        details = []

        if not is_charged_day:
            total_fee = 0
            explanation = "本日為免收費日（週日或週末優惠時段），全日免收停車費。"
        else:
            total_fee = 0
            step_hours = 0.5  # 半小時計費步長
            steps = int(duration / step_hours)
            for i in range(steps):
                time_point = cur_hour + (i * step_hours)
                # 判斷此半小時是否在收費區間內
                if charge_start <= time_point < charge_end:
                    effective_hours += step_hours
                    hour_index = int(effective_hours)
                    if is_progressive:
                        # 累進費率規則：第 1 小時 30 元，第 2 小時 40 元，第 3 小時起 50 元/時
                        if hour_index < 1:
                            cur_step_rate = 30 / 2
                        elif hour_index < 2:
                            cur_step_rate = 40 / 2
                        else:
                            cur_step_rate = 50 / 2
                    else:
                        cur_step_rate = (base_rate / 2)
                    total_fee += int(cur_step_rate)

            total_fee = int(total_fee)
            explanation = f"預計停放 {duration} 小時，落入收費時段（{int(charge_start):02d}:00至{int(charge_end):02d}:00）共 {effective_hours:.1f} 小時。"

        resp = {
            "road_name": target_road["name"] if target_road else (road_name or "未指定路段"),
            "fee_str": fee_str,
            "base_rate": base_rate,
            "is_progressive": is_progressive,
            "charge_time_range": f"{int(charge_start):02d}:00 - {int(charge_end):02d}:00",
            "charge_days": charge_days,
            "is_charged_today": is_charged_day,
            "requested_duration": duration,
            "effective_charged_hours": effective_hours,
            "estimated_fee": total_fee,
            "explanation": explanation
        }
        self._set_headers(200)
        self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))

    def handle_get_stats(self):
        conn = self.get_db()
        cursor = conn.cursor()

        # 1. 總覽數據
        cursor.execute("SELECT count(*), sum(total_spaces), sum(CASE WHEN avail_spaces > 0 THEN avail_spaces ELSE 0 END) FROM roads")
        roads_cnt, total_sp, avail_sp = cursor.fetchone()

        cursor.execute("SELECT count(*) FROM cells")
        total_cells = cursor.fetchone()[0]

        cursor.execute("SELECT count(*) FROM loading_zones")
        loading_cnt = cursor.fetchone()[0]

        # 2. 行政區公有車位
        cursor.execute("SELECT * FROM district_stats ORDER BY car_total DESC")
        districts = [dict(r) for r in cursor.fetchall()]

        # 3. 歷年時間數列
        cursor.execute("SELECT * FROM historical_stats ORDER BY rowid ASC")
        historical = [dict(r) for r in cursor.fetchall()]

        conn.close()

        resp = {
            "overview": {
                "total_roads": roads_cnt or 0,
                "total_spaces": total_sp or 0,
                "available_spaces": avail_sp or 0,
                "monitored_cells": total_cells,
                "loading_zones_count": loading_cnt,
                "last_sync": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            },
            "districts": districts,
            "historical": historical
        }
        self._set_headers(200)
        self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))

    def handle_refresh(self):
        try:
            import data_pipeline
            data_pipeline.sync_realtime_road_parking()
            self._set_headers(200)
            self.wfile.write(json.dumps({"status": "success", "message": "即時在席資料同步成功！"}, ensure_ascii=False).encode("utf-8"))
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False).encode("utf-8"))


def run_server():
    server_address = ("", PORT)
    httpd = ThreadedHTTPServer(server_address, ParkingAPIHandler)
    print(f"==================================================")
    print(f" 臺北市路邊停車智慧系統 API 伺服器啟動！")
    print(f" 監聽位址：http://localhost:{PORT}")
    print(f" 前端展示：http://localhost:{PORT}/")
    print(f"==================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n伺服器停止中...")
        httpd.shutdown()


if __name__ == "__main__":
    run_server()
