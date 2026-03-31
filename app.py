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
st.markdown("本平台結合 API 自動校正與 AI 技術，監控數位資產公司的淨值表現。")

# --- 2. 後端核心邏輯 ---

@st.cache_data(ttl=3600)
def get_ai_insight(ticker, price, btc_price, holdings, premium_pct, _api_key):
    try:
        client = genai.Client(api_key=_api_key)
        prompt = f"你是一位資深量化分析師。分析標的：{ticker}。目前股價：${price:.2f}，BTC價格：${btc_price:.2f}，持幣：{holdings:,.0f}顆，折溢價率：{premium_pct:.2f}%。請用繁體中文簡述市場情緒與風險。"
        response = client.models.generate_content(model="gemini-flash-latest", contents=prompt)
        return response.text
    except Exception as e:
        return f"AI 暫時無法回應: {str(e)}"

def fetch_live_btc_holdings(ticker_symbol):
    if ticker_symbol.upper() == 'MSTR': return 331200.0
    url = "https://api.coingecko.com/api/v3/companies/public_treasury/bitcoin"
    try:
        response = requests.get(url, headers={"accept": "application/json", "User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            for c in response.json().get('companies', []):
                if ticker_symbol.upper() in c['symbol'].upper(): return float(c['total_holdings'])
    except: pass
    return 0.0

def get_historical_holdings(dates, current_val, ticker):
    h = pd.Series(current_val, index=dates)
    if ticker.upper() == 'MSTR':
        h[dates < '2023-01-01'] = 132500
        h[(dates >= '2023-01-01') & (dates < '2024-01-01')] = 189150
        h[(dates >= '2024-01-01') & (dates < '2024-04-01')] = 214246
        h[(dates >= '2024-04-01') & (dates < '2024-08-01')] = 226500
        h[(dates >= '2024-08-01') & (dates < '2024-11-01')] = 252220
    return h

@st.cache_data(ttl=86400)
def load_data_pipeline_v11(ticker):
    end = datetime.date.today()
    start = '2022-06-01'
    
    # 【關鍵修正】：加入 multi_level_index=False 徹底解決 'Close' 找不到的問題
    btc = yf.download('BTC-USD', start=start, end=end, multi_level_index=False)
    stock = yf.download(ticker, start=start, end=end, multi_level_index=False)
    
    if btc.empty or stock.empty:
        return pd.DataFrame()

    # 確保欄位名稱正確 (有些版本會變成大寫或有空白)
    btc.columns = [c.strip() for c in btc.columns]
    stock.columns = [c.strip() for c in stock.columns]
    
    # 抓取發行股數
    t_obj = yf.Ticker(ticker)
    shares = t_obj.get_shares_full(start=start, end=end)
    
    def clean(s): 
        s.index = pd.DatetimeIndex(s.index).tz_localize(None)
        return s[~s.index.duplicated(keep='last')]
    
    # 這裡現在絕對能抓到 'Close' 了
    btc_c = clean(btc['Close']).rename('BTC_Price')
    stk_c = clean(stock['Close']).rename('Stock_Price')
    
    if shares is None or shares.empty:
        shares = pd.Series(345000000.0 if ticker.upper() == 'MSTR' else 312000000.0, index=stk_c.index)
    else:
        shares = clean(shares)
    
    shares = shares.rename('Shares')
    
    df = pd.concat([stk_c, btc_c, shares], axis=1)
    df['Shares'] = df['Shares'].ffill().bfill()
    df = df.dropna(subset=['Stock_Price', 'BTC_Price'])
    
    # MSTR 1:10 分割校正
    if ticker.upper() == 'MSTR':
        split_date = pd.to_datetime('2024-08-08')
        df.loc[df.index < split_date, 'Shares'] *= 10.0
        
    current_btc = fetch_live_btc_holdings(ticker)
    df['Market_Cap'] = df['Stock_Price'] * df['Shares']
    df['Holdings'] = get_historical_holdings(df.index, current_btc, ticker)
    df['mNAV'] = df['Holdings'] * df['BTC_Price']
    df['Premium_Pct'] = ((df['Market_Cap'] - df['mNAV']) / df['mNAV']) * 100
    df['Premium_to_NAV'] = df['Premium_Pct'].clip(lower=0)
    df['Discount_to_NAV'] = df['Premium_Pct'].clip(upper=0)
    
    return df

# --- 3. 前端網頁介面 ---
try:
    st.sidebar.header("⚙️ 參數設定")
    ticker_choice = st.sidebar.selectbox("選擇標的", ["MSTR", "MARA"])
    df = load_data_pipeline_v11(ticker_choice)
    
    if df.empty:
        st.error("無法取得資料，可能是 Yahoo Finance 暫時限制存取，請稍後再試。")
    else:
        dates = st.sidebar.date_input("分析區間", [df.index.min().date(), df.index.max().date()])
        
        if isinstance(dates, list) and len(dates) == 2:
            mask = (df.index >= pd.Timestamp(dates[0])) & (df.index <= pd.Timestamp(dates[1]))
            f_df = df.loc[mask]
        else:
            f_df = df

        if not f_df.empty:
            latest = f_df.iloc[-1]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric(f"最新 {ticker_choice}", f"${latest['Stock_Price']:.2f}")
            c2.metric("最新 BTC", f"${latest['BTC_Price']:.2f}")
            c3.metric("BTC 持倉", f"{latest['Holdings']:,.0f}")
            c4.metric("折溢價率", f"{latest['Premium_Pct']:.2f}%")

            st.subheader("📈 市值 vs. 比特幣淨資產價值 (mNAV)")
            fig1 = go.Figure()
            fig1.add_trace(go.Scatter(x=f_df.index, y=f_df['Market_Cap'], name="總市值", line=dict(color='royalblue')))
            fig1.add_trace(go.Scatter(x=f_df.index, y=f_df['mNAV'], name="mNAV", line=dict(color='orange')))
            fig1.update_layout(hovermode="x unified")
            st.plotly_chart(fig1, use_container_width=True)

            st.subheader("📉 Premium / Discount 區間分析")
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=f_df.index, y=f_df['Premium_to_NAV'], fill='tozeroy', name="Premium", line=dict(color='green')))
            fig2.add_trace(go.Scatter(x=f_df.index, y=f_df['Discount_to_NAV'], fill='tozeroy', name="Discount", line=dict(color='red')))
            st.plotly_chart(fig2, use_container_width=True)

            st.markdown("---")
            st.subheader("🤖 AI 財經洞見分析")
            if st.button("✨ 產生 AI 分析報告"):
                with st.spinner("AI 分析師正在研讀數據..."):
                    try:
                        api_key = st.secrets["GEMINI_API_KEY"]
                        insight = get_ai_insight(ticker_choice, latest['Stock_Price'], latest['BTC_Price'], latest['Holdings'], latest['Premium_Pct'], api_key)
                        st.info(insight)
                    except Exception as e:
                        st.error(f"AI 呼叫失敗: {e}")

except Exception as e:
    st.error(f"發生錯誤: {e}")
