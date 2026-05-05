#!/usr/bin/env python3
"""
强生战法V3 - 每日信号检查（优化版）
输出格式化的信号报告，用于微信通知
"""
import akshare as ak
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timedelta

INITIAL_CAPITAL = 100000
LOTS_PER_TRADE = 100
ATR_MULTIPLIER = 2.5
TAKE_PROFIT = 1.20
STOP_LOSS_MAX = 0.90

WATCHLIST = [
    ("002236", "大华股份", "stock"),
    ("000636", "风华高科", "stock"),
    ("159870", "有色金属ETF", "etf"),
]

STATE_FILE = "/root/wondertrader/run_stk_sim/portfolio.json"

def calc_qs(c): return c.ewm(span=10,adjust=False).mean().ewm(span=10,adjust=False).mean()
def calc_dage(c): return (c.rolling(14).mean()+c.rolling(28).mean()+c.rolling(57).mean()+c.rolling(114).mean())/4
def calc_bbi(c): return (c.rolling(3).mean()+c.rolling(6).mean()+c.rolling(12).mean()+c.rolling(24).mean())/4
def calc_kdj(h,l,c,n=9):
    ll=l.rolling(n).min(); hh=h.rolling(n).max()
    rsv=((c-ll)/(hh-ll)*100).fillna(50)
    k=rsv.ewm(com=2,adjust=False).mean(); d=k.ewm(com=2,adjust=False).mean(); j=3*k-2*d
    return k,d,j
def calc_atr(h,l,c,p=14):
    tr=pd.concat([h-l,abs(h-c.shift(1)),abs(l-c.shift(1))],axis=1).max(axis=1)
    return tr.rolling(p).mean()

def analyze_stock(code, name, positions, asset_type="stock"):
    """分析单只股票，返回信号"""
    try:
        if asset_type == "etf":
            raw = ak.fund_etf_hist_em(symbol=code, period='daily',
                start_date=(datetime.now()-timedelta(days=300)).strftime('%Y%m%d'),
                end_date=datetime.now().strftime('%Y%m%d'), adjust='qfq')
        else:
            raw = ak.stock_zh_a_hist(symbol=code, period='daily',
                start_date=(datetime.now()-timedelta(days=300)).strftime('%Y%m%d'),
                end_date=datetime.now().strftime('%Y%m%d'), adjust='qfq')
        if raw is None or len(raw) < 120:
            return None
    except:
        return None

    cm = {'日期':'date','开盘':'open','收盘':'close','最高':'high','最低':'low','成交量':'vol','成交额':'amount'}
    df = raw.rename(columns=cm)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    df['qs']=calc_qs(df['close']); df['dage']=calc_dage(df['close']); df['bbi']=calc_bbi(df['close'])
    df['ma20']=df['close'].rolling(20).mean(); df['atr']=calc_atr(df['high'],df['low'],df['close'])
    df['k'],df['d'],df['j']=calc_kdj(df['high'],df['low'],df['close'])
    df['prev_close']=df['close'].shift(1); df['prev_vol']=df['vol'].shift(1)
    df['prev_j']=df['j'].shift(1); df['prev_d']=df['d'].shift(1)
    df['prev2_close']=df['close'].shift(2); df['prev2_vol']=df['vol'].shift(2)
    df['vol_ma4']=df['vol'].shift(1).rolling(4).mean()
    df['today_chg']=(df['close']/df['prev_close']-1)*100
    df['yest_chg']=(df['prev_close']/df['prev2_close']-1)*100
    df['j_min3']=df['j'].rolling(3).min(); df['j_min4']=df['j'].rolling(4).min()
    df['trend_up']=df['qs']>df['dage']

    score=pd.Series(0,index=df.index)
    score+=(df['close']>df['prev_close']).astype(int)
    score+=(df['close']>=df['bbi']).astype(int)
    true_yin=(df['close']<df['prev_close'])&(df['vol']>df['prev_vol']*1.1)
    score+=(~true_yin).astype(int)
    score+=((df['ma20']>0)&(df['close']>df['ma20'])).astype(int)
    j_death=(df['prev_j']>df['prev_d'])&(df['j']<df['d'])
    score+=(~j_death).astype(int)
    df['score']=score

    df['b1']=df['trend_up']&(df['qs']>df['dage']*0.99)&(df['j']<=10)&(df['score']>=4)
    df['b2']=(df['j_min3']<15)&(df['vol']>df['vol_ma4']*1.25)&(df['yest_chg']<5.5)&(df['today_chg']>3.8)&(df['j']<80)&df['trend_up']&(df['score']>=3)
    vr=df['vol']/df['prev_vol']; tb=(df['today_chg']>0)|((df['today_chg']>-3)&((vr<0.6)|(vr>0.9)))
    df['b3']=(df['j_min4']<15)&(df['prev_vol']>df['prev2_vol'])&(df['yest_chg']>3)&(df['today_chg']<4)&df['trend_up']&tb&(df['score']>=3)
    df['death_cross']=(df['qs']<df['dage'])&(df['qs'].shift(1)>=df['dage'].shift(1))

    last=df.iloc[-1]; prev_qs=df.iloc[-2]['qs']; prev_dage=df.iloc[-2]['dage']

    result = {
        'code':code, 'name':name,
        'close':last['close'], 'qs':last['qs'], 'dage':last['dage'],
        'j':last['j'], 'score':int(last['score']), 'trend_up':bool(last['trend_up']),
        'b1':bool(last['b1']), 'b2':bool(last['b2']), 'b3':bool(last['b3']),
        'death_cross':bool(last['death_cross']),
        'today_chg':last['today_chg'], 'atr':last['atr'],
    }

    # 检查持仓卖出
    if code in positions:
        pos = positions[code]
        pos['hold_days'] = pos.get('hold_days', 0) + 1
        cost = pos['cost_price']
        close = last['close']
        pnl = (close/cost-1)*100

        sell_reason = None
        if last['death_cross']: sell_reason = '死叉清仓'
        elif close/cost < STOP_LOSS_MAX: sell_reason = '亏损超10%'
        elif close < last['dage'] and prev_qs >= prev_dage: sell_reason = '破大哥线'
        elif close/cost >= TAKE_PROFIT: sell_reason = '止盈'
        elif pos['hold_days']>=5 and last['score']<=1 and pnl<0: sell_reason = '评分过低'
        elif pos['hold_days']>=3 and not last['trend_up']: sell_reason = '趋势转弱'

        if sell_reason:
            result['signal'] = 'SELL'
            result['sell_reason'] = sell_reason
            result['pnl_pct'] = pnl
            result['hold_days'] = pos['hold_days']
            return result
        result['signal'] = 'HOLD'
        result['hold_days'] = pos['hold_days']
        result['pnl_pct'] = pnl
        return result

    # 检查买入
    if last['trend_up'] and last['score'] >= 3:
        sig = None
        if last['b1']: sig = 'B1'
        elif last['b2']: sig = 'B2'
        elif last['b3']: sig = 'B3'
        if sig:
            result['signal'] = 'BUY'
            result['buy_signal'] = sig
            result['atr_stop'] = last['close'] - ATR_MULTIPLIER * last['atr']
            return result

    result['signal'] = 'NONE'
    return result

