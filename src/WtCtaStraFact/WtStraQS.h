#pragma once
#include "../Includes/CtaStrategyDefs.h"
#include <deque>

class WtStraQS : public CtaStrategy
{
public:
	WtStraQS(const char* id);
	virtual ~WtStraQS();

public:
	virtual const char* getFactName() override;
	virtual const char* getName() override;
	virtual bool init(WTSVariant* cfg) override;
	virtual void on_schedule(ICtaStraCtx* ctx, uint32_t curDate, uint32_t curTime) override;
	virtual void on_init(ICtaStraCtx* ctx) override;
	virtual void on_tick(ICtaStraCtx* ctx, const char* stdCode, WTSTickData* newTick) override;
	virtual void on_session_begin(ICtaStraCtx* ctx, uint32_t uTDate) override;

private:
	// 工具函数
	double calcEMA(const double* data, int len, int period);
	double calcMA(const double* data, int len, int period);
	double calcBBI(const double* closes, int len);
	void  calcKDJ(const double* highs, const double* lows, const double* closes, int len,
	              double& k, double& d, double& j);

	// 评分系统
	int calcScore(double curClose, double prevClose, double curBBI, double prevJ, double prevD, double curJ,
	              double curVol, double prevVol, double ma20);

	// 信号检测
	bool checkB1(double zge, double dage, double j, int score);
	bool checkB2(double zge, double dage, double j, int score,
	             double curVol, double volMA4, double yestChange, double todayChange,
	             const double* jArr, int jLen);
	bool checkB3(double zge, double dage, double j, int score,
	             double curVol, double prevVol, double prevPrevVol,
	             double yestChange, double todayChange,
	             const double* jArr, int jLen);

private:
	// 策略参数
	std::string _code;          // 合约代码
	std::string _period;        // K线周期
	uint32_t    _count;         // K线条数
	uint32_t    _lots;          // 每次交易手数
	bool        _isstk;         // 是否股票
	double      _stopLossRatio; // 止损比例 (0.93)
	double      _takeProfitRatio; // 止盈比例 (1.20)
	double      _b1JThreshold;  // B1的J阈值 (10)
	int         _minScore;      // 最低开仓评分 (4)
	int         _holdDaysForSell; // 允许评分卖出的最少持仓天数 (5)
	int         _holdDaysForTrend; // 允许趋势卖出的最少持仓天数 (3)

	std::string _moncode;       // 当前主力合约

	// 持仓状态
	bool        _inPosition;
	double      _costPrice;
	double      _stopLossPrice;
	int         _holdDays;

	// ATR相关
	double      _atrMultiplier;  // ATR倍数 (2.5)
	std::deque<double> _trueRanges; // 真实波幅队列
	uint32_t    _atrPeriod;      // ATR周期 (14)

	// 计算ATR
	double calcATR(const double* highs, const double* lows, const double* closes, int len);
};
