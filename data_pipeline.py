#!/usr/bin/env python3
"""
臺北市路邊停車智慧導航與管理系統 - 資料管線與空間資料庫建構模組 (data_pipeline.py)
整合：
1. 空間圖資 (SHP/DBF 33 萬格位坐標轉換 TWD97 -> WGS84)
2. 即時在席 XML 饋送 (2,342 條路段與格位狀態)
3. 營業用共用臨停區 CSV (225 處)
4. 收費路段清冊與規則 ODS
5. 12 行政區車位月報 JSON
6. 35 年歷年長條時間數列 CSV
"""

import os
import sys
import json
import math
import struct
import sqlite3
import zipfile
import urllib.request
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
API_DATA_DIR = os.path.join(BASE_DIR, "api_data")
DB_PATH = os.path.join(BASE_DIR, "parking.db")
CACHE_COORDS_PATH = os.path.join(API_DATA_DIR, "road_coords_cache.json")

os.makedirs(API_DATA_DIR, exist_ok=True)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# 臺北市 12 行政區預設中心點 (WGS84)
DISTRICT_CENTROIDS = {
    "中正區": (25.0324, 121.5183),
    "大同區": (25.0633, 121.5132),
    "中山區": (25.0685, 121.5338),
    "松山區": (25.0592, 121.5574),
    "大安區": (25.0264, 121.5434),
    "萬華區": (25.0354, 121.4997),
    "信義區": (25.0339, 121.5645),
    "士林區": (25.0922, 121.5204),
    "北投區": (25.1321, 121.4987),
    "內湖區": (25.0835, 121.5898),
    "南港區": (25.0553, 121.6171),
    "文山區": (24.9982, 121.5701),
}


def twd97_to_wgs84(x, y):
    """
    TWD97 二度分帶 (TM2, EPSG:3826) 投影轉 WGS84 經緯度 (EPSG:4326)
    純數學分析轉換，零外部相依
    """
    a = 6378137.0
    b = 6356752.314245
    long0 = 121.0 * math.pi / 180.0
    k0 = 0.9999
    dx = 250000.0

    x -= dx
    e = (1 - (b**2) / (a**2))**0.5
    e2 = (e**2) / (1 - e**2)

    M = y / k0
    mu = M / (a * (1.0 - (e**2) / 4.0 - 3.0 * (e**4) / 64.0 - 5.0 * (e**6) / 256.0))
    e1 = (1.0 - (1.0 - e**2)**0.5) / (1.0 + (1.0 - e**2)**0.5)

    J1 = (3.0 * e1 / 2.0 - 27.0 * (e1**3) / 32.0)
    J2 = (21.0 * (e1**2) / 16.0 - 55.0 * (e1**4) / 32.0)
    J3 = (151.0 * (e1**3) / 96.0)
    J4 = (1097.0 * (e1**4) / 512.0)

    fp = mu + J1 * math.sin(2.0 * mu) + J2 * math.sin(4.0 * mu) + J3 * math.sin(6.0 * mu) + J4 * math.sin(8.0 * mu)

    C1 = e2 * math.cos(fp)**2
    T1 = math.tan(fp)**2
    R1 = a * (1.0 - e**2) / ((1.0 - (e * math.sin(fp))**2)**1.5)
    N1 = a / ((1.0 - (e * math.sin(fp))**2)**0.5)
    D = x / (N1 * k0)

    Q1 = N1 * math.tan(fp) / R1
    Q2 = (D**2 / 2.0)
    Q3 = (5.0 + 3.0 * T1 + 10.0 * C1 - 4.0 * (C1**2) - 9.0 * e2) * (D**4) / 24.0
    Q4 = (61.0 + 90.0 * T1 + 298.0 * C1 + 45.0 * (T1**2) - 252.0 * e2 - 3.0 * (C1**2)) * (D**6) / 720.0
    lat = fp - Q1 * (Q2 - Q3 + Q4)

    Q5 = D
    Q6 = (1.0 + 2.0 * T1 + C1) * (D**3) / 6.0
    Q7 = (5.0 - 2.0 * C1 + 28.0 * T1 - 3.0 * (C1**2) + 8.0 * e2 + 24.0 * (T1**2)) * (D**5) / 120.0
    lon = long0 + (Q5 - Q6 + Q7) / math.cos(fp)

    return math.degrees(lat), math.degrees(lon)


