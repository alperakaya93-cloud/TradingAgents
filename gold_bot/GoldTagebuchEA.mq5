//+------------------------------------------------------------------+
//|                                              GoldTagebuchEA.mq5   |
//|  Trend-following pullback bot for gold (XAUUSD / XAUEUR) on M5.   |
//|                                                                   |
//|  Entry: higher-timeframe close above its EMA (trend), fast EMA    |
//|  above slow EMA, and the closed bar dipped into the fast EMA and  |
//|  closed back above it as a bullish candle with RSI in range.      |
//|  Exit:  ATR stop and target, break-even, then an ATR trailing     |
//|  stop. Guards: risk per trade, max positions, session hours,      |
//|  spread, daily loss limit, max drawdown, Friday close.            |
//|                                                                   |
//|  Demo use only. No financial advice.                              |
//+------------------------------------------------------------------+
#property copyright "TradingAgents"
#property version   "1.00"
#property description "Gold M5 trend/pullback EA with ATR risk management"

#include <Trade/Trade.mqh>

input group "Signal"
input ENUM_TIMEFRAMES InpTrendTF       = PERIOD_H1; // Trend timeframe
input int             InpTrendEMA      = 200;       // Trend EMA period
input int             InpFastEMA       = 21;        // Fast EMA (entry timeframe)
input int             InpSlowEMA       = 50;        // Slow EMA (entry timeframe)
input int             InpRSIPeriod     = 14;        // RSI period
input double          InpRSIMin        = 45.0;      // Buy only when RSI >= this
input double          InpRSIMax        = 70.0;      // Buy only when RSI <= this
input bool            InpAllowShorts   = false;     // Also sell in a downtrend

input group "Risk"
input double InpRiskPercent       = 0.5;  // Risk per trade in % of equity
input double InpFixedLots         = 0.0;  // Fixed lots (0 = use risk %)
input int    InpATRPeriod         = 14;   // ATR period
input double InpSLATR             = 1.5;  // Stop loss in ATR
input double InpTPATR             = 2.0;  // Take profit in ATR
input double InpBreakEvenATR      = 1.0;  // Move stop to entry after this profit in ATR
input double InpTrailATR          = 1.2;  // Trailing distance in ATR
input int    InpMaxPositions      = 2;    // Max open positions
input int    InpCooldownBars      = 2;    // Bars to wait between entries
input double InpDailyLossPercent  = 3.0;  // No new trades after this daily loss %
input double InpMaxDrawdownPercent= 15.0; // Stop trading after this drawdown %

input group "Filters"
input int    InpStartHour         = 1;    // Session start hour (server time)
input int    InpEndHour           = 22;   // Session end hour (server time)
input int    InpMaxSpreadPoints   = 60;   // Max spread in points
input int    InpFridayCloseHour   = 21;   // Close everything Friday from this hour

input group "General"
input ulong  InpMagic             = 102102;      // Magic number
input string InpComment           = "TAGEBUCH";  // Order comment
input bool   InpShowPanel         = true;        // Show status panel on chart

CTrade   trade;
int      hTrend = INVALID_HANDLE, hFast = INVALID_HANDLE, hSlow = INVALID_HANDLE;
int      hRSI = INVALID_HANDLE, hATR = INVALID_HANDLE;
datetime lastBarTime = 0;
datetime lastEntryTime = 0;
int      currentDay = -1;
double   dayStartEquity = 0.0;
double   peakEquity = 0.0;
bool     halted = false;
string   lastStatus = "";

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpFastEMA >= InpSlowEMA || InpSLATR <= 0 || InpTPATR <= 0)
     {
      Print("Invalid inputs: fast EMA must be below slow EMA, SL/TP must be > 0");
      return(INIT_PARAMETERS_INCORRECT);
     }
   hTrend = iMA(_Symbol, InpTrendTF, InpTrendEMA, 0, MODE_EMA, PRICE_CLOSE);
   hFast  = iMA(_Symbol, PERIOD_CURRENT, InpFastEMA, 0, MODE_EMA, PRICE_CLOSE);
   hSlow  = iMA(_Symbol, PERIOD_CURRENT, InpSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   hRSI   = iRSI(_Symbol, PERIOD_CURRENT, InpRSIPeriod, PRICE_CLOSE);
   hATR   = iATR(_Symbol, PERIOD_CURRENT, InpATRPeriod);
   if(hTrend == INVALID_HANDLE || hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE ||
      hRSI == INVALID_HANDLE || hATR == INVALID_HANDLE)
     {
      Print("Failed to create indicator handles: ", GetLastError());
      return(INIT_FAILED);
     }
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(30);
   trade.SetTypeFillingBySymbol(_Symbol);
   peakEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(hTrend);
   IndicatorRelease(hFast);
   IndicatorRelease(hSlow);
   IndicatorRelease(hRSI);
   IndicatorRelease(hATR);
   Comment("");
  }

