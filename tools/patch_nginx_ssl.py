#!/usr/bin/env python3
"""Patch nginx smc config to add SSL for CF Full mode."""

with open("/etc/nginx/sites-enabled/smc") as f:
    c = f.read()

ssl_www = """listen 80;
    listen 443 ssl;
    server_name www.aione-smc.com;

    ssl_certificate /etc/ssl/certs/aione-selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/aione-selfsigned.key;
    ssl_protocols TLSv1.2 TLSv1.3;"""

ssl_main = """listen 80;
    listen 443 ssl;
    server_name aione-smc.com;

    ssl_certificate /etc/ssl/certs/aione-selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/aione-selfsigned.key;
    ssl_protocols TLSv1.2 TLSv1.3;"""

c = c.replace("listen 80;\n    server_name www.aione-smc.com;", ssl_www)
c = c.replace("listen 80;\n    server_name aione-smc.com;", ssl_main)

with open("/etc/nginx/sites-enabled/smc", "w") as f:
    f.write(c)

print("PATCHED OK")
