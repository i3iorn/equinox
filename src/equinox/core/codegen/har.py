import json

from equinox.core.request import Response
from equinox.versioning import get_app_version


class HARGenerator:
    def generate(self, response: Response) -> str:
        request = response.request

        har = {
            "log": {
                "version": get_app_version(),
                "creator": {"name": "Equinox", "version": "2.0"},
                "entries": [
                    {
                        "startedDateTime": response.timestamp.isoformat(),
                        "time": response.elapsed * 1000,
                        "request": {
                            "method": request.method,
                            "url": response.sent_url or request.url,
                            "httpVersion": "HTTP/1.1",
                            "headers": [
                                {"name": k, "value": v} for k, v in (request.headers or {}).items()
                            ],
                            "queryString": [
                                {"name": k, "value": v} for k, v in (request.params or {}).items()
                            ],
                            "postData": {
                                "mimeType": request.headers.get("Content-Type", ""),
                                "text": request.body or "",
                            },
                        },
                        "response": {
                            "status": response.status_code,
                            "statusText": response.reason,
                            "httpVersion": "HTTP/1.1",
                            "headers": [
                                {"name": k, "value": v} for k, v in response.headers.items()
                            ],
                            "content": {
                                "size": response.size,
                                "mimeType": response.content_type or "",
                                "text": response.text,
                            },
                            "redirectURL": "",
                            "headersSize": -1,
                            "bodySize": response.size,
                        },
                        "timings": response.timings or {},
                    },
                ],
            },
        }
        return json.dumps(har, indent=4)
