#!/usr/bin/env python3
"""
臺北市路邊停車智慧導航 - 即時數據建置與歷史日誌儲存模組 (build_live_data.py)
供 GitHub Actions 每 10 分鐘自動執行：
1. 抓取臺北市即時 XML (TCMSV_roadquery.xml)
2. 結合預先計算之經緯度座標，產出極輕量 live_roads.json (供前端靜態秒開)
3. 儲存時序歷史日誌 (以每日 Gzip 壓縮 CSV 存放於 history/ 目錄，單日僅約 200KB，供未來尖離峰分析)
"""

import os
import gzip
import json
import datetime
import urllib.request
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
API_DATA_DIR = os.path.join(BASE_DIR, "api_data")
HISTORY_DIR = os.path.join(BASE_DIR, "history")
CACHE_COORDS_PATH = os.path.join(API_DATA_DIR, "road_coords_cache.json")
OUTPUT_LIVE_JSON = os.path.join(API_DATA_DIR, "live_roads.json")

os.makedirs(API_DATA_DIR, exist_ok=True)
os.makedirs(HISTORY_DIR, exist_ok=True)

# 臺北市 12 行政區中心座標 fallback
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


def normalize_road_name(name):
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


def fetch_and_build():
    now_utc = datetime.datetime.utcnow()
    # 臺灣時間 UTC+8
    now_tw = now_utc + datetime.timedelta(hours=8)
    time_str = now_tw.strftime("%Y-%m-%d %H:%M:%S")
    date_str = now_tw.strftime("%Y-%m-%d")

    print(f"[{time_str}] 開始執行臺北市即時路邊停車在席數據建置作業...")

    # 1. 載入坐標快取
    coords_cache = {}
    if os.path.exists(CACHE_COORDS_PATH):
        with open(CACHE_COORDS_PATH, "r", encoding="utf-8") as f:
            coords_cache = json.load(f)

    # 2. 下載即時 XML
    url = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_roadquery.xml"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (TaipeiParkingBot/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        xml_bytes = resp.read()

    # 3. 解析 XML
    root = ET.fromstring(xml_bytes)
    roads = []
    total_spaces_sum = 0
    avail_spaces_sum = 0
    history_log_lines = []

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

        if avail_sp > 0:
            avail_spaces_sum += avail_sp
        if total_sp > 0:
            total_spaces_sum += total_sp

        # 匹配座標與行政區
        norm_name = normalize_road_name(road_name)
        cinfo = coords_cache.get(norm_name)
        lat, lon, district = None, None, None

        if cinfo:
            lat = cinfo["lat"]
            lon = cinfo["lon"]
            district = cinfo["district"]

        if not district:
            for d in DISTRICT_CENTROIDS:
                if d[:2] in road_name:
                    district = d
                    break
        if not district:
            district = "臺北市"

        if lat is None or lon is None:
            d_center = DISTRICT_CENTROIDS.get(district, (25.0421, 121.5328))
            lat, lon = d_center

        # 抽樣個別格位
        cell_count = 0
        cells_sample = []
        cell_status_list = road.find("cellStatusList")
        if cell_status_list is not None:
            c_nodes = cell_status_list.findall("cell")
            cell_count = len(c_nodes)
            for c in c_nodes:
                ps_id = c.findtext("psId", default="").strip()
                c_status = int(c.findtext("cellStatus", default="1"))
                if ps_id:
                    cells_sample.append({
                        "ps_id": ps_id,
                        "cell_status": c_status
                    })

        roads.append({
            "road_id": road_id,
            "name": road_name,
            "district": district,
            "lat": lat,
            "lon": lon,
            "total_spaces": total_sp,
            "avail_spaces": avail_sp,
            "fee_str": fee_str,
            "usage_rate": usage_r,
            "cell_count": cell_count,
            "cells": cells_sample
        })

        # 紀錄輕量化時序日誌：timestamp,road_id,avail,total,usage
        history_log_lines.append(f"{time_str},{road_id},{avail_sp},{total_sp},{usage_r}\n")

    # 4. 排序：有空位且可用數多者優先
    roads.sort(key=lambda r: (0 if r["avail_spaces"] > 0 else (1 if r["avail_spaces"] == 0 else 2), -r["avail_spaces"]))

    payload = {
        "update_time": time_str,
        "total_roads": len(roads),
        "total_spaces": total_spaces_sum,
        "avail_spaces": avail_spaces_sum,
        "roads": roads
    }

    # 輸出前端秒開之 live_roads.json
    with open(OUTPUT_LIVE_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    live_size_kb = os.path.getsize(OUTPUT_LIVE_JSON) / 1024
    print(f"[完成] 產出 live_roads.json：路段總數 {len(roads)} 條，可用空位 {avail_spaces_sum} 格，檔案大小：{live_size_kb:.1f} KB")

    # 5. 儲存時序歷史日誌（以日為單位之 Gzip CSV）
    history_gz_path = os.path.join(HISTORY_DIR, f"parking_log_{date_str}.csv.gz")
    header = "timestamp,road_id,avail_spaces,total_spaces,usage_rate\n"
    
    # 若檔案不存在則寫入表頭，若已存在則追加
    is_new = not os.path.exists(history_gz_path)
    with gzip.open(history_gz_path, "at", encoding="utf-8") as gz:
        if is_new:
            gz.write(header)
        gz.writelines(history_log_lines)

    log_size_kb = os.path.getsize(history_gz_path) / 1024
    print(f"[完成] 寫入時序歷史日誌：{history_gz_path}（累積壓縮大小：{log_size_kb:.1f} KB）")


if __name__ == "__main__":
    fetch_and_build()
