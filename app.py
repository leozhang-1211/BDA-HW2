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
            elif date < pd.to_datetime('2024-11-01'):
                holdings_series[date] = 252220
            else:
                holdings_series[date] = current_holdings
        else:
            holdings_series[date] = current_holdings 
    return holdings_series

@st.cache_data(ttl=86400, show_spinner="連線至華爾街與區塊鏈節點，即時抓取最新數據中...")
def load_dat_pipeline_v5(ticker_symbol):
    end_date = datetime.date.today().strftime('%Y-%m-%d')
    start_date = '2022-06-01'
    
    current_btc_holdings = fetch_live_btc_holdings(ticker_symbol)
    btc_close = yf.download('BTC-USD', start=start_date, end=end_date)['Close'].squeeze()
    stock_close = yf.download(ticker_symbol, start=start_date, end=end_date)['Close'].squeeze()
    
    ticker_obj = yf.Ticker(ticker_symbol)
    historical_shares = ticker_obj.get_shares_full(start=start_date, end=end_date)
    splits = ticker_obj.splits
    
    def clean_series(s):
        if s is None or s.empty:
            return s
        s.index = pd.DatetimeIndex(s.index)
        if s.index.tz is not None:
            s.index = s.index.tz_localize(None)
        s = s[~s.index.duplicated(keep='last')]
        return s

    btc_close = clean_series(btc_close)
    stock_close = clean_series(stock_close)
    historical_shares = clean_series(historical_shares)
    
    split_multipliers = pd.Series(1.0, index=historical_shares.index)
    if ticker_symbol.upper() == 'MSTR':
        split_date = pd.to_datetime('2024-08-08')
        split_multipliers[split_multipliers.index < split_date] *= 10.0
        
    if splits is not None and not splits.empty:
        splits.index = pd.DatetimeIndex(splits.index)
        if splits.index.tz is not None:
            splits.index = splits.index.tz_localize(None)
        splits = splits[~splits.index.duplicated(keep='last')]
        for s_date, s_ratio in splits.items():
            if ticker_symbol.upper() == 'MSTR' and s_date.strftime('%Y-%m-%d') == '2024-08-08':
                continue
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

# --- 3. 前端網頁介面 (Frontend UI) ---
try:
    st.sidebar.header("⚙️ 參數設定")
    selected_ticker = st.sidebar.selectbox("選擇 DAT.co 標的", ["MSTR", "MARA"])
    
    df = load_dat_pipeline_v5(selected_ticker)
    
    date_range = st.sidebar.date_input("選擇分析區間", [df.index.min(), df.index.max()])
    mask = (df.index >= pd.Timestamp(date_range[0])) & (df.index <= pd.Timestamp(date_range[1]))
    filtered_df = df.loc[mask]

    latest = filtered_df.iloc[-1]
    
    # 關鍵數據看版
    col1, col2, col3, col4 = st.columns(4)
    col1.metric(f"最新 {selected_ticker} 股價", f"${latest['Stock_Price']:.2f}")
    col2.metric("最新 BTC 價格", f"${latest['BTC_Price']:.2f}")
    col3.metric("目前 BTC 持幣量", f"{latest['Historical_Holdings']:,.0f} 顆")
    diff = latest['NAV_Diff_Percentage']
    col4.metric("折溢價率 (NAV Diff %)", f"{diff:.2f}%", delta_color="normal")

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

    # --- 4. AI 智能分析區 (Bonus Feature) ---
    st.markdown("---")
    st.subheader("🤖 AI 趨勢分析與財經洞見 (AI-Generated Insights)")
    
    if st.button("✨ 根據最新數據生成 AI 分析報告"):
        with st.spinner("AI 量化分析師正在解讀市場數據中..."):
            try:
                # 從 Streamlit Secrets 讀取 API Key
                api_key = st.secrets["GEMINI_API_KEY"]
                genai.configure(api_key=api_key)
                model = genai.GenerativeModel('gemini-1.5-flash-latest')
                
                prompt = f"""
                你現在是一位華爾街資深的加密貨幣與量化金融分析師。
                請根據以下 {selected_ticker} 公司的最新財務與市場指標，寫出一份簡短、專業且具備洞見的總結報告（請用繁體中文回答，約 150-200 字即可）。
                
                最新數據如下：
                - 分析目標：{selected_ticker}
                - 目前股票價格：${latest['Stock_Price']:.2f} USD
                - 目前比特幣價格：${latest['BTC_Price']:.2f} USD
                - 公司比特幣持倉量：{latest['Historical_Holdings']:,.0f} 顆
                - 目前相對於持幣淨值(mNAV)的折溢價率：{diff:.2f}%
                
                請在報告中回答以下重點：
                1. 解釋目前的折溢價狀態代表什麼市場情緒？(FOMO 或 恐慌？)
                2. 這個溢價/折價狀態對投資人有什麼潛在風險或機會？
                """
                
                response = model.generate_content(prompt)
                st.success("分析完成！")
                st.info(response.text)
                
            except KeyError:
                st.error("⚠️ 系統找不到 API Key！請確認是否已在 Streamlit Secrets 中設定 `GEMINI_API_KEY`。")
            except Exception as e:
                st.error(f"呼叫 AI API 時發生錯誤：{e}")

except Exception as e:
    st.error(f"資料抓取或處理發生錯誤: {e}")
