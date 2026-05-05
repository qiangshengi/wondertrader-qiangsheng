#!/usr/bin/env python3
"""
强生战法V3 - 全自动模拟盘
自动执行买卖，自动计算盈亏，每日收盘后运行
"""
import akshare as ak
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timedelta

# ===== 配置 =====
INITIAL_CAPITAL = 100000  # 10万起步
MAX_POSITIONS = 5         # 最多同时持5只
POSITION_SIZE = 0.15      # 每只最多用15%资金
FEE_RATE = 0.0003         # 万三手续费

WATCHLIST = [
    ("002236", "大华股份", "stock"),
    ("000636", "风华高科", "stock"),
    ("159870", "有色金属ETF", "etf"),
]

STATE_FILE = "/root/wondertrader/run_stk_sim/sim_portfolio.json"
LOG_FILE = "/root/wondertrader/run_stk_sim/sim_trade_log.csv"

# ===== 指标 =====
def calc_qs(c): return c.ewm(span=10,adjust=False).mean().ewm(span=10,adjust=False).mean()
def calc_dage(c): return (c.rolling(14).mean()+c.rolling(28).mean()+c.rolling(57).mean()+c.rolling(114).mean())/4
def calc_bbi(c): return (c.rolling(3).mean()+c.rolling(6).mean()+c.rolling(12).mean()+c.rolling(24).mean())/4
def calc_kdj(h,l,c,n=9):
    ll=l.rolling(n).min(); hh=h.rolling(n).max()
    rsv=((c-ll)/(hh-ll)*100).fillna(50)
    k=rsv.ewm(com=2,adjust=False).mean(); d=k.ewm(com=2,adjust=False).mean()
    return k, d, 3*k-2*d
def calc_atr(h,l,c,p=14):
    tr=pd.concat([h-l,abs(h-c.shift(1)),abs(l-c.shift(1))],axis=1).max(axis=1)
    return tr.rolling(p).mean()

# ===== 信号检测 =====
def get_signals(code, name, asset_type="stock"):
    """获取当前信号和指标状态"""
    try:
        if asset_type == "etf":
            raw = ak.fund_etf_hist_em(symbol=code, period='daily',
                start_date=(datetime.now()-timedelta(days=300)).strftime('%Y%m%d'),
                end_date=datetime.now().strftime('%Y%m%d'), adjust='qfq')
        else:
            raw = ak.stock_zh_a_hist(symbol=code, period='daily',
                start_date=(datetime.now()-timedelta(days=300)).strftime('%Y%m%d'),
                end_date=datetime.now().strftime('%Y%m%d'), adjust='qfq')
        if raw is None or len(raw) < 120: return None
    except: return None

    cm={'日期':'date','开盘':'open','收盘':'close','最高':'high','最低':'low','成交量':'vol','成交额':'amount'}
    df=raw.rename(columns=cm)
    df['date']=pd.to_datetime(df['date']); df=df.sort_values('date').reset_index(drop=True)

    df['qs']=calc_qs(df['close']); df['dage']=calc_dage(df['close']); df['bbi']=calc_bbi(df['close'])
    df['ma20']=df['close'].rolling(20).mean(); df['atr']=calc_atr(df['high'],df['low'],df['close'])
    df['k'],df['d'],df['j']=calc_kdj(df['high'],df['low'],df['close'])
    df['pc']=df['close'].shift(1); df['pv']=df['vol'].shift(1)
    df['pj']=df['j'].shift(1); df['pd']=df['d'].shift(1)
    df['p2c']=df['close'].shift(2); df['p2v']=df['vol'].shift(2)
    df['vma4']=df['vol'].shift(1).rolling(4).mean()
    df['chg']=(df['close']/df['pc']-1)*100; df['ychg']=(df['pc']/df['p2c']-1)*100
    df['j3']=df['j'].rolling(3).min(); df['j4']=df['j'].rolling(4).min()
    df['up']=df['qs']>df['dage']

    s=pd.Series(0,index=df.index)
    s+=(df['close']>df['pc']).astype(int); s+=(df['close']>=df['bbi']).astype(int)
    ty=(df['close']<df['pc'])&(df['vol']>df['pv']*1.1); s+=(~ty).astype(int)
    s+=((df['ma20']>0)&(df['close']>df['ma20'])).astype(int)
    jd=(df['pj']>df['pd'])&(df['j']<df['pd']); s+=(~jd).astype(int)
    df['score']=s

    df['b1']=df['up']&(df['qs']>df['dage']*0.99)&(df['j']<=10)&(df['score']>=4)
    df['b2']=(df['j3']<15)&(df['vol']>df['vma4']*1.25)&(df['ychg']<5.5)&(df['chg']>3.8)&(df['j']<80)&df['up']&(df['score']>=3)
    vr=df['vol']/df['pv']; tb=(df['chg']>0)|((df['chg']>-3)&((vr<0.6)|(vr>0.9)))
    df['b3']=(df['j4']<15)&(df['pv']>df['p2v'])&(df['ychg']>3)&(df['chg']<4)&df['up']&tb&(df['score']>=3)
    df['death']=(df['qs']<df['dage'])&(df['qs'].shift(1)>=df['dage'].shift(1))

    L=df.iloc[-1]; P=df.iloc[-2]
    return {
        'code':code,'name':name,'close':L['close'],'open':L['open'],
        'qs':L['qs'],'dage':L['dage'],'j':L['j'],'score':int(L['score']),
        'up':bool(L['up']),'b1':bool(L['b1']),'b2':bool(L['b2']),'b3':bool(L['b3']),
        'death':bool(L['death']),'chg':L['chg'],'atr':L['atr'],
        'prev_qs':P['qs'],'prev_dage':P['dage'],
    }

