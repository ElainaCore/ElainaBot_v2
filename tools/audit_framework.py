"""全量受版本控制文件检查；不执行插件、启动服务或读取部署凭据。"""
import ast
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml


def main():
    root = Path(__file__).resolve().parents[1]
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    records = []
    errors = []
    for name in filter(None, paths):
        path = root / name
        data = path.read_bytes()
        record = {'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            record['check'] = 'binary: hash only; behavior not verified'
        else:
            record['check'] = 'text decoded'
            try:
                if path.suffix == '.py':
                    ast.parse(text, filename=name)
                    record['check'] = 'Python AST parsed'
                elif path.suffix == '.js' and shutil.which('node'):
                    checked = subprocess.run(
                        ['node', '--input-type=module', '--check'], input=text,
                        capture_output=True, text=True, encoding='utf-8',
                    )
                    if checked.returncode:
                        errors.append({'path': name, 'error': checked.stderr})
                    record['check'] = 'JavaScript syntax checked'
                elif path.suffix == '.json':
                    json.loads(text)
                    record['check'] = 'JSON parsed'
                elif path.suffix in {'.yaml', '.yml'}:
                    yaml.safe_load(text)
                    record['check'] = 'YAML parsed'
                elif path.suffix == '.toml':
                    tomllib.loads(text)
                    record['check'] = 'TOML parsed'
            except (SyntaxError, ValueError, yaml.YAMLError) as exc:
                errors.append({'path': name, 'error': str(exc)})
        records.append(record)
    result = {'files': records, 'errors': errors}
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Checked {len(records)} tracked files; {len(errors)} parse errors')
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
