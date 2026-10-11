# /// script
# requires-python = ">=3.11"
# dependencies = ["dnspython>=2.6"]
# ///
"""Reproduce the ServFail-without-EDE that pins systemd-resolved, against any pdns_recursor binary.

The fault captured on a production resolver on 2026-10-10 (packaging/README.md):
a name in a forwarded zone is a CNAME into another forwarded zone, the first
hop answers, the second times out, and the recursor replies ServFail carrying
the CNAME but no Extended DNS Error. This script builds exactly that, entirely
on loopback and on its own ports, without touching the production recursor:

  127.0.0.1:5398  fake forwarder for a.test: x.a.test CNAME y.b.test
  127.0.0.1:5397  fake forwarder for b.test: receives queries, never answers
  127.0.0.1:5399  the pdns_recursor under test, forwarding both zones

and asks the recursor for `x.a.test A` with DO set, twice (CNAME from the
network, then from the cache), plus `y.b.test A` directly as the control.

  python3 ede-cname-chase-test.py --binary /usr/sbin/pdns_recursor --expect ede      # this package
  python3 ede-cname-chase-test.py --binary /usr/sbin/pdns_recursor --expect no-ede   # stock 5.4.6

Needs dnspython (python3-dnspython, or `uv run`, which reads the header above).

Exit status 0 when every answer matches --expect, 1 otherwise.
"""
import argparse
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time

import dns.edns
import dns.flags
import dns.message
import dns.query
import dns.rcode
import dns.rdatatype
import dns.rrset

CNAME_PORT = 5398
BLACKHOLE_PORT = 5397
REC_PORT = 5399

CONFIG = """\
incoming:
  listen: ['127.0.0.1']
  port: {rec_port}
  allow_from: ['127.0.0.0/8']
outgoing:
  dont_query: []
dnssec:
  validation: 'off'
packetcache:
  # Otherwise the second x.a.test query is the first answer replayed, and the
  # CNAME-from-the-record-cache path is never exercised.
  disable: true
recursor:
  socket_dir: '{dir}'
  extended_resolution_errors: true
  forward_zones_recurse:
    - zone: a.test
      forwarders: ['127.0.0.1:{cname_port}']
    - zone: b.test
      forwarders: ['127.0.0.1:{blackhole_port}']
logging:
  loglevel: 4
"""


def cname_server(sock: socket.socket) -> None:
    """Answer every query with `<qname> CNAME y.b.test.`, like a recursing forwarder that only knows the first hop."""
    while True:
        try:
            wire, addr = sock.recvfrom(4096)
        except OSError:
            return
        query = dns.message.from_wire(wire)
        reply = dns.message.make_response(query, recursion_available=True)
        qname = query.question[0].name
        reply.answer.append(dns.rrset.from_text(qname, 60, "IN", "CNAME", "y.b.test."))
        sock.sendto(reply.to_wire(), addr)


def blackhole_server(sock: socket.socket) -> None:
    while True:
        try:
            sock.recvfrom(4096)
        except OSError:
            return


def bind(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))
    return sock


def ask(name: str) -> tuple[str, list[str], list[str]]:
    """(rcode, answer rrsets, EDE options) for `name A` with DO, as resolved sends it."""
    query = dns.message.make_query(name, dns.rdatatype.A, want_dnssec=True)
    query.flags |= dns.flags.CD
    reply = dns.query.udp(query, "127.0.0.1", port=REC_PORT, timeout=15)
    edes = [f"{int(o.code)} {o.text or ''}".strip() for o in reply.options if o.otype == dns.edns.OptionType.EDE]
    return dns.rcode.to_text(reply.rcode()), [r.to_text() for r in reply.answer], edes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--binary", required=True, help="pdns_recursor binary to test")
    ap.add_argument("--expect", choices=["ede", "no-ede"], required=True,
                    help="whether the ServFail for the CNAME name should carry an EDE")
    args = ap.parse_args()

    socks = [bind(CNAME_PORT), bind(BLACKHOLE_PORT)]
    threading.Thread(target=cname_server, args=(socks[0],), daemon=True).start()
    threading.Thread(target=blackhole_server, args=(socks[1],), daemon=True).start()

    with tempfile.TemporaryDirectory(prefix="rec-ede-test-") as tmp:
        tmpdir = pathlib.Path(tmp)
        (tmpdir / "recursor.yml").write_text(CONFIG.format(
            dir=tmp, rec_port=REC_PORT, cname_port=CNAME_PORT, blackhole_port=BLACKHOLE_PORT))
        log = (tmpdir / "recursor.log").open("w")
        rec = subprocess.Popen([args.binary, f"--config-dir={tmp}"], stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.time() + 30
            while True:
                if rec.poll() is not None:
                    print((tmpdir / "recursor.log").read_text())
                    print(f"recursor exited with {rec.returncode}")
                    return 1
                try:
                    ask("version.bind.")
                    break
                except Exception:  # not listening yet
                    if time.time() > deadline:
                        print((tmpdir / "recursor.log").read_text())
                        print("recursor did not start answering")
                        return 1
                    time.sleep(0.5)

            version = subprocess.run([args.binary, "--version"], capture_output=True, text=True)
            print((version.stdout + version.stderr).strip().splitlines()[0])

            ok = True
            for label, name in (("control, target asked directly", "y.b.test."),
                                ("CNAME from the network", "x.a.test."),
                                ("CNAME from the cache", "x.a.test.")):
                rcode, answer, edes = ask(name)
                print(f"{label}: {name} -> {rcode} answer={answer} EDE={edes}")
                if rcode != "SERVFAIL":
                    print("  unexpected rcode, the scenario did not reproduce")
                    ok = False
                elif name == "x.a.test.":
                    if not answer:
                        print("  no CNAME in the answer, the scenario did not reproduce")
                        ok = False
                    if bool(edes) != (args.expect == "ede"):
                        print(f"  MISMATCH: expected {args.expect}")
                        ok = False
                elif not edes:
                    print("  the control has no EDE either, so this binary sends none at all")
                    ok = False
            print("PASS" if ok else "FAIL")
            return 0 if ok else 1
        finally:
            rec.terminate()
            try:
                rec.wait(timeout=10)
            except subprocess.TimeoutExpired:
                rec.kill()
            for sock in socks:
                sock.close()


if __name__ == "__main__":
    sys.exit(main())
