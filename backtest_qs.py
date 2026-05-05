#!/usr/bin/env python3
"""
强生战法回测系统
- 随机抽100只A股
- 回测最近一年
- 统计胜率、盈亏比、收益率等
"""

import akshare as ak
import pandas as pd
import numpy as np
import random
import json
import time
import sys
from datetime import datetime, timedelta

# ============== 指标计算 ==============

def calc_zge_line(close):
    """强生白线 = EMA(EMA(C,10),10)"""
    ema1 = close.ewm(span=10, adjust=False).mean()
    return ema1.ewm(span=10, adjust=False).mean()

def calc_dage_line(close):
    """大哥线 = (MA14+MA28+MA57+MA114)/4"""
    ma14 = close.rolling(14).mean()
    ma28 = close.rolling(28).mean()
    ma57 = close.rolling(57).mean()
    ma114 = close.rolling(114).mean()
    return (ma14 + ma28 + ma57 + ma114) / 4

def calc_bbi(close):
    """BBI = (MA3+MA6+MA12+MA24)/4"""
    return (close.rolling(3).mean() + close.rolling(6).mean() + 
            close.rolling(12).mean() + close.rolling(24).mean()) / 4

def calc_kdj(high, low, close, n=9, m1=3, m2=3):
    """KDJ(9,3,3)"""
    lowest_low = low.rolling(n).min()
    highest_high = high.rolling(n).max()
    rsv = (close - lowest_low) / (highest_high - lowest_low) * 100
    rsv = rsv.fillna(50)
    
    k = pd.Series(50.0, index=close.index)
    d = pd.Series(50.0, index=close.index)
    
    for i in range(1, len(close)):
        k.iloc[i] = (2/3) * k.iloc[i-1] + (1/3) * rsv.iloc[i]
        d.iloc[i] = (2/3) * d.iloc[i-1] + (1/3) * k.iloc[i]
    
    j = 3 * k - 2 * d
    return k, d, j

def calc_ma(close, n):
    return close.rolling(n).mean()

# ============== 信号判断 ==============

def calc_score(row, prev_row):
    """评分系统 (0-5)"""
    score = 0
    # 收盘上升
    if row['close'] > prev_row['close']:
        score += 1
    # 不破BBI
    if row['close'] >= row['bbi']:
        score += 1
    # 无巨量真阴
    is_true_yin = (row['close'] < prev_row['close']) and (row['vol'] > prev_row['vol'] * 1.1)
    if not is_true_yin:
        score += 1
    # 趋势向上
    ma20 = row.get('ma20', 0)
    if ma20 > 0 and row['close'] > ma20:
        score += 1
    # J没死叉
    if not (prev_row['j'] > prev_row['d'] and row['j'] < row['d']):
        score += 1
    return score

