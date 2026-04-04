import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import plotly.graph_objects as go
import datetime
from google import genai

# --- 1. 網站基本設定 ---
st.set_page_config(page_title="DAT.co 數位資產監控平台", layout="wide")
st.title("📊 DAT.co 數位資產監控平台 (V15 最終修復版)")
st.markdown("本平台監控數位資產庫存型企業的折溢價表現，並提供區間 AI 智能分析。")

# --- 2. 後端核心邏輯 ---

@st.cache_data(ttl=3600)
def get_ai_insight_v15(ticker, start_date, end_date, start_price, end_price, avg_premium, max_premium, _api_key):
    """具備時空意識的 AI 分析：分析選定區間的市場表現"""
    try:
        client = genai.Client(api_key=_api_key)
        prompt = f"""
        你是一位專業的華爾街量化分析師。請針對以下區間數據進行深度解讀：
        
        【分析對象】：{ticker}
        【分析區間】：{start_date} 到 {end_date}
        【股價變化】：起始價 ${start_price:.2f} -> 結束價 ${end_price:.2f}
        【平均溢價】：{avg_premium:.2f}%
        【最高溢價】：{max_premium:.2f}%
        
        請用繁體中文撰寫約 200 字分析：
        1. 描述此特定時段市場對該公司的熱度變化。
        2. 根據折溢價波動，判斷是否存在 FOMO 追高或價值低估情形。
        3. 總結該階段的投資情緒。
        """
        # 使用最穩定的模型名稱
        response = client.models.generate_content(model="gemini-flash-latest", contents=prompt)
        return response.text
    except Exception as e:
        return f"AI 暫時無法回應: {str(e)}"