# ===== 持仓管理 =====
def load_pf():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f: return json.load(f)
    return {"cash":INITIAL_CAPITAL,"positions":{},"history":[],"total_trades":0,
            "total_wins":0,"total_pnl":0.0,"start_date":datetime.now().strftime('%Y-%m-%d')}

def save_pf(pf):
    with open(STATE_FILE,'w') as f: json.dump(pf,f,indent=2,ensure_ascii=False)

def append_log(row):
    header = not os.path.exists(LOG_FILE)
    with open(LOG_FILE,'a') as f:
        if header: f.write("date,action,code,name,price,qty,reason,pnl_pct,pnl_money,hold_days,cash\n")
        f.write(row+'\n')

# ===== 主逻辑 =====
def run_simulation():
    pf = load_pf()
    cash = pf['cash']
    pos = pf['positions']
    today = datetime.now().strftime('%Y-%m-%d')
    trades = []

    # 1. 先检查持仓的卖出信号
    sell_list = []
    for code in list(pos.keys()):
        name = pos[code]['name']
        asset_type = pos[code].get('asset_type', 'stock')
        sig = get_signals(code, name, asset_type)
        if sig is None: continue

        cost = pos[code]['cost_price']
        hold = pos[code].get('hold_days', 0) + 1
        pos[code]['hold_days'] = hold
        close = sig['close']
        pnl_pct = (close/cost-1)*100

        reason = None
        if sig['death']: reason = '死叉清仓'
        elif close/cost < 0.90: reason = '亏损超10%'
        elif close < sig['dage'] and sig['prev_qs'] >= sig['prev_dage']: reason = '破大哥线'
        elif close/cost >= 1.20: reason = '止盈20%'
        elif hold >= 5 and sig['score'] <= 1 and pnl_pct < 0: reason = '评分过低'
        elif hold >= 3 and not sig['up']: reason = '趋势转弱'

        if reason:
            qty = pos[code]['qty']
            proceeds = qty * close * (1 - FEE_RATE)
            pnl_money = proceeds - qty * cost
            cash += proceeds
            sell_list.append(code)

            pf['total_trades'] += 1
            if pnl_money > 0: pf['total_wins'] += 1
            pf['total_pnl'] += pnl_money

            trade = {
                'date':today,'action':'SELL','code':code,'name':name,
                'price':close,'qty':qty,'reason':reason,
                'pnl_pct':round(pnl_pct,2),'pnl_money':round(pnl_money,2),'hold':hold,
            }
            trades.append(trade)
            pf['history'].append(trade)
            append_log(f"{today},SELL,{code},{name},{close:.2f},{qty},{reason},{pnl_pct:.2f},{pnl_money:.2f},{hold},{cash:.2f}")

    for code in sell_list:
        del pos[code]

    # 2. 扫描所有股票的买入信号
    buy_candidates = []
    for code, name, asset_type in WATCHLIST:
        if code in pos: continue  # 已持仓跳过
        sig = get_signals(code, name, asset_type)
        if sig is None: continue

        if sig['up'] and sig['score'] >= 3:
            buy_sig = None
            if sig['b1']: buy_sig = 'B1'
            elif sig['b2']: buy_sig = 'B2'
            elif sig['b3']: buy_sig = 'B3'
            if buy_sig:
                buy_candidates.append({
                    'code':code,'name':name,'price':sig['close'],
                    'signal':buy_sig,'score':sig['score'],
                    'atr_stop':sig['close'] - 2.5 * sig['atr'],
                    'j':sig['j'],'qs':sig['qs'],'dage':sig['dage'],
                    'asset_type':asset_type,
                })

    # 3. 按评分排序，优先买评分高的
    buy_candidates.sort(key=lambda x: -x['score'])

    available_slots = MAX_POSITIONS - len(pos)
    for bc in buy_candidates[:available_slots]:
        budget = cash * POSITION_SIZE
        qty = int(budget / bc['price'] / 100) * 100  # 取整到100股
        if qty < 100: continue

        cost = qty * bc['price'] * (1 + FEE_RATE)
        if cost > cash: continue

        cash -= cost
        pos[bc['code']] = {
            'name':bc['name'],'cost_price':bc['price'],'qty':qty,
            'buy_date':today,'signal':bc['signal'],'hold_days':0,
            'asset_type':bc['asset_type'],
        }

        trade = {
            'date':today,'action':'BUY','code':bc['code'],'name':bc['name'],
            'price':bc['price'],'qty':qty,'signal':bc['signal'],
            'score':bc['score'],
        }
        trades.append(trade)
        pf['history'].append(trade)
        append_log(f"{today},BUY,{bc['code']},{bc['name']},{bc['price']:.2f},{qty},{bc['signal']},,,,{cash:.2f}")

    pf['cash'] = round(cash, 2)
    save_pf(pf)

    # 4. 生成报告
    report = generate_report(pf, trades, pos)
    print(report)

    with open('/root/wondertrader/run_stk_sim/sim_report.txt','w') as f:
        f.write(report)

    return report