def detect_signals(df):
    """检测所有信号"""
    signals = []
    
    for i in range(max(114, 2), len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        
        score = calc_score(row, prev)
        
        # B1: 强生白线 > 大哥线*0.99 且 J <= 13
        b1 = row['zge'] > row['dage'] * 0.99 and row['j'] <= 13
        
        # B2: 近3日内J<15 + 放量1.25 + 昨涨<5.5 + 今涨>3.8 + J<80 + 强生>大哥
        j_below_15_recent = any(df.iloc[max(0,i-2):i+1]['j'] < 15)
        yesterday_change = (prev['close'] / df.iloc[i-2]['close'] - 1) * 100 if i >= 2 else 0
        today_change = (row['close'] / prev['close'] - 1) * 100
        vol_ma4 = df.iloc[max(0,i-5):i-1]['vol'].mean() if i >= 5 else row['vol']
        b2 = (j_below_15_recent and row['vol'] > vol_ma4 * 1.25 and 
              yesterday_change < 5.5 and today_change > 3.8 and 
              row['j'] < 80 and row['zge'] > row['dage'])
        
        # B3: 近4日内J<15 + 昨日放量 + 昨涨>3 + 今涨<4 + 强生>大哥 + 真突破
        j_below_15_4d = any(df.iloc[max(0,i-3):i+1]['j'] < 15)
        yesterday_vol_up = prev['vol'] > df.iloc[i-2]['vol'] if i >= 2 else False
        vol_ratio = row['vol'] / prev['vol'] if prev['vol'] > 0 else 1
        shrink_half = vol_ratio < 0.6
        flat_vol = vol_ratio > 0.9
        true_break = today_change > 0 or (today_change > -3 and (shrink_half or flat_vol))
        b3 = (j_below_15_4d and yesterday_vol_up and yesterday_change > 3 and 
              today_change < 4 and row['zge'] > row['dage'] and true_break)
        
        # 突破前高
        if i >= 20:
            high_20 = df.iloc[i-20:i]['high'].max()
            breakthrough = (row['close'] > df.iloc[i-1]['high_20_prev'] if 'high_20_prev' in df.columns else False)
            bt_count = 0
            for j in range(max(0,i-7), i+1):
                if j >= 20:
                    h20 = df.iloc[j-20:j]['high'].max()
                    if df.iloc[j]['close'] > h20:
                        bt_count += 1
            breakthrough = (row['close'] > high_20 and row['vol'] > prev['vol'] * 0.8 and 
                          today_change > 4 and bt_count <= 1)
        else:
            breakthrough = False
        
        # 死叉清仓
        death_cross = row['zge'] < row['dage'] and prev['zge'] > prev['dage']
        
        # 震仓
        shock = (row['zge'] > row['dage'] and today_change < -1 and today_change > -8 and 
                row['vol'] < prev['vol'] * 0.7)
        
        signals.append({
            'idx': i,
            'date': row['date'],
            'close': row['close'],
            'score': score,
            'b1': b1,
            'b2': b2,
            'b3': b3,
            'breakthrough': breakthrough,
            'death_cross': death_cross,
            'shock': shock,
            'j': row['j'],
            'zge': row['zge'],
            'dage': row['dage'],
            'today_change': today_change,
        })
    
    return signals

# ============== 回测引擎 ==============

def backtest_stock(code, name=""):
    """回测单只股票"""
    try:
        # 获取最近1年+前置数据（需要至少114日均线）
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=600)).strftime("%Y%m%d")
        
        df = ak.stock_zh_a_hist(symbol=code, period="daily", 
                                start_date=start_date, end_date=end_date, adjust="qfq")
        
        if df is None or len(df) < 130:
            return None
        
        df = df.reset_index(drop=True)
        # 自适应列名（akshare可能返回11或12列）
        col_map = {
            '日期': 'date', '股票代码': 'code',
            '开盘': 'open', '收盘': 'close', '最高': 'high', '最低': 'low',
            '成交量': 'vol', '成交额': 'amount', '振幅': 'amplitude',
            '涨跌幅': 'pct_change', '涨跌额': 'change_amount', '换手率': 'turnover'
        }
        df = df.rename(columns=col_map)
        
        # 计算指标
        df['zge'] = calc_zge_line(df['close'])
        df['dage'] = calc_dage_line(df['close'])
        df['bbi'] = calc_bbi(df['close'])
        df['ma20'] = calc_ma(df['close'], 20)
        k, d, j = calc_kdj(df['high'], df['low'], df['close'])
        df['k'] = k
        df['d'] = d
        df['j'] = j
        
        # 预计算近20日高点
        df['high_20'] = df['high'].rolling(20).max()
        
        # 检测信号
        signals = detect_signals(df)
        
        if not signals:
            return None
        
        # 模拟交易
        trades = []
        position = None  # None=空仓, dict=持仓
        cost_price = 0
        stop_loss = 0
        
        for sig in signals:
            if position is None:
                # 寻找买入信号: 评分>=3 且 有B1/B2/B3信号
                if sig['score'] >= 3 and (sig['b1'] or sig['b2'] or sig['b3']):
                    buy_price = sig['close']
                    cost_price = buy_price
                    # 止损价 = B1信号出现后的最低价 * 0.98 (简化为买入价*0.95)
                    stop_loss = buy_price * 0.95
                    position = {
                        'buy_date': str(sig['date']),
                        'buy_price': buy_price,
                        'score': sig['score'],
                        'signal_type': 'B1' if sig['b1'] else ('B2' if sig['b2'] else 'B3'),
                    }
            else:
                # 卖出条件
                sell_reason = None
                sell_price = sig['close']
                
                # 1. 清仓死叉
                if sig['death_cross']:
                    sell_reason = '死叉清仓'
                # 2. 破止损
                elif sig['close'] < stop_loss:
                    sell_reason = '破止损'
                # 3. 止盈: 涨幅>20%
                elif (sig['close'] / cost_price - 1) > 0.20:
                    sell_reason = '止盈20%'
                # 4. 评分降到1分以下
                elif sig['score'] <= 1:
                    sell_reason = '评分过低'
                
                if sell_reason:
                    pnl = (sell_price / position['buy_price'] - 1) * 100
                    holding_days = sig['idx'] - [s for s in signals if str(s['date']) == position['buy_date']][0]['idx'] if any(str(s['date']) == position['buy_date'] for s in signals) else 0
                    
                    trades.append({
                        'buy_date': position['buy_date'],
                        'buy_price': round(position['buy_price'], 2),
                        'sell_date': str(sig['date']),
                        'sell_price': round(sell_price, 2),
                        'pnl_pct': round(pnl, 2),
                        'holding_days': max(holding_days, 1),
                        'buy_signal': position['signal_type'],
                        'sell_reason': sell_reason,
                        'buy_score': position['score'],
                    })
                    position = None
                    cost_price = 0
                    stop_loss = 0
        
        # 如果还有持仓，以最后价格平仓
        if position is not None:
            last = signals[-1]
            pnl = (last['close'] / position['buy_price'] - 1) * 100
            trades.append({
                'buy_date': position['buy_date'],
                'buy_price': round(position['buy_price'], 2),
                'sell_date': str(last['date']),
                'sell_price': round(last['close'], 2),
                'pnl_pct': round(pnl, 2),
                'holding_days': 1,
                'buy_signal': position['signal_type'],
                'sell_reason': '期末平仓',
                'buy_score': position['score'],
            })
        
        return {
            'code': code,
            'name': name,
            'trades': trades,
            'data_points': len(df),
        }
    
    except Exception as e:
        return {'code': code, 'name': name, 'error': str(e), 'trades': []}

