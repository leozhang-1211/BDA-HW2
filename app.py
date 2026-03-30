import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import plotly.graph_objects as go
import datetime
from google import genai

# --- 1. 網站基本設定 ---
st.set_page_config(page_title="DAT.co 指標監控平台", layout="wide")
st.title("📊 Digital Asset Treasury (DAT.co) 即時監控平台")
st.markdown("本平台結合 2.0 世代 AI 與量化防呆機制，實時追蹤公司的 mNAV 與折溢價指標。")

# --- 2. 後端資料管線 ---

@st.cache_data(ttl=3600) # AI 回應快取一小時，節省 API 額度
def get_ai_insight(ticker, price, btc_price, holdings, premium_pct, _api_key):
    try:
        client = genai.Client(api_key=_api_key)
        prompt = f"""
        你是一位專業量化分析師。分析標的：{ticker}。
        目前股價：${price:.2f}，BTC價格：${btc_price:.2f}，
        持幣量：{holdings:,.0f}顆，折溢價率：{premium_pct:.2f}%。
        請用繁體中文簡述目前市場的情緒與投資風險。
        """
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"AI 暫時無法回應: {str(e)}"

def fetch_live_btc_holdings(ticker_symbol):
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
            if date < pd.to_datetime('2023-01-01'): holdings_series[date] = 132500
            elif date < pd.to_datetime('2024-01-01'): holdings_series[date] = 189150
            elif date < pd.to_datetime('2024-04-01'): holdings_series[date] = 214246
            elif date < pd.to_datetime('2024-08-01'): holdings_series[date] = 226500
            elif date < pd.to_datetime('2024-11-01'): holdings_series[date] = 252220
            else: holdings_series[date] = current_holdings
        else:
            holdings_series[date] = current_holdings 
    return holdings_series

@st.cache_data(ttl=86400, show_spinner="正在從華爾街同步數據...")
def load_dat_pipeline_final(ticker_symbol):
    end_date = datetime.date.today().strftime('%Y-%m-%d')
    start_date = '2022-06-01'
    current_btc_holdings = fetch_live_btc_holdings(ticker_symbol)
    btc_close = yf.download('BTC-USD', start=start_date, end=end_date)['Close'].squeeze()
    stock_close = yf.download(ticker_symbol, start=start_date, end=end_date)['Close'].squeeze()
    ticker_obj = yf.Ticker(ticker_symbol)
    historical_shares = ticker_obj.get_shares_full(start=start_date, end=end_date)
    splits = ticker_obj.splits
    
    def clean_series(s):
        if s is None or s.empty: return s
        s.index = pd.DatetimeIndex(s.index)
        if s.index.tz is not None: s.index = s.index.tz_localize(None)
        return s[~s.index.duplicated(keep='last')]

    btc_close = clean_series(btc_close)
    stock_close = clean_series(stock_close)
    historical_shares = clean_series(historical_shares)
    
    split_multipliers = pd.Series(1.0, index=historical_shares.index)
    if ticker_symbol.upper() == 'MSTR':
        split_date = pd.to_datetime('2024-08-08')
        split_multipliers[split_multipliers.index < split_date] *= 10.0
    if splits is not None and not splits.empty:
        splits.index = pd.DatetimeIndex(splits.index)
        if splits.index.tz is not None: splits.index = splits.index.tz_localize(None)
        for s_date, s_ratio in splits.items():
            if ticker_symbol.upper() == 'MSTR' and s_date.strftime('%Y-%m-%d') == '2024-08-08': continue
            split_multipliers[split_multipliers.index < s_date] *= s_ratio
            
    adjusted_shares = (historical_shares * split_multipliers).rename('Shares_Outstanding')
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

# --- 3. 前端網頁介面 ---
try:
    st.sidebar.header("⚙️ 參數設定")
    selected_ticker = st.sidebar.selectbox("選擇標的", ["MSTR", "MARA"])
    df = load_dat_pipeline_final(selected_ticker)
    
    if not df.empty:
        min_date, max_date = df.index.min(), df.index.max()
        date_range = st.sidebar.date_input("分析區間", [min_date.date(), max_date.date()])
        
        if len(date_range) == 2:
            mask = (df.index >= pd.Timestamp(date_range[0])) & (df.index <= pd.Timestamp(date_range[1]))
            f_df = df.loc[mask]
        else:
            f_df = df

        if not f_df.empty:
            latest = f_df.iloc[-1]
            # 【關鍵修復點】：在這裡統一計算 diff 變數，確保下方所有區塊都讀得到
            current_premium_pct = latest['NAV_Diff_Percentage']
            
            # 數據看版
            c1, c2, c3, c4 = st.columns(4)
            c1.metric(f"最新 {selected_ticker}", f"${latest['Stock_Price']:.2f}")
            c2.metric("最新 BTC", f"${latest['BTC_Price']:.2f}")
            c3.metric("BTC 持倉", f"{latest['Historical_Holdings']:,.0f}")
            c4.metric("折溢價率", f"{current_premium_pct:.2f}%")

            # 圖表
            st.subheader("📈 市值 vs. 比特幣淨資產價值 (mNAV)")
            fig1 = go.Figure()
            fig1.add_trace(go.Scatter(x=f_df.index, y=f_df['Market_Cap'], name="總市值", line=dict(color='royalblue')))
            fig1.add_trace(go.Scatter(x=f_df.index, y=f_df['mNAV'], name="mNAV", line=dict(color='orange')))
            st.plotly_chart(fig1, use_container_width=True)

            st.subheader("📉 Premium / Discount 區間分析")
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=f_df.index, y=f_df['Premium_to_NAV'], fill='tozeroy', name="Premium", line=dict(color='green')))
            fig2.add_trace(go.Scatter(x=f_df.index, y=f_df['Discount_to_NAV'], fill='tozeroy', name="Discount", line=dict(color='red')))
            st.plotly_chart(fig2, use_container_width=True)

            # --- 4. AI 智能分析 ---
            st.markdown("---")
            st.subheader("🤖 AI 財經洞見分析")
            if st.button("✨ 產生 AI 分析報告"):
                with st.spinner("AI 分析師正在研讀數據 (免費版 API 若頻繁呼叫可能需等待)..."):
                    try:
                        api_key = st.secrets["GEMINI_API_KEY"]
                        # 使用定義好的變數 current_premium_pct 傳入
                        insight = get_ai_insight(
                            selected_ticker, 
                            latest['Stock_Price'], 
                            latest['BTC_Price'], 
                            latest['Historical_Holdings'], 
                            current_premium_pct, 
                            api_key
                        )
                        st.info(insight)
                    except Exception as ai_err:
                        if "429" in str(ai_err):
                            st.error("⚠️ API 額度用完，請等待一分鐘後再試。")
                        else:
                            st.error(f"AI 錯誤: {ai_err}")

except Exception as e:
    st.error(f"系統錯誤: {e}")