def generate_report(pf, trades, pos):
    today = datetime.now().strftime('%Y-%m-%d')
    cash = pf['cash']
    total_val = cash

    lines = [f"🐉 强生战法V3 模拟盘 · {today}"]

    # 卖出
    sells = [t for t in trades if t['action']=='SELL']
    if sells:
        lines.append(f"\n🔴 卖出({len(sells)}只):")
        for t in sells:
            e = "✅" if t['pnl_money']>0 else "❌"
            lines.append(f"{e} {t['name']}({t['code']}) {t['reason']} {t['pnl_pct']:+.1f}% ¥{t['pnl_money']:+,.0f} 持{t['hold']}天")

    # 买入
    buys = [t for t in trades if t['action']=='BUY']
    if buys:
        lines.append(f"\n🟢 买入({len(buys)}只):")
        for t in buys:
            lines.append(f"📈 {t['name']}({t['code']}) [{t['signal']}] ¥{t['price']:.2f}×{t['qty']}股 评分{t['score']}")

    # 持仓
    if pos:
        lines.append(f"\n📦 持仓({len(pos)}只):")
        for code, p in pos.items():
            # 这里没有实时价格，显示成本
            lines.append(f"  {p['name']}({code}) {p['qty']}股 成本¥{p['cost_price']:.2f} 持{p['hold_days']}天")
            total_val += p['qty'] * p['cost_price']  # 用成本价估算

    if not sells and not buys:
        lines.append("\n⚪ 今日无新交易")

    # 账户概览
    total_pnl = total_val - INITIAL_CAPITAL
    total_ret = (total_val / INITIAL_CAPITAL - 1) * 100
    win_rate = pf['total_wins'] / pf['total_trades'] * 100 if pf['total_trades'] > 0 else 0

    lines.append(f"\n💰 账户:")
    lines.append(f"  可用: ¥{cash:,.0f} | 持仓: {len(pos)}只")
    lines.append(f"  总资产: ¥{total_val:,.0f} | 收益: {total_ret:+.1f}%")
    lines.append(f"  累计交易: {pf['total_trades']}笔 | 胜率: {win_rate:.0f}%")

    return '\n'.join(lines)

if __name__ == '__main__':
    run_simulation()
