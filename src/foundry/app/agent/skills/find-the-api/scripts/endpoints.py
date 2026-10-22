"""Lists the endpoints in a recording from `foundry.core record`, one line each, busiest first."""

import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from foundry.core.models import Exchange

TRACKERS = re.compile(
    r"google|doubleclick|gstatic|clarity\.ms|linkedin|licdn|bing\.com|facebook|stripe|sentry|hotjar|segment|"
    r"usermaven|productfruits|newrelic|nr-data|datadog|intercom|hubspot|cloudflareinsights|kernel\.sh"
)
ID = re.compile(r"/(?:\d+|[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9a-f]{24,})(?=/|$)")


def main(recording: Path) -> None:
    seen: dict[tuple[str, str], list[Exchange]] = defaultdict(list)
    for line in filter(None, recording.read_text().split("\n")):
        exchange = Exchange.model_validate_json(line)
        parts = urlsplit(exchange.url)
        if exchange.kind not in ("document", "xhr", "fetch") or TRACKERS.search(parts.netloc):
            continue
        seen[(exchange.method, parts.netloc + ID.sub("/{id}", parts.path))].append(exchange)
    for (method, endpoint), calls in sorted(seen.items(), key=lambda item: -len(item[1])):
        statuses = ",".join(sorted({str(call.status) for call in calls}))
        kind = calls[0].headers.get("content-type", "").split(";")[0]
        print(f"{len(calls):>4}  {method:<6} {endpoint}  [{statuses}] {kind}\n        e.g. {calls[-1].url}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
