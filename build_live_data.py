#!/usr/bin/env python3
"""
雙北路邊停車智慧導航 - 即時數據建置與歷史日誌儲存模組 (build_live_data.py)
供 GitHub Actions 每 10 分鐘自動執行：
1. 抓取臺北市即時 XML (TCMSV_roadquery.xml)
2. 抓取新北市即時 CSV (新北市路邊停車空位查詢)
3. 結合坐標快取與即時經緯度，產出極輕量 live_roads.json (供前端靜態秒開)
4. 儲存雙北時序歷史日誌 (以每日 Gzip 壓縮 CSV 存放於 history/ 目錄，供未來尖離峰大數據分析)
"""

import os
import io
import csv
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

# 臺北市與新北市行政區中心座標 fallback
DISTRICT_CENTROIDS = {
    # 臺北市
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
    # 新北市核心都會區
    "板橋區": (25.0143, 121.4627),
    "三重區": (25.0722, 121.4986),
    "中和區": (24.9996, 121.5003),
    "永和區": (25.0089, 121.5147),
    "新莊區": (25.0360, 121.4503),
    "新店區": (24.9681, 121.5417),
    "土城區": (24.9723, 121.4437),
    "蘆洲區": (25.0849, 121.4727),
    "汐止區": (25.0629, 121.6575),
    "樹林區": (24.9908, 121.4239),
    "鶯歌區": (24.9546, 121.3547),
    "三峽區": (24.9344, 121.3742),
    "淡水區": (25.1694, 121.4442),
    "五股區": (25.0827, 121.4385),
    "泰山區": (25.0597, 121.4305),
    "林口區": (25.0772, 121.3934),
    "深坑區": (25.0024, 121.6157),
    "石碇區": (24.9918, 121.6586),
    "坪林區": (24.9372, 121.7118),
    "三芝區": (25.2583, 121.5008),
    "石門區": (25.2905, 121.5684),
    "八里區": (25.1466, 121.3984),
    "平溪區": (25.0257, 121.7386),
    "雙溪區": (25.0343, 121.8654),
    "貢寮區": (25.0223, 121.9082),
    "金山區": (25.2223, 121.6377),
    "萬里區": (25.1783, 121.6892),
    "烏來區": (24.8655, 121.5506)
}

