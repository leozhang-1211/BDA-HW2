import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import plotly.graph_objects as go
import datetime

# --- 1. 網站基本設定 ---
st.set_page_config(page_title="DAT.co 指標監控平台", layout="wide")
st.title("📊 Digital Asset Treasury (DAT.co) 即時監控平台")
st.markdown("本平台結合 API 動態抓取技術，實時追蹤 MicroStrategy (MSTR) 的 mNAV 與折溢價指標。")

# --- 2. 後端資料管線 (Data Pipeline) ---

def fetch_live_btc_holdings(ticker_symbol):
    """從 CoinGecko 抓取最新持幣量"""
    url = "https://api.coingecko.com/api/v3/companies/public_treasury/bitcoin"
    headers = {"accept": "application/json", "User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status() 
        data = response.json()
        for company in data.get('companies', []):
            if ticker_symbol.upper() in company['symbol'].upper():
                return float(company['total_holdings'])
    except Exception:
        pass
    return 331200.0 # 備用數值

def get_dynamic_historical_holdings(dates_series, current_holdings, ticker_symbol):
    """動態歷史持幣量階梯函數 (避免時空旅行偏誤)"""
    holdings_series = pd.Series(index=dates_series, dtype=float)
    for date in dates_series:
        if ticker_symbol == 'MSTR':
            if date < pd.to_datetime('2023-01-01'):
                holdings_series[date] = 132500
            elif date < pd.to_datetime('2024-01-01'):
                holdings_series[date] = 189150
            elif date < pd.to_datetime('2024-08-01'):
                holdings_series[date] = 226500
            else:
                holdings_series[date] = current_holdings
        else:
            # 若為其他公司，暫以最新持幣量估算
            holdings_series[date] = current_holdings 
    return holdings_series

# 加上 st.cache_data 並設定 ttl=86400 (24小時)，確保每天自動更新且不爆 API Quota
@st.cache_data(ttl=86400, show_spinner="連線至華爾街與區塊鏈節點，即時抓取最新數據中...")
def load_dynamic_data(ticker_symbol):
    end_date = datetime.date.today().strftime('%Y-%m-%d')
    start_date = '2022-06-01'
    
    current_btc_holdings = fetch_live_btc_holdings(ticker_symbol)

    # 抓取幣價與股價
    btc_close = yf.download('BTC-USD', start=start_date, end=end_date)['Close'].squeeze()
    stock_close = yf.download(ticker_symbol, start=start_date, end=end_date)['Close'].squeeze()
    
    # 抓取歷史股本與分割資訊
    ticker_obj = yf.Ticker(ticker_symbol)
    historical_shares = ticker_obj.get_shares_full(start=start_date, end=end_date)
    splits = ticker_obj.splits
    
    # 統一抹除時區與重複值
    for series in [btc_close, stock_close, historical_shares]:
        series.index = pd.to_datetime(series.index).tz_localize(None)
        series = series[~series.index.duplicated(keep='last')]
    
    # 處理股票分割乘數
    if not splits.empty:
        splits.index = pd.to_datetime(splits.index).tz_localize(None)
        splits = splits[~splits.index.duplicated(keep='last')]
    
    split_multipliers = pd.Series(1.0, index=historical_shares.index)
    for split_date, split_ratio in splits.items():
        split_multipliers[split_multipliers.index < split_date] *= split_ratio
    
    adjusted_shares = (historical_shares * split_multipliers).rename('Shares_Outstanding')

    # 合併與計算
    df = pd.concat([stock_close.rename('Stock_Price'), btc_close.rename('BTC_Price'), adjusted_shares], axis=1)
    df['Shares_Outstanding'] = df['Shares_Outstanding'].ffill().bfill() 
    df = df.dropna(subset=['Stock_Price', 'BTC_Price'])

    df['Market_Cap'] = df['Stock_Price'] * df['Shares_Outstanding']
    df['Historical_Holdings'] = get_dynamic_historical_holdings(df.index, current_btc_holdings, ticker_symbol)
    df['mNAV'] = df['Historical_Holdings'] * df['BTC_Price']
    
    df['NAV_Diff_Percentage'] = ((df['Market_Cap'] - df['mNAV']) / df['mNAV']) * 100
    df['Premium_to_NAV'] = df['NAV_Diff_Percentage'].apply(lambda x: x if x > 0 else 0)
    df['Discount_to_NAV'] = df['NAV_Diff_Percentage'].apply(lambda x: x if x < 0 else 0)
    
    return df

# --- 3. 前端網頁介面 (Frontend UI) ---

try:
    # 側邊欄設定
    st.sidebar.header("⚙️ 參數設定")
    selected_ticker = st.sidebar.selectbox("選擇 DAT.co 標的", ["MSTR", "MARA"])
    
    # 觸發資料載入 (這裡會自動呼叫 API)
    df = load_dynamic_data(selected_ticker)
    
    # 日期篩選器
    date_range = st.sidebar.date_input("選擇分析區間", [df.index.min(), df.index.max()])
    mask = (df.index >= pd.Timestamp(date_range[0])) & (df.index <= pd.Timestamp(date_range[1]))
    filtered_df = df.loc[mask]

    # 關鍵數據看版
    col1, col2, col3, col4 = st.columns(4)
    latest = filtered_df.iloc[-1]
    col1.metric(f"最新 {selected_ticker} 股價", f"${latest['Stock_Price']:.2f}")
    col2.metric("最新 BTC 價格", f"${latest['BTC_Price']:.2f}")
    col3.metric("目前 BTC 持幣量", f"{latest['Historical_Holdings']:,.0f} 顆")
    col4.metric("折溢價率 (NAV Diff %)", f"{latest['NAV_Diff_Percentage']:.2f}%", delta_color="normal")

    # 主圖表：mNAV 與市值走勢
    st.subheader(f"📈 {selected_ticker} 市值 vs. 比特幣淨資產價值 (mNAV)")
    fig_main = go.Figure()
    fig_main.add_trace(go.Scatter(x=filtered_df.index, y=filtered_df['Market_Cap'], name="企業總市值", line=dict(color='royalblue')))
    fig_main.add_trace(go.Scatter(x=filtered_df.index, y=filtered_df['mNAV'], name="底層比特幣淨值 (mNAV)", line=dict(color='orange')))
    fig_main.update_layout(hovermode="x unified", height=450, yaxis_title="美元 (USD)")
    st.plotly_chart(fig_main, use_container_width=True)

    # 指標圖表：折溢價區間圖
    st.subheader("📉 Premium / Discount to NAV 區間分析")
    fig_diff = go.Figure()
    fig_diff.add_trace(go.Scatter(x=filtered_df.index, y=filtered_df['Premium_to_NAV'], fill='tozeroy', name="Premium (溢價)", line=dict(color='green', width=1)))
    fig_diff.add_trace(go.Scatter(x=filtered_df.index, y=filtered_df['Discount_to_NAV'], fill='tozeroy', name="Discount (折價)", line=dict(color='red', width=1)))
    fig_diff.update_layout(height=350, yaxis_title="百分比 (%)", hovermode="x unified")
    st.plotly_chart(fig_diff, use_container_width=True)

except Exception as e:
    st.error(f"資料抓取或處理發生錯誤: {e}")