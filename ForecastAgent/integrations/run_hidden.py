"""Start the local synchronization process without creating a console window."""
from datetime import datetime
from pathlib import Path
import subprocess
import sys


def main():
    if len(sys.argv) < 2:
        return 2
    log = Path('E:/metaculus_data/logs/hidden-sync-launcher.log')
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('ab') as output:
        output.write((datetime.now().astimezone().isoformat() + ' Hidden child starting\n').encode())
        output.flush()
        try:
            process = subprocess.Popen(sys.argv[1:], stdout=output, stderr=output,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            code = process.wait()
        except Exception as exc:
            output.write(('Launcher failed: ' + type(exc).__name__ + '\n').encode())
            return 1
        output.write((datetime.now().astimezone().isoformat() + ' Child exit ' + str(code) + '\n').encode())
        return code


if __name__ == '__main__':
    sys.exit(main())
