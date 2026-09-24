"""Register the native host for this Windows user only."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

def main():
    if os.name != 'nt':
        raise RuntimeError('This installer is for Windows.')
    import winreg
    import hid
    root = Path(__file__).resolve().parent
    print('Load the extension folder as an unpacked extension first.')
    extension_id = input('Paste the 32-character extension ID: ').strip()
    if not re.fullmatch('[a-p]{32}', extension_id):
        raise ValueError('Invalid extension ID.')
    native = root / 'native'
    launcher = native / 'run-host.cmd'
    manifest = native / 'host-manifest.json'
    command = subprocess.list2cmdline([sys.executable, '-u', str(native / 'host.py')])
    if '%' in command or '\n' in command or '\r' in command:
        raise ValueError('Unpack to a simple path with no percent signs.')
    launcher.write_text('@echo off\n' + command + '\n', encoding='utf-8')
    data = {'name': 'local.huntsman.readonly', 'description': 'Huntsman read-only diagnostic bridge',
            'path': str(launcher), 'type': 'stdio', 'allowed_origins': [f'chrome-extension://{extension_id}/']}
    manifest.write_text(json.dumps(data, indent=2), encoding='utf-8')
    for browser in ('Google\\Chrome', 'Microsoft\\Edge'):
        key_path = f'Software\\{browser}\\NativeMessagingHosts\\local.huntsman.readonly'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, str(manifest))
    print('Registered for the current Windows user (Chrome and Edge).')
    print('Close desktop Synapse. Open one tab at https://synapse.razer.com.')
    print('Log:', native / 'diagnose.log')

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Install failed:', error)
        sys.exit(1)
