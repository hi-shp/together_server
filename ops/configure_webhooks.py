"""Move existing webhook literals into .env locally, without printing values."""
import argparse
import ast
from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess

from dotenv import dotenv_values, set_key


MAPPINGS = {
    'DISCORD_LOG_WEBHOOK_URL': ('run_main.py', 'webhook_url'),
    'DISCORD_NIGHT_WEBHOOK_URL': ('run_main.py', 'WEBHOOK_URL'),
    'DISCORD_MESSAGE_WEBHOOK_URL': ('selenium_service.py', 'message_bot_url'),
    'DISCORD_EMAIL_WEBHOOK_URL': ('selenium_service.py', 'email_bot_url'),
}


def literal_value(source, name):
    values = {node.value.value for node in ast.walk(ast.parse(source))
              if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
              and isinstance(node.value.value, str)
              and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)}
    if len(values) > 1:
        raise ValueError(f'Ambiguous legacy setting: {name}')
    return next(iter(values), None)


def migrate(target, sources):
    target = Path(target)
    if target.is_symlink() or not target.is_file():
        raise ValueError('Expected an existing regular .env file')
    previous = dotenv_values(target, interpolate=False)
    updates = {}
    for key, (filename, variable) in MAPPINGS.items():
        if previous.get(key):
            continue
        value = literal_value(sources[filename], variable)
        if not value:
            raise ValueError(f'Missing legacy setting: {key}')
        updates[key] = value
    if not updates:
        print('Existing webhook settings retained; no changes required.', flush=True)
        return
    backup = target.with_name('.env.backup.' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(target.read_bytes())
    try:
        os.chmod(target, 0o600)
        for key, value in updates.items():
            set_key(str(target), key, value)
        actual = dotenv_values(target, interpolate=False)
        if any(actual.get(key) != value for key, value in updates.items()):
            raise ValueError('Webhook migration verification failed')
        if any(actual.get(key) != value for key, value in previous.items() if key not in updates):
            raise ValueError('Existing environment settings changed')
        os.chmod(target, 0o600)
    except BaseException:
        shutil.copyfile(backup, target)
        os.chmod(target, 0o600)
        raise
    print(f'Migrated {len(updates)} existing webhook settings; values verified and not displayed.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--from-ref', help='Local Git revision containing the original code')
    args = parser.parse_args()
    sources = {}
    for filename in {item[0] for item in MAPPINGS.values()}:
        if args.from_ref:
            sources[filename] = subprocess.check_output(
                ['git', 'show', f'{args.from_ref}:{filename}'], cwd=args.root).decode('utf-8')
        else:
            sources[filename] = (args.root / filename).read_text(encoding='utf-8')
    migrate(args.root / '.env', sources)


if __name__ == '__main__':
    main()
