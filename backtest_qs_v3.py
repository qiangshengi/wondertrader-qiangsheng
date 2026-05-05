#!/usr/bin/env python3
"""
强生战法回测 V3 - 三方向优化并行对比
方向1: 止损93%→90%
方向2: 只用B2/B3，去掉B1
方向3: ATR动态止损
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

def calc_atr(high, low, close, period=14):
    """计算ATR (Average True Range)"""
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()

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
        trend_up = row['zge'] > row['dage']
        
        # B1: J<=10, 评分>=4
        b1 = trend_up and row['zge'] > row['dage'] * 0.99 and row['j'] <= 10 and score >= 4
        
        # B2
        j_below_15_recent = any(df.iloc[max(0,i-2):i+1]['j'] < 15)
        yesterday_change = (prev['close'] / df.iloc[i-2]['close'] - 1) * 100 if i >= 2 else 0
        today_change = (row['close'] / prev['close'] - 1) * 100
        vol_ma4 = df.iloc[max(0,i-5):i-1]['vol'].mean() if i >= 5 else row['vol']
        b2 = (j_below_15_recent and row['vol'] > vol_ma4 * 1.25 and 
              yesterday_change < 5.5 and today_change > 3.8 and 
              row['j'] < 80 and trend_up and score >= 3)
        
        # B3
        j_below_15_4d = any(df.iloc[max(0,i-3):i+1]['j'] < 15)
        yesterday_vol_up = prev['vol'] > df.iloc[i-2]['vol'] if i >= 2 else False
        vol_ratio = row['vol'] / prev['vol'] if prev['vol'] > 0 else 1
        shrink_half = vol_ratio < 0.6
        flat_vol = vol_ratio > 0.9
        true_break = today_change > 0 or (today_change > -3 and (shrink_half or flat_vol))
        b3 = (j_below_15_4d and yesterday_vol_up and yesterday_change > 3 and 
              today_change < 4 and trend_up and true_break and score >= 3)
        
        death_cross = row['zge'] < row['dage'] and prev['zge'] > prev['dage']
        
        signals.append({
            'idx': i,
            'date': row['date'],
            'close': row['close'],
            'low': row['low'],
            'high': row['high'],
            'score': score,
            'b1': b1,
            'b2': b2,
            'b3': b3,
            'death_cross': death_cross,
            'j': row['j'],
            'zge': row['zge'],
            'dage': row['dage'],
            'trend_up': trend_up,
            'today_change': today_change,
            'atr': row.get('atr', 0),
        })
    return signals

# ============== 三种优化方案 ==============

def backtest_variant(signals, variant_name, params):
    """通用回测引擎，支持不同参数"""
    trades = []
    position = None
    cost_price = 0
    stop_loss = 0
    hold_days = 0
    entry_idx = 0
    
    for sig in signals:
        if position is None:
            # 开仓条件
            can_open = False
            signal_type = ''
            
            if params.get('use_b1', True) and sig['b1']:
                can_open = True
                signal_type = 'B1'
            if sig['b2']:
                can_open = True
                signal_type = 'B2'
            if sig['b3']:
                can_open = True
                signal_type = 'B3'
            
            if sig['trend_up'] and sig['score'] >= 3 and can_open:
                buy_price = sig['close']
                cost_price = buy_price
                hold_days = 0
                entry_idx = sig['idx']
                
                # 止损计算
                stop_method = params.get('stop_method', 'fixed')
                if stop_method == 'fixed':
                    stop_loss = buy_price * params['stop_ratio']
                elif stop_method == 'atr':
                    atr_val = sig['atr'] if sig['atr'] > 0 else buy_price * 0.03
                    stop_loss = buy_price - params['atr_multiplier'] * atr_val
                
                position = {
                    'buy_date': str(sig['date']),
                    'buy_price': buy_price,
                    'score': sig['score'],
                    'signal_type': signal_type,
                }
        else:
            hold_days = sig['idx'] - entry_idx
            sell_reason = None
            sell_price = sig['close']
            
            # 死叉清仓
            if sig['death_cross']:
                sell_reason = '死叉清仓'
            # 破止损
            elif sig['close'] < stop_loss:
                sell_reason = '破止损'
            # 止盈
            elif (sig['close'] / cost_price - 1) > params.get('take_profit', 0.20):
                sell_reason = '止盈'
            # 评分过低
            elif hold_days >= params.get('hold_days_sell', 5) and sig['score'] <= 1:
                sell_reason = '评分过低'
            # 趋势转弱
            elif hold_days >= params.get('hold_days_trend', 3) and not sig['trend_up']:
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
    if position is not None and signals:
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
    
    return trades

def backtest_stock_multi(code, name=""):
    """回测单只股票，同时跑三种优化"""
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
        df['atr'] = calc_atr(df['high'], df['low'], df['close'], 14)
        k, d, j = calc_kdj(df['high'], df['low'], df['close'])
        df['k'] = k
        df['d'] = d
        df['j'] = j
        
        signals = detect_signals(df)
        if not signals:
            return None
        
        # 方案1: 止损90%（原93%）
        v1_params = {
            'use_b1': True,
            'stop_method': 'fixed',
            'stop_ratio': 0.90,
            'take_profit': 0.20,
            'hold_days_sell': 5,
            'hold_days_trend': 3,
        }
        
        # 方案2: 只用B2/B3（去掉B1）
        v2_params = {
            'use_b1': False,
            'stop_method': 'fixed',
            'stop_ratio': 0.93,
            'take_profit': 0.20,
            'hold_days_sell': 5,
            'hold_days_trend': 3,
        }
        
        # 方案3: ATR动态止损
        v3_params = {
            'use_b1': True,
            'stop_method': 'atr',
            'atr_multiplier': 2.5,
            'take_profit': 0.20,
            'hold_days_sell': 5,
            'hold_days_trend': 3,
        }
        
        results = {}
        for name_key, params in [('止损90%', v1_params), ('仅B2B3', v2_params), ('ATR止损', v3_params)]:
            trades = backtest_variant(signals, name_key, params)
            for t in trades:
                t['code'] = code
                t['stock_name'] = name
            results[name_key] = trades
        
        return {'code': code, 'name': name, 'results': results, 'data_points': len(df)}
    
    except Exception as e:
        return {'code': code, 'name': name, 'error': str(e)}

# ============== 统计函数 ==============

def calc_stats(trades_df, name):
    if trades_df.empty:
        return {'name': name, 'trades': 0}
    
    total = len(trades_df)
    wins = trades_df[trades_df['pnl_pct'] > 0]
    losses = trades_df[trades_df['pnl_pct'] <= 0]
    
    win_rate = len(wins) / total * 100
    avg_pnl = trades_df['pnl_pct'].mean()
    avg_win = wins['pnl_pct'].mean() if len(wins) > 0 else 0
    avg_lose = losses['pnl_pct'].mean() if len(losses) > 0 else 0
    profit_factor = abs(wins['pnl_pct'].sum() / losses['pnl_pct'].sum()) if len(losses) > 0 and losses['pnl_pct'].sum() != 0 else float('inf')
    
    cumulative = (1 + trades_df['pnl_pct'] / 100).cumprod()
    max_dd = 0
    peak = 1
    for v in cumulative:
        if v > peak: peak = v
        dd = (peak - v) / peak
        if dd > max_dd: max_dd = dd
    
    # 按信号统计
    sig_stats = trades_df.groupby('buy_signal').agg(
        count=('pnl_pct', 'count'),
        win_rate=('pnl_pct', lambda x: (x > 0).mean() * 100),
        avg_pnl=('pnl_pct', 'mean'),
    ).round(2) if len(trades_df) > 0 else pd.DataFrame()
    
    # 按卖出原因统计
    sell_stats = trades_df.groupby('sell_reason').agg(
        count=('pnl_pct', 'count'),
        avg_pnl=('pnl_pct', 'mean'),
    ).round(2) if len(trades_df) > 0 else pd.DataFrame()
    
    return {
        'name': name,
        'trades': total,
        'win_rate': round(win_rate, 1),
        'avg_pnl': round(avg_pnl, 2),
        'avg_win': round(avg_win, 2),
        'avg_lose': round(avg_lose, 2),
        'profit_factor': round(profit_factor, 2),
        'cumulative': round((cumulative.iloc[-1] - 1) * 100, 1),
        'max_drawdown': round(max_dd * 100, 1),
        'avg_holding': round(trades_df['holding_days'].mean(), 1),
        'sig_stats': sig_stats,
        'sell_stats': sell_stats,
    }

# ============== 主程序 ==============

def main():
    print("=" * 70)
    print("强生战法 V3 - 三方向优化并行对比")
    print("=" * 70)
    print("\n方向1: 止损93%→90%（减少假止损）")
    print("方向2: 只用B2/B3，去掉弱B1信号")
    print("方向3: ATR动态止损（2.5倍ATR）")
    
    # 获取股票列表
    print("\n[1/3] 获取A股列表...")
    try:
        stock_list = ak.stock_zh_a_spot_em()
        stock_list = stock_list[~stock_list['名称'].str.contains('ST|退|N ', na=False)]
        stock_list = stock_list[stock_list['代码'].str.startswith(('00', '30', '60'))]
        all_codes = stock_list[['代码', '名称']].values.tolist()
        print(f"  可选股票数: {len(all_codes)}")
    except Exception as e:
        print(f"  获取失败: {e}, 使用备用列表")
        all_codes = [
            ['000001','平安银行'],['000333','美的集团'],['000568','泸州老窖'],
            ['000651','格力电器'],['000858','五粮液'],['002230','科大讯飞'],
            ['002415','海康威视'],['002594','比亚迪'],['300059','东方财富'],
            ['300750','宁德时代'],['600036','招商银行'],['600519','贵州茅台'],
            ['601088','中国神华'],['601318','中国平安'],['600309','万华化学'],
            ['600690','海尔智家'],['000858','五粮液'],['601012','隆基绿能'],
        ]
    
    # 随机抽取100只（用相同种子保持一致性）
    print("\n[2/3] 随机抽取100只...")
    sample_size = min(100, len(all_codes))
    random.seed(42)
    sampled = random.sample(all_codes, sample_size)
    print(f"  已抽取 {sample_size} 只")
    
    # 三种方案的交易记录收集
    all_trades = {'止损90%': [], '仅B2B3': [], 'ATR止损': []}
    errors = []
    
    print(f"\n[3/3] 开始三方向并行回测...")
    for idx, (code, name) in enumerate(sampled):
        sys.stdout.write(f"\r  进度: {idx+1}/{sample_size} [{name}({code})]          ")
        sys.stdout.flush()
        
        result = backtest_stock_multi(code, name)
        if result is None:
            errors.append(f"{code} {name}: 数据不足")
            continue
        if 'error' in result:
            errors.append(f"{code} {name}: {result['error']}")
            continue
        
        for variant_name, trades in result['results'].items():
            all_trades[variant_name].extend(trades)
        
        time.sleep(0.3)
    
    print(f"\n\n{'='*70}")
    print("📊 强生战法 V3 - 三方向优化对比结果")
    print("=" * 70)
    
    # V2 baseline 数据
    v2_baseline = {
        'name': 'V2基线(止损93%+全部信号)',
        'trades': 326,
        'win_rate': 37.1,
        'avg_pnl': 1.04,
        'avg_win': 11.69,
        'avg_lose': -5.24,
        'profit_factor': 1.32,
        'cumulative': 405.7,
        'max_drawdown': 71.9,
        'avg_holding': 9.4,
    }
    
    stats_results = {'V2基线': v2_baseline}
    
    for variant_name, trades_list in all_trades.items():
        if trades_list:
            df = pd.DataFrame(trades_list)
            stats = calc_stats(df, variant_name)
            stats_results[variant_name] = stats
        else:
            stats_results[variant_name] = {'name': variant_name, 'trades': 0}
    
    # 总体对比表
    print(f"\n{'='*70}")
    print(f"{'指标':<12} {'V2基线':>10} {'止损90%':>10} {'仅B2B3':>10} {'ATR止损':>10}")
    print(f"{'='*70}")
    
    for metric, label in [('trades', '交易次数'), ('win_rate', '胜率%'), ('avg_pnl', '均收益%'),
                           ('avg_win', '均盈利%'), ('avg_lose', '均亏损%'), ('profit_factor', '盈亏比'),
                           ('cumulative', '累计收益%'), ('max_drawdown', '最大回撤%'), ('avg_holding', '持仓天数')]:
        row = f"{label:<12}"
        for key in ['V2基线', '止损90%', '仅B2B3', 'ATR止损']:
            val = stats_results.get(key, {}).get(metric, 'N/A')
            if isinstance(val, float):
                row += f"{val:>10.1f}"
            elif isinstance(val, int):
                row += f"{val:>10}"
            else:
                row += f"{str(val):>10}"
        print(row)
    
    # 每个方案的详细信号和卖出统计
    for variant_name in ['止损90%', '仅B2B3', 'ATR止损']:
        stats = stats_results.get(variant_name, {})
        if stats.get('trades', 0) == 0:
            continue
        
        print(f"\n{'='*70}")
        print(f"📋 {variant_name} 详细统计")
        print(f"{'='*70}")
        
        sig = stats.get('sig_stats')
        if sig is not None and not sig.empty:
            print(f"\n  按信号统计:")
            print(sig.to_string())
        
        sell = stats.get('sell_stats')
        if sell is not None and not sell.empty:
            print(f"\n  按卖出原因统计:")
            print(sell.to_string())
        
        # Top/Bottom 股票
        trades_df = pd.DataFrame(all_trades[variant_name])
        if not trades_df.empty:
            stock_stats = trades_df.groupby(['code', 'stock_name']).agg(
                trades=('pnl_pct', 'count'),
                total_pnl=('pnl_pct', 'sum'),
                win_rate=('pnl_pct', lambda x: (x > 0).mean() * 100),
            ).round(2).sort_values('total_pnl', ascending=False)
            
            print(f"\n  🏆 最佳股票 TOP 5:")
            for (c, n), row in stock_stats.head(5).iterrows():
                print(f"    {n}({c}): {row['trades']:.0f}次, 胜率{row['win_rate']:.0f}%, 收益{row['total_pnl']:.1f}%")
            
            print(f"\n  💀 最差股票 BOTTOM 3:")
            for (c, n), row in stock_stats.tail(3).iterrows():
                print(f"    {n}({c}): {row['trades']:.0f}次, 胜率{row['win_rate']:.0f}%, 收益{row['total_pnl']:.1f}%")
    
    # 结论
    print(f"\n{'='*70}")
    print("📝 结论与推荐")
    print(f"{'='*70}")
    
    best_variant = None
    best_score = -999
    for key in ['止损90%', '仅B2B3', 'ATR止损']:
        s = stats_results.get(key, {})
        if s.get('trades', 0) == 0:
            continue
        # 综合评分: 盈亏比*2 + 胜率/10 - 最大回撤/10
        score = s.get('profit_factor', 0) * 2 + s.get('win_rate', 0) / 10 - s.get('max_drawdown', 0) / 10
        if score > best_score:
            best_score = score
            best_variant = key
    
    if best_variant:
        best = stats_results[best_variant]
        print(f"\n  🏅 推荐方案: {best_variant}")
        print(f"     胜率: {best.get('win_rate', 'N/A')}%")
        print(f"     盈亏比: {best.get('profit_factor', 'N/A')}")
        print(f"     累计收益: {best.get('cumulative', 'N/A')}%")
        print(f"     最大回撤: {best.get('max_drawdown', 'N/A')}%")
    
    # 保存结果
    summary = {}
    for key, val in stats_results.items():
        summary[key] = {k: v for k, v in val.items() if not isinstance(v, pd.DataFrame)}
    with open('/root/wondertrader/backtest_v3_summary.json', 'w') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    for variant_name, trades_list in all_trades.items():
        if trades_list:
            pd.DataFrame(trades_list).to_csv(
                f'/root/wondertrader/backtest_v3_{variant_name}.csv', 
                index=False, encoding='utf-8-sig')
    
    if errors:
        print(f"\n⚠️ 跳过 ({len(errors)}只)")
    
    print("\n✅ V3 三方向优化回测完成!")

if __name__ == '__main__':
    main()
