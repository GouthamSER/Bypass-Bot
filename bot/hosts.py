"""Host recognition and display names. These labels do not imply API access."""
from urllib.parse import urlsplit

HOST_LABELS = {
    "gofile.io": "GoFile", "gofile.com": "GoFile",
    "pixeldrain.com": "Pixeldrain", "drive.google.com": "Google Drive",
    "docs.google.com": "Google Drive", "googleusercontent.com": "Google Drive",
    "mediafire.com": "MediaFire", "1fichier.com": "1Fichier",
    "mega.nz": "MEGA", "mega.co.nz": "MEGA",
    "terabox.com": "TeraBox", "1024terabox.com": "TeraBox",
    "teraboxapp.com": "TeraBox", "send.cm": "Send.cm",
    "krakenfiles.com": "KrakenFiles", "qiwi.gg": "Qiwi",
    "rapidgator.net": "Rapidgator", "nitroflare.com": "NitroFlare",
    "turbobit.net": "Turbobit", "uploadgig.com": "UploadGig",
    "uptobox.com": "Uptobox", "multiup.io": "MultiUp",
    "drop.download": "Drop.download", "1cloudfile.com": "1CloudFile",
    "buzzheavier.com": "BuzzHeavier", "filepress.to": "FilePress",
    "gdflix.io": "GDFlix", "gdflix.dev": "GDFlix", "gdflix.net": "GDFlix",
    "gdtot.com": "GDTot", "gdtot.dad": "GDTot",
    "zippyshare.com": "Zippyshare",
    "dropbox.com": "Dropbox", "1drv.ms": "OneDrive",
    "onedrive.live.com": "OneDrive", "archive.org": "Internet Archive",
    "wetransfer.com": "WeTransfer", "anonfiles.com": "AnonFiles",
    "messycloud.ink": "MessyCloud", "juicybits.site": "JuicyBits",
    "file.io": "File.io", "userscloud.com": "UsersCloud",
}


def hostname(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def is_domain(host: str, domain: str) -> bool:
    """True only for an exact domain or a real subdomain (not substring matches)."""
    host, domain = host.rstrip(".").lower(), domain.rstrip(".").lower()
    return bool(host and domain and (host == domain or host.endswith("." + domain)))


def host_label(url: str) -> str:
    host = hostname(url)
    for domain, label in HOST_LABELS.items():
        if is_domain(host, domain):
            return label
    return host or "Download link"
