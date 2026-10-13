"""Import results and inspect shared data from the project root."""
import argparse
import json
from pathlib import Path
from adc_shared.client import DataClient

parser = argparse.ArgumentParser()
parser.add_argument('--url', default=None)
commands = parser.add_subparsers(dest='command', required=True)
imp = commands.add_parser('import')
imp.add_argument('json_file', type=Path)
imp.add_argument('--run-id', required=True, help='Stable ID for safe retries; a new ID for each new run')
commands.add_parser('runs')
cases = commands.add_parser('reviews')
cases.add_argument('run_id')
args = parser.parse_args()
client = DataClient(args.url)
if args.command == 'import':
    result = client.save_run(json.loads(args.json_file.read_text(encoding='utf-8-sig')), args.run_id)
elif args.command == 'runs':
    result = client.request('GET', '/runs')
else:
    result = list(client.review_cases(args.run_id))
print(json.dumps(result, ensure_ascii=False, indent=2))
