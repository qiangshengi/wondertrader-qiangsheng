#!/usr/bin/env python3
"""
强生战法模拟盘 - 实时行情数据推送
用akshare获取实时行情，通过UDP推送给WonderTrader ParserUDP

用法:
  python3 feed_realtime.py          # 交易时间内自动循环推送
  python3 feed_realtime.py --once   # 推送一次（测试用）
"""
import akshare as ak
import socket
import struct
import time
import sys
from datetime import datetime, timedelta

# 配置
UDP_HOST = "127.0.0.1"
UDP_PORT = 3997  # ParserUDP sport

# 监控的股票列表 (code, exchange)
STOCKS = [
    ("601318", "SSE"),  # 中国平安
    ("601012", "SSE"),  # 隆基绿能
    ("300059", "SZSE"), # 东方财富
    ("002594", "SZSE"), # 比亚迪
    ("300750", "SZSE"), # 宁德时代
    ("600276", "SSE"),  # 恒瑞医药
    ("600519", "SSE"),  # 贵州茅台
    ("600036", "SSE"),  # 招商银行
    ("603288", "SSE"),  # 海天味业
    ("601888", "SSE"),  # 中国中免
]

def build_tick_msg(exchg, code, price, open_p, high, low, vol, amount, bid1, ask1):
    """
    构造WTSTickStruct格式的UDP消息
    WonderTrader ParserUDP接收的是序列化的WTSTickStruct
    """
    # WTSTickStruct 格式 (简化版，关键字段)
    # 这个需要根据WonderTrader实际的UDP协议来构造
    # 先用日K bar的方式推送
    pass

def fetch_realtime():
    """获取实时行情"""
    try:
        df = ak.stock_zh_a_spot_em()
        return df
    except Exception as e:
        print(f"[ERROR] 获取行情失败: {e}")
        return None

def get_stock_price(df, code):
    """从实时行情中提取某只股票数据"""
    row = df[df['代码'] == code]
    if row.empty:
        return None
    row = row.iloc[0]
    return {
        'code': code,
        'name': row['名称'],
        'price': float(row['最新价']),
        'open': float(row['今开']),
        'high': float(row['最高']),
        'low': float(row['最低']),
        'vol': float(row['成交量']),
        'amount': float(row['成交额']),
        'pre_close': float(row['昨收']),
        'change_pct': float(row['涨跌幅']),
    }

def send_udp_bar(sock, exchg, code, date, time_val, open_p, high, low, close, vol, amount):
    """
    通过UDP发送bar数据给WonderTrader
    WonderTrader ParserUDP的tick格式：
    """
    # 构造一个简化的tick数据包
    # WonderTrader的UDP tick格式是一个固定大小的结构体
    # WTSTickStruct: exchg(10) + code(32) + price(8) + open(8) + high(8) + low(8) + ...
    
    # 由于UDP格式比较复杂，我们改用另一种方式：
    # 通过WtDtPorter的ext parser接口推送
    # 或者直接用CSV更新 + strategy on_schedule读取
    
    # 简化方案：将实时数据写入本地文件，让策略通过stra_get_bars读取
    pass

def is_trading_time():
    """判断是否在交易时间"""
    now = datetime.now()
    # 周末不交易
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    # 9:15-11:30, 13:00-15:00
    return (925 <= t <= 1130) or (1300 <= t <= 1500)

def update_daily_data(stocks_data):
    """
    将实时数据追加到本地DSB数据中
    这样策略通过stra_get_bars就能读到最新数据
    """
    storage_dir = "/root/wondertrader/run_stk_sim/stk_data"
    
    for code, exchg in stocks_data:
        # 写入一个今日行情文件
        pass

if __name__ == '__main__':
    once_mode = '--once' in sys.argv
    
    print("=" * 50)
    print("🐉 强生战法模拟盘 - 行情数据推送")
    print("=" * 50)
    print(f"目标: {len(STOCKS)}只股票")
    print(f"UDP: {UDP_HOST}:{UDP_PORT}")
    print()
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    while True:
        now = datetime.now()
        
        if not is_trading_time() and not once_mode:
            print(f"[{now.strftime('%H:%M:%S')}] 非交易时间，等待...")
            time.sleep(60)
            continue
        
        print(f"\n[{now.strftime('%H:%M:%S')}] 获取实时行情...")
        df = fetch_realtime()
        
        if df is not None:
            for code, exchg in STOCKS:
                info = get_stock_price(df, code)
                if info:
                    sign = "+" if info['change_pct'] >= 0 else ""
                    print(f"  {info['name']}({code}): ¥{info['price']:.2f} {sign}{info['change_pct']:.1f}%")
        
        if once_mode:
            break
        
        # 每30秒推送一次
        time.sleep(30)
