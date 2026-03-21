"""Template-based finding generation -- used when LLM is not available (Phase 1-2)."""

from __future__ import annotations

from watchtower.analyzers.baseline_compare import AnomalyResult


def _severity_for_anomaly(result: AnomalyResult) -> str:
    """Determine severity based on deviation factor."""
    if result.is_immediate:
        return "critical"
    if result.is_anomaly:
        return "warning"
    return "info"


def finding_from_anomaly(result: AnomalyResult, neighbor_info: dict | None = None) -> dict:
    """Generate a template-based finding from an anomaly result.

    Args:
        result: The anomaly detection result
        neighbor_info: Optional LLDP neighbor info for the affected port

    Returns:
        Dict with severity, summary, and detail fields.
    """
    severity = _severity_for_anomaly(result)
    metric_label = result.metric.replace("_", " ")

    neighbor_str = ""
    if neighbor_info:
        hostname = neighbor_info.get("neighbor_hostname", "unknown")
        port = neighbor_info.get("neighbor_port", "unknown")
        neighbor_str = f" (link to {hostname}:{port})"

    summary = (
        f"{metric_label.capitalize()} anomaly on {result.port}{neighbor_str}. "
        f"Current: {result.current_value:.0f}, "
        f"baseline p95: {result.baseline_p95:.1f} "
        f"({result.deviation_factor:.1f}x deviation)."
    )

    detail = (
        f"Port {result.port} {metric_label} is at {result.current_value:.0f}, "
        f"which is {result.deviation_factor:.1f}x the p95 baseline of {result.baseline_p95:.1f}. "
        f"Baseline p50: {result.baseline_p50:.1f}, p99: {result.baseline_p99:.1f}. "
        f"{'Baseline is warmed up.' if result.warmed_up else 'Baseline is still warming up -- confidence is lower.'}"
    )

    return {
        "severity": severity,
        "summary": summary,
        "detail": detail,
    }


def finding_from_bgp_change(neighbor_ip: str, old_state: str, new_state: str,
                             description: str = "") -> dict:
    """Generate a finding for a BGP session state change."""
    desc_str = f" ({description})" if description else ""

    if new_state == "Established":
        severity = "info"
        summary = f"BGP session to {neighbor_ip}{desc_str} came up."
    elif old_state == "Established":
        severity = "warning"
        summary = f"BGP session to {neighbor_ip}{desc_str} went down (now: {new_state})."
    else:
        severity = "info"
        summary = f"BGP session to {neighbor_ip}{desc_str} changed: {old_state} -> {new_state}."

    return {"severity": severity, "summary": summary, "detail": summary}


def finding_from_link_change(port: str, new_state: str,
                              neighbor_info: dict | None = None) -> dict:
    """Generate a finding for a link state change."""
    neighbor_str = ""
    if neighbor_info:
        hostname = neighbor_info.get("neighbor_hostname", "unknown")
        neighbor_str = f" (link to {hostname})"

    if new_state == "down":
        severity = "warning"
        summary = f"Link down on {port}{neighbor_str}."
    else:
        severity = "info"
        summary = f"Link up on {port}{neighbor_str}."

    return {"severity": severity, "summary": summary, "detail": summary}


def finding_from_optic_degradation(port: str, rx_power_avg: float,
                                    baseline_rx_power: float | None = None,
                                    neighbor_info: dict | None = None) -> dict:
    """Generate a finding for optic power degradation."""
    neighbor_str = ""
    if neighbor_info:
        hostname = neighbor_info.get("neighbor_hostname", "unknown")
        neighbor_str = f" (link to {hostname})"

    baseline_str = ""
    if baseline_rx_power is not None:
        baseline_str = f", baseline: {baseline_rx_power:.1f} dBm"

    severity = "critical" if rx_power_avg < -10.0 else "warning"

    summary = (
        f"Optic degradation on {port}{neighbor_str}. "
        f"RX power: {rx_power_avg:.1f} dBm{baseline_str}."
    )

    return {"severity": severity, "summary": summary, "detail": summary}
