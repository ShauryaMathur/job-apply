"""
Save jobright.ai session cookies so the scraper can authenticate.

Step 1 — open Chrome, go to https://jobright.ai/jobs/recommend (logged in)

Step 2 — open DevTools console (F12 → Console) and run:

  copy(JSON.stringify([...document.cookie.split(';').map(c=>{const[n,...v]=c.trim().split('=');return{name:n,value:v.join('='),domain:'.jobright.ai',path:'/',secure:true,httpOnly:false,sameSite:'Lax'}})]))

  This copies the cookies JSON to your clipboard.

Step 3 — run this script and paste when prompted:

  python scripts/save_cookies.py

Step 4 — restart backend:

  docker-compose restart backend
"""

import json
import sys
from pathlib import Path

COOKIES_FILE = Path(__file__).parent.parent / "jobright-profile" / "cookies.json"


def main() -> None:
    print()
    print("Paste the JSON from the DevTools console one-liner, then press Enter:")
    print()

    try:
        raw = input().strip()
    except (EOFError, KeyboardInterrupt):
        sys.exit(0)

    if not raw:
        print("Nothing pasted.")
        sys.exit(1)

    try:
        cookies = json.loads(raw)
        assert isinstance(cookies, list), "Expected a JSON array"
    except Exception as e:
        print(f"Could not parse JSON: {e}")
        sys.exit(1)

    COOKIES_FILE.parent.mkdir(exist_ok=True)
    COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
    print(f"\n✓  Saved {len(cookies)} cookies → {COOKIES_FILE}")
    print("   docker-compose restart backend\n")


if __name__ == "__main__":
    main()
