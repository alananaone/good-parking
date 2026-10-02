"""
Vercel Serverless Function 進入點 (api/index.py)
橋接根目錄 server.py 之 ParkingAPIHandler
"""

import os
import sys

# 將專案根目錄加入 Python 搜尋路徑
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from server import ParkingAPIHandler


# Vercel Python 執行環境約定尋找 handler 類別
class handler(ParkingAPIHandler):
    pass
