from flixar import FlixarStrategy
import pandas as pd
import pytz

class Options50PercentSL(FlixarStrategy):
    """
    Options 50% SL Strategy:
    1. Entry: 9:35 AM IST, Sell ATM Straddle (CE & PE).
    2. Exit: 2:55 PM IST or 50% Stop Loss from the average entry premium.
    
    Configuration Requirements:
    - instrumentType: "OPTIONS"
    - underlying: "NSE:NIFTY50-INDEX" (Example)
    - legs: [
        {"side": "SELL", "type": "CE", "strikeLogic": "ATM", "quantity": 1},
        {"side": "SELL", "type": "PE", "strikeLogic": "ATM", "quantity": 1}
      ]
    """
    def __init__(self, config):
        super().__init__(config)
        self.underlying = config.get('underlying', self.symbol)
        self.entered = False
        self.exited = False
        self.timezone = pytz.timezone('Asia/Kolkata')
        self.log(f"Strategy initialized for underlying: {self.underlying}")

    def on_tick(self, tick, history):
        """
        Called on every tick of the underlying index.
        """
        # 1. Sync state (handle runner restarts)
        if not hasattr(self, '_pos_synced'):
            pos = self.get_position()
            if pos:
                self.entered = True
                self.log(f"🔄 Synced existing position for {self.underlying}")
            self._pos_synced = True

        # 2. Get current time in IST
        now = pd.Timestamp.now(tz='Asia/Kolkata')
        current_time_str = now.strftime("%H:%M")

        # 3. Entry Logic: 9:35 AM
        if not self.entered and current_time_str >= "17:15" and current_time_str < "23:30":
            self.log(f"🚀 5:15 PM reached. Entering ATM Straddle for {self.underlying}...")
            # Calling self.sell() with instrumentType: "OPTIONS" triggers the runner's
            # multi-leg resolution and execution logic.
            if self.sell():
                self.entered = True
                self.log("✅ Straddle entry orders dispatched.")
            return

        # 4. Exit Logic: SL or 2:55 PM
        if self.entered and not self.exited:
            # Check for Time Exit: 2:55 PM
            if current_time_str >= "23:30":
                self.log(f"🕒 11:30 PM reached. Closing all positions for {self.underlying}...")
                if self.buy(exit_reason='TARGET_HIT'): # Reverses the straddle
                    self.exited = True
                    self.log("✅ Time exit orders dispatched.")
                return

            # Check for 50% Stop Loss
            pos = self.get_position()
            if pos and 'legs' in pos:
                legs = pos['legs']
                total_entry_premium = sum(leg['price'] for leg in legs.values())
                
                current_total_premium = 0
                all_legs_prices_available = True
                
                for leg_symbol in legs.keys():
                    # The runner populates self._runner.history for all symbols seen on Redis
                    leg_history = self._runner.history.get(leg_symbol)
                    if leg_history is not None and not leg_history.empty:
                        ltp = float(leg_history.iloc[-1]['ltp'])
                        current_total_premium += ltp
                    else:
                        all_legs_prices_available = False
                        break
                
                if all_legs_prices_available:
                    # For a Short Straddle, SL is hit if combined premium INCREASES.
                    # 50% SL = Entry Premium * 1.5
                    sl_limit = total_entry_premium * 1.5
                    
                    if current_total_premium >= sl_limit:
                        self.log(f"🚨 SL HIT! Current Premium: {current_total_premium:.2f} >= Limit: {sl_limit:.2f}")
                        if self.buy(exit_reason='SL_HIT'):
                            self.exited = True
                            self.log("✅ SL exit orders dispatched.")
            else:
                # If position is gone from runner but we haven't marked ourselves as exited
                # (e.g. manual exit or platform auto-exit)
                pass

    def log(self, message):
        """Helper for user logging"""
        super().log(f"[options50percentSL] {message}")
