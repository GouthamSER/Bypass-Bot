from urllib.parse import urlparse

# These are display labels only, not bypass integrations.
HOST_LABELS = {
    "gofile.io": "GoFile",
    "gofile.com": "GoFile",
    "pixeldrain.com": "Pixeldrain",
    "drive.google.com": "Google Drive",
    "docs.google.com": "Google Drive",
    "mediafire.com": "MediaFire",
    "1fichier.com": "1Fichier",
    "mega.nz": "MEGA",
    "terabox.com": "TeraBox",
    "1024terabox.com": "TeraBox",
    "send.cm": "Send.cm",
    "krakenfiles.com": "KrakenFiles",
    "qiwi.gg": "Qiwi",
    "buzzheavier.com": "BuzzHeavier",
    "filepress.to": "FilePress",
    "gdflix": "GDFlix",
    "gdtot": "GDTot",
    "fastcloud": "Fast Cloud",
}


def host_label(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for domain, label in HOST_LABELS.items():
        if host == domain or host.endswith("." + domain) or domain in host:
            return label
    return host or "Download link"
