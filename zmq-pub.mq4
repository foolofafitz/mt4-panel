#property strict
#property indicator_chart_window

#include <Zmq/Zmq.mqh>
#include <json.mqh>

Context context;

Socket socket(context, ZMQ_PUB);
string url = "tcp://localhost:5557";

int OnInit() {
   socket.connect(url);

   socket.setSendHighWaterMark(10);
   socket.setXpubNoDrop(false);
   socket.setSendTimeout(10);
   socket.setLinger(0);

   EventSetTimer(1);

   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) {
   EventKillTimer();
   socket.disconnect(url);
}

int OnCalculate(const int rates_total, // size of input time series
      const int prev_calculated,       // bars handled in previous call
      const datetime &time[],          // Time
      const double &open[],            // Open
      const double &high[],            // High
      const double &low[],             // Low
      const double &close[],           // Close
      const long &tick_volume[],       // Tick Volume
      const long &volume[],            // Real Volume
      const int &spread[]              // Spread
      )
{
   Publish();
   return 0;
}

void OnTimer() {
   Publish();
}

void Publish() {
   ZmqMsg msg;
   double profit = 0.0;
   double swap = 0.0;
   int count = 0;
   string position = "NONE";
   double lots = 0.0;

   JSONNode account;
   account["balance"] = AccountBalance();
   account["profit"] = AccountProfit();
   account["equity"] = AccountEquity();

   JSONNode *symbol = account["symbol"];
   symbol["name"] = Symbol();
   symbol["bid"] = Bid;
   symbol["ask"] = Ask;
   symbol["digits"] = Digits;
   symbol["atr"] = iATR(Symbol(), PERIOD_H1, 100, 1);

   JSONNode *orders = account["orders"];

   for (int i=0; i < OrdersTotal(); i++) {

      if(!OrderSelect(i, SELECT_BY_POS))
         continue;

      if(OrderSymbol() == Symbol()) {
         JSONNode js;

         js["ticket"] = OrderTicket();
         js["time"] = int(OrderOpenTime());
         js["type"] = OrderType();
         js["size"] = OrderLots();
         js["symbol"] = Symbol();
         js["open_price"] = NormalizeDouble(OrderOpenPrice(), Digits);
         js["sl"] = OrderStopLoss();
         js["tp"] = OrderTakeProfit();
         js["swap"] = NormalizeDouble(OrderSwap(), 2);
         js["profit"] = NormalizeDouble(OrderProfit(), 2);
         orders.Add(js);

         lots += OrderLots();
         profit += OrderProfit();
         swap += OrderSwap();
         count += 1;
      }
   }

   if (count > 0) {
      msg.rebuild(string(AccountNumber()) + " " + account.Serialize());
      socket.send(msg, true);
   }
}
