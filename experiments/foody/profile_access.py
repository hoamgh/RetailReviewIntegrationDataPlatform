"""Reuse only the dedicated manually signed-in Foody profile, never personal Chrome."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import crawl
from storage_session import AUTH_JS, SEED


def validate_profile(path):
    path = Path(path).resolve()
    base = (Path(os.environ['LOCALAPPDATA']) / 'FnbFoodyResearch').resolve()
    if path.parent != base or not re.fullmatch(r'manual-foody-\d{8}-\d{6}', path.name):
        raise ValueError('Only the task-specific manually created Foody profile is allowed')
    if not path.is_dir():
        raise ValueError('Manual Foody profile missing')
    return path


def profile_owners(path):
    """Fail closed on inventory errors; never kill Chrome or delete its locks."""
    executable = shutil.which('pwsh') or shutil.which('powershell')
    if not executable:
        raise RuntimeError('Cannot safely check profile ownership')
    script = r'''
    $ErrorActionPreference = 'Stop'
    $wanted = [IO.Path]::GetFullPath($env:FNB_FOODY_PROFILE_DIR).TrimEnd('\')
    $owners = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
      Where-Object { $_.CommandLine -and $_.CommandLine -notmatch '--type=' } |
      ForEach-Object {
        if ($_.CommandLine -match '--user-data-dir=(?:"([^"]+)"|(\S+))') {
          $candidate = if ($Matches[1]) { $Matches[1] } else { $Matches[2] }
          if ([IO.Path]::GetFullPath($candidate).TrimEnd('\') -ieq $wanted) { $_.ProcessId }
        }
      })
    ConvertTo-Json -InputObject $owners -Compress
    '''
    env = dict(os.environ, FNB_FOODY_PROFILE_DIR=str(path))
    result = subprocess.run([executable, '-NoProfile', '-Command', script], env=env,
                            capture_output=True, text=True, timeout=15, check=True)
    return json.loads(result.stdout)


def available_profile(path):
    path = validate_profile(path)
    if profile_owners(path):
        raise RuntimeError('PROFILE_IN_USE: close only the new dedicated Foody Chrome window first')
    return path


def check_access(path):
    from playwright.sync_api import sync_playwright
    path = available_profile(path)  # No launch or data mutation if still open.
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(path), channel='chrome',
                    headless=False, viewport={'width':1280, 'height':900})
        try:
            page = context.pages[0] if context.pages else context.new_page()
            response = page.goto(SEED, wait_until='domcontentloaded', timeout=30000)
            try:
                page.wait_for_function('''() => [...document.querySelectorAll('header a')]
                    .some(a=>a.querySelector('img')&&/^javascript:/i.test(a.getAttribute('href')||''))''',
                    timeout=15000)
            except Exception:
                pass
            signals = page.evaluate(AUTH_JS)
            parsed = crawl.parse_page(page.content(), SEED, review_limit=0)
            return dict(status='AUTH_REUSE_PASS' if signals['logged_in'] and parsed['links'] else 'AUTH_REUSE_UNVERIFIED',
                        account_recognized=signals['logged_in'], http_status=response.status if response else None,
                        discovered_links=len(parsed['links']), review_samples=0, pages_visited=1,
                        restriction=signals['restriction'], profile='DEDICATED_MANUAL_FOODY')
        finally:
            # This is only the context this command launched after owner closed
            # its manual window, never another open/personal Chrome instance.
            context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-directory', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true', help='Check profile ownership without launching Chrome')
    args = parser.parse_args()
    try:
        if args.check_only:
            path = validate_profile(args.profile_directory)
            result = dict(status='PROFILE_IN_USE' if profile_owners(path) else 'PROFILE_AVAILABLE',
                          browser_launched=False)
        else:
            result = check_access(args.profile_directory)
    except Exception as exc:
        result = dict(status='BLOCKED', reason='PROFILE_IN_USE' if 'PROFILE_IN_USE' in str(exc)
                      else type(exc).__name__, browser_launched=False)
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0 if result['status'] in ('AUTH_REUSE_PASS', 'PROFILE_AVAILABLE') else 1


if __name__ == '__main__':
    raise SystemExit(main())
