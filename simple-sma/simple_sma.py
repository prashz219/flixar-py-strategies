from flixar import FlixarStrategy
import pandas as pd

class SimpleSMACrossover(FlixarStrategy):
    """
    A simple Simple Moving Average (SMA) crossover strategy.
    Buys when the 5-period SMA crosses above the 20-period SMA.
    Sells when the 5-period SMA crosses below the 20-period SMA.
    """
    def __init__(self, config):
        super().__init__(config)
        self.short_window = 5
        self.long_window = 10
        self.position = 0 # 0 for flat, 1 for long, -1 for short
        self.last_trade_candle = None # Track the timestamp of the last candle that triggered a trade
        self.log(f"Strategy initialized for symbol: {self.symbol}")

    def on_tick(self, tick, history):
        """
        Logic triggered on every price movement.
        Resampled into 1-minute candles for indicator calculation.
        """
        # Sync initial position state from runner if not already set
        if not hasattr(self, '_pos_synced') or not self._pos_synced:
            runner_pos = self.get_position()
            if runner_pos:
                self.position = 1 if runner_pos.get('side') == 'BUY' else -1
                self.log(f"🔄 Synced initial position from runner: {self.position}")
            self._pos_synced = True

        if len(history) < 2:
            return

        # 1. Prepare history for resampling
        df = history.copy()
        
        # Robust datetime conversion
        if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
            # Robust conversion for mixed types (ms, seconds, and ISO strings)
            def parse_ts(ts):
                try:
                    # If numeric (ms or s)
                    if isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.replace('.','',1).isdigit()):
                        num = float(ts)
                        # ms if > 13 digits (roughly > yr 2033 in seconds)
                        if num > 1e12:
                            return pd.to_datetime(num, unit='ms', utc=True)
                        else:
                            return pd.to_datetime(num, unit='s', utc=True)
                    # If string (ISO etc)
                    return pd.to_datetime(ts, utc=True)
                except:
                    return pd.NaT

            df['dt'] = df['timestamp'].apply(parse_ts)
        else:
            df['dt'] = df['timestamp']
            
        df.set_index('dt', inplace=True)
        df.sort_index(inplace=True)


        # 2. Extract the last two minutes for calculations to save time
        recent_cutoff = df.index[-1] - pd.Timedelta(minutes=self.long_window * 2)
        recent_df = df[df.index >= recent_cutoff]

        # 3. Resample recent ticks to 1-minute candles
        try:
            resampled = recent_df['ltp'].resample('1min').last().dropna()
        except Exception as e:
            self.log(f"Resample error: {e}")
            return

        # Ensure we have enough data (Long Window + 1 for crossover check)
        if len(resampled) < self.long_window + 1:
            self.log(f"Waiting for more data... current candles: {len(resampled)}/{self.long_window + 1}")
            return
        
        # 3. Calculate SMAs on resampled 1-minute data
        # Current values
        short_sma = resampled.tail(self.short_window).mean()
        long_sma = resampled.tail(self.long_window).mean()

        # Previous values (one candle ago)
        prev_short_sma = resampled.iloc[-self.short_window-1:-1].mean()
        prev_long_sma = resampled.iloc[-self.long_window-1:-1].mean()

        current_candle_ts = resampled.index[-1]
        if self.last_trade_candle == current_candle_ts:
            return  # Triggered once already for this candle

        self.log(f"{self.symbol} | LTP: {tick['ltp']} | SMA{self.short_window}/{self.long_window}: {short_sma:.1f}/{long_sma:.1f} | Prev: {prev_short_sma:.1f}/{prev_long_sma:.1f}")

        # Strategy Logic (True Crossover Event)
        # 🚀 BUY: Fast SMA crossed ABOVE Slow SMA
        if prev_short_sma <= prev_long_sma and short_sma > long_sma:
            if self.position <= 0:  # Flat or Short
                if self.position == -1:
                    self.log("🔻 CROSS OVER UP! Reversing SHORT position to LONG.")
                    # Reversal: We need to buy 2x qty (once to close -1, once to open +1)
                    # For simplicity in this runner's place_order logic which expects ENTRY/EXIT,
                    # we send two separate orders if the runner doesn't handle net positions.
                    if self.buy(qty=self.qty): # Close Short
                        if self.buy(qty=self.qty): # Open Long
                            self.position = 1
                            self.last_trade_candle = current_candle_ts
                        else:
                            self.position = 0
                else:
                    self.log("🚀 GOLDEN CROSS! Entering LONG position.")
                    if self.buy(qty=self.qty):
                        self.position = 1
                        self.last_trade_candle = current_candle_ts

        # 🔻 SELL: Fast SMA crossed BELOW Slow SMA
        elif prev_short_sma >= prev_long_sma and short_sma < long_sma:
            if self.position >= 0: # Flat or Long
                if self.position == 1:
                    self.log("🚀 CROSS OVER DOWN! Reversing LONG position to SHORT.")
                    if self.sell(qty=self.qty): # Close Long
                        if self.sell(qty=self.qty): # Open Short
                            self.position = -1
                            self.last_trade_candle = current_candle_ts
                        else:
                            self.position = 0
                else:
                    self.log("🔻 DEATH CROSS! Entering SHORT position.")
                    if self.sell(qty=self.qty):
                        self.position = -1
                        self.last_trade_candle = current_candle_ts