def normalize_road_name(name):
    """標準化路名以利比對"""
    if not name:
        return ""
    s = name.split("（")[0].split("(")[0].replace(" ", "").strip()
    table = {
        "1段": "一段", "2段": "二段", "3段": "三段", "4段": "四段",
        "5段": "五段", "6段": "六段", "7段": "七段", "8段": "八段"
    }
    for k, v in table.items():
        if k in s:
            s = s.replace(k, v)
    return s


def build_road_coordinates_cache():
    """
    從 downloads/park01_202608271732UTF8.zip 萃取 33 萬格位之路名與座標
    並聚合產出每條路段之中心經緯度
    """
    if os.path.exists(CACHE_COORDS_PATH):
        print(f"[快取] 讀取現有路段坐標快取：{CACHE_COORDS_PATH}")
        with open(CACHE_COORDS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    zip_path = os.path.join(DOWNLOADS_DIR, "park01_202608271732UTF8.zip")
    if not os.path.exists(zip_path):
        print(f"[警告] 找不到 SHP 壓縮檔：{zip_path}，使用預設行政區坐標")
        return {}

    print(f"[分析] 開始從 SHP/DBF 萃取 33 萬格位空間幾何座標...")
    accum = {}  # norm_name -> [sum_lat, sum_lon, count, area_name]

    with zipfile.ZipFile(zip_path) as z:
        with z.open("park01_202608271732.dbf") as dbf, \
             z.open("park01_202608271732.shx") as shx, \
             z.open("park01_202608271732.shp") as shp:

            # 讀取 DBF 標頭
            d_hdr = dbf.read(32)
            num_records, hlen, rlen = struct.unpack("<IHH", d_hdr[4:12])
            dbf.seek(hlen)
            shx.seek(100)

            chunk_size = 5000
            for i in range(0, num_records, chunk_size):
                cur_chunk = min(chunk_size, num_records - i)
                dbf_bytes = dbf.read(cur_chunk * rlen)
                shx_bytes = shx.read(cur_chunk * 8)

                for j in range(cur_chunk):
                    rec = dbf_bytes[j * rlen : (j + 1) * rlen]
                    # 萃取路名與行政區 (位移量依前述分析設定)
                    rname = rec[1170:1170+254].decode("utf-8", errors="ignore").strip()
                    aname = rec[1424:1424+10].decode("utf-8", errors="ignore").strip()

                    nname = normalize_road_name(rname)
                    if not nname:
                        continue

                    # 讀取對應之 SHP Bounding Box
                    shx_entry = shx_bytes[j * 8 : (j + 1) * 8]
                    off_words, _ = struct.unpack(">ii", shx_entry)
                    byte_offset = off_words * 2

                    shp.seek(byte_offset + 8)
                    shape_data = shp.read(36)
                    _, xmin, ymin, xmax, ymax = struct.unpack("<idddd", shape_data)
                    cx, cy = (xmin + xmax) / 2.0, (ymin + ymax) / 2.0

                    if nname not in accum:
                        accum[nname] = [cx, cy, 1, aname]
                    else:
                        entry = accum[nname]
                        entry[0] += cx
                        entry[1] += cy
                        entry[2] += 1
                        if not entry[3] and aname:
                            entry[3] = aname

    # 批次轉換 TWD97 -> WGS84
    coords_cache = {}
    for nname, (sum_x, sum_y, cnt, aname) in accum.items():
        avg_x = sum_x / cnt
        avg_y = sum_y / cnt
        lat, lon = twd97_to_wgs84(avg_x, avg_y)
        coords_cache[nname] = {
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "district": aname,
            "sample_count": cnt
        }

    with open(CACHE_COORDS_PATH, "w", encoding="utf-8") as f:
        json.dump(coords_cache, f, ensure_ascii=False, indent=2)

    print(f"[完成] 萃取完成，共索引 {len(coords_cache)} 條實體路段座標，已快取至 {CACHE_COORDS_PATH}")
    return coords_cache


def init_database():
    """初始化 SQLite 資料庫與資料表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. 路段資料表 (roads)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS roads (
        road_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        district TEXT,
        lat REAL,
        lon REAL,
        total_spaces INTEGER,
        avail_spaces INTEGER,
        fee_str TEXT,
        usage_rate REAL,
        update_time TEXT,
        cell_count INTEGER
    )
    """)

    # 2. 格位在席狀態資料表 (cells)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cells (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        road_id TEXT NOT NULL,
        ps_id TEXT NOT NULL,
        cell_status INTEGER NOT NULL,
        detect_time TEXT,
        FOREIGN KEY(road_id) REFERENCES roads(road_id)
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cells_road_id ON cells(road_id)")

    # 3. 營業用共用臨停區資料表 (loading_zones)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS loading_zones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        district TEXT NOT NULL,
        address TEXT NOT NULL,
        spaces INTEGER NOT NULL,
        nearby TEXT,
        lat REAL,
        lon REAL
    )
    """)

    # 4. 路邊收費規則表 (fee_rules)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fee_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        road_name TEXT NOT NULL,
        segment TEXT,
        charge_time TEXT,
        rate_hourly TEXT,
        charge_days TEXT
    )
    """)

    # 5. 各行政區公有車位統計表 (district_stats)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS district_stats (
        district TEXT PRIMARY KEY,
        car_total INTEGER,
        moto_total INTEGER,
        car_offstreet INTEGER,
        moto_offstreet INTEGER,
        car_onstreet INTEGER,
        moto_onstreet INTEGER
    )
    """)

    # 6. 歷年時間數列統計表 (historical_stats)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS historical_stats (
        period TEXT PRIMARY KEY,
        total_spaces INTEGER,
        car_spaces INTEGER,
        moto_spaces INTEGER,
        onstreet_spaces INTEGER,
        offstreet_spaces INTEGER,
        building_spaces INTEGER
    )
    """)

    conn.commit()
    conn.close()
    print(f"[完成] SQLite 資料庫架構初始化完畢：{DB_PATH}")


