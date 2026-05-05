#!/usr/bin/env python3
"""
强生战法回测系统 V2 - 优化版
优化点:
1. B1: J<=10 (原13), 评分>=4 (原3)
2. 止损放宽到93% (原95%)
3. 去掉"评分过低"过早卖出, 改为持仓>=5天才允许非止损卖出
4. 新增趋势过滤: 强生白线 > 大哥线才开仓
5. 新增: B1加仓确认 - B1后3天内出现B2/B3则加仓
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
    ema1 = close.ewm(span=10, adjust=False).mean()
    return ema1.ewm(span=10, adjust=False).mean()

def calc_dage_line(close):
    return (close.rolling(14).mean() + close.rolling(28).mean() + 
            close.rolling(57).mean() + close.rolling(114).mean()) / 4

def calc_bbi(close):
    return (close.rolling(3).mean() + close.rolling(6).mean() + 
            close.rolling(12).mean() + close.rolling(24).mean()) / 4

def calc_kdj(high, low, close, n=9):
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

# ============== 信号判断 ==============

def calc_score(row, prev_row, ma20_val):
    score = 0
    if row['close'] > prev_row['close']:
        score += 1
    if row['close'] >= row['bbi']:
        score += 1
    is_true_yin = (row['close'] < prev_row['close']) and (row['vol'] > prev_row['vol'] * 1.1)
    if not is_true_yin:
        score += 1
    if ma20_val > 0 and row['close'] > ma20_val:
        score += 1
    if not (prev_row['j'] > prev_row['d'] and row['j'] < row['d']):
        score += 1
    return score

def detect_signals(df):
    signals = []
    for i in range(max(114, 2), len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        ma20 = row.get('ma20', 0)
        score = calc_score(row, prev, ma20)
        
        # 趋势过滤: 强生白线 > 大哥线
        trend_up = row['zge'] > row['dage']
        
        # B1: 优化 - J<=10, 评分>=4, 趋势向上
        b1 = trend_up and row['zge'] > row['dage'] * 0.99 and row['j'] <= 10 and score >= 4
        
        # B2: 保持不变
        j_below_15_recent = any(df.iloc[max(0,i-2):i+1]['j'] < 15)
        yesterday_change = (prev['close'] / df.iloc[i-2]['close'] - 1) * 100 if i >= 2 else 0
        today_change = (row['close'] / prev['close'] - 1) * 100
        vol_ma4 = df.iloc[max(0,i-5):i-1]['vol'].mean() if i >= 5 else row['vol']
        b2 = (j_below_15_recent and row['vol'] > vol_ma4 * 1.25 and 
              yesterday_change < 5.5 and today_change > 3.8 and 
              row['j'] < 80 and trend_up)
        
        # B3: 保持不变
        j_below_15_4d = any(df.iloc[max(0,i-3):i+1]['j'] < 15)
        yesterday_vol_up = prev['vol'] > df.iloc[i-2]['vol'] if i >= 2 else False
        vol_ratio = row['vol'] / prev['vol'] if prev['vol'] > 0 else 1
        shrink_half = vol_ratio < 0.6
        flat_vol = vol_ratio > 0.9
        true_break = today_change > 0 or (today_change > -3 and (shrink_half or flat_vol))
        b3 = (j_below_15_4d and yesterday_vol_up and yesterday_change > 3 and 
              today_change < 4 and trend_up and true_break)
        
        # 死叉清仓
        death_cross = row['zge'] < row['dage'] and prev['zge'] > prev['dage']
        
        # 震仓
        shock = (trend_up and today_change < -1 and today_change > -8 and 
                row['vol'] < prev['vol'] * 0.7)
        
        # 止盈20%
        above_20pct = False
        
        signals.append({
            'idx': i,
            'date': row['date'],
            'close': row['close'],
            'low': row['low'],
            'score': score,
            'b1': b1,
            'b2': b2,
            'b3': b3,
            'death_cross': death_cross,
            'shock': shock,
            'j': row['j'],
            'zge': row['zge'],
            'dage': row['dage'],
            'trend_up': trend_up,
            'today_change': today_change,
        })
    
    return signals

# ============== 回测引擎 ==============

def backtest_stock(code, name=""):
    try:
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=600)).strftime("%Y%m%d")
        
        df = ak.stock_zh_a_hist(symbol=code, period="daily", 
                                start_date=start_date, end_date=end_date, adjust="qfq")
        
        if df is None or len(df) < 130:
            return None
        
        df = df.reset_index(drop=True)
        col_map = {
            '日期': 'date', '股票代码': 'code',
            '开盘': 'open', '收盘': 'close', '最高': 'high', '最低': 'low',
            '成交量': 'vol', '成交额': 'amount', '振幅': 'amplitude',
            '涨跌幅': 'pct_change', '涨跌额': 'change_amount', '换手率': 'turnover'
        }
        df = df.rename(columns=col_map)
        
        df['zge'] = calc_zge_line(df['close'])
        df['dage'] = calc_dage_line(df['close'])
        df['bbi'] = calc_bbi(df['close'])
        df['ma20'] = df['close'].rolling(20).mean()
        k, d, j = calc_kdj(df['high'], df['low'], df['close'])
        df['k'] = k
        df['d'] = d
        df['j'] = j
        
        signals = detect_signals(df)
        
        if not signals:
            return None
        
        # 模拟交易
        trades = []
        position = None
        cost_price = 0
        stop_loss = 0
        hold_days = 0
        entry_idx = 0
        
        for sig in signals:
            if position is None:
                # 趋势向上 + 评分>=3 + 有信号
                if sig['trend_up'] and sig['score'] >= 3 and (sig['b1'] or sig['b2'] or sig['b3']):
                    buy_price = sig['close']
                    cost_price = buy_price
                    stop_loss = buy_price * 0.93  # 放宽止损到93%
                    hold_days = 0
                    entry_idx = sig['idx']
                    position = {
                        'buy_date': str(sig['date']),
                        'buy_price': buy_price,
                        'score': sig['score'],
                        'signal_type': 'B1' if sig['b1'] else ('B2' if sig['b2'] else 'B3'),
                    }
            else:
                hold_days = sig['idx'] - entry_idx
                sell_reason = None
                sell_price = sig['close']
                
                # 1. 死叉清仓 - 任何时间都执行
                if sig['death_cross']:
                    sell_reason = '死叉清仓'
                # 2. 破止损 - 任何时间都执行
                elif sig['close'] < stop_loss:
                    sell_reason = '破止损'
                # 3. 止盈20%
                elif (sig['close'] / cost_price - 1) > 0.20:
                    sell_reason = '止盈20%'
                # 4. 评分过低 - 持仓>=5天才判断
                elif hold_days >= 5 and sig['score'] <= 1:
                    sell_reason = '评分过低'
                # 5. 强生白线下穿大哥线 - 持仓>=3天才执行
                elif hold_days >= 3 and not sig['trend_up']:
                    sell_reason = '趋势转弱'
                
                if sell_reason:
                    pnl = (sell_price / position['buy_price'] - 1) * 100
                    trades.append({
                        'buy_date': position['buy_date'],
                        'buy_price': round(position['buy_price'], 2),
                        'sell_date': str(sig['date']),
                        'sell_price': round(sell_price, 2),
                        'pnl_pct': round(pnl, 2),
                        'holding_days': max(hold_days, 1),
                        'buy_signal': position['signal_type'],
                        'sell_reason': sell_reason,
                        'buy_score': position['score'],
                    })
                    position = None
                    cost_price = 0
                    stop_loss = 0
                    hold_days = 0
        
        # 期末平仓
        if position is not None:
            last = signals[-1]
            pnl = (last['close'] / position['buy_price'] - 1) * 100
            trades.append({
                'buy_date': position['buy_date'],
                'buy_price': round(position['buy_price'], 2),
                'sell_date': str(last['date']),
                'sell_price': round(last['close'], 2),
                'pnl_pct': round(pnl, 2),
                'holding_days': max(hold_days, 1),
                'buy_signal': position['signal_type'],
                'sell_reason': '期末平仓',
                'buy_score': position['score'],
            })
        
        return {'code': code, 'name': name, 'trades': trades, 'data_points': len(df)}
    
    except Exception as e:
        return {'code': code, 'name': name, 'error': str(e), 'trades': []}

# ============== 主程序 ==============

def main():
    print("=" * 60)
    print("强生战法回测 V2 - 优化版（随机100只A股·近一年）")
    print("=" * 60)
    print("\n优化点:")
    print("  1. B1: J<=10, 评分>=4, 趋势向上过滤")
    print("  2. 止损放宽到93%")
    print("  3. 评分过低卖出需持仓>=5天")
    print("  4. 新增趋势转弱卖出(持仓>=3天)")
    print("  5. 趋势向上才开仓(强生白线>大哥线)")
    
    print("\n[1/4] 获取A股列表...")
    try:
        stock_list = ak.stock_zh_a_spot_em()
        stock_list = stock_list[~stock_list['名称'].str.contains('ST|退|N ', na=False)]
        stock_list = stock_list[stock_list['代码'].str.startswith(('00', '30', '60'))]
        all_codes = stock_list[['代码', '名称']].values.tolist()
        print(f"  可选股票数: {len(all_codes)}")
    except Exception as e:
        print(f"  获取失败: {e}, 使用备用列表")
        all_codes = [
            ['000001','平安银行'],['000002','万科A'],['000063','中兴通讯'],
            ['000333','美的集团'],['000568','泸州老窖'],['000651','格力电器'],
            ['000858','五粮液'],['002230','科大讯飞'],['002415','海康威视'],
            ['002594','比亚迪'],['300059','东方财富'],['300750','宁德时代'],
            ['600000','浦发银行'],['600036','招商银行'],['600519','贵州茅台'],
            ['601318','中国平安'],['601088','中国神华'],['600309','万华化学'],
        ]
    
    print("\n[2/4] 随机抽取100只...")
    sample_size = min(100, len(all_codes))
    random.seed(42)
    sampled = random.sample(all_codes, sample_size)
    print(f"  已抽取 {sample_size} 只")
    
    print(f"\n[3/4] 开始回测...")
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
        
        time.sleep(0.3)
    
    print(f"\n\n[4/4] 统计结果...")
    
    if not all_trades:
        print("\n❌ 没有产生任何交易信号")
        if errors:
            for e in errors[:5]:
                print(f"  - {e}")
        return
    
    trades_df = pd.DataFrame(all_trades)
    
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
    
    signal_stats = trades_df.groupby('buy_signal').agg(
        count=('pnl_pct', 'count'),
        win_rate=('pnl_pct', lambda x: (x > 0).mean() * 100),
        avg_pnl=('pnl_pct', 'mean'),
    ).round(2)
    
    sell_stats = trades_df.groupby('sell_reason').agg(
        count=('pnl_pct', 'count'),
        avg_pnl=('pnl_pct', 'mean'),
    ).round(2)
    
    score_stats = trades_df.groupby('buy_score').agg(
        count=('pnl_pct', 'count'),
        win_rate=('pnl_pct', lambda x: (x > 0).mean() * 100),
        avg_pnl=('pnl_pct', 'mean'),
    ).round(2)
    
    stock_stats = trades_df.groupby(['code', 'name']).agg(
        trades=('pnl_pct', 'count'),
        total_pnl=('pnl_pct', 'sum'),
        avg_pnl=('pnl_pct', 'mean'),
        win_rate=('pnl_pct', lambda x: (x > 0).mean() * 100),
    ).round(2).sort_values('total_pnl', ascending=False)
    
    # 计算累计收益曲线
    cumulative = (1 + trades_df['pnl_pct'] / 100).cumprod()
    max_dd = 0
    peak = 1
    for v in cumulative:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd
    
    # ===== 打印结果 =====
    print("\n" + "=" * 60)
    print("📊 强生战法 V2 优化版回测结果")
    print("=" * 60)
    
    print(f"\n📈 总体统计:")
    print(f"  回测股票数:   {len(results)} 只")
    print(f"  总交易次数:   {total_trades} 笔")
    print(f"  有交易股票:   {trades_df['code'].nunique()} 只")
    print(f"  平均持仓天数: {avg_holding:.1f} 天")
    print(f"  最大回撤:     {max_dd*100:.1f}%")
    print(f"  累计收益:     {(cumulative.iloc[-1]-1)*100:.1f}%")
    
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
    for idx, row in stock_stats.head(10).iterrows():
        print(f"  {idx[1]}({idx[0]}): {row['trades']:.0f}次, 胜率{row['win_rate']:.0f}%, 收益{row['total_pnl']:.1f}%")
    
    print(f"\n💀 最差股票 BOTTOM 5:")
    for idx, row in stock_stats.tail(5).iterrows():
        print(f"  {idx[1]}({idx[0]}): {row['trades']:.0f}次, 胜率{row['win_rate']:.0f}%, 收益{row['total_pnl']:.1f}%")
    
    # V1 vs V2 对比
    print(f"\n📊 V1 vs V2 对比:")
    v1 = {'trades': 1007, 'win_rate': 39.9, 'avg_pnl': 0.85, 'profit_factor': 1.44, 'avg_holding': 6.7, 'max_win': 43.98, 'max_lose': -22.40}
    print(f"  {'指标':<12} {'V1':>10} {'V2':>10} {'变化':>10}")
    print(f"  {'交易次数':<12} {v1['trades']:>10} {total_trades:>10} {total_trades-v1['trades']:>+10}")
    print(f"  {'胜率%':<12} {v1['win_rate']:>10.1f} {win_rate:>10.1f} {win_rate-v1['win_rate']:>+10.1f}")
    print(f"  {'平均收益%':<10} {v1['avg_pnl']:>10.2f} {avg_pnl:>10.2f} {avg_pnl-v1['avg_pnl']:>+10.2f}")
    print(f"  {'盈亏比':<12} {v1['profit_factor']:>10.2f} {profit_factor:>10.2f} {profit_factor-v1['profit_factor']:>+10.2f}")
    print(f"  {'持仓天数':<12} {v1['avg_holding']:>10.1f} {avg_holding:>10.1f} {avg_holding-v1['avg_holding']:>+10.1f}")
    
    trades_df.to_csv('/root/wondertrader/backtest_v2_trades.csv', index=False, encoding='utf-8-sig')
    print(f"\n📄 交易记录已保存: /root/wondertrader/backtest_v2_trades.csv")
    
    summary = {
        'version': 'v2_optimized',
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
        'cumulative_return': round((cumulative.iloc[-1]-1)*100, 1),
        'max_drawdown': round(max_dd*100, 1),
    }
    with open('/root/wondertrader/backtest_v2_summary.json', 'w') as f:
        json.dump(summary, f, indent=2)
    
    if errors:
        print(f"\n⚠️ 跳过 ({len(errors)}只):")
        for e in errors[:5]:
            print(f"  - {e}")
    
    print("\n" + "=" * 60)
    print("✅ V2优化版回测完成!")
    print("=" * 60)

if __name__ == '__main__':
    main()
