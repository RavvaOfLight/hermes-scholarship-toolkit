"""Test helper: run the service on PORT with TOKEN over a scratch data dir seeded from a JSON file."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autofill_server as srv, autofill_store as store
port, token, data_dir, seed = int(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), json.loads(Path(sys.argv[4]).read_text())
store.set_values(seed["values"], data_dir)
for list_id, rows in seed.get("lists", {}).items():
    store.list_add_many(list_id, rows, data_dir=data_dir)
s = srv.make_server(port, token, data_dir)
print("ready", flush=True)
s.serve_forever()