def import_loading_zones():
    """匯入 225 筆營業用共用臨停區資料"""
    csv_path = os.path.join(DOWNLOADS_DIR, "11508 臺北市共用臨停區.csv")
    json_path = os.path.join(API_DATA_DIR, "shared_loading_zones.json")

    records = []
    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            for item in raw_data:
                try:
                    records.append((
                        item.get("行政區", "").strip(),
                        item.get("地址", "").strip(),
                        int(item.get("格位數", 1)),
                        item.get("周邊景點或商圈", "").strip(),
                        float(item.get("場站緯度", 0.0)),
                        float(item.get("場站經度", 0.0))
                    ))
                except (ValueError, TypeError):
                    continue
    elif os.path.exists(csv_path):
        import csv
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    records.append((
                        row.get("行政區", "").strip(),
                        row.get("地址", "").strip(),
                        int(row.get("格位數", 1)),
                        row.get("周邊景點或商圈", "").strip(),
                        float(row.get("場站緯度", 0.0)),
                        float(row.get("場站經度", 0.0))
                    ))
                except (ValueError, TypeError):
                    continue

    if records:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM loading_zones")
        cursor.executemany("""
        INSERT INTO loading_zones (district, address, spaces, nearby, lat, lon)
        VALUES (?, ?, ?, ?, ?, ?)
        """, records)
        conn.commit()
        conn.close()
        print(f"[完成] 匯入營業用共用臨停區：共 {len(records)} 筆")


