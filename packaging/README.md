# PowerDNS Recursor, packaged for Debian (welland)

This is [mithro/apt-repo-action](https://github.com/mithro/apt-repo-action)'s
**Set A** layout (`docs/packaging.md`): a GitHub fork of
[PowerDNS/pdns](https://github.com/PowerDNS/pdns) with

- `upstream`: an unmodified copy of upstream's `rel/rec-5.4.x`, the
  recursor 5.4 release branch, only ever fast-forwarded;
- `packaging` (the default branch): `upstream` plus our changes, `debian/`,
  `packaging/` and `.github/workflows/`.

Upstream's repository holds three products (the authoritative server,
dnsdist and the recursor). Only the **recursor** is packaged here:
`pdns-recursor`, the name Debian uses, so this build replaces Debian's on
upgrade.

## What is built

Upstream's recursor **5.4.6** (tag `rec-5.4.6`), the release Debian has in
forky and sid, plus three changes so that **every ServFail the recursor
sends carries an Extended DNS Error** (RFC 8914):

1. the extended error of a failed CNAME chase is kept. The chase ran in a
   fresh resolver context whose error was thrown away, so a name whose CNAME
   target could not be reached came back as a bare ServFail;
2. the extended error is kept when a resolve ends in
   `ImmediateServFailException` (the total-time limit sets one, then throws);
3. a ServFail that still has none gets the generic `Other` error.

Why it matters: systemd-resolved accepts a ServFail with an extended error
as the final answer. One without makes it retry with fewer EDNS features,
and when the retry without the DO bit succeeds it stops asking that server
for DNSSEC records at all. With local trust anchors, every lookup under the
anchored zones then fails validation, for hours. On ten64.welland that was
eight outages in two weeks, each started by one CNAME into a zone whose
forwarder timed out.

## Where `debian/` comes from

Debian's packaging, from
[salsa.debian.org/dns-team/pdns-recursor](https://salsa.debian.org/dns-team/pdns-recursor)
at `f872cd2` (tag `debian/5.4.6-1`), imported unchanged with its changelog
history, then adapted in separate commits. Debian builds the recursor's
release tarball; this builds upstream's git tree, where the recursor is the
directory `pdns/recursordist` and the files upstream generates for the
tarball are missing:

- `dh` builds with `--sourcedirectory=pdns/recursordist`, and the paths in
  `debian/rules` and the install lists follow;
- `BUILDER_VERSION` gives upstream's `version.sh` the package's upstream
  version (it would otherwise describe our branch);
- `pubsuffix.cc` is made from Debian's `publicsuffix` package
  (`debian/mkpubsuffixcc`), not downloaded during the build;
- the **man pages** (`pdns_recursor.1`, `rec_control.1`) are the release
  tarball's, committed under `pdns/recursordist/`: meson would otherwise
  generate them with pip, from the network;
- no LTO across Rust and C++ (`RUSTFLAGS=-Clinker-plugin-lto` is dropped):
  it only links when rustc, clang and lld share an LLVM version, and in
  forky and sid they do not, so Debian's own 5.4.6-1 fails to build there;
- Debian's `testrunner-log-verbosity` patch is dropped: it changes the
  autotools build, which is not used;
- our `Maintainer:` and `Vcs-*`; Debian's maintainer is kept as
  `XSBC-Original-Maintainer:`.

## Our changes to upstream's code

Each is a commit on `packaging` with an `Upstream:` trailer saying where it
stands upstream. See
`git log upstream..packaging -- ':!debian' ':!packaging' ':!.github'`.

`packaging/ede-cname-chase-test.py` reproduces the fault on loopback
against any `pdns_recursor` binary, and the install test runs it against
the built package. Stock 5.4.6 fails it:

    $ python3 packaging/ede-cname-chase-test.py --binary /usr/sbin/pdns_recursor --expect ede
    control, target asked directly: y.b.test. -> SERVFAIL answer=[] EDE=['22 delegation b.test']
    CNAME from the network: x.a.test. -> SERVFAIL answer=['x.a.test. 60 IN CNAME y.b.test.'] EDE=[]
      MISMATCH: expected ede

## Version

apt-repo-action's shared `scripts/deb-version.py` makes it
(`docs/packaging.md`, "Set A"); `version-args` in
`.github/workflows/deb.yml` asks for the Set A form and limits upstream's
tags to the recursor's (`rec-*`).

    5.4.6-1+welland<M>[~deb<R>][~pr<P>]

While `packaging` is built on the release `debian/changelog` is Debian's
for, the base is Debian's version (`5.4.6-1`), so ours sorts above Debian's
build of that release. `M` is the number of commits on `packaging` that
aren't on `upstream`; `~deb<R>` is the suite's Debian release (sid has
none) and `~pr<P>` a pull request's preview.

Once a newer upstream release is merged the base is `<release>-0`
(`5.4.7-0+welland<M>`), which sorts below Debian's own `5.4.7-1`. A host
that must keep this build then needs Debian's pinned away:

    Package: pdns-recursor
    Pin: release o=Debian
    Pin-Priority: -1

Bringing `debian/` up to Debian's packaging of the new release (and its
changelog entry) makes the base `5.4.7-1` again.

## Updating to a new upstream

`Sync upstream` (`.github/workflows/sync-upstream.yml`) runs weekly: it
fast-forwards `upstream` to upstream's `rel/rec-5.4.x`, copies the `rec-*`
tags, and opens the pull request "Merge upstream <describe>" into
`packaging`. Before merging it:

1. replace the committed man pages with the new release tarball's
   (`https://downloads.powerdns.com/releases/pdns-recursor-<version>.tar.xz`,
   `pdns_recursor.1` and `rec_control.1` at its root), with `git add -f`;
2. check Debian's packaging repository for a `debian/<version>-1` tag, and
   merge what changed in its `debian/`.

Merge with a merge commit, never a rebase. A new minor series
(`rel/rec-5.5.x`) is a change to `UPSTREAM_BRANCH` in the workflow: the
`upstream` branch cannot fast-forward from one release branch to another,
so it is a decision for a person. By hand:

```sh
git fetch https://github.com/PowerDNS/pdns.git rel/rec-5.4.x 'refs/tags/rec-*:refs/tags/rec-*'
git push origin FETCH_HEAD:refs/heads/upstream 'refs/tags/rec-*:refs/tags/rec-*'
git checkout -b sync/upstream origin/packaging
git merge --no-ff origin/upstream
```

Upstream's own workflows (`build-and-test-all.yml` and the rest) are
disabled in this repository's Actions settings, not deleted, so merging
upstream never conflicts on them.

## Install

```sh
sudo install -d -m0755 /etc/apt/keyrings
curl -fsSL https://mith.ro/pdns/pdns.gpg | sudo tee /etc/apt/keyrings/pdns.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/pdns.gpg] https://mith.ro/pdns/sid/ ./" \
  | sudo tee /etc/apt/sources.list.d/pdns.list
sudo apt update
```

Put your suite in place of `sid`: `forky` or `sid`.

The repository's signing key fingerprint is
`2F29 4F27 0937 CB6B 71D8  329B 99AF DDE5 257B F7F8`.
