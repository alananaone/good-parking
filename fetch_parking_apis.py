#!/usr/bin/env python3
"""
臺北市路邊停車資料集 API 串接與擷取模組
支援：
1. 資料集 1：營業用共用臨停區位置與數量 API
2. 資料集 2：即時路邊停車格位在席與路段空位 XML 資料饋送
3. 資料集 4：公有收費停車位統計表 API
"""

import os
import json
import urllib.request
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
API_OUTPUT_DIR = os.path.join(BASE_DIR, "api_data")

os.makedirs(API_OUTPUT_DIR, exist_ok=True)


def fetch_shared_loading_zones():
    """
    資料集 1：營業用共用臨停區位置與數量
    端點：臺北資料大平台 REST API
    """
    url = "https://data.taipei/api/v1/dataset/a76540cb-b6ee-410f-a2ca-de9432d62390?scope=resourceAquire&limit=1000"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    
    results = data.get("result", {}).get("results", [])
    output_path = os.path.join(API_OUTPUT_DIR, "shared_loading_zones.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"[完成] 資料集 1：營業用共用臨停區抓取成功，共 {len(results)} 筆，已儲存至 {output_path}")
    return results


def fetch_realtime_road_parking(save_raw_xml=True):
    """
    資料集 2：臺北市路邊停車格位使用情形即時資料 (Live Data Feed)
    端點：Azure Blob TCMSV_roadquery.xml
    更新頻率：約數分鐘/即時
    """
    url = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_roadquery.xml"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        xml_content = resp.read()
    
    if save_raw_xml:
        xml_path = os.path.join(API_OUTPUT_DIR, "TCMSV_roadquery.xml")
        with open(xml_path, "wb") as f:
            f.write(xml_content)
        print(f"[完成] 資料集 2：即時路邊停車 XML 下載完成 ({len(xml_content) / 1024 / 1024:.2f} MB)")

    # 解析 XML 為結構化摘要
    root = ET.fromstring(xml_content)
    road_list = []
    
    for road in root.findall("ROAD"):
        road_id = road.findtext("roadSegID", default="")
        road_name = road.findtext("roadSegName", default="")
        total = road.findtext("roadSegTotalValue", default="0")
        avail = road.findtext("roadSegAvail", default="0")
        fee = road.findtext("roadSegFee", default="")
        usage = road.findtext("roadSegUsage", default="")
        update_time = road.findtext("roadSegUpdatetime", default="")
        
        # 提取該路段之個別車格在席狀態
        cells = []
        cell_status_list = road.find("cellStatusList")
        if cell_status_list is not None:
            for cell in cell_status_list.findall("cell"):
                cells.append({
                    "psId": cell.findtext("psId", default=""),
                    "cellStatus": cell.findtext("cellStatus", default=""),  # 1: 佔用/有車, 2: 空位
                    "time": cell.findtext("data_Dt", default="")
                })
        
        road_list.append({
            "roadSegID": road_id,
            "roadSegName": road_name,
            "totalSpaces": total,
            "availableSpaces": avail,
            "fee": fee,
            "usage": usage,
            "updateTime": update_time,
            "cellCount": len(cells),
            "sampleCells": cells[:3]  # 抽樣前 3 格
        })
    
    summary_path = os.path.join(API_OUTPUT_DIR, "realtime_road_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(road_list, f, ensure_ascii=False, indent=2)
        
    print(f"[完成] 資料集 2：解析路段總數 {len(road_list)} 條，已輸出結構化摘要至 {summary_path}")
    return road_list


def fetch_district_parking_stats():
    """
    資料集 4：臺北市公有收費停車位統計表
    端點：臺北資料大平台 REST API
    """
    url = "https://data.taipei/api/v1/dataset/45f04fa0-35e2-4b8e-9335-b5da0bee033a?scope=resourceAquire"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    
    results = data.get("result", {}).get("results", [])
    output_path = os.path.join(API_OUTPUT_DIR, "district_paid_parking_stats.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
        
    print(f"[完成] 資料集 4：公有收費停車位統計抓取成功，共 {len(results)} 筆行政區紀錄，已儲存至 {output_path}")
    return results


if __name__ == "__main__":
    print("=== 開始執行臺北市路邊停車 API 串接作業 ===")
    fetch_shared_loading_zones()
    fetch_realtime_road_parking()
    fetch_district_parking_stats()
    print("=== 所有可串接之 API 作業已執行完畢 ===")
