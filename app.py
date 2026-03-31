import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import plotly.graph_objects as go
import datetime
from google import genai

# --- 1. 網站基本設定 ---
st.set_page_config(page_title="DAT.co 指標監控平台", layout="wide")
st.title("📊 DAT.co 指標監控平台 - AI 深度分析版")

# --- 2. 後端核心邏輯 ---

@st.cache_data(ttl=3600)
def get_ai_insight_v2(ticker, start_date, end_date, start_price, end_price, avg_premium, max_premium, _api_key):
    """具備時空意識的 AI 分析：分析特定區間的趨勢"""
    try:
        client = genai.Client(api_key=_api_key)
        prompt = f"""
        你是一位專業的華爾街量化分析師。請針對以下區間的數據進行深度解讀：
        
        【分析對象】：{ticker}
        【時間區間】：{start_date} 至 {end_date}
        【區間股價變化】：從 ${start_price:.2f} 變動至 ${end_price:.2f}
        【區間平均溢價率】：{avg_premium:.2f}%
        【區間最高溢價率】：{max_premium:.2f}%
        
        請用繁體中文提供約 200 字的專業分析：
        1. 評估這段時間內市場對該公司的情緒變化。
        2. 根據溢價率的波動，判斷此區間是否存在過熱 (FOMO) 或價值低估。
        3. 給予該階段的投資策略建議。
        """
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
def load_data_v12(ticker):
    end = datetime.date.today()
    start = '2022-06-01'
    # 解決 'Close' 報錯與 MultiIndex 問題
    btc = yf.download('BTC-USD', start=start, end=end, multi_level_index=False)
    stock = yf.download(ticker, start=start, end=end, multi_level_index=False)
    
    if btc.empty or stock.empty: return pd.DataFrame()
    
    def clean(s): 
        s.index = pd.DatetimeIndex(s.index).tz_localize(None)
        return s[~s.index.duplicated(keep='last')]
    
    btc_c = clean(btc['Close']).rename('BTC_Price')
    stk_c = clean(stock['Close']).rename('Stock_Price')
    
    # 抓取股數並處理 MSTR 分割
    t_obj = yf.Ticker(ticker)
    shares = t_obj.get_shares_full(start=start, end=end)
    if shares is None or shares.empty:
        shares = pd.Series(345000000.0 if ticker.upper() == 'MSTR' else 312000000.0, index=stk_c.index)
    else:
        shares = clean(shares)
    
    df = pd.concat([stk_c, btc_c, shares.rename('Shares')], axis=1)
    df['Shares'] = df['Shares'].ffill().bfill()
    df = df.dropna(subset=['Stock_Price', 'BTC_Price'])
    
    if ticker.upper() == 'MSTR':
        split_date = pd.to_datetime('2024-08-08')
        df.loc[df.index < split_date, 'Shares'] *= 10.0
        
    cur_btc = fetch_live_btc_holdings(ticker)
    df['Market_Cap'] = df['Stock_Price'] * df['Shares']
    df['Holdings'] = get_historical_holdings(df.index, cur_btc, ticker)
    df['mNAV'] = df['Holdings'] * df['BTC_Price']
    df['Premium_Pct'] = ((df['Market_Cap'] - df['mNAV']) / df['mNAV']) * 100
    df['Premium_to_NAV'] = df['Premium_Pct'].clip(lower=0)
    df['Discount_to_NAV'] = df['Premium_Pct'].clip(upper=0)
    return df

# --- 3. 前端網頁介面 ---
try:
    st.sidebar.header("⚙️ 參數設定")
    ticker_choice = st.sidebar.selectbox("選擇標的", ["MSTR", "MARA"])
    df_full = load_data_v12(ticker_choice)
    
    if df_full.empty:
        st.error("無法取得資料。")
    else:
        # 1. 建立日期篩選
        dates = st.sidebar.date_input("分析區間", [df_full.index.min().date(), df_full.index.max().date()])
        
        # 2. 核心過濾邏輯：所有元件都使用 f_df (Filtered DataFrame)
        if isinstance(dates, list) and len(dates) == 2:
            start_p, end_p = pd.Timestamp(dates[0]), pd.Timestamp(dates[1])
            f_df = df_full.loc[start_p:end_p]
        else:
            f_df = df_full

        if not f_df.empty:
            # 取得區間統計數據
            latest = f_df.iloc[-1]
            first = f_df.iloc[0]
            avg_prem = f_df['Premium_Pct'].mean()
            max_prem = f_df['Premium_Pct'].max()
            
            # 數據看板 (反映區間最後一天)
            st.markdown(f"### 📍 區間數據看板 ({dates[0]} 至 {dates[1]})")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric(f"{ticker_choice} 收盤價", f"${latest['Stock_Price']:.2f}")
            c2.metric("BTC 收盤價", f"${latest['BTC_Price']:.2f}")
            c3.metric("BTC 持倉", f"{latest['Holdings']:,.0f}")
            c4.metric("區間平均溢價", f"{avg_prem:.2f}%")

            # 圖表連動
            st.subheader("📈 市值 vs. 比特幣淨資產價值 (mNAV)")
            fig1 = go.Figure()
            fig1.add_trace(go.Scatter(x=f_df.index, y=f_df['Market_Cap'], name="總市值", line=dict(color='royalblue')))
            fig1.add_trace(go.Scatter(x=f_df.index, y=f_df['mNAV'], name="mNAV", line=dict(color='orange')))
            fig1.update_layout(hovermode="x unified")
            st.plotly_chart(fig1, use_container_width=True)

            st.subheader("📉 折溢價區間波動分析")
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=f_df.index, y=f_df['Premium_to_NAV'], fill='tozeroy', name="Premium", line=dict(color='green')))
            fig2.add_trace(go.Scatter(x=f_df.index, y=f_df['Discount_to_NAV'], fill='tozeroy', name="Discount", line=dict(color='red')))
            st.plotly_chart(fig2, use_container_width=True)

            # --- AI 分析區 (連動時空背景) ---
            st.markdown("---")
            st.subheader(f"🤖 AI 2.0 區間深度洞見 ({dates[0]} ~ {dates[1]})")
            if st.button("✨ 產生區間分析報告"):
                with st.spinner("AI 正在分析該段時間的市場情緒趨勢..."):
                    try:
                        api_key = st.secrets["GEMINI_API_KEY"]
                        # 將區間的關鍵特徵傳給 AI
                        insight = get_ai_insight_v2(
                            ticker_choice, 
                            dates[0], dates[1],
                            first['Stock_Price'], latest['Stock_Price'],
                            avg_prem, max_prem,
                            api_key
                        )
                        st.info(insight)
                    except Exception as e:
                        st.error(f"AI 分析失敗: {e}")

except Exception as e:
    st.error(f"發生錯誤: {e}")
