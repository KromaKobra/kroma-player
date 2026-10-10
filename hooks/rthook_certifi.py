import os
import sys

# In a frozen build, certifi can't find its cacert.pem via the normal package
# path. Point the standard SSL env vars at the bundled copy so that HTTPS
# verifies — Kroma's own requests (updates, lyrics, artwork), and in
# kroma-plugin-host, a plugin's. setdefault, so a plugin process started by
# Kroma keeps the bundle Kroma pointed it at.
if hasattr(sys, "_MEIPASS"):
    cert_path = os.path.join(sys._MEIPASS, "certifi", "cacert.pem")
    if os.path.exists(cert_path):
        os.environ.setdefault("SSL_CERT_FILE", cert_path)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", cert_path)
