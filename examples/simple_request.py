"""
Simple example of using Equinox programmatically
"""

from equinox import HTTPClient, Request
from equinox.auth import BearerAuth

# Create HTTP client
client = HTTPClient()

# Example 1: Simple GET request
print("=" * 60)
print("Example 1: Simple GET request")
print("=" * 60)

request = Request(method="GET", url="https://httpbin.org/get")
response = client.send(request)

print(f"Status: {response.status_code} {response.reason}")
print(f"Response time: {response.elapsed:.3f}s")
print(f"Content-Type: {response.content_type}")
print()

# Example 2: POST request with JSON body
print("=" * 60)
print("Example 2: POST request with JSON")
print("=" * 60)

request = Request(
    method="POST",
    url="https://httpbin.org/post",
    headers={"Content-Type": "application/json"},
    body='{"name": "Equinox", "version": "0.1.0"}',
)
response = client.send(request)

print(f"Status: {response.status_code}")
if response.is_json:
    data = response.json()
    print(f"Echoed data: {data.get('json', {})}")
print()

# Example 3: Request with query parameters
print("=" * 60)
print("Example 3: Request with query parameters")
print("=" * 60)

request = Request(
    method="GET",
    url="https://httpbin.org/get",
    params={"foo": "bar", "test": "value"},
)
response = client.send(request)

print(f"Status: {response.status_code}")
if response.is_json:
    data = response.json()
    print(f"Query params: {data.get('args', {})}")
print()

# Example 4: Request with authentication
print("=" * 60)
print("Example 4: Request with Bearer authentication")
print("=" * 60)

auth = BearerAuth("test-token-12345")
request = Request(
    method="GET",
    url="https://httpbin.org/bearer",
    auth=auth,
)
response = client.send(request)

print(f"Status: {response.status_code}")
print(f"Response: {response.text[:100]}...")
print()

# Example 5: Request with custom headers
print("=" * 60)
print("Example 5: Request with custom headers")
print("=" * 60)

request = Request(
    method="GET",
    url="https://httpbin.org/headers",
    headers={
        "User-Agent": "Equinox/0.1.0",
        "X-Custom-Header": "Custom-Value",
    },
)
response = client.send(request)

print(f"Status: {response.status_code}")
if response.is_json:
    data = response.json()
    print(f"Received headers: {list(data.get('headers', {}).keys())[:5]}")
print()

# Example 6: Convert request to curl
print("=" * 60)
print("Example 6: Convert request to curl command")
print("=" * 60)

request = Request(
    method="POST",
    url="https://api.example.com/users",
    headers={"Content-Type": "application/json"},
    body='{"name": "John Doe"}',
)

print(request.to_curl())
print()

print("✅ All examples completed!")
