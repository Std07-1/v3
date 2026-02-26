import tempfile
from core.model.bars import CandleBar
from core.derive import GenericBuffer, derive_bar

def test_derive_calendar_pause_flat():
    # 1. Create a GenericBuffer for M1 (60s)
    buf = GenericBuffer(tf_s=60, max_keep=100)
    
    # 2. Add 5 M1 bars to form one M5 bucket (open=0 .. open=240000)
    # 3 bars are real, 2 bars are calendar_pause_flat
    
    # Bucket starts at 0
    b0 = CandleBar("TEST", 60, 0, 60000, 1.0, 2.0, 0.5, 1.5, 100, True, "history")
    b1 = CandleBar("TEST", 60, 60000, 120000, 1.5, 2.5, 1.0, 2.0, 150, True, "history")
    b2 = CandleBar("TEST", 60, 120000, 180000, 2.0, 3.0, 1.5, 2.5, 200, True, "history")
    
    b3 = CandleBar("TEST", 60, 180000, 240000, 2.5, 2.5, 2.5, 2.5, 0, True, "history", {"calendar_pause_flat": True})
    b4 = CandleBar("TEST", 60, 240000, 300000, 2.5, 2.5, 2.5, 2.5, 0, True, "history", {"calendar_pause_flat": True})
    
    buf.upsert_many([b0, b1, b2, b3, b4])
    
    # 3. Derive M5
    def mock_is_trading(t):
        return True # all minutes are trading to has_range logic
        
    derived = derive_bar(
        symbol="TEST",
        target_tf_s=300,
        source_buffer=buf,
        bucket_open_ms=0,
        is_trading_fn=mock_is_trading
    )
    
    print("--- DERIVED M5 ---")
    if derived:
        print(f"tf_s={derived.tf_s}, complete={derived.complete}")
        print(f"open={derived.open_time_ms} close={derived.close_time_ms}")
        print(f"extensions={derived.extensions}")
        
        # Check if derived from 3 bars but marked complete
        if derived.complete and "partial_calendar_pause" in derived.extensions:
            print("CONFIRMED: M5 derived as complete=True from <5 real bars because of calendar_pause_flat dropping.")
    else:
        print("Not derived.")
        
if __name__ == "__main__":
    test_derive_calendar_pause_flat()
