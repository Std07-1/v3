import urllib.request
r = urllib.request.urlopen('http://127.0.0.1:8000/')
html = r.read().decode()
import re
m = re.search(r'index-[^"]+\.js', html)
print("SERVED:", m.group(0) if m else "NOT FOUND")
