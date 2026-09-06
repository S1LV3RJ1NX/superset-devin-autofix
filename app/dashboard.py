"""Server-rendered operator dashboard."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from html import escape
from urllib.parse import urlparse

from app.models import Job, JobStatus

REFRESH_SECONDS = 10

_STATUS_LABELS = {
    JobStatus.RECEIVED: "Received",
    JobStatus.QUEUED: "Queued",
    JobStatus.SESSION_CREATED: "Session created",
    JobStatus.RUNNING: "Running",
    JobStatus.SUCCEEDED: "Succeeded",
    JobStatus.FAILED: "Failed",
    JobStatus.TIMED_OUT: "Timed out",
    JobStatus.NEEDS_HUMAN_INPUT: "Needs human review",
}

_PAGE_STYLE = """
:root {
  color-scheme: dark;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
    sans-serif;
  background: #08111f;
  color: #e8eef8;
}
* { box-sizing: border-box; }
body {
  min-width: 320px;
  margin: 0;
  background:
    radial-gradient(circle at 12% 0%, rgba(32, 103, 187, 0.24), transparent 34rem),
    radial-gradient(circle at 90% 20%, rgba(36, 178, 151, 0.12), transparent 30rem),
    #08111f;
}
a { color: #8bc5ff; text-underline-offset: 0.2em; }
a:hover { color: #c2e1ff; }
.shell { width: min(1180px, calc(100% - 40px)); margin: 0 auto; padding: 42px 0 56px; }
.topbar { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; }
.eyebrow {
  margin: 0 0 8px;
  color: #6ee7cf;
  font-size: 0.74rem;
  font-weight: 800;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
h1 { margin: 0; font-size: clamp(2rem, 5vw, 3.5rem); line-height: 1; letter-spacing: -0.05em; }
.subtitle { max-width: 660px; margin: 16px 0 0; color: #9cacbf; line-height: 1.6; }
.live {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
  margin-top: 4px;
  padding: 9px 12px;
  border: 1px solid #223653;
  border-radius: 999px;
  background: rgba(12, 25, 44, 0.78);
  color: #b8c5d8;
  font-size: 0.78rem;
  white-space: nowrap;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #48d7a7;
  box-shadow: 0 0 12px #48d7a7;
}
.section-label {
  margin: 34px 0 13px;
  color: #91a2b8;
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}
.metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.metric, .workflow, .empty, .error {
  border: 1px solid rgba(104, 132, 168, 0.2);
  background: linear-gradient(145deg, rgba(17, 34, 57, 0.94), rgba(11, 24, 42, 0.94));
  box-shadow: 0 18px 46px rgba(0, 0, 0, 0.16);
}
.metric { min-height: 132px; padding: 20px; border-radius: 16px; }
.metric.outcome-metric { grid-column: 1 / -1; min-height: auto; }
.metric-label { color: #92a4ba; font-size: 0.76rem; font-weight: 700; text-transform: uppercase; }
.metric-value { margin-top: 12px; font-size: 2rem; font-weight: 750; letter-spacing: -0.04em; }
.metric-note { margin-top: 7px; color: #6f839d; font-size: 0.78rem; }
.outcomes { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 14px; }
.outcome { padding: 9px; border-radius: 10px; background: rgba(5, 13, 25, 0.42); }
.outcome strong { display: block; font-size: 1.15rem; }
.outcome span { color: #7f92aa; font-size: 0.68rem; }
.workflow { overflow: hidden; border-radius: 20px; }
.workflow-head {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  padding: 25px 26px 22px;
}
.issue-kicker { margin: 0 0 8px; color: #7f92aa; font-size: 0.78rem; }
.issue-title {
  max-width: 780px;
  margin: 0;
  font-size: clamp(1.25rem, 3vw, 1.8rem);
  line-height: 1.28;
}
.issue-title a { color: #f2f6fc; text-decoration: none; }
.issue-title a:hover { color: #8bc5ff; }
.status {
  align-self: flex-start;
  padding: 7px 10px;
  border: 1px solid #32527a;
  border-radius: 999px;
  background: #142b49;
  color: #b9dbff;
  font-size: 0.72rem;
  font-weight: 800;
  white-space: nowrap;
}
.status.succeeded { border-color: #286b5a; background: #123b34; color: #81e6c7; }
.status.failed, .status.timed_out { border-color: #79454a; background: #42242a; color: #ffadb4; }
.status.needs_human_input { border-color: #856930; background: #433619; color: #ffdc8a; }
.callout {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  margin: 0 26px 22px;
  padding: 15px 17px;
  border: 1px solid rgba(255, 205, 102, 0.24);
  border-radius: 12px;
  background: rgba(94, 65, 14, 0.22);
}
.callout p { margin: 0; font-weight: 700; }
.button {
  display: inline-block;
  padding: 9px 13px;
  border-radius: 9px;
  background: #ecf5ff;
  color: #10223a;
  font-size: 0.8rem;
  font-weight: 800;
  text-decoration: none;
  white-space: nowrap;
}
.button:hover { color: #10223a; background: white; }
.timeline { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid #1e314d; }
.time { padding: 18px 26px; border-right: 1px solid #1e314d; }
.time:last-child { border-right: 0; }
.time-label {
  display: block;
  margin-bottom: 5px;
  color: #788ba4;
  font-size: 0.7rem;
  text-transform: uppercase;
}
.time-value { color: #d7e1ef; font-size: 0.88rem; }
details { border-top: 1px solid #1e314d; }
summary { padding: 17px 26px; color: #8fa3bc; cursor: pointer; font-size: 0.82rem; }
.details-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 20px;
  padding: 0 26px 25px;
}
.detail { min-width: 0; }
.detail h3 { margin: 0 0 7px; color: #7f92aa; font-size: 0.7rem; text-transform: uppercase; }
.detail p, .detail li {
  margin: 0;
  color: #c4d0df;
  font-size: 0.84rem;
  line-height: 1.55;
  overflow-wrap: anywhere;
}
.detail ul { margin: 0; padding-left: 18px; }
.detail.full { grid-column: 1 / -1; }
.empty, .error { padding: 48px 28px; border-radius: 20px; text-align: center; }
.empty h2, .error h2 { margin: 0 0 8px; }
.empty p, .error p { max-width: 520px; margin: 0 auto; color: #8ea0b7; line-height: 1.6; }
.footer { margin-top: 20px; color: #667990; font-size: 0.72rem; text-align: right; }
@media (max-width: 820px) {
  .metrics { grid-template-columns: repeat(2, 1fr); }
  .topbar, .workflow-head, .callout { align-items: flex-start; flex-direction: column; }
  .timeline { grid-template-columns: 1fr; }
  .time { border-right: 0; border-bottom: 1px solid #1e314d; }
  .time:last-child { border-bottom: 0; }
}
@media (max-width: 520px) {
  .shell { width: min(100% - 24px, 1180px); padding-top: 26px; }
  .metrics, .details-grid { grid-template-columns: 1fr; }
  .metric { min-height: auto; }
  .outcomes { grid-template-columns: repeat(2, 1fr); }
  .detail.full { grid-column: auto; }
}
"""


def render_dashboard(
    metrics: Mapping[str, object],
    latest_job: Job | None,
    *,
    generated_at: datetime | None = None,
) -> str:
    """Render the dashboard from durable repository data."""
    rendered_at = generated_at or datetime.now(UTC)
    terminal_counts = _terminal_counts(metrics)
    metric_cards = "".join(
        (
            _metric("Tasks started", _integer(metrics, "tasks_started"), "Production workflows"),
            _metric("Active tasks", _integer(metrics, "active_tasks"), "Received through running"),
            _metric("PRs opened", _integer(metrics, "tasks_with_pr"), "Observed production PRs"),
            _metric(
                "Completion rate",
                _percentage(metrics.get("completion_rate")),
                "Succeeded outcomes",
            ),
            _metric(
                "Average time to PR",
                _format_duration(_number(metrics.get("average_elapsed_seconds_to_pr"))),
                "From webhook receipt",
            ),
            _metric(
                "Simulated tasks",
                _integer(metrics, "simulated_tasks"),
                "Excluded from production",
            ),
            _outcome_metric(terminal_counts),
        )
    )
    body = f"""
      <header class="topbar">
        <div>
          <p class="eyebrow">Autofix control plane</p>
          <h1>Engineering operations</h1>
          <p class="subtitle">Live remediation throughput and the latest production workflow,
          read directly from the durable control-plane database.</p>
        </div>
        <div class="live"><span class="dot"></span>Live · refreshes every {REFRESH_SECONDS}s</div>
      </header>
      <p class="section-label">Control-plane summary</p>
      <section class="metrics" aria-label="Control-plane summary">
        {metric_cards}
      </section>
      <p class="section-label">Latest workflow</p>
      {_workflow(latest_job)}
      <p class="footer">Snapshot generated {_format_datetime(rendered_at)}</p>
    """
    return _page("Engineering operations", body)


def render_dashboard_error() -> str:
    """Render a retryable dashboard data error."""
    body = f"""
      <header class="topbar">
        <div>
          <p class="eyebrow">Autofix control plane</p>
          <h1>Engineering operations</h1>
        </div>
        <div class="live"><span class="dot"></span>Retrying in {REFRESH_SECONDS}s</div>
      </header>
      <p class="section-label">Dashboard status</p>
      <section class="error" role="alert">
        <h2>Dashboard data is temporarily unavailable</h2>
        <p>The durable control-plane database could not be read. Automatic refresh will retry
        without changing any workflow state.</p>
      </section>
    """
    return _page("Dashboard unavailable", body)


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="{REFRESH_SECONDS}">
  <title>{escape(title)}</title>
  <style>{_PAGE_STYLE}</style>
</head>
<body>
  <main class="shell">{body}</main>
</body>
</html>
"""


def _metric(label: str, value: str | int, note: str) -> str:
    return f"""
        <article class="metric">
          <div class="metric-label">{escape(label)}</div>
          <div class="metric-value">{escape(str(value))}</div>
          <div class="metric-note">{escape(note)}</div>
        </article>
    """


def _outcome_metric(counts: Mapping[JobStatus, int]) -> str:
    outcomes = (
        (JobStatus.SUCCEEDED, "Succeeded"),
        (JobStatus.NEEDS_HUMAN_INPUT, "Human review"),
        (JobStatus.FAILED, "Failed"),
        (JobStatus.TIMED_OUT, "Timed out"),
    )
    items = "".join(
        f'<div class="outcome"><strong>{counts[status]}</strong><span>{label}</span></div>'
        for status, label in outcomes
    )
    return f"""
        <article class="metric outcome-metric">
          <div class="metric-label">Terminal outcomes</div>
          <div class="outcomes">{items}</div>
        </article>
    """


def _workflow(job: Job | None) -> str:
    if job is None:
        return """
      <section class="empty">
        <h2>No production workflows yet</h2>
        <p>A qualifying GitHub label event will create the first durable workflow. Simulations
        remain visible in the summary but are not presented as production work.</p>
      </section>
        """

    issue_url = _safe_url(job.issue_url)
    issue_title = escape(job.issue_title)
    issue_heading = (
        f'<a href="{issue_url}" target="_blank" rel="noopener noreferrer">{issue_title}</a>'
        if issue_url
        else issue_title
    )
    pr_url = _safe_url(job.pr_url)
    pr_action = (
        f'<a class="button" href="{pr_url}" target="_blank" '
        'rel="noopener noreferrer">Open pull request</a>'
        if pr_url
        else ""
    )
    return f"""
      <article class="workflow">
        <div class="workflow-head">
          <div>
            <p class="issue-kicker">{escape(job.repository)} · Issue #{job.issue_number}</p>
            <h2 class="issue-title">{issue_heading}</h2>
          </div>
          <span class="status {job.status.value}">{_STATUS_LABELS[job.status]}</span>
        </div>
        <div class="callout">
          <p>{escape(_workflow_message(job, has_pr=pr_url is not None))}</p>
          {pr_action}
        </div>
        <div class="timeline">
          {_time_item("Created", _format_datetime(job.received_at))}
          {_time_item("Completed", _format_datetime(job.completed_at))}
          {_time_item("Time to PR", _format_duration(job.elapsed_seconds_to_pr))}
        </div>
        {_workflow_details(job)}
      </article>
    """


def _workflow_message(job: Job, *, has_pr: bool) -> str:
    if job.status is JobStatus.NEEDS_HUMAN_INPUT:
        return (
            "PR opened — human review required"
            if has_pr
            else "Human input required before work can continue"
        )
    if job.status is JobStatus.SUCCEEDED:
        return "Remediation completed — PR ready for review" if has_pr else "Remediation completed"
    if job.status is JobStatus.FAILED:
        return "Workflow failed — operator attention required"
    if job.status is JobStatus.TIMED_OUT:
        return "Workflow timed out — remote session terminated"
    return "Devin remediation is in progress"


def _time_item(label: str, value: str) -> str:
    return f"""
          <div class="time">
            <span class="time-label">{escape(label)}</span>
            <span class="time-value">{escape(value)}</span>
          </div>
    """


def _workflow_details(job: Job) -> str:
    details = [
        _detail("Repository", escape(job.repository)),
        _link_detail("Devin session", job.devin_url, "Open Devin session"),
    ]
    summary = _structured_string(job.structured_output, "summary")
    if summary:
        details.append(_detail("Completion summary", escape(summary), full=True))
    if job.last_message:
        details.append(_detail("Latest Devin update", escape(job.last_message), full=True))
    if job.error:
        details.append(_detail("Operator note", escape(job.error), full=True))
    validation = _structured_strings(job.structured_output, "validation")
    if validation:
        details.append(_list_detail("Validation", validation))
    limitations = _structured_strings(job.structured_output, "limitations")
    if limitations:
        details.append(_list_detail("Limitations", limitations))
    return f"""
        <details>
          <summary>Show workflow details</summary>
          <div class="details-grid">{"".join(details)}</div>
        </details>
    """


def _detail(label: str, value: str, *, full: bool = False) -> str:
    css_class = "detail full" if full else "detail"
    return f'<div class="{css_class}"><h3>{escape(label)}</h3><p>{value}</p></div>'


def _link_detail(label: str, value: str | None, link_label: str) -> str:
    url = _safe_url(value)
    if url is None:
        return _detail(label, "Not available")
    return _detail(
        label,
        f'<a href="{url}" target="_blank" rel="noopener noreferrer">{escape(link_label)}</a>',
    )


def _list_detail(label: str, values: list[str]) -> str:
    items = "".join(f"<li>{escape(value)}</li>" for value in values)
    return f'<div class="detail"><h3>{escape(label)}</h3><ul>{items}</ul></div>'


def _terminal_counts(metrics: Mapping[str, object]) -> dict[JobStatus, int]:
    raw_counts = metrics.get("terminal_status_counts")
    counts = raw_counts if isinstance(raw_counts, Mapping) else {}
    terminal_statuses = (
        JobStatus.SUCCEEDED,
        JobStatus.NEEDS_HUMAN_INPUT,
        JobStatus.FAILED,
        JobStatus.TIMED_OUT,
    )
    return {status: _coerce_integer(counts.get(status.value)) for status in terminal_statuses}


def _integer(metrics: Mapping[str, object], key: str) -> int:
    return _coerce_integer(metrics.get(key))


def _coerce_integer(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        return int(value)
    return 0


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _percentage(value: object) -> str:
    number = _number(value)
    if number is None:
        return "—"
    return f"{number * 100:.1f}%"


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total_seconds = max(0, round(seconds))
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, remaining_seconds = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remaining_seconds}s"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {remaining_minutes}m"
    days, remaining_hours = divmod(hours, 24)
    return f"{days}d {remaining_hours}h"


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone(UTC).strftime("%b %d, %Y · %H:%M UTC")


def _safe_url(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return escape(value, quote=True)


def _structured_string(output: Mapping[str, object] | None, key: str) -> str | None:
    if output is None:
        return None
    value = output.get(key)
    return value if isinstance(value, str) else None


def _structured_strings(output: Mapping[str, object] | None, key: str) -> list[str]:
    if output is None:
        return []
    value = output.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
