import concurrent.futures
import json
import os
import tempfile

from core.model.bars import CandleBar
from runtime.store.ssot_jsonl import JsonlAppender

def test_jsonl_parallel_append():
    with tempfile.TemporaryDirectory() as tmp:
        def worker(worker_id: int):
            # Each worker creates its OWN appender (simulating multiple processes or threads)
            # Actually, inside the same process, we can just use the same appender, or multiple appenders to same dir.
            appender = JsonlAppender(tmp)
            for i in range(2000):
                bar = CandleBar(
                    symbol="TEST/USD",
                    tf_s=60,
                    open_time_ms=i * 60000, 
                    close_time_ms=(i+1)*60000,
                    o=1.0, h=2.0 + (worker_id/10.0), low=0.5, c=1.5,
                    v=worker_id,
                    complete=True,
                    src="history",
                    extensions={"notes": "some longer text to make the payload bigger and increase race chance"}
                )
                appender.append(bar)
            appender.close()
                
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker, w) for w in range(10)]
            for f in futures:
                f.result()
                
        files_found = []
        for root, dirs, files in os.walk(tmp):
            for file in files:
                if file.endswith('.jsonl'):
                    files_found.append(os.path.join(root, file))
                    
        assert len(files_found) > 0, f"Expected at least 1 file, found {len(files_found)}"
        
        valid_lines = 0
        corrupt_lines = 0
        # The file size or contents should contain exactly 20000 lines if no lines were lost.
        for file_path in files_found:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line_idx, line in enumerate(f):
                    try:
                        json.loads(line)
                        valid_lines += 1
                    except Exception as e:
                        print(f"Corruption at line {line_idx} in {file_path}: {line.strip()!r}")
                        corrupt_lines += 1
                    
        print(f"Total lines correctly parsed: {valid_lines}")
        print(f"Total corrupt lines: {corrupt_lines}")
        
        if corrupt_lines > 0 or valid_lines < 20000:
            print("CONFIRMED: JSONL corruption or lost lines detected.")
        else:
            print("NOT_A_BUG: No corruption with basic threading. Let's try multiprocessing or larger payloads.")

if __name__ == "__main__":
    test_jsonl_parallel_append()
