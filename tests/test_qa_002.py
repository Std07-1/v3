import os
import tempfile
import time

from core.model.bars import CandleBar
from runtime.store.uds import UnifiedDataStore

from runtime.store.ssot_jsonl import JsonlAppender

class MockRedisLayer:
    def __init__(self):
        self.published = []
        self.snapshots = []
        
    def write_preview_curr(self, *args, **kwargs):
        pass
        
    def get_prime_ready_payload(self):
        return {"ready": True}

class MockUpdatesBus:
    def __init__(self):
        self.fail_publish = False
        self.events = []
        
    def read_updates(self, symbol, tf_s, since_seq, limit):
        return [], 0, None, None

def test_uds_split_brain():
    with tempfile.TemporaryDirectory() as tmp:
    
        # Mocking jsonl appender by letting UDS create DiskLayer
        # Actually UDS creates DiskLayer, we just pass data_root
        
        updates_bus = MockUpdatesBus()
        updates_bus.fail_publish = True # Simulate Redis/Updates failure
        
        appender = JsonlAppender(tmp)
        uds = UnifiedDataStore(
            data_root=tmp,
            boot_id="test_boot",
            tf_allowlist={60},
            min_coldload_bars={60: 100},
            role="writer",
            updates_bus=updates_bus,
            jsonl_appender=appender
        )
        
        # Override _publish_update to simulate failure but continue execution
        original_pub = uds._publish_update
        def fake_publish(bar, warnings):
            warnings.append("updates_failed_mock")
            return False
        uds._publish_update = fake_publish
        
        # Override _write_redis_snapshot
        def fake_redis(bar, warnings):
            warnings.append("redis_failed_mock")
            return False
        uds._write_redis_snapshot = fake_redis
        
        bar = CandleBar(
            symbol="TEST/USD",
            tf_s=60,
            open_time_ms=60000, 
            close_time_ms=120000,
            o=1.0, h=2.0, low=0.5, c=1.5,
            v=10,
            complete=True,
            src="history"
        )
        
        result = uds.commit_final_bar(bar)
        
        print(f"CommitResult ok: {result.ok}")
        print(f"SSOT written: {result.ssot_written}")
        print(f"Redis written: {result.redis_written}")
        print(f"Updates published: {result.updates_published}")
        print(f"Watermark advanced to: {uds._wm_by_key.get(('TEST/USD', 60))}")
        
        if result.ok and not result.redis_written and result.ssot_written:
            print("CONFIRMED: Split-brain detected. ok=True when Redis failed.")
        else:
            print("Failed to reproduce split-brain.")

if __name__ == "__main__":
    test_uds_split_brain()
