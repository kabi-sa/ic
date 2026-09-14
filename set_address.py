"""Point the hub at the address colleagues will actually use.

Every approval e-mail carries a link back into the hub. If the stored address is
`localhost`, that link only ever works on the machine running the hub -- an approver
clicking it from their own PC gets nothing. This writes the right address into the
settings, and can work it out by itself.

    python set_address.py                     # detect this PC's network address
    python set_address.py http://ic-hub:8080  # or state it, if IT gave you a name
"""

import socket
import sys

import db


def detect(port=8080):
    """This machine's address on the office network.

    Opening a UDP socket towards a public address makes Windows pick the interface it
    would really use, which is more reliable than reading the hostname -- that often
    resolves to a loopback or a VPN address instead.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        ip = probe.getsockname()[0]
    except OSError:
        ip = socket.gethostbyname(socket.gethostname())
    finally:
        probe.close()
    return "http://%s:%d" % (ip, port)


def main():
    stated = sys.argv[1] if len(sys.argv) > 1 else None
    url = (stated or detect()).rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url

    conn = db.connect()
    was = db.get_setting(conn, "app_base_url", "")
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('app_base_url',?)", (url,))
    conn.commit()
    conn.close()

    print("  was : %s" % (was or "(not set)"))
    print("  now : %s" % url)
    print()
    if "localhost" in url or "127.0.0.1" in url:
        print("  Careful: that address only works on this PC. Approval links e-mailed to")
        print("  colleagues will not open for them. Run this without arguments to detect")
        print("  the network address instead.")
    else:
        print("  Approval links in e-mails will now open for anyone on the KABi network.")
        print("  Check it from another PC: open %s" % url)


if __name__ == "__main__":
    main()
