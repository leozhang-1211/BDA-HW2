import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import plotly.graph_objects as go
import datetime
from google import genai

# --- 1. 網站基本設定 ---
st.set_page_config(page_title="DAT.co 數位資產監控平台", layout="wide")
st.title("📊 DAT.co 數位資產監控平台")
st.markdown("本平台結合 AI 2.0 與動態時空過濾技術，分析特定時間區間內的折溢價表現。")

# --- 2. 後端核心邏輯 ---

@st.cache_data(ttl=3600)
def get_ai_insight_v13(ticker, start_date, end_date, start_price, end_price, avg_premium, max_premium, _api_key):
    """具備時空意識的 AI 分析：分析使用者選定的特定區間"""
    try:
        client = genai.Client(api_key=_api_key)
        prompt = f"""
        你是一位專業的華爾街量化分析師。請針對以下選定區間的數據進行深度解讀：
        
        【分析對象】：{ticker}
        【時間區間】：從 {start_date} 到 {end_date}
        【區間股價走勢】：起始價 ${start_price:.2f} -> 結束價 ${end_price:.2f}
        【區間平均溢價率】：{avg_premium:.2f}%
        【區間最高溢價紀錄】：{max_premium:.2f}%
        
        請以繁體中文撰寫一份約 200 字的專業分析：
        1. 描述這段特定時間內的市場情緒變化（例如：是否有 FOMO 追高或是折價恐慌？）。
        2. 解釋該區間的股價變動與比特幣溢價率的關聯性。
        3. 給予該階段歷史表現的策略評等。
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
def load_data_v13(ticker):
    end = datetime.date.today()
    start = '2022-06-01'
    # 解決 'Close' 找不到與 MultiIndex 問題
    btc = yf.download('BTC-USD', start=start, end=end, multi_level_index=False)
    stock = yf.download(ticker, start=start, end=end, multi_level_index=False)
    
    if btc.empty or stock.empty: return pd.DataFrame()
    
    def clean(s): 
        s.index = pd.DatetimeIndex(s.index).tz_localize(None)
        return s[~s.index.duplicated(keep='last')]
    
    btc_c = clean(btc['Close']).rename('BTC_Price')
    stk_c = clean(stock['Close']).rename('Stock_Price')
    
    # 處理股數與 MSTR 1:10 分割
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
    df_full = load_data_v13(ticker_choice)
    
    if df_full.empty:
        st.error("無法取得資料。")
    else:
        # 建立日期選取器
        dates = st.sidebar.date_input("分析區間", [df_full.index.min().date(), df_full.index.max().date()])
        
        # 【核心 Bug 修復點】：Streamlit 的 date_input 回傳的是 tuple
        if len(dates) == 2:
            start_date, end_date = dates
            # 使用布林遮罩確保 100% 過濾
            mask = (df_full.index.date >= start_date) & (df_full.index.date <= end_date)
            f_df = df_full.loc[mask]
        else:
            f_df = df_full

        if not f_df.empty:
            # 取得區間統計
            latest = f_df.iloc[-1]
            first = f_df.iloc[0]
            avg_prem = f_df['Premium_Pct'].mean()
            max_prem = f_df['Premium_Pct'].max()
            
            # 數據看板 (動態反映所選區間)
            st.markdown(f"### 📍 區間分析看板 ({f_df.index.min().date()} ~ {f_df.index.max().date()})")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric(f"{ticker_choice} 區間終值", f"${latest['Stock_Price']:.2f}")
            c2.metric("BTC 區間終值", f"${latest['BTC_Price']:.2f}")
            c3.metric("區間持倉量", f"{latest['Holdings']:,.0f}")
            c4.metric("區間平均溢價", f"{avg_prem:.2f}%")

            # --- 圖表區 (現在會隨著 f_df 變動) ---
            st.subheader("📈 市值 vs. 比特幣淨資產價值 (mNAV)")
            fig1 = go.Figure()
            # 確保 x 和 y 都只抓取過濾後的 f_df
            fig1.add_trace(go.Scatter(x=f_df.index, y=f_df['Market_Cap'], name="總市值", line=dict(color='royalblue')))
            fig1.add_trace(go.Scatter(x=f_df.index, y=f_df['mNAV'], name="mNAV", line=dict(color='orange')))
            fig1.update_layout(hovermode="x unified", height=450)
            st.plotly_chart(fig1, use_container_width=True)

            st.subheader("📉 折溢價區間波動分析")
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=f_df.index, y=f_df['Premium_to_NAV'], fill='tozeroy', name="Premium (溢價)", line=dict(color='green', width=1)))
            fig2.add_trace(go.Scatter(x=f_df.index, y=f_df['Discount_to_NAV'], fill='tozeroy', name="Discount (折價)", line=dict(color='red', width=1)))
            fig2.update_layout(hovermode="x unified", height=350)
            st.plotly_chart(fig2, use_container_width=True)

            # --- AI 分析區 (連動時空背景) ---
            st.markdown("---")
            st.subheader(f"🤖 AI 2.0 區間深度洞見 ({f_df.index.min().date()} ~ {f_df.index.max().date()})")
            
            # 使用唯一 Key 確保按鈕點擊觸發重新計算
            if st.button("✨ 生成此區間的 AI 分析報告", key="generate_ai"):
                with st.spinner("AI 正在深度掃描該時段的市場情緒..."):
                    try:
                        api_key = st.secrets["GEMINI_API_KEY"]
                        # 將區間特徵傳給 AI
                        insight = get_ai_insight_v13(
                            ticker_choice, 
                            f_df.index.min().date(), f_df.index.max().date(),
                            first['Stock_Price'], latest['Stock_Price'],
                            avg_prem, max_prem,
                            api_key
                        )
                        st.info(insight)
                    except Exception as e:
                        st.error(f"AI 分析失敗: {e}")

except Exception as e:
    st.error(f"發生錯誤: {e}")