//+------------------------------------------------------------------+
double Buffer(const int handle, const int shift)
  {
   double v[1];
   if(CopyBuffer(handle, 0, shift, 1, v) != 1)
      return(EMPTY_VALUE);
   return(v[0]);
  }

//+------------------------------------------------------------------+
int CountPositions()
  {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == (long)InpMagic)
         count++;
     }
   return(count);
  }

//+------------------------------------------------------------------+
void CloseAll(const string why)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == (long)InpMagic)
        {
         if(!trade.PositionClose(ticket))
            Print("Close failed (", why, "): ", trade.ResultRetcodeDescription());
        }
     }
  }

//+------------------------------------------------------------------+
double NormalizeLots(double lots)
  {
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   lots = MathFloor(lots / step + 1e-9) * step;
   if(lots < minLot)
      return(0.0); // rounding up to the minimum would risk more than asked
   return(MathMin(lots, maxLot));
  }

//+------------------------------------------------------------------+
double LotsForRisk(const ENUM_ORDER_TYPE type, const double price, const double slDistance)
  {
   double lots = InpFixedLots;
   if(lots <= 0)
     {
      double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
      double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      if(tickValue <= 0 || tickSize <= 0 || slDistance <= 0)
         return(0.0);
      double riskMoney = AccountInfoDouble(ACCOUNT_EQUITY) * InpRiskPercent / 100.0;
      lots = riskMoney / (slDistance / tickSize * tickValue);
     }
   double marginPerLot = 0.0;
   if(OrderCalcMargin(type, _Symbol, 1.0, price, marginPerLot) && marginPerLot > 0)
      lots = MathMin(lots, 0.9 * AccountInfoDouble(ACCOUNT_MARGIN_FREE) / marginPerLot);
   return(NormalizeLots(lots));
  }

//+------------------------------------------------------------------+
bool InSession(const MqlDateTime &dt)
  {
   if(InpStartHour < InpEndHour)
      return(dt.hour >= InpStartHour && dt.hour < InpEndHour);
   return(dt.hour >= InpStartHour || dt.hour < InpEndHour);
  }

//+------------------------------------------------------------------+
//| +1 buy, -1 sell, 0 nothing; judged on the last closed bar.        |
//+------------------------------------------------------------------+
int Signal()
  {
   double trendClose = iClose(_Symbol, InpTrendTF, 1);
   double trendEma   = Buffer(hTrend, 1);
   double fast = Buffer(hFast, 1), slow = Buffer(hSlow, 1), rsi = Buffer(hRSI, 1);
   if(trendClose <= 0 || trendEma == EMPTY_VALUE || fast == EMPTY_VALUE ||
      slow == EMPTY_VALUE || rsi == EMPTY_VALUE)
      return(0);

   double o = iOpen(_Symbol, PERIOD_CURRENT, 1), c = iClose(_Symbol, PERIOD_CURRENT, 1);
   double h = iHigh(_Symbol, PERIOD_CURRENT, 1), l = iLow(_Symbol, PERIOD_CURRENT, 1);

   if(trendClose > trendEma && fast > slow && l <= fast && c > fast && c > o &&
      rsi >= InpRSIMin && rsi <= InpRSIMax)
      return(1);
   if(InpAllowShorts && trendClose < trendEma && fast < slow && h >= fast && c < fast &&
      c < o && rsi >= 100.0 - InpRSIMax && rsi <= 100.0 - InpRSIMin)
      return(-1);
   return(0);
  }