NTPC_AREAS = {
    '65000010': '板橋區', '65000020': '三重區', '65000030': '中和區', '65000040': '永和區',
    '65000050': '新莊區', '65000060': '新店區', '65000070': '樹林區', '65000080': '鶯歌區',
    '65000090': '三峽區', '65000100': '淡水區', '65000110': '汐止區', '65000120': '瑞芳區',
    '65000130': '土城區', '65000140': '蘆洲區', '65000150': '五股區', '65000160': '泰山區',
    '65000170': '林口區', '65000180': '深坑區', '65000190': '石碇區', '65000200': '坪林區',
    '65000210': '三芝區', '65000220': '石門區', '65000230': '八里區', '65000240': '平溪區',
    '65000250': '雙溪區', '65000260': '貢寮區', '65000270': '金山區', '65000280': '萬里區',
    '65000290': '烏來區'
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

    print(f"[{time_str}] 開始執行雙北即時路邊停車在席數據建置作業...")

    # 1. 載入臺北坐標快取
    coords_cache = {}
    if os.path.exists(CACHE_COORDS_PATH):
        try:
            with open(CACHE_COORDS_PATH, "r", encoding="utf-8") as f:
                coords_cache = json.load(f)
        except Exception:
            pass

    all_roads = []
    total_spaces_sum = 0
    avail_spaces_sum = 0
    history_log_lines = []

    # ==========================
    # 2. 抓取與解析【臺北市】XML
    # ==========================
    tpe_roads_count = 0
    try:
        url_tpe = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_roadquery.xml"
        req_tpe = urllib.request.Request(url_tpe, headers={"User-Agent": "Mozilla/5.0 (TaipeiParkingBot/1.0)"})
        with urllib.request.urlopen(req_tpe, timeout=30) as resp:
            xml_bytes = resp.read()

        root = ET.fromstring(xml_bytes)
        for road in root.findall("ROAD"):
            road_id = road.findtext("roadSegID", default="").strip()
            road_name = road.findtext("roadSegName", default="").strip()
            fee_str = road.findtext("roadSegFee", default="").strip()

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

            all_roads.append({
                "road_id": road_id,
                "city": "臺北市",
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

            history_log_lines.append(f"{time_str},TPE_{road_id},{avail_sp},{total_sp},{usage_r}\n")
            tpe_roads_count += 1
        print(f"[臺北市] 成功解析 {tpe_roads_count} 條路段")
    except Exception as e:
        print(f"[警告] 抓取臺北市 XML 失敗：{e}")

    # ==========================
    # 3. 抓取與解析【新北市】CSV
    # ==========================
    ntpc_roads_count = 0
    try:
        url_ntpc = "https://data.ntpc.gov.tw/api/datasets/54a507c4-c038-41b5-bf60-bbecb9d052c6/csv/file"
        req_ntpc = urllib.request.Request(url_ntpc, headers={"User-Agent": "Mozilla/5.0 (TaipeiParkingBot/1.0)"})
        with urllib.request.urlopen(req_ntpc, timeout=45) as resp:
            ntpc_content = resp.read().decode("utf-8-sig")

        # 同步儲存至本地備援
        local_ntpc_csv = os.path.join(API_DATA_DIR, "new_taipei_live.csv")
        with open(local_ntpc_csv, "w", encoding="utf-8") as f:
            f.write(ntpc_content)

        reader = csv.DictReader(io.StringIO(ntpc_content))
        ntpc_map = {}

        for row in reader:
            rname = row.get("roadname", "").strip()
            if not rname:
                continue
            acode = row.get("areacode", "").strip()
            dist = NTPC_AREAS.get(acode, "新北市")
            is_free = (row.get("cellstatus", "").upper() == "Y")

            try:
                lat = float(row.get("latitude", 0))
                lon = float(row.get("longitude", 0))
            except ValueError:
                lat, lon = 0, 0

            key = f"{dist}_{rname}"
            if key not in ntpc_map:
                ntpc_map[key] = {
                    "road_id": f"NTPC_{acode}_{rname}",
                    "city": "新北市",
                    "name": rname,
                    "district": dist,
                    "lats": [],
                    "lons": [],
                    "total_spaces": 0,
                    "avail_spaces": 0,
                    "fee_str": row.get("paycash", "").strip() or "30元/時",
                    "cells": []
                }

            item = ntpc_map[key]
            item["total_spaces"] += 1
            if is_free:
                item["avail_spaces"] += 1
            if lat > 20 and lon > 100:
                item["lats"].append(lat)
                item["lons"].append(lon)
            item["cells"].append({
                "ps_id": row.get("cellid", "").strip(),
                "cell_status": 2 if is_free else 1
            })

        for key, rdata in ntpc_map.items():
            lats = rdata["lats"]
            lons = rdata["lons"]
            if lats and lons:
                r_lat = sum(lats) / len(lats)
                r_lon = sum(lons) / len(lons)
            else:
                r_lat, r_lon = DISTRICT_CENTROIDS.get(rdata["district"], (25.0143, 121.4627))

            total_sp = rdata["total_spaces"]
            avail_sp = rdata["avail_spaces"]
            usage_r = round((total_sp - avail_sp) / max(total_sp, 1), 2)

            total_spaces_sum += total_sp
            avail_spaces_sum += avail_sp

            all_roads.append({
                "road_id": rdata["road_id"],
                "city": "新北市",
                "name": rdata["name"],
                "district": rdata["district"],
                "lat": round(r_lat, 6),
                "lon": round(r_lon, 6),
                "total_spaces": total_sp,
                "avail_spaces": avail_sp,
                "fee_str": rdata["fee_str"],
                "usage_rate": usage_r,
                "cell_count": len(rdata["cells"]),
                "cells": rdata["cells"][:50] # 保留前 50 個代表車格以平衡前端大小
            })

            history_log_lines.append(f"{time_str},{rdata['road_id']},{avail_sp},{total_sp},{usage_r}\n")
            ntpc_roads_count += 1

        print(f"[新北市] 成功聚合 {ntpc_roads_count} 條路段，共計 {sum(r['total_spaces'] for r in ntpc_map.values())} 個在席車格")
    except Exception as e:
        print(f"[警告] 抓取新北市 CSV 失敗（嘗試讀取本地備援）：{e}")
        # 若聯網失敗嘗試讀取本地已有之 new_taipei_live.csv
        local_ntpc_csv = os.path.join(API_DATA_DIR, "new_taipei_live.csv")
        if os.path.exists(local_ntpc_csv):
            try:
                with open(local_ntpc_csv, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    ntpc_map = {}
                    for row in reader:
                        rname = row.get("roadname", "").strip()
                        if not rname:
                            continue
                        acode = row.get("areacode", "").strip()
                        dist = NTPC_AREAS.get(acode, "新北市")
                        is_free = (row.get("cellstatus", "").upper() == "Y")
                        key = f"{dist}_{rname}"
                        if key not in ntpc_map:
                            ntpc_map[key] = {
                                "road_id": f"NTPC_{acode}_{rname}",
                                "city": "新北市",
                                "name": rname,
                                "district": dist,
                                "lats": [],
                                "lons": [],
                                "total_spaces": 0,
                                "avail_spaces": 0,
                                "fee_str": row.get("paycash", "").strip() or "30元/時",
                                "cells": []
                            }
                        item = ntpc_map[key]
                        item["total_spaces"] += 1
                        if is_free:
                            item["avail_spaces"] += 1
                        try:
                            lat = float(row.get("latitude", 0))
                            lon = float(row.get("longitude", 0))
                            if lat > 20 and lon > 100:
                                item["lats"].append(lat)
                                item["lons"].append(lon)
                        except ValueError:
                            pass
                        item["cells"].append({
                            "ps_id": row.get("cellid", "").strip(),
                            "cell_status": 2 if is_free else 1
                        })
                    for key, rdata in ntpc_map.items():
                        lats = rdata["lats"]
                        lons = rdata["lons"]
                        r_lat, r_lon = (sum(lats)/len(lats), sum(lons)/len(lons)) if (lats and lons) else DISTRICT_CENTROIDS.get(rdata["district"], (25.0143, 121.4627))
                        total_sp = rdata["total_spaces"]
                        avail_sp = rdata["avail_spaces"]
                        usage_r = round((total_sp - avail_sp) / max(total_sp, 1), 2)
                        total_spaces_sum += total_sp
                        avail_spaces_sum += avail_sp
                        all_roads.append({
                            "road_id": rdata["road_id"],
                            "city": "新北市",
                            "name": rdata["name"],
                            "district": rdata["district"],
                            "lat": round(r_lat, 6),
                            "lon": round(r_lon, 6),
                            "total_spaces": total_sp,
                            "avail_spaces": avail_sp,
                            "fee_str": rdata["fee_str"],
                            "usage_rate": usage_r,
                            "cell_count": len(rdata["cells"]),
                            "cells": rdata["cells"][:50]
                        })
                        ntpc_roads_count += 1
                print(f"[新北市備援] 成功聚合 {ntpc_roads_count} 條路段")
            except Exception as e2:
                print(f"[警告] 本地新北市備援讀取失敗：{e2}")

    # 4. 排序：可用空位數多者優先
    all_roads.sort(key=lambda r: (0 if r["avail_spaces"] > 0 else (1 if r["avail_spaces"] == 0 else 2), -r["avail_spaces"]))

    payload = {
        "update_time": time_str,
        "total_roads": len(all_roads),
        "total_spaces": total_spaces_sum,
        "avail_spaces": avail_spaces_sum,
        "tpe_roads": tpe_roads_count,
        "ntpc_roads": ntpc_roads_count,
        "roads": all_roads
    }

    # 輸出雙北前端秒開之 live_roads.json
    with open(OUTPUT_LIVE_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    live_size_kb = os.path.getsize(OUTPUT_LIVE_JSON) / 1024
    print(f"[完成] 產出雙北 live_roads.json：雙北路段總數 {len(all_roads)} 條，可用空位 {avail_spaces_sum} 格，檔案大小：{live_size_kb:.1f} KB")

    # 5. 儲存時序歷史日誌（以日為單位之 Gzip CSV）
    history_gz_path = os.path.join(HISTORY_DIR, f"parking_log_{date_str}.csv.gz")
    header = "timestamp,road_id,avail_spaces,total_spaces,usage_rate\n"
    
    is_new = not os.path.exists(history_gz_path)
    with gzip.open(history_gz_path, "at", encoding="utf-8") as gz:
        if is_new:
            gz.write(header)
        gz.writelines(history_log_lines)

    log_size_kb = os.path.getsize(history_gz_path) / 1024
    print(f"[完成] 寫入雙北時序歷史日誌：{history_gz_path}（累積壓縮大小：{log_size_kb:.1f} KB）")


if __name__ == "__main__":
    fetch_and_build()
