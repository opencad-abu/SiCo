"""Strict LSF output parsing; scheduler observations never imply verified artifacts."""

import re

from ..transport.framing import ProtocolError, strict_json

STATES = {"PEND", "RUN", "PSUSP", "USUSP", "SSUSP", "WAIT", "DONE", "EXIT", "UNKWN", "ZOMBI"}
FIELDS = 'jobid user stat job_name delimiter="|"'


def missing(raw, identity):
    """LSF 10 may return exit 0 with plain text for an absent exact job name."""
    text = raw.strip()
    if text in {'No unfinished job found', 'No unfinished job found.',
                'No job found', 'No job found.',
                'No matching job found', 'No matching job found.'}:
        return True
    match = re.fullmatch(r'Job <([^<>\r\n]+)> is not found\.?', text)
    if match is None:
        return False
    if match[1] != identity:
        raise ProtocolError('LSF missing-job reply belongs to another query')
    return True


def submitted(raw):
    match = re.fullmatch(r"\s*Job <([1-9][0-9]*)> is submitted to queue <[^<>\r\n]+>\.\s*", raw)
    if match is None:
        raise ProtocolError("LSF submission response is unconfirmed")
    return match[1]


def jobs(raw, mode):
    if mode == "json":
        data = strict_json(raw.encode())
        if not isinstance(data, dict) or not isinstance(data.get("RECORDS"), list):
            raise ProtocolError("Invalid LSF structured status")
        rows = [(r.get("JOBID"), r.get("USER"), r.get("STAT"), r.get("JOB_NAME"))
                for r in data["RECORDS"] if isinstance(r, dict)]
        if len(rows) != len(data["RECORDS"]):
            raise ProtocolError("Invalid LSF job record")
    else:
        rows = [tuple(v.strip() for v in line.split("|"))
                for line in raw.splitlines() if line.strip()]
    result = []
    if len(rows) > 256:
        raise ProtocolError("LSF status result exceeds budget")
    for row in rows:
        if (len(row) != 4 or not all(isinstance(v, str) for v in row)
                or not re.fullmatch(r"[1-9][0-9]*", row[0]) or row[2] not in STATES
                or any(len(v) > 256 for v in row)):
            raise ProtocolError("Invalid LSF job status")
        result.append(dict(job_id=row[0], owner=row[1], state=row[2], name=row[3]))
    return result


def history(raw, expected):
    # LSF 10.1 wraps continuation lines with indentation, even inside field names.
    text = re.sub(r"\n[ \t]{10,}", "", raw)
    headers = re.findall(r"Job <([0-9]+)>, Job Name <([^<>]+)>, User <([^<>]+)>", text)
    if len(headers) != 1:
        raise ProtocolError("LSF history identity is unconfirmed")
    if headers[0] != (expected["job_id"], expected["name"], expected["owner"]):
        raise ProtocolError("LSF history belongs to another job")
    state = "DONE" if "Done successfully." in text else "EXIT" if re.search(
        r"Exited (?:with exit code|by signal)|Terminated by", text) else "UNKWN"
    return dict(expected, state=state)