def load_portfolio():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"capital": INITIAL_CAPITAL, "positions": {}, "history": []}

def save_portfolio(p):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(p, f, indent=2, ensure_ascii=False)

def main():
    pf = load_portfolio()
    capital = pf['capital']
    positions = pf['positions']
    today = datetime.now().strftime('%Y-%m-%d')

    results = []
    for code, name, asset_type in WATCHLIST:
        r = analyze_stock(code, name, positions, asset_type)
        if r: results.append(r)

    buys = [r for r in results if r['signal']=='BUY']
    sells = [r for r in results if r['signal']=='SELL']
    holds = [r for r in results if r['signal']=='HOLD']
    nones = [r for r in results if r['signal']=='NONE']

    # 执行卖出
    for r in sells:
        code = r['code']
        pos = positions[code]
        capital += pos['qty'] * r['close'] * 0.999
        pf['history'].append({'date':today,'type':'SELL','code':code,'name':r['name'],
            'price':r['close'],'reason':r['sell_reason'],'pnl_pct':round(r['pnl_pct'],2),'hold':r['hold_days']})
        del positions[code]

    # 执行买入
    for r in buys:
        code = r['code']
        cost = LOTS_PER_TRADE * r['close'] * 1.0003
        if cost <= capital:
            positions[code] = {'name':r['name'],'cost_price':r['close'],'qty':LOTS_PER_TRADE,
                'buy_date':today,'buy_signal':r['buy_signal'],'hold_days':0}
            capital -= cost
            pf['history'].append({'date':today,'type':'BUY','code':code,'name':r['name'],
                'price':r['close'],'signal':r['buy_signal'],'score':r['score']})

    pf['capital'] = round(capital, 2)
    save_portfolio(pf)

    # 生成报告
    lines = [f"🐉 强生战法V3 · {today}"]

    if sells:
        lines.append(f"\n🔴 卖出({len(sells)}只):")
        for r in sells:
            e = "✅" if r['pnl_pct']>0 else "❌"
            lines.append(f"{e} {r['name']}({r['code']}) {r['sell_reason']} {r['pnl_pct']:+.1f}% 持{r['hold_days']}天")

    if buys:
        lines.append(f"\n🟢 买入({len(buys)}只):")
        for r in buys:
            lines.append(f"📈 {r['name']}({r['code']}) [{r['buy_signal']}] ¥{r['close']:.2f} 评分{r['score']} 止损¥{r['atr_stop']:.0f}")

    if holds:
        lines.append(f"\n📦 持仓({len(holds)}只):")
        for r in holds:
            e = "🟢" if r['pnl_pct']>0 else "🔴"
            lines.append(f"{e} {r['name']}({r['code']}) {r['pnl_pct']:+.1f}% 持{r['hold_days']}天")

    if not buys and not sells:
        lines.append("\n⚪ 今日无新信号")

    lines.append(f"\n💰 可用资金: ¥{capital:,.0f}")

    report = '\n'.join(lines)
    print(report)

    with open('/root/wondertrader/run_stk_sim/daily_report.txt', 'w') as f:
        f.write(report)

if __name__ == '__main__':
    main()
