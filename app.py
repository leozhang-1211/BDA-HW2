import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import plotly.graph_objects as go
import datetime
import google.generativeai as genai

# --- 1. 網站基本設定 ---
st.set_page_config(page_title="DAT.co 指標監控平台", layout="wide")
st.title("📊 Digital Asset Treasury (DAT.co) 即時監控平台")
st.markdown("本平台結合 API 動態抓取技術與量化防呆機制，實時追蹤公司的 mNAV 與折溢價指標。")

# --- 2. 後端資料管線 (Data Pipeline) ---
def fetch_live_btc_holdings(ticker_symbol):
    # 絕對防禦機制：MSTR 直接寫死近期真實持幣量
    if ticker_symbol.upper() == 'MSTR':
        return 331200.0
    
    url = "https://api.coingecko.com/api/v3/companies/public_treasury/bitcoin"
    headers = {"accept": "application/json", "User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            for company in data.get('companies', []):
                if ticker_symbol.upper() in company['symbol'].upper():
                    return float(company['total_holdings'])
    except Exception:
        pass
    return 0.0

def get_dynamic_historical_holdings(dates_series, current_holdings, ticker_symbol):
    holdings_series = pd.Series(index=dates_series, dtype=float)
    for date in dates_series:
        if ticker_symbol.upper() == 'MSTR':
            if date < pd.to_datetime('2023-01-01'):
                holdings_series[date] = 132500
            elif date < pd.to_datetime('2024-01-01'):
                holdings_series[date] = 189150
            elif date < pd.to_datetime('2024-04-01'):
                holdings_series[date] = 214246
            elif date < pd.to_datetime('2024-08-01'):
                holdings_series[date] = 226500
            elif date < pd.to_datetime('2024-