def fetch_live_btc_holdings(ticker_symbol):
    """抓取最新持幣量，並針對 MSTR 實施強制校正"""
    if ticker_symbol.upper() == 'MSTR': return 331200.0
    url = "https://api.coingecko.com/api/v3/companies/public_treasury/bitcoin"
    try:
        response = requests.get(url, headers={"accept": "application/json", "User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            for c in response.json().get('companies', []):
                if ticker_symbol.upper() in c['symbol'].upper(): return float(c['total_holdings'])
    except: pass
    return 0.0

@st.cache_data(ttl=86400)
def load_data_v15(ticker):
    """強韌型資料抓取管線"""
    end = datetime.date.today()
    start = '2023-01-01' # 縮短區間可提高抓取成功率
    
    # 解決 'Close' 找不到與 MultiIndex 問題，加入 repair 提高穩定性
    btc = yf.download('BTC-USD', start=start, end=end, multi_level_index=False, repair=True)
    stock = yf.download(ticker, start=start, end=end, multi_level_index=False, repair=True)
    
    if btc.empty or stock.empty:
        # 備援方案：若 download 失敗，嘗試使用 Ticker 抓取
        btc = yf.Ticker('BTC-USD').history(period="2y")
        stock = yf.Ticker(ticker).history(period="2y")

    if btc.empty or stock.empty: return pd.DataFrame()
    
    # 強制校正欄位名稱
    btc.columns = [c.strip().capitalize() for c in btc.columns]
    stock.columns = [c.strip().capitalize() for c in stock.columns]
    
    def clean(df):
        df.index = pd.DatetimeIndex(df.index).tz_localize(None)
        return df[~df.index.duplicated(keep='last')]
    
    btc = clean(btc)
    stock = clean(stock)
    
    # 抓取發行股數
    t_obj = yf.Ticker(ticker)
    shares = t_obj.get_shares_full(start=start, end=end)
    if shares is not None and not shares.empty:
        shares.index = pd.DatetimeIndex(shares.index).tz_localize(None)
        shares = shares[~shares.index.duplicated(keep='last')]
    else:
        shares = pd.Series(345000000.0 if ticker.upper() == 'MSTR' else 312000000.0, index=stock.index)

    # 核心合併邏輯：Inner Join 確保 Stock_Price 永遠存在
    df = pd.concat([
        stock['Close'].rename('Stock_Price'),
        btc['Close'].rename('BTC_Price'),
        shares.rename('Shares')
    ], axis=1, join='inner')
    
    df['Shares'] = df['Shares'].ffill().bfill()
    
    # MSTR 1:10 拆股校正 (2024-08-08)
    if ticker.upper() == 'MSTR':
        split_date = pd.to_datetime('2024-08-08')
        df.loc[df.index < split_date, 'Shares'] *= 10.0
        
    cur_btc = fetch_live_btc_holdings(ticker)
    df['Market_Cap'] = df['Stock_Price'] * df['Shares']
    
    # 歷史持倉設定
    df['Holdings'] = cur_btc
    if ticker.upper() == 'MSTR':
        df.loc[df.index < '2023-01-01', 'Holdings'] = 132500
        df.loc[(df.index >= '2023-01-01') & (df.index < '2024-01-01'), 'Holdings'] = 189150
        df.loc[(df.index >= '2024-01-01') & (df.index < '2024-04-01'), 'Holdings'] = 214246
        df.loc[(df.index >= '2024-04-01') & (df.index < '2024-08-01'), 'Holdings'] = 226500
        df.loc[(df.index >= '2024-08-01') & (df.index < '2024-11-01'), 'Holdings'] = 252220
        
    df['mNAV'] = df['Holdings'] * df['BTC_Price']
    df['Premium_Pct'] = ((df['Market_Cap'] - df['mNAV']) / df['mNAV']) * 100
    df['Premium_to_NAV'] = df['Premium_Pct'].clip(lower=0)
    df['Discount_to_NAV'] = df['Premium_Pct'].clip(upper=0)
    return df

# --- 3. 前端介面設計 ---
try:
    st.sidebar.header("⚙️ 參數設定")
    ticker_choice = st.sidebar.selectbox("選擇標的", ["MSTR", "MARA"])
    df_full = load_data_v15(ticker_choice)
    
    if df_full.empty:
        st.error("❌ 抓不到 Yahoo Finance 數據。這可能是暫時的 API 限制，請點擊右下角 'Manage App' 進行 Reboot 或稍後重整。")
    else:
        # 日期選擇器
        date_range = st.sidebar.date_input("分析區間", [df_full.index.min().date(), df_full.index.max().date()])
        
        # 完美連動過濾邏輯
        if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
            start_date, end_date = date_range
            mask = (df_full.index.date >= start_date) & (df_full.index.date <= end_date)
            f_df = df_full.loc[mask]
        else:
            f_df = df_full

        if f_df.empty:
            st.warning("⚠️ 所選區間沒有資料（可能是選到了股市休市的週末），請拉寬區間。")
        else:
            # 區間運算
            latest = f_df.iloc[-1]
            first = f_df.iloc[0]
            avg_prem = f_df['Premium_Pct'].mean()
            
            # 數據看板
            st.markdown(f"### 📍 區間分析報告 ({f_df.index.min().date()} ~ {f_df.index.max().date()})")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric(f"{ticker_choice} 股價", f"${latest['Stock_Price']:.2f}")
            c2.metric("BTC 股價", f"${latest['BTC_Price']:.2f}")
            c3.metric("BTC 持倉", f"{latest['Holdings']:,.0f}")
            c4.metric("平均折溢價", f"{avg_prem:.2f}%")

            # 圖表展示
            st.subheader("📈 市值 vs. 比特幣淨資產價值 (mNAV)")
            fig1 = go.Figure()
            fig1.add_trace(go.Scatter(x=f_df.index, y=f_df['Market_Cap'], name="總市值", line=dict(color='royalblue')))
            fig1.add_trace(go.Scatter(x=f_df.index, y=f_df['mNAV'], name="mNAV (BTC價值)", line=dict(color='orange')))
            fig1.update_layout(hovermode="x unified", height=400)
            st.plotly_chart(fig1, use_container_width=True)

            st.subheader("📉 折溢價區間波動分析")
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=f_df.index, y=f_df['Premium_to_NAV'], fill='tozeroy', name="Premium (溢價)", line=dict(color='green', width=1)))
            fig2.add_trace(go.Scatter(x=f_df.index, y=f_df['Discount_to_NAV'], fill='tozeroy', name="Discount (折價)", line=dict(color='red', width=1)))
            fig2.update_layout(hovermode="x unified", height=300)
            st.plotly_chart(fig2, use_container_width=True)

            # --- AI 分析連動 ---
            st.markdown("---")
            st.subheader(f"🤖 AI 區間深度洞見 ({f_df.index.min().date()} ~ {f_df.index.max().date()})")
            if st.button("✨ 產生此區間 AI 分析", key="ai_btn"):
                with st.spinner("AI 分析師正在研讀該區間數據..."):
                    try:
                        api_key = st.secrets["GEMINI_API_KEY"]
                        insight = get_ai_insight_v15(
                            ticker_choice, 
                            f_df.index.min().date(), f_df.index.max().date(),
                            first['Stock_Price'], latest['Stock_Price'],
                            avg_prem, f_df['Premium_Pct'].max(),
                            api_key
                        )
                        st.info(insight)
                    except Exception as ai_e:
                        st.error(f"AI 呼叫失敗: {ai_e}")

except Exception as e:
    st.error(f"系統異常: {e}")