def import_fee_rules_from_ods():
    """解析 downloads/各行政區路邊收費路段資訊.ods 並寫入資料庫"""
    ods_path = os.path.join(DOWNLOADS_DIR, "各行政區路邊收費路段資訊.ods")
    if not os.path.exists(ods_path):
        print(f"[警告] 找不到 ODS 檔案：{ods_path}")
        return

    try:
        with zipfile.ZipFile(ods_path) as z:
            with z.open("content.xml") as f:
                tree = ET.parse(f)
                root = tree.getroot()

        ns = {
            "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
            "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
        }

        records = []
        rows = root.findall(".//table:table-row", ns)
        for row in rows[1:]:  # 跳過表頭
            cells = []
            for cell in row.findall(".//table:table-cell", ns):
                t_nodes = cell.findall(".//text:p", ns)
                txt = " ".join(t.text for t in t_nodes if t.text).strip()
                repeat = cell.attrib.get("{urn:oasis:names:tc:opendocument:xmlns:table:1.0}number-columns-repeated")
                cnt = int(repeat) if repeat and int(repeat) < 20 else 1
                for _ in range(cnt):
                    cells.append(txt)

            if len(cells) >= 10:
                road_name = cells[0].strip()
                segment = cells[1].strip()
                charge_time = cells[7].strip()
                rate_hourly = cells[8].strip()
                charge_days = cells[9].strip()

                if road_name:
                    records.append((road_name, segment, charge_time, rate_hourly, charge_days))

        if records:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM fee_rules")
            cursor.executemany("""
            INSERT INTO fee_rules (road_name, segment, charge_time, rate_hourly, charge_days)
            VALUES (?, ?, ?, ?, ?)
            """, records)
            conn.commit()
            conn.close()
            print(f"[完成] 匯入路邊收費規則表：共 {len(records)} 筆路段規則")

    except Exception as e:
        print(f"[錯誤] 解析 ODS 失敗：{e}")