//+------------------------------------------------------------------+
//| Break-even, then trail the stop by ATR.                           |
//+------------------------------------------------------------------+
void ManagePositions(const double atr)
  {
   if(atr == EMPTY_VALUE || atr <= 0)
      return;
   double stopsLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol ||
         PositionGetInteger(POSITION_MAGIC) != (long)InpMagic)
         continue;

      long   type  = PositionGetInteger(POSITION_TYPE);
      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl    = PositionGetDouble(POSITION_SL);
      double tp    = PositionGetDouble(POSITION_TP);
      double newSl = sl;

      if(type == POSITION_TYPE_BUY)
        {
         if(bid - entry < InpBreakEvenATR * atr)
            continue;
         newSl = MathMax(entry, bid - InpTrailATR * atr);
         newSl = MathMin(newSl, bid - stopsLevel);
         if(newSl <= sl + _Point)
            continue;
        }
      else
        {
         if(entry - ask < InpBreakEvenATR * atr)
            continue;
         newSl = MathMin(entry, ask + InpTrailATR * atr);
         newSl = MathMax(newSl, ask + stopsLevel);
         if(sl > 0 && newSl >= sl - _Point)
            continue;
        }
      if(!trade.PositionModify(ticket, NormalizeDouble(newSl, _Digits), tp))
         Print("Trailing modify failed: ", trade.ResultRetcodeDescription());
     }
  }

//+------------------------------------------------------------------+
void UpdatePanel(const int signal)
  {
   if(!InpShowPanel)
      return;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double drawdown = peakEquity > 0 ? 100.0 * (1.0 - equity / peakEquity) : 0.0;
   Comment(StringFormat(
              ">>>> TAGEBUCH GOLD BOT <<<<\n"
              "Balance: %.2f   Equity: %.2f\n"
              "Today: %+.2f   Drawdown: %.2f%%\n"
              "Positions: %d/%d   Last signal: %s\n"
              "Status: %s",
              balance, equity, equity - dayStartEquity, drawdown,
              CountPositions(), InpMaxPositions,
              signal > 0 ? "BUY" : (signal < 0 ? "SELL" : "-"), lastStatus));
  }

//+------------------------------------------------------------------+
void OpenTrade(const int direction)
  {
   double atr = Buffer(hATR, 1);
   if(atr == EMPTY_VALUE || atr <= 0)
      return;
   ENUM_ORDER_TYPE type = direction > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double price = direction > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double slDistance = InpSLATR * atr;
   double sl = NormalizeDouble(price - direction * slDistance, _Digits);
   double tp = NormalizeDouble(price + direction * InpTPATR * atr, _Digits);
   double lots = LotsForRisk(type, price, slDistance);
   if(lots <= 0)
     {
      lastStatus = "lot size below minimum";
      return;
     }
   bool ok = direction > 0 ? trade.Buy(lots, _Symbol, price, sl, tp, InpComment)
                           : trade.Sell(lots, _Symbol, price, sl, tp, InpComment);
   if(ok)
     {
      lastEntryTime = iTime(_Symbol, PERIOD_CURRENT, 0);
      lastStatus = StringFormat("%s %.2f lots @ %.2f", direction > 0 ? "BUY" : "SELL", lots, price);
     }
   else
      lastStatus = "order failed: " + trade.ResultRetcodeDescription();
   Print(lastStatus);
  }
//+------------------------------------------------------------------+
void OnTick()
  {
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);

   if(dt.day_of_year != currentDay)
     {
      currentDay = dt.day_of_year;
      dayStartEquity = equity;
     }
   peakEquity = MathMax(peakEquity, equity);
   if(!halted && equity < peakEquity * (1.0 - InpMaxDrawdownPercent / 100.0))
     {
      halted = true;
      CloseAll("max drawdown");
      Print("Max drawdown reached, trading stopped. Restart the EA to resume.");
     }

   if(dt.day_of_week == 5 && dt.hour >= InpFridayCloseHour)
      CloseAll("friday");

   ManagePositions(Buffer(hATR, 1));

   datetime barTime = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(barTime == lastBarTime)
      return;
   lastBarTime = barTime;

   int signal = Signal();
   lastStatus = "waiting";
   if(halted)
      lastStatus = "halted (max drawdown)";
   else if(!InSession(dt))
      lastStatus = "outside session";
   else if(dt.day_of_week == 5 && dt.hour >= InpFridayCloseHour)
      lastStatus = "weekend break";
   else if(equity <= dayStartEquity * (1.0 - InpDailyLossPercent / 100.0))
      lastStatus = "daily loss limit";
   else if(SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) > InpMaxSpreadPoints)
      lastStatus = "spread too wide";
   else if(CountPositions() >= InpMaxPositions)
      lastStatus = "max positions";
   else if(lastEntryTime > 0 &&
           iBarShift(_Symbol, PERIOD_CURRENT, lastEntryTime) <= InpCooldownBars)
      lastStatus = "cooldown";
   else if(signal != 0)
      OpenTrade(signal);
   UpdatePanel(signal);
  }

//+------------------------------------------------------------------+
