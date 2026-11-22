"""Print one raw EPC API response, to see the real field names (design doc §5.2).

    python scripts/epc_probe.py "OX4 1AA"                  # search by postcode
    python scripts/epc_probe.py "OX4 1AA" --cert <id>      # one certificate

Reads EPC_API_KEY from the environment or the repo's .env file.
Only look up an address with the person's (or their family's) consent.
"""
import argparse
import json
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from warmly.model.epc_lookup import API_BASE, api_key, headers  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("postcode")
    parser.add_argument("--cert", help="a certificate id from the search results")
    parser.add_argument("--path", default="/api/domestic/search",
                        help="API path to try (the guidance page lists them)")
    args = parser.parse_args()

    key = api_key()
    if not key:
        sys.exit("No EPC_API_KEY in the environment or .env")
    if args.cert:
        url, params = f"{API_BASE}/api/certificate", {"certificate_number": args.cert}
    else:
        url, params = f"{API_BASE}{args.path}", {"postcode": args.postcode}
    response = requests.get(url, params=params, headers=headers(key), timeout=20)
    print(f"{response.status_code} {response.url}")
    try:
        print(json.dumps(response.json(), indent=2))
    except ValueError:
        print(response.text[:2000])


if __name__ == "__main__":
    main()