def import_district_and_historical_stats():
    """匯入行政區月報統計與 35 年歷史長條時間數列"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. 行政區月報
    d_path = os.path.join(API_DATA_DIR, "district_paid_parking_stats.json")
    if os.path.exists(d_path):
        with open(d_path, "r", encoding="utf-8") as f:
            d_data = json.load(f)
            d_records = []
            for row in d_data:
                dist = row.get("分區", "").strip()
                if not dist or dist in ["總計", "小計"]:
                    continue
                try:
                    d_records.append((
                        dist,
                        int(row.get("汽車總數值（格）", 0)),
                        int(row.get("機車總數值（格）", 0)),
                        int(row.get("汽車路外總數值（格）", 0)),
                        int(row.get("機車路外總數值（格）", 0)),
                        int(row.get("汽車路邊總數值（格）", 0)),
                        int(row.get("機車路邊總數值（格）", 0))
                    ))
                except ValueError:
                    continue

            cursor.execute("DELETE FROM district_stats")
            cursor.executemany("""
            INSERT INTO district_stats (district, car_total, moto_total, car_offstreet, moto_offstreet, car_onstreet, moto_onstreet)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, d_records)
            print(f"[完成] 匯入行政區公有車位統計：共 {len(d_records)} 區")

    # 2. 歷史時間數列
    h_path = os.path.join(DOWNLOADS_DIR, "臺北市停車位數時間數列統計.csv")
    if os.path.exists(h_path):
        import csv
        with open(h_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            h_records = []
            for row in reader:
                if len(row) >= 50:
                    period = row[0].strip()
                    try:
                        total = int(row[1]) if row[1] else 0
                        car = int(row[2]) if row[2] else 0
                        moto = int(row[3]) if row[3] else 0
                        onstreet = int(row[4]) if row[4] else 0
                        offstreet = int(row[20]) if row[20] else 0
                        building = int(row[48]) if row[48] else 0
                        h_records.append((period, total, car, moto, onstreet, offstreet, building))
                    except ValueError:
                        continue

            cursor.execute("DELETE FROM historical_stats")
            cursor.executemany("""
            INSERT INTO historical_stats (period, total_spaces, car_spaces, moto_spaces, onstreet_spaces, offstreet_spaces, building_spaces)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, h_records)
            print(f"[完成] 匯入歷年時間數列統計：共 {len(h_records)} 年度期別")

    conn.commit()
    conn.close()


def sync_realtime_road_parking(coords_cache=None):
    """
    同步即時在席資料 (TCMSV_roadquery.xml)
    寫入 roads 與 cells 資料表
    """
    if coords_cache is None:
        coords_cache = build_road_coordinates_cache()

    xml_path = os.path.join(API_DATA_DIR, "TCMSV_roadquery.xml")
    if not os.path.exists(xml_path):
        print("[下載] 下載最新即時 TCMSV_roadquery.xml ...")
        url = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_roadquery.xml"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
        with open(xml_path, "wb") as f:
            f.write(content)

    print("[解析] 開始解析 TCMSV_roadquery.xml 並更新即時資料表...")
    tree = ET.parse(xml_path)
    root = tree.getroot()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("DELETE FROM cells")
    cursor.execute("DELETE FROM roads")

    road_records = []
    cell_records = []

    for road in root.findall("ROAD"):
        road_id = road.findtext("roadSegID", default="").strip()
        road_name = road.findtext("roadSegName", default="").strip()
        fee_str = road.findtext("roadSegFee", default="").strip()
        update_time = road.findtext("roadSegUpdatetime", default="").strip()

        try:
            total_sp = int(road.findtext("roadSegTotalValue", default="0"))
        except ValueError:
            total_sp = 0

        try:
            avail_sp = int(road.findtext("roadSegAvail", default="-99"))
        except ValueError:
            avail_sp = -99

        try:
            usage_r = float(road.findtext("roadSegUsage", default="-99.0"))
        except ValueError:
            usage_r = -99.0

        # 比對座標與行政區
        norm_rname = normalize_road_name(road_name)
        coord_info = coords_cache.get(norm_rname)

        lat, lon, district = None, None, None
        if coord_info:
            lat = coord_info["lat"]
            lon = coord_info["lon"]
            district = coord_info["district"]

        # 嘗試以路名推估行政區
        if not district:
            for d in DISTRICT_CENTROIDS:
                if d[:2] in road_name:
                    district = d
                    break
        if not district:
            district = "臺北市"

        if lat is None or lon is None:
            # 採用行政區中心預設值
            d_center = DISTRICT_CENTROIDS.get(district, (25.0421, 121.5328))
            lat, lon = d_center

        # 解析格位在席狀態
        cells_in_road = 0
        cell_status_list = road.find("cellStatusList")
        if cell_status_list is not None:
            for cell in cell_status_list.findall("cell"):
                ps_id = cell.findtext("psId", default="").strip()
                status_str = cell.findtext("cellStatus", default="").strip()
                d_time = cell.findtext("data_Dt", default="").strip()
                try:
                    c_status = int(status_str)
                except ValueError:
                    c_status = 1

                if ps_id:
                    cells_in_road += 1
                    cell_records.append((road_id, ps_id, c_status, d_time))

        road_records.append((
            road_id, road_name, district, lat, lon,
            total_sp, avail_sp, fee_str, usage_r, update_time, cells_in_road
        ))

    # 批次寫入資料庫
    cursor.executemany("""
    INSERT INTO roads (road_id, name, district, lat, lon, total_spaces, avail_spaces, fee_str, usage_rate, update_time, cell_count)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, road_records)

    cursor.executemany("""
    INSERT INTO cells (road_id, ps_id, cell_status, detect_time)
    VALUES (?, ?, ?, ?)
    """, cell_records)

    conn.commit()
    conn.close()

    print(f"[完成] 即時路況同步完畢：寫入 {len(road_records)} 條收費路段，{len(cell_records)} 個實體格位狀態")


def run_full_pipeline():
    """執行全套資料管線"""
    print("==================================================")
    print(" 臺北市路邊停車智慧導航與管理系統 - 資料管線建置")
    print("==================================================")
    init_database()
    coords = build_road_coordinates_cache()
    import_loading_zones()
    import_fee_rules_from_ods()
    import_district_and_historical_stats()
    sync_realtime_road_parking(coords)
    print("==================================================")
    print(" 資料管線建置全部成功完成！")
    print("==================================================")


if __name__ == "__main__":
    run_full_pipeline()
