#!/bin/sh
# Smoke test run by mithro/apt-repo-action's install test, as root in a
# clean container of the suite, after the built packages are installed.
set -eu

pdns_recursor --version
test -x /usr/bin/rec_control

# Debian's packaging is in place: the unit, the configuration, and the man
# pages, which a build from git only has because they are committed
# (packaging/README.md).
test -f /usr/lib/systemd/system/pdns-recursor.service
test -f /etc/powerdns/recursor.conf
dpkg -L pdns-recursor | grep -q '/man1/pdns_recursor\.1'
dpkg -L pdns-recursor | grep -q '/man1/rec_control\.1'

# What this build exists for: a ServFail caused by a failed CNAME chase
# carries an extended DNS error. Runs the installed binary on loopback
# against two fake forwarders.
apt-get install -y --no-install-recommends python3 python3-dnspython
python3 packaging/ede-cname-chase-test.py --binary /usr/sbin/pdns_recursor --expect ede

echo "install test passed"
