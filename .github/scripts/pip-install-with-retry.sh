#!/usr/bin/env bash
# Install one published requirement, retrying while PyPI's index catches up (#1111).
#
# `publish.yml`'s verify job waits for PyPI to serve a release and then installs
# it. Those two reads are of DIFFERENT surfaces. The wait polls the JSON API,
# `https://pypi.org/pypi/<project>/<version>/json`; `pip` resolves through the
# simple index, `https://pypi.org/simple/<project>/`, which is served from its
# own cache and lags behind it.
#
# Cutting v1.21.0 that gap was twenty-five seconds wide. Both distributions were
# listed on attempt 1, both artifacts hashed to the digests PyPI declares for
# them, and `pip install keel-workflow==1.21.0` then reported a version list
# ending at 1.20.0. The job failed, `release-broken: v1.21.0` (#1110) was filed
# automatically against a release that was correct, and a plain re-run of the
# same job passed with nothing changed.
#
# So the five-minute PYPI_WAIT_* budget had been spent watching a surface that
# was already ready, and the surface that was not ready got exactly one attempt.
# This script gives the budget to the install. The install is the assertion — an
# index that cannot resolve the version *yet* is a wait, not a result — so
# retrying the assertion is what closes the gap. Gating on a third surface
# (`pip index versions`, or a scratch `pip download --dry-run`) would only narrow
# it: nothing binds the index page one request reads to the page the next request
# resolves against, and it would put an explicitly experimental subcommand's
# output between a correct release and a green job.
#
# It is a wait and not a softener. When the budget is genuinely spent this exits
# non-zero with an `::error::` naming the requirement, the attempts made and the
# elapsed seconds — which fails the step, fails the job, and files the report.
# There is no path through this file that reports success without an install.
#
# Every attempt's own output goes to stdout rather than being captured, because
# the failure this has to stay diagnosable for is the other one: a release whose
# wheel really is broken now retries for the full budget and then fails with the
# installer's own words in the log the `release-broken` issue quotes. Telling the
# two apart by *parsing* pip's error text was the alternative — it would save
# five minutes on a job that is about to fail anyway, in exchange for a guess
# about wording that changes between pip releases, and a wrong guess turns a real
# defect back into a five-minute wait ending in the wrong error.
#
# Inputs arrive through the environment, never through an Actions `${{ }}`
# expression: those are substituted into this file's source before bash parses
# it, so a value containing a quote would be a syntax error rather than a string.
#
#   INSTALLER            the pip to run                     (required)
#   REQUIREMENT          what to install, e.g. `pkg==1.2.3` (required)
#   PYPI_WAIT_ATTEMPTS   install attempts                   (default: 20)
#   PYPI_WAIT_SECONDS    seconds between attempts           (default: 15)
#
# `INSTALLER` and not `PIP_…`: pip reads every `PIP_<OPTION>` variable in the
# environment as one of its own command-line options, so a name in that space
# would be passed straight back into the thing it names.
#
# The two budget variables are the verify job's own, read here under the same
# names the JSON-API wait reads them under, so one edit moves both waits and
# neither can quietly be given different patience. The defaults match the job's
# 20 x 15s = five minutes for a caller that sets neither.
set -euo pipefail

installer="${INSTALLER:-}"
requirement="${REQUIREMENT:-}"
attempts="${PYPI_WAIT_ATTEMPTS:-20}"
interval="${PYPI_WAIT_SECONDS:-15}"

# Exit 2, distinct from the exhausted-budget 1: a mistake in the call is not a
# slow index and must not be reported as one. A budget spent waiting for
# something nobody asked for, ending in an error that blames PyPI, is the second
# failure mode this file exists to avoid.
fail_config() {
  echo "::error title=pip-install-with-retry.sh is misconfigured::$1"
  exit 2
}

[ -n "$installer" ] || fail_config "INSTALLER is empty; there is no installer to run"
[ -n "$requirement" ] || fail_config "REQUIREMENT is empty; there is nothing to install"

# An installer that cannot run is a configuration mistake, not a slow index.
# Undiagnosed it exits 127 from every attempt, which the loop below would read as
# "the index has not caught up yet" — burning the whole budget and then blaming
# PyPI for a missing pip.
command -v "$installer" >/dev/null 2>&1 \
  || fail_config "INSTALLER='${installer}' is not an executable this runner can find"

# A count that is not a count must be diagnosed rather than left to `seq`, whose
# complaint is about an operand and reads like a broken index rather than a
# typo'd knob.
require_whole_number() {
  case "$2" in
    "" | *[!0-9]*) fail_config "$1 must be a whole number, not '$2'" ;;
  esac
}

require_whole_number PYPI_WAIT_ATTEMPTS "$attempts"
require_whole_number PYPI_WAIT_SECONDS "$interval"

# Base ten explicitly: `08` is a valid attempt count and an invalid octal literal.
attempts="$((10#$attempts))"
interval="$((10#$interval))"

# Zero attempts would fall straight past the loop into the failure below, so it
# is already loud — but it would fail every release with a message naming PyPI
# for a knob nobody meant to set to nothing. It is refused here instead, where
# the error can name the knob.
[ "$attempts" -ge 1 ] \
  || fail_config "PYPI_WAIT_ATTEMPTS must be at least 1, not '${attempts}'"

started="$(date +%s)"

for attempt in $(seq 1 "$attempts"); do
  # `--no-cache-dir`, or the retry is decorative. PyPI serves the simple index
  # with `cache-control: max-age=600, public`, and pip's HTTP cache honours that
  # without revalidating — so attempt 1 records the very version list that is
  # missing the release, and every attempt inside the next ten minutes reads
  # that failure back off local disk without opening a connection. The whole
  # budget here is 285 s, comfortably inside the window, so the loop would have
  # retried the answer instead of the question. It also means each attempt is a
  # real download, which is the thing this job claims to be verifying.
  if "$installer" install --no-cache-dir --disable-pip-version-check "$requirement"; then
    echo "installed ${requirement} on attempt ${attempt}/${attempts}"
    exit 0
  fi
  # No sleep after the last attempt: the budget is the waiting between tries, and
  # a trailing one only delays the failure it has already decided on.
  if [ "$attempt" -lt "$attempts" ]; then
    echo "the index cannot resolve ${requirement} yet (attempt ${attempt}/${attempts}); retrying in ${interval}s"
    sleep "$interval"
  fi
done

# The first thing a maintainer reads, and the last 100 lines of it are what the
# `release-broken` issue quotes. It names what was waited for and how long,
# because "did PyPI ever serve this" is the question that decides whether the
# recovery is a re-run or a new version.
elapsed="$(($(date +%s) - started))"
echo "::error title=PyPI never served an installable ${requirement}::pip could not resolve ${requirement} in ${attempts} attempts over ${elapsed}s. The JSON API listed both distributions and their digests matched, so the upload itself succeeded; re-run this job once the simple index catches up, and treat it as a broken release only if it does not."
exit 1