# ============== 主程序 ==============

def main():
    print("=" * 60)
    print("强生战法回测系统 - 随机100只A股 最近一年")
    print("=" * 60)
    
    # 获取A股列表
    print("\n[1/4] 获取A股列表...")
    try:
        stock_list = ak.stock_zh_a_spot_em()
        # 过滤: 排除ST、退市、新股（上市不足1年）
        stock_list = stock_list[~stock_list['名称'].str.contains('ST|退|N ', na=False)]
        stock_list = stock_list[stock_list['代码'].str.startswith(('00', '30', '60'))]
        all_codes = stock_list[['代码', '名称']].values.tolist()
        print(f"  可选股票数: {len(all_codes)}")
    except Exception as e:
        print(f"  获取股票列表失败: {e}")
        # 备用列表
        all_codes = [
            ['000001', '平安银行'], ['000002', '万科A'], ['000063', '中兴通讯'],
            ['000333', '美的集团'], ['000568', '泸州老窖'], ['000651', '格力电器'],
            ['000858', '五粮液'], ['002230', '科大讯飞'], ['002415', '海康威视'],
            ['002594', '比亚迪'], ['300059', '东方财富'], ['300750', '宁德时代'],
            ['600000', '浦发银行'], ['600016', '民生银行'], ['600030', '中信证券'],
            ['600036', '招商银行'], ['600048', '保利发展'], ['600050', '中国联通'],
            ['600104', '上汽集团'], ['600276', '恒瑞医药'], ['600309', '万华化学'],
            ['600519', '贵州茅台'], ['600585', '海螺水泥'], ['600690', '海尔智家'],
            ['600887', '伊利股份'], ['601012', '隆基绿能'], ['601088', '中国神华'],
            ['601318', '中国平安'], ['601398', '工商银行'], ['601857', '中国石油'],
            ['601888', '中国中免'], ['603259', '药明康德'],
        ]
    
    # 随机抽取100只
    print("\n[2/4] 随机抽取100只股票...")
    sample_size = min(100, len(all_codes))
    random.seed(42)  # 可重复
    sampled = random.sample(all_codes, sample_size)
    print(f"  已抽取 {sample_size} 只股票")
    
    # 逐只回测
    print(f"\n[3/4] 开始回测 (共{sample_size}只)...")
    results = []
    all_trades = []
    errors = []
    
    for idx, (code, name) in enumerate(sampled):
        sys.stdout.write(f"\r  进度: {idx+1}/{sample_size} [{name}({code})]          ")
        sys.stdout.flush()
        
        result = backtest_stock(code, name)
        if result is None:
            errors.append(f"{code} {name}: 数据不足")
            continue
        if 'error' in result:
            errors.append(f"{code} {name}: {result['error']}")
            continue
        
        results.append(result)
        for t in result['trades']:
            t['code'] = code
            t['name'] = name
            all_trades.append(t)
        
        time.sleep(0.3)  # 避免请求过快
    
    print(f"\n\n[4/4] 统计结果...")
    
    if not all_trades:
        print("\n❌ 没有产生任何交易信号，可能是数据问题")
        if errors:
            print(f"\n错误信息 (前5条):")
            for e in errors[:5]:
                print(f"  - {e}")
        return
    
    trades_df = pd.DataFrame(all_trades)
    
    # ===== 统计 =====
    total_trades = len(trades_df)
    win_trades = trades_df[trades_df['pnl_pct'] > 0]
    lose_trades = trades_df[trades_df['pnl_pct'] <= 0]
    
    win_rate = len(win_trades) / total_trades * 100
    avg_pnl = trades_df['pnl_pct'].mean()
    median_pnl = trades_df['pnl_pct'].median()
    avg_win = win_trades['pnl_pct'].mean() if len(win_trades) > 0 else 0
    avg_lose = lose_trades['pnl_pct'].mean() if len(lose_trades) > 0 else 0
    max_win = trades_df['pnl_pct'].max()
    max_lose = trades_df['pnl_pct'].min()
    profit_factor = abs(win_trades['pnl_pct'].sum() / lose_trades['pnl_pct'].sum()) if len(lose_trades) > 0 and lose_trades['pnl_pct'].sum() != 0 else float('inf')
    avg_holding = trades_df['holding_days'].mean()
    
    # 按买入信号类型统计
    signal_stats = trades_df.groupby('buy_signal').agg(
        count=('pnl_pct', 'count'),
        win_rate=('pnl_pct', lambda x: (x > 0).mean() * 100),
        avg_pnl=('pnl_pct', 'mean'),
    ).round(2)
    
    # 按卖出原因统计
    sell_stats = trades_df.groupby('sell_reason').agg(
        count=('pnl_pct', 'count'),
        avg_pnl=('pnl_pct', 'mean'),
    ).round(2)
    
    # 按评分统计
    score_stats = trades_df.groupby('buy_score').agg(
        count=('pnl_pct', 'count'),
        win_rate=('pnl_pct', lambda x: (x > 0).mean() * 100),
        avg_pnl=('pnl_pct', 'mean'),
    ).round(2)
    
    # 每只股票的统计
    stock_stats = trades_df.groupby(['code', 'name']).agg(
        trades=('pnl_pct', 'count'),
        total_pnl=('pnl_pct', 'sum'),
        avg_pnl=('pnl_pct', 'mean'),
        win_rate=('pnl_pct', lambda x: (x > 0).mean() * 100),
    ).round(2).sort_values('total_pnl', ascending=False)
    
    # ===== 打印结果 =====
    print("\n" + "=" * 60)
    print("📊 强生战法回测结果")
    print("=" * 60)
    
    print(f"\n📈 总体统计:")
    print(f"  回测股票数:   {len(results)} 只")
    print(f"  总交易次数:   {total_trades} 笔")
    print(f"  有交易股票:   {trades_df['code'].nunique()} 只")
    print(f"  平均持仓天数: {avg_holding:.1f} 天")
    
    print(f"\n💰 收益统计:")
    print(f"  胜率:         {win_rate:.1f}%")
    print(f"  平均收益:     {avg_pnl:.2f}%")
    print(f"  收益中位数:   {median_pnl:.2f}%")
    print(f"  平均盈利:     {avg_win:.2f}%")
    print(f"  平均亏损:     {avg_lose:.2f}%")
    print(f"  最大盈利:     {max_win:.2f}%")
    print(f"  最大亏损:     {max_lose:.2f}%")
    print(f"  盈亏比:       {profit_factor:.2f}")
    
    print(f"\n🔔 按买入信号统计:")
    print(signal_stats.to_string())
    
    print(f"\n📤 按卖出原因统计:")
    print(sell_stats.to_string())
    
    print(f"\n⭐ 按买入评分统计:")
    print(score_stats.to_string())
    
    print(f"\n🏆 最佳股票 TOP 10:")
    top10 = stock_stats.head(10)
    for idx, row in top10.iterrows():
        print(f"  {idx[1]}({idx[0]}): 交易{row['trades']:.0f}次, 胜率{row['win_rate']:.0f}%, 总收益{row['total_pnl']:.1f}%")
    
    print(f"\n💀 最差股票 BOTTOM 5:")
    bottom5 = stock_stats.tail(5)
    for idx, row in bottom5.iterrows():
        print(f"  {idx[1]}({idx[0]}): 交易{row['trades']:.0f}次, 胜率{row['win_rate']:.0f}%, 总收益{row['total_pnl']:.1f}%")
    
    # 保存详细交易记录
    trades_df.to_csv('/root/wondertrader/backtest_trades.csv', index=False, encoding='utf-8-sig')
    print(f"\n📄 详细交易记录已保存: /root/wondertrader/backtest_trades.csv")
    
    # 保存统计摘要
    summary = {
        'total_stocks': len(results),
        'total_trades': total_trades,
        'win_rate': round(win_rate, 1),
        'avg_pnl': round(avg_pnl, 2),
        'median_pnl': round(median_pnl, 2),
        'avg_win': round(avg_win, 2),
        'avg_lose': round(avg_lose, 2),
        'max_win': round(max_win, 2),
        'max_lose': round(max_lose, 2),
        'profit_factor': round(profit_factor, 2),
        'avg_holding_days': round(avg_holding, 1),
    }
    with open('/root/wondertrader/backtest_summary.json', 'w') as f:
        json.dump(summary, f, indent=2)
    
    if errors:
        print(f"\n⚠️ 跳过的股票 ({len(errors)}只):")
        for e in errors[:5]:
            print(f"  - {e}")
        if len(errors) > 5:
            print(f"  ... 还有 {len(errors)-5} 只")
    
    print("\n" + "=" * 60)
    print("✅ 回测完成!")
    print("=" * 60)

if __name__ == '__main__':
    main()
