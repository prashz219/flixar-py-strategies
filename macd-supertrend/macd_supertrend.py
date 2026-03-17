from flixar import FlixarStrategy
import pandas as pd
import numpy as np
import datetime
import logging

class MACDSupertrendStrategy(FlixarStrategy):
    """
    MACD + Supertrend Strategy.
    Entry: Supertrend flips direction AND MACD is confirmation.
    Exit: MACD Crossover.
    Timeframe: 15-minute candles (resampled from ticks).
    """
    def __init__(self, config):
        super().__init__(config)
        
        # MACD Parameters
        self.macd_fast = config.get("macd_fast", 12)
        self.macd_slow = config.get("macd_slow", 26)
        self.macd_signal = config.get("macd_signal", 9)
        
        # Supertrend Parameters
        self.st_period = config.get("supertrend_period", 10)
        self.st_multiplier = config.get("supertrend_mul", 3)
        
        # Strategy State
        self.position = 0 # 0: Flat, 1: Long, -1: Short
        self.last_processed_candle = None
        
        self.log(f"Strategy initialized: {self.name} for {self.symbol}")

    def on_tick(self, tick, history):
        """
        Called on every price update.
        """
        if len(history) < 2:
            return

        # 1. Sync initial position state from runner
        if not hasattr(self, '_pos_synced'):
            runner_pos = self.get_position()
            if runner_pos:
                self.position = 1 if runner_pos.get('side') == 'BUY' else -1
                self.log(f"Synced initial position: {self.position}")
            self._pos_synced = True

        # 2. Resample ticks to 15-minute candles
        df = self._prepare_candles(history)
        if df is None or len(df) < max(self.macd_slow, self.st_period) + 2:
            return

        # 3. Calculate Indicators
        df = self._calculate_indicators(df)
        
        # Get current and previous values for signals
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        current_candle_ts = df.index[-1]
        if self.last_processed_candle == current_candle_ts:
            return # Only process once per candle completion logic if needed, 
                   # but here we follow tick-by-tick within the candle for entry/exit.
        
        # 4. Check Time Constraints
        # Use the timestamp from the tick for consistent logic with historical/backtest data
        tick_dt = pd.to_datetime(tick['timestamp'], unit='s', utc=True).tz_convert('Asia/Kolkata')
        now_time = tick_dt.time()
        
        start_time = datetime.time(9, 16)
        no_new_trade_time = datetime.time(15, 10)
        sq_off_time = datetime.time(15, 0) # Square off at 3 PM as per user logic

        # Square off at 3:00 PM
        if now_time >= sq_off_time:
            if self.position == 1:
                self.log("Closing LONG position (Intraday Square-off)")
                if self.sell(qty=self.qty):
                    self.position = 0
            elif self.position == -1:
                self.log("Closing SHORT position (Intraday Square-off)")
                if self.buy(qty=self.qty):
                    self.position = 0
            return

        # 5. Exit Logic
        if self.position == 1: # Long
            # Exit if MACD crosses below Signal
            if prev['macd'] >= prev['signal'] and curr['macd'] < curr['signal']:
                self.log("Exiting LONG (MACD Cross Down)")
                if self.sell(qty=self.qty):
                    self.position = 0
                    
        elif self.position == -1: # Short
            # Exit if MACD crosses above Signal
            if prev['macd'] <= prev['signal'] and curr['macd'] > curr['signal']:
                self.log("Exiting SHORT (MACD Cross Up)")
                if self.buy(qty=self.qty):
                    self.position = 0

        # 6. Entry Logic (Only between 9:16 AM and 3:10 PM)
        if start_time <= now_time <= no_new_trade_time and self.position == 0:
            # LONG Entry: Supertrend flips to Up (-1 -> 1) AND MACD > Signal
            if prev['st_dir'] == -1 and curr['st_dir'] == 1 and curr['macd'] > curr['signal']:
                self.log("Entering LONG (Supertrend Flip + MACD Confirmation)")
                if self.buy(qty=self.qty):
                    self.position = 1
                    self.last_processed_candle = current_candle_ts

            # SHORT Entry: Supertrend flips to Down (1 -> -1) AND MACD < Signal
            elif prev['st_dir'] == 1 and curr['st_dir'] == -1 and curr['macd'] < curr['signal']:
                self.log("Entering SHORT (Supertrend Flip + MACD Confirmation)")
                if self.sell(qty=self.qty):
                    self.position = -1
                    self.last_processed_candle = current_candle_ts

    def _prepare_candles(self, history):
        df = history.copy()
        try:
            # Robust datetime conversion (copied from simple_sma.py logic)
            if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
                def parse_ts(ts):
                    try:
                        if isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.replace('.','',1).isdigit()):
                            num = float(ts)
                            return pd.to_datetime(num, unit='ms' if num > 1e12 else 's', utc=True)
                        return pd.to_datetime(ts, utc=True)
                    except:
                        return pd.NaT
                df['dt'] = df['timestamp'].apply(parse_ts)
            else:
                df['dt'] = df['timestamp']
            
            df.set_index('dt', inplace=True)
            df.sort_index(inplace=True)
            
            # Resample to 15-minute candles
            resampled = df['ltp'].resample('15min').ohlc().dropna()
            return resampled
        except Exception as e:
            self.log(f"Resampling error: {e}")
            return None

    def _calculate_indicators(self, df):
        # MACD
        ema_fast = df['close'].ewm(span=self.macd_fast, adjust=False).mean()
        ema_slow = df['close'].ewm(span=self.macd_slow, adjust=False).mean()
        df['macd'] = ema_fast - ema_slow
        df['signal'] = df['macd'].ewm(span=self.macd_signal, adjust=False).mean()
        
        # Supertrend
        # Manual calculation to avoid extra dependencies
        high = df['high']
        low = df['low']
        close = df['close']
        
        # ATR
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=self.st_period).mean()
        
        hl2 = (high + low) / 2
        basic_upper = hl2 + (self.st_multiplier * atr)
        basic_lower = hl2 - (self.st_multiplier * atr)
        
        final_upper = basic_upper.copy()
        final_lower = basic_lower.copy()
        
        # Calculate final bands
        for i in range(1, len(df)):
            # Final Upper
            if basic_upper.iloc[i] < final_upper.iloc[i-1] or close.iloc[i-1] > final_upper.iloc[i-1]:
                final_upper.iloc[i] = basic_upper.iloc[i]
            else:
                final_upper.iloc[i] = final_upper.iloc[i-1]
                
            # Final Lower
            if basic_lower.iloc[i] > final_lower.iloc[i-1] or close.iloc[i-1] < final_lower.iloc[i-1]:
                final_lower.iloc[i] = basic_lower.iloc[i]
            else:
                final_lower.iloc[i] = final_lower.iloc[i-1]
                
        # Direction and Supertrend Line
        st_dir = np.where(df.index == df.index[0], 1, 0)
        supertrend = np.zeros(len(df))
        
        # Initialize first bar logic
        st_dir[0] = 1 if close.iloc[0] > basic_upper.iloc[0] else -1
        supertrend[0] = final_lower.iloc[0] if st_dir[0] == 1 else final_upper.iloc[0]

        for i in range(1, len(df)):
            prev_dir = st_dir[i-1]
            if prev_dir == -1 and close.iloc[i] > final_upper.iloc[i]:
                st_dir[i] = 1
            elif prev_dir == 1 and close.iloc[i] < final_lower.iloc[i]:
                st_dir[i] = -1
            else:
                st_dir[i] = prev_dir
                
            supertrend[i] = final_lower.iloc[i] if st_dir[i] == 1 else final_upper.iloc[i]
            
        df['st_dir'] = st_dir
        df['supertrend'] = supertrend
        
        return df
