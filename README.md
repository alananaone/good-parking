# 臺北市路邊停車智慧導航與管理系統（Taipei Smart On-Street Parking）

一套整合臺北市開放資料大平台（data.taipei）五大實證資料集之現代化路邊停車智慧導航、收費規則試算與空間數據分析系統。

---

## 系統特色與亮點

1. **即時路邊空位監測**：
   - 每數分鐘整合臺北市停車管理工程處即時資料饋送（TCMSV XML），涵蓋全臺北市 2,342 條收費路段與 26,438 個個別在席格位狀態（空位可停／佔用中）。
2. **輕量向量 Web GIS 地圖**：
   - 預設採用簡約灰階畫布底圖，並支援內政部國土測繪中心「臺灣通用電子地圖」與全球街道地圖切換。
   - 地圖圓點具備動態自適應縮放機制：縮小瀏覽全景時自動收斂避免視覺混亂，放大至街道層級時相應放大 15% 以上，搭配懸停浮動提示（Tooltip）顯示即時剩餘空位。
3. **智慧停車費用試算引擎**：
   - 結合全市 2,863 筆路段收費規程，自動判定星期收費日（週日免費）、各路段收費時段（例如 09:00 至 17:00）以及累進費率階梯規則。
4. **營業用共用臨停專區**：
   - 專供貨運物流與外送車輛查詢全臺北市 225 處合法共用裝卸貨與臨時停車格位。
5. **停車大數據儀表板**：
   - 整合 12 行政區公有路邊與路外車位總量分佈長條圖，以及民國 77 年至今 35 年長條歷史供給演進趨勢圖。
6. **生產級極簡純白設計體系**：
   - 純白底色搭配微色差平面排版，無厚重陰影與過度弧度，全站絕對零 Emoji，嚴格落實全包圍邊框原則，並超額達成 WCAG 2.1 AAA 級無障礙對比標準。

---

## 專案結構

```text
.
├── api/
│   └── index.py            # Vercel Serverless Function 進入點
├── api_data/               # 結構化快取與即時資料
│   ├── TCMSV_roadquery.xml
│   ├── district_paid_parking_stats.json
│   ├── realtime_road_summary.json
│   ├── road_coords_cache.json
│   └── shared_loading_zones.json
├── downloads/              # 實體原始檔案（CSV、ODS）
├── data_pipeline.py        # ETL 資料管線與 TWD97 空間投影轉換
├── server.py               # 多執行緒 REST API 與靜態檔案伺服器
├── index.html              # 前端 Web GIS 單頁應用程式
├── parking.db              # 本地 SQLite 空間資料庫
├── run_tests.py            # 全功能自動化測試套件
├── vercel.json             # Vercel 佈署設定檔
└── README.md
```

---

## 本地快速啟動

無需安裝重量級外部函式庫，直接使用 Python 3 原生標準庫即可運行：

```bash
# 啟動本機伺服器
python3 server.py
```
啟動後於瀏覽器開啟：`http://localhost:8000` 即可檢視完整介面。

若需手動同步最新即時在席資料，執行：
```bash
python3 data_pipeline.py
```

若需執行全功能自動化測試套件：
```bash
python3 run_tests.py
```

---

## 佈署於 Vercel

本專案已完全相容 Vercel 之 Serverless 靜態與邊緣架構：

1. 將本專案推送至 GitHub 儲存庫（`https://github.com/alananaone/good-parking`）。
2. 登入 [Vercel](https://vercel.com/)，點擊「Add New Project」並匯入該 GitHub 專案。
3. Framework Preset 選擇「Other」（或自動偵測）。
4. 點擊「Deploy」，Vercel 將自動依據 `vercel.json` 託管靜態前端並掛載 `api/index.py` Serverless 路由。

---

## 授權與資料來源聲明

- 本系統之實證資料均引自臺北市政府開放資料大平台（data.taipei）及內政部國土測繪中心。
- 遵循政府資料開放授權條款。
